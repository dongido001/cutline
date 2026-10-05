"""Cast guidance regression tests for both image generation entry points."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from backend import draft, media, tasks
from backend.cast import load_cast_references
from backend.database import Base
from backend.models import Character, CharacterReference, Job, Project, Scene, ScriptVersion, Shot, ShotContinuityFrame, ShotImage, ShotTake
from backend.settings import settings

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'cast.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, 'SessionLocal', sessionmaker(engine))
    with Session(engine) as db:
        project = Project(title='Cast regression', status='shot_plan', aspect_ratio='16:9')
        version = ScriptVersion(project=project, source_text='test')
        scene = Scene(script_version=version, order_index=0, title='Kitchen', wardrobe_notes='Blue coat')
        shot = Shot(scene=scene, order_index=0, title='Two shot', image_prompt='MARA and ELIAS in the kitchen')
        mara = Character(project=project, name='MARA')
        elias = Character(project=project, name='ELIAS')
        for character in (mara, elias):
            filename = character.name + '.png'
            character.references.append(CharacterReference(filename=filename, mime_type='image/png'))
            (settings.media_dir / filename).write_bytes(character.name.encode())
        job = Job(project=project, kind='shot_image', status='queued')
        db.add_all([project, job]); db.flush()
        shot.character_ids = [elias.id, mara.id, elias.id]
        db.commit()
        yield db, project, shot, job, mara, elias
    engine.dispose()

def run_generation(path, db, project, shot, job):
    if path == 'draft':
        return draft._generate_missing_images(db, project, job, [shot])
    tasks._generate_shot_image(shot.id, job.id)
    db.expire_all()
    return []

@pytest.mark.parametrize('path', ['draft', 'manual'])
@pytest.mark.parametrize('with_cast', [True, False])
def test_exact_cast_and_continuity(setup, monkeypatch, path, with_cast):
    db, project, shot, job, mara, elias = setup
    if not with_cast:
        shot.character_ids = []; db.commit()
    calls = []
    def generate(prompt, scene, framing, aspect, **kwargs):
        calls.append((prompt, kwargs['references']))
        (settings.media_dir / 'generated.png').write_bytes(b'image')
        return 'generated.png', 'image/png', 'test'
    monkeypatch.setattr(media, 'generate_storyboard_image', generate)
    monkeypatch.setattr(tasks, 'generate_storyboard_image', generate)
    assert run_generation(path, db, project, shot, job) == []
    assert len(calls) == 1
    assert 'Wardrobe: Blue coat' in calls[0][0]
    assert [(r.character_name, r.data) for r in calls[0][1]] == (
        [('ELIAS', b'ELIAS'), ('MARA', b'MARA')] if with_cast else [])
    assert shot.image.filename == 'generated.png'
    if with_cast:
        assert shot.continuity_snapshot['character_ids'] == [elias.id, mara.id]
        assert {item['name'] for item in shot.continuity_snapshot['characters']} == {'ELIAS', 'MARA'}
        image_take = next(take for take in shot.takes if take.media_type == 'image')
        assert image_take.settings['continuity_snapshot'] == shot.continuity_snapshot
        assert {ref_id for item in shot.continuity_snapshot['characters'] for ref_id in item['reference_ids']} == {
            character.references[0].id for character in (elias, mara)
        }

@pytest.mark.parametrize('path', ['draft', 'manual'])
def test_missing_reference_blocks_generation(setup, monkeypatch, path):
    db, project, shot, job, mara, elias = setup
    (settings.media_dir / 'MARA.png').unlink()
    def unexpected(*args, **kwargs):
        pytest.fail('Must not generate with only part of the selected cast')
    monkeypatch.setattr(media, 'generate_storyboard_image', unexpected)
    monkeypatch.setattr(tasks, 'generate_storyboard_image', unexpected)
    if path == 'draft':
        errors = run_generation(path, db, project, shot, job)
        assert len(errors) == 1 and 'MARA' in errors[0]
        assert shot.image is None
    else:
        shot.image = ShotImage(filename='existing.png', mime_type='image/png', source='uploaded', provider='manual')
        (settings.media_dir / 'existing.png').write_bytes(b'original')
        db.commit()
        with pytest.raises(RuntimeError, match='MARA'):
            run_generation(path, db, project, shot, job)
        db.expire_all()
        assert job.status == 'failed'
        assert shot.image.filename == 'existing.png'
        assert (settings.media_dir / 'existing.png').read_bytes() == b'original'

def test_unknown_and_cross_project_cast_rejected(setup):
    db, project, shot, job, mara, elias = setup
    other = Project(title='Other')
    outsider = Character(project=other, name='OTHER')
    db.add(other); db.commit()
    for ids in ([mara.id, 'missing-id'], [mara.id, outsider.id]):
        with pytest.raises(RuntimeError, match='not found in this project'):
            load_cast_references(db, project.id, ids)

def test_draft_resume_preserves_images(setup, monkeypatch):
    db, project, shot, job, mara, elias = setup
    shot.image = ShotImage(filename='existing.png', mime_type='image/png', source='uploaded', provider='manual')
    db.commit()
    def unexpected(*args, **kwargs):
        pytest.fail('Resume must not regenerate existing images')
    monkeypatch.setattr(media, 'generate_storyboard_image', unexpected)
    assert draft._generate_missing_images(db, project, job, [shot]) == []
    assert shot.image.filename == 'existing.png'


def test_video_worker_uses_approved_image_and_its_saved_snapshot(setup, monkeypatch):
    db, project, shot, _job, mara, _elias = setup
    snapshot = {
        'version': 1, 'character_ids': [mara.id],
        'characters': [{'id': mara.id, 'name': 'MARA', 'reference_ids': [mara.references[0].id]}],
        'scene': {'title': 'Kitchen', 'location': 'Kitchen', 'time_of_day': 'Night',
                  'set_notes': 'Rain window', 'lighting_notes': 'Blue light',
                  'wardrobe_notes': 'Green coat', 'props_notes': 'Red mug'},
        'framing': 'Close-up', 'action': 'Mara looks up.', 'video_prompt': 'Slow push-in.',
        'aspect_ratio': '16:9', 'image_prompt_base': 'MARA in the kitchen',
        'effective_image_prompt': 'MARA in the kitchen with saved continuity',
    }
    image_path = settings.media_dir / 'approved.png'
    image_path.write_bytes(b'approved frame')
    shot.image = ShotImage(filename='approved.png', mime_type='image/png', source='generated', provider='test')
    shot.continuity_snapshot = snapshot
    shot.scene.wardrobe_notes = 'A changed current wardrobe'
    shot.takes.append(ShotTake(
        media_type='image', aspect_ratio='16:9', filename='approved.png', mime_type='image/png',
        source='generated', provider='test', prompt=snapshot['effective_image_prompt'],
        settings={'continuity_snapshot': snapshot}, selected=True,
    ))
    video_job = Job(project=project, kind='shot_clip', status='queued')
    db.add(video_job)
    db.commit()
    called = {}

    def render(image, prompt, duration, aspect, scene_id=None):
        called.update(image=image, prompt=prompt, aspect=aspect)
        (settings.media_dir / 'clip.mp4').write_bytes(b'clip')
        return 'clip.mp4', 'video/mp4', 'flowkit'

    monkeypatch.setattr(tasks, 'render_shot_clip', render)
    monkeypatch.setattr(tasks.settings, 'video_provider', 'flowkit')
    tasks._generate_shot_clip(shot.id, video_job.id)
    assert called['image'] == image_path
    assert 'exact opening frame and visual anchor' in called['prompt']
    assert 'Wardrobe continuity: Green coat.' in called['prompt']
    assert 'A changed current wardrobe' not in called['prompt']


def test_video_worker_prefers_selected_continuity_frame(setup, monkeypatch):
    db, project, shot, _job, _mara, _elias = setup
    image_path = settings.media_dir / 'storyboard.png'
    frame_path = settings.media_dir / 'continuity.png'
    image_path.write_bytes(b'storyboard image')
    frame_path.write_bytes(b'previous-shot frame')
    shot.image = ShotImage(
        filename='storyboard.png',
        mime_type='image/png',
        source='generated',
        provider='test',
    )
    shot.continuity_frames.append(ShotContinuityFrame(
        aspect_ratio='16:9',
        filename='continuity.png',
        mime_type='image/png',
        source_shot_id='previous-shot',
        source_shot_title='Doorway',
        source_time_ms=2400,
    ))
    video_job = Job(project=project, kind='shot_clip', status='queued')
    db.add(video_job)
    db.commit()
    called = {}

    def render(image, prompt, duration, aspect, scene_id=None):
        called.update(image=image, prompt=prompt, scene_id=scene_id)
        (settings.media_dir / 'continuity-clip.mp4').write_bytes(b'clip')
        return 'continuity-clip.mp4', 'video/mp4', 'flowkit'

    monkeypatch.setattr(tasks, 'render_shot_clip', render)
    monkeypatch.setattr(tasks.settings, 'video_provider', 'flowkit')
    tasks._generate_shot_clip(shot.id, video_job.id)

    assert called['image'] == frame_path
    assert 'Continue directly from the selected frame of the previous shot' in called['prompt']
    assert 'Preserve its background, set layout, lighting, props' in called['prompt']
    clip_take = next(take for take in shot.takes if take.media_type == 'clip')
    assert clip_take.settings['continuity_frame']['source_time_ms'] == 2400


def test_draft_clip_uses_the_image_snapshot_for_video_prompt(setup, monkeypatch):
    db, project, shot, job, mara, _elias = setup
    snapshot = {
        'version': 1, 'character_ids': [mara.id],
        'characters': [{'id': mara.id, 'name': 'MARA', 'reference_ids': [mara.references[0].id]}],
        'scene': {'title': 'Kitchen', 'location': 'Kitchen', 'time_of_day': 'Night',
                  'set_notes': 'Rain window', 'lighting_notes': 'Blue light',
                  'wardrobe_notes': 'Green coat', 'props_notes': 'Red mug'},
        'framing': 'Close-up', 'action': 'Mara looks up.', 'video_prompt': 'Slow push-in.',
        'aspect_ratio': '16:9', 'image_prompt_base': 'MARA in the kitchen',
        'effective_image_prompt': 'MARA in the kitchen with saved continuity',
    }
    image_path = settings.media_dir / 'draft-approved.png'
    image_path.write_bytes(b'approved frame')
    shot.image = ShotImage(filename='draft-approved.png', mime_type='image/png', source='generated', provider='test')
    shot.continuity_snapshot = snapshot
    shot.scene.wardrobe_notes = 'Changed wardrobe'
    shot.takes.append(ShotTake(
        media_type='image', aspect_ratio='16:9', filename='draft-approved.png', mime_type='image/png',
        source='generated', provider='test', prompt=snapshot['effective_image_prompt'],
        settings={'continuity_snapshot': snapshot}, selected=True,
    ))
    db.commit()
    called = {}

    def render(image, prompt, duration, aspect, scene_id=None):
        called.update(image=image, prompt=prompt, scene_id=scene_id)
        (settings.media_dir / 'draft-clip.mp4').write_bytes(b'clip')
        return 'draft-clip.mp4', 'video/mp4', 'flowkit'

    monkeypatch.setattr(draft, 'video_provider_status', lambda: ('flowkit', 'FlowKit', True, True, 'veo', 'ready'))
    monkeypatch.setattr(draft, 'generate_shot_clip', render)
    assert draft._generate_missing_clips(db, project, job, [shot]) == []
    assert called['scene_id'] == str(shot.scene.id)
    assert called['image'] == image_path
    assert 'exact opening frame and visual anchor' in called['prompt']
    assert 'Wardrobe continuity: Green coat.' in called['prompt']
    assert 'Changed wardrobe' not in called['prompt']


def test_draft_clip_prefers_selected_continuity_frame(setup, monkeypatch):
    db, project, shot, job, _mara, _elias = setup
    frame_path = settings.media_dir / 'draft-continuity.png'
    frame_path.write_bytes(b'previous-shot frame')
    shot.continuity_frames.append(ShotContinuityFrame(
        aspect_ratio='16:9',
        filename='draft-continuity.png',
        mime_type='image/png',
        source_shot_id='previous-shot',
        source_shot_title='Doorway',
        source_time_ms=3300,
    ))
    db.commit()
    called = {}

    def render(image, prompt, duration, aspect, scene_id=None):
        called.update(image=image, prompt=prompt, scene_id=scene_id)
        (settings.media_dir / 'draft-continuity-clip.mp4').write_bytes(b'clip')
        return 'draft-continuity-clip.mp4', 'video/mp4', 'flowkit'

    monkeypatch.setattr(draft, 'video_provider_status', lambda: ('flowkit', 'FlowKit', True, True, 'veo', 'ready'))
    monkeypatch.setattr(draft, 'generate_shot_clip', render)
    assert draft._generate_missing_clips(db, project, job, [shot]) == []
    assert called['scene_id'] == str(shot.scene.id)

    assert called['image'] == frame_path
    assert 'Continue directly from the selected frame of the previous shot' in called['prompt']
    clip_take = next(take for take in shot.takes if take.media_type == 'clip')
    assert clip_take.settings['continuity_frame']['source_shot_title'] == 'Doorway'
