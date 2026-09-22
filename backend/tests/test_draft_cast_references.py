"""Cast guidance regression tests for both image generation entry points."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from backend import draft, media, tasks
from backend.cast import load_cast_references
from backend.database import Base
from backend.models import Character, CharacterReference, Job, Project, Scene, ScriptVersion, Shot, ShotImage
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
