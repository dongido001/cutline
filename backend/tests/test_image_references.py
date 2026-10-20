from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend import api, draft, media, tasks
from backend.database import Base, get_db
from backend.image_references import reference_snapshot
from backend.models import Job, Project, Scene, ScriptVersion, Shot, ShotClip, ShotImage, ShotTake
from backend.settings import settings


def png(color='red'):
    buffer = BytesIO()
    Image.new('RGB', (32, 24), color).save(buffer, format='PNG')
    return buffer.getvalue()


def test_automatic_reference_never_uses_another_room_with_a_shared_compound_name(setup):
    from backend.image_references import default_visual_reference, location_establishing_shot
    db, client, project, shots = setup
    shots[0].scene.location = "COMPOUND COURTYARD"
    kitchen = Scene(script_version=shots[0].scene.script_version, order_index=1, title="Dinner", location="@COMPOUND_KITCHEN")
    shots[2].scene = kitchen
    db.add(kitchen)
    db.commit()
    assert location_establishing_shot(shots[2]) is None
    assert default_visual_reference(shots[2], '16:9') is None
    kitchen.location = "EXT. COMPOUND COURTYARD - NIGHT"
    assert location_establishing_shot(shots[2]).id == shots[0].id


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'refs.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, 'SessionLocal', sessionmaker(engine))
    monkeypatch.setattr(api, 'enqueue_shot_image', lambda *a, **kw: SimpleNamespace(id='queued-image'))
    monkeypatch.setattr(api, 'enqueue_create_draft', lambda *a, **kw: SimpleNamespace(id='queued-draft'))
    with Session(engine) as db:
        project = Project(title='References', status='shot_plan', aspect_ratio='16:9')
        version = ScriptVersion(project=project, source_text='test')
        scene = Scene(script_version=version, title='Room', order_index=0)
        shots = [Shot(scene=scene, order_index=i, title=f'Shot {i + 1}', image_prompt='Wide view of an empty room', framing='Wide') for i in range(3)]
        for i, shot in enumerate(shots[:2]):
            filename = f'still-{i}.png'
            (settings.media_dir / filename).write_bytes(png('red' if i == 0 else 'blue'))
            shot.image = ShotImage(filename=filename, mime_type='image/png', source='uploaded', provider='test')
        shots[0].clip = ShotClip(filename='clip.mp4', mime_type='video/mp4', source='generated', provider='test')
        (settings.media_dir / 'clip.mp4').write_bytes(b'video')
        db.add(project)
        db.commit()
        api.app.dependency_overrides[get_db] = lambda: db
        try:
            yield db, TestClient(api.app), project, shots
        finally:
            api.app.dependency_overrides.clear()
    engine.dispose()


def choose(client, target, source, aspect='16:9', video=False):
    item = source.clip if video else source.image
    data = dict(source_shot_id=source.id, source_media_id=item.id,
                source_media_kind='clip' if video else 'image', aspect_ratio=aspect)
    files = None
    if video:
        data['source_time_ms'] = '1234'
        files = {'file': ('frame.png', png('green'), 'image/png')}
    return client.post(f'/api/shots/{target.id}/image-reference', data=data, files=files)


@pytest.mark.parametrize('source_index,video', [(1, False), (0, False), (0, True)])
@pytest.mark.parametrize('aspect', ['16:9', '9:16'])
def test_sources_persist_and_survive_source_changes(setup, source_index, video, aspect):
    db, client, project, shots = setup
    sources = client.get(f'/api/shots/{shots[2].id}/image-reference/sources').json()
    assert [s['id'] for s in sources] == [s.id for s in shots[:2]]
    response = choose(client, shots[2], shots[source_index], aspect, video)
    assert response.status_code == 200, response.text
    ref = response.json()['image_references'][0]
    assert ref['aspect_ratio'] == aspect
    assert ref['source_time_ms'] == (1234 if video else None)
    assert ref['source_media_type'] == ('clip' if video else 'image')
    expected = png('green') if video else png('red' if source_index == 0 else 'blue')
    assert (settings.media_dir / ref['filename']).read_bytes() == expected
    source_media = shots[source_index].clip if video else shots[source_index].image
    (settings.media_dir / source_media.filename).unlink()
    db.expire_all()
    assert reference_snapshot(shots[2], aspect)['filename'] == ref['filename']
    assert (settings.media_dir / ref['filename']).read_bytes() == expected
    assert not shots[2].continuity_frames


def test_validation_and_unused_asset_cleanup(setup):
    db, client, project, shots = setup
    assert choose(client, shots[0], shots[1]).status_code == 409
    assert choose(client, shots[1], shots[1]).status_code == 409
    assert choose(client, shots[2], shots[0], '4:3').status_code == 422
    data = dict(source_shot_id=shots[0].id, source_media_id=shots[0].clip.id,
                source_media_kind='clip', aspect_ratio='16:9', source_time_ms='20')
    assert client.post(f'/api/shots/{shots[2].id}/image-reference', data=data).status_code == 422
    assert client.post(f'/api/shots/{shots[2].id}/image-reference', data=data,
                       files={'file': ('bad.png', b'not image', 'image/png')}).status_code == 400
    first = choose(client, shots[2], shots[0]).json()['image_references'][0]
    second = choose(client, shots[2], shots[1]).json()['image_references'][0]
    assert not (settings.media_dir / first['filename']).exists()
    assert client.delete(f'/api/shots/{shots[2].id}/image-reference?aspect_ratio=16:9').status_code == 204
    assert not (settings.media_dir / second['filename']).exists()
    assert not reference_snapshot(shots[2], '16:9')


@pytest.mark.parametrize('path', ['manual', 'draft'])
@pytest.mark.parametrize('with_reference', [True, False])
def test_queue_snapshot_provider_bytes_and_historical_take(setup, monkeypatch, path, with_reference):
    db, client, project, shots = setup
    target = shots[2]
    first = (
        choose(client, target, shots[0], video=True).json()['image_references'][0]
        if with_reference
        else None
    )
    if path == 'manual':
        response = client.post(
            f'/api/shots/{target.id}/image/generate',
            json={
                'image_reference_id': first['id'] if with_reference else None,
                'use_default_image_reference': not with_reference,
            },
        )
    else:
        response = client.post(f'/api/projects/{project.id}/draft', json={'variant': 'landscape'})
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    choose(client, target, shots[1])
    client.delete(f'/api/shots/{target.id}/image-reference?aspect_ratio=16:9')
    calls = []
    def request(provider, prompt, aspect, progress=None, references=None):
        calls.append((prompt, aspect, references))
        return png(), 'test', {}
    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', request)
    if path == 'manual':
        tasks._generate_shot_image(target.id, job_id)
    else:
        assert draft._generate_missing_images(db, project, db.get(Job, job_id), [target]) == []
    db.expire_all()
    job = db.get(Job, job_id)
    if path == 'manual':
        assert job.status == 'completed', job.error
    assert len(calls) == 1
    prompt, aspect, references = calls[0]
    assert aspect == '16:9'
    assert '1344x768' in prompt
    take = db.scalar(select(ShotTake).where(ShotTake.shot_id == target.id))
    if with_reference:
        assert references[0].data == png('green')
        assert 'composition remain authoritative' in prompt
        assert take.settings['image_reference'] == first
        assert (settings.media_dir / first['filename']).is_file()
        assert 'Visual continuity reference' in take.prompt
    else:
        assert references[0].data == png('blue')
        assert 'composition remain authoritative' in prompt
        assert take.settings['image_reference']['source_shot_id'] == shots[1].id
        assert take.settings['image_reference']['filename'].startswith('image-reference-')
        assert (settings.media_dir / take.settings['image_reference']['filename']).is_file()
        assert 'Visual continuity reference' in take.prompt
    choose(client, target, shots[1])
    client.delete(f'/api/shots/{target.id}/image-reference?aspect_ratio=16:9')
    db.refresh(take)
    if with_reference:
        assert take.settings.get('image_reference') == first
    else:
        assert take.settings['image_reference']['source_shot_id'] == shots[1].id


def test_skip_default_previous_shot_still(setup, monkeypatch):
    db, client, _, shots = setup
    target = shots[2]
    response = client.post(
        f'/api/shots/{target.id}/image/generate',
        json={'use_default_image_reference': False},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    calls = []

    def request(provider, prompt, aspect, progress=None, references=None):
        calls.append((prompt, aspect, references))
        return png(), 'test', {}

    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', request)
    tasks._generate_shot_image(target.id, job_id)
    prompt, aspect, references = calls[0]
    assert references == []
    assert 'Visual continuity reference' not in prompt
    take = db.scalar(select(ShotTake).where(ShotTake.shot_id == target.id))
    assert 'image_reference' not in take.settings


def test_selected_reference_must_exist_at_queue_time(setup):
    _, client, _, shots = setup
    target = shots[2]
    choose(client, target, shots[0])

    response = client.post(
    f'/api/shots/{target.id}/image/generate',
    json={'image_reference_id': 'stale-reference-id'},
    )

    assert response.status_code == 409
    assert 'no longer available' in response.json()['detail']


def test_missing_queued_reference_fails_without_calling_provider(setup, monkeypatch):
    db, client, _, shots = setup
    target = shots[2]
    reference = choose(client, target, shots[0]).json()['image_references'][0]
    response = client.post(
        f'/api/shots/{target.id}/image/generate',
        json={'image_reference_id': reference['id']},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    (settings.media_dir / reference['filename']).unlink()

    calls = []
    monkeypatch.setattr(media, '_request_provider_image', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match='Saved image reference is missing'):
        tasks._generate_shot_image(target.id, job_id)

    db.expire_all()
    job = db.get(Job, job_id)
    assert job.status == 'failed'
    assert calls == []


@pytest.mark.parametrize('aspect,size', [('9:16', '768x1344'), ('16:9', '1344x768')])
def test_provider_transport_keeps_dimensions_and_reference_roles(monkeypatch, tmp_path, aspect, size):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(settings, 'image_providers', 'unsupported,chatgpt_web,gemini_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [
        {'id': p, 'available': True} for p in ['unsupported', 'chatgpt_web', 'gemini_web']])
    calls = []
    def request(provider, prompt, aspect, progress=None, references=None):
        calls.append(provider)
        assert size in prompt
        assert 'Identity references are attached for: MARA.' in prompt
        assert 'Visual continuity reference (not a cast portrait)' not in prompt
        assert len(references) == 2
        assert references[1].data == png('green')
        if provider == 'chatgpt_web':
            raise RuntimeError('offline')
        return png(), 'test', {}
    monkeypatch.setattr(media, '_request_provider_image', request)
    media.generate_storyboard_image('A wide shot', 'Room', 'Wide', aspect, references=[
        media.FaceReferenceImage('MARA', 'image/png', png()),
        media.VisualReferenceImage('image/png', png('green'))])
    assert calls == ['chatgpt_web', 'gemini_web']


def test_no_placeholder_for_visual_reference(monkeypatch):
    monkeypatch.setattr(settings, 'image_providers', '')
    with pytest.raises(RuntimeError, match='supports image references'):
        media.generate_storyboard_image('room', 'room', 'wide', '16:9', references=[media.VisualReferenceImage('image/png', png())])


def test_face_reference_attachment_failure_does_not_drop_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    calls = []
    def request(provider, prompt, aspect, progress=None, references=None):
        calls.append({'provider': provider, 'has_refs': bool(references)})
        if references:
            raise RuntimeError('ChatGPT did not finish uploading the face reference image(s)')
        return png(), 'test', {}
    monkeypatch.setattr(media, '_request_provider_image', request)
    with pytest.raises(RuntimeError, match='ChatGPT did not finish uploading'):
        media.generate_storyboard_image(
            'A portrait of Gabe', 'Room', 'Close-up', '16:9',
            references=[media.FaceReferenceImage('GABE', 'image/png', png())],
        )
    assert len(calls) == 1
    assert calls[0] == {'provider': 'chatgpt_web', 'has_refs': True}


def test_upload_shot_image_and_followup_edit_take(setup, monkeypatch):
    db, client, project, shots = setup
    shot = shots[2]

    # 1. Upload an image directly -> should create an uploaded ShotTake and select it
    upload_res = client.post(
        f'/api/shots/{shot.id}/image/upload',
        files={'file': ('custom.png', png('purple'), 'image/png')},
    )
    assert upload_res.status_code == 200, upload_res.text
    shot_data = upload_res.json()
    image_takes = [t for t in shot_data['takes'] if t['media_type'] == 'image']
    assert len(image_takes) == 1
    take1 = image_takes[0]
    assert take1['source'] == 'uploaded'
    assert take1['selected'] is True
    assert shot_data['selected_image_take_id'] == take1['id']

    # Set a conversation_url on take1 to test in-conversation follow-up edit
    take1_row = db.get(ShotTake, take1['id'])
    take1_row.settings = {'conversation_url': 'https://chatgpt.com/c/existing-conv-123'}
    db.commit()

    # 2. Submit a follow-up edit on take1 with an attached reference image
    edit_res = client.post(
        f'/api/shots/{shot.id}/takes/{take1["id"]}/edit',
        data={'prompt': 'Change her jacket to emerald green'},
        files=[('files', ('jacket_ref.png', png('green'), 'image/png'))],
    )
    assert edit_res.status_code == 202, edit_res.text
    job_data = edit_res.json()
    assert job_data['conversation_url'] == 'https://chatgpt.com/c/existing-conv-123'

    seen = {}

    def fake_request(provider, prompt, aspect, progress=None, references=None, conversation_url=None):
        seen['provider'] = provider
        seen['prompt'] = prompt
        seen['references'] = list(references or [])
        seen['conversation_url'] = conversation_url
        if progress:
            progress({
                'provider': provider,
                'phase': 'generating',
                'conversation_url': conversation_url or 'https://chatgpt.com/c/new-conv',
            })
        return png('yellow'), 'gpt-image', {'conversation_url': conversation_url}

    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', fake_request)

    tasks._generate_shot_image(shot.id, job_data['id'], '16:9')
    db.expire_all()

    assert seen['conversation_url'] == 'https://chatgpt.com/c/existing-conv-123'
    assert 'Change her jacket to emerald green' in seen['prompt']
    assert 'Do not duplicate the reference composition' not in seen['prompt']
    assert [r.reference_type for r in seen['references']] == ['edit_target_fallback', 'edit_attachment']

    updated_shot = db.get(Shot, shot.id)
    updated_image_takes = [t for t in updated_shot.takes if t.media_type == 'image']
    assert len(updated_image_takes) == 2
    take2 = updated_image_takes[-1]
    assert take2.selected is True
    assert take2.settings.get('parent_take_id') == take1['id']
    assert take2.settings.get('edit_prompt') == 'Change her jacket to emerald green'
    assert take2.settings.get('edit_attachment_count') == 1
    assert take2.settings.get('conversation_url') == 'https://chatgpt.com/c/existing-conv-123'
    # Original shot.image_prompt remains intact
    assert updated_shot.image_prompt == 'Wide view of an empty room'


def test_cross_aspect_conversion_and_non_destructive_draft(setup, monkeypatch):
    db, client, project, shots = setup
    project.aspect_ratio = '9:16'
    shot = shots[0]
    shot.image_prompt = 'Wide view of an empty room. Vertical 9:16 composition for Shorts.'
    shot.video_prompt = 'Camera pans slowly. Vertical 9:16 composition for Shorts.'
    db.commit()

    seen_image_calls = []

    def fake_request(provider, prompt, aspect, progress=None, references=None, conversation_url=None):
        seen_image_calls.append((prompt, aspect, list(references or [])))
        return png('cyan'), 'gpt-image', {}

    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', fake_request)

    res = client.post(
        f'/api/shots/{shot.id}/image/generate',
        json={'aspect_ratio': '16:9', 'use_default_image_reference': True},
    )
    assert res.status_code == 202, res.text
    job_id = res.json()['id']
    tasks._generate_shot_image(shot.id, job_id, '16:9')
    db.expire_all()

    assert len(seen_image_calls) == 1
    prompt, aspect, refs = seen_image_calls[0]
    assert aspect == '16:9'
    assert '1344x768' in prompt
    assert 'Vertical 9:16' not in prompt
    assert 'for Shorts' not in prompt
    assert 'Aspect ratio conversion: Reframe and expand/outpaint' in prompt
    assert len(refs) == 1
    assert refs[0].reference_type == 'edit_target'
    assert refs[0].data == png('red')

    refreshed_shot = db.get(Shot, shot.id)
    assert refreshed_shot.image.filename == 'still-0.png'
    variant_img = next(
        (v for v in refreshed_shot.media_variants if v.media_type == 'image' and v.aspect_ratio == '16:9'),
        None,
    )
    assert variant_img is not None
    assert variant_img.filename != 'still-0.png'


def test_both_aspects_and_generate_video_pipeline(setup, monkeypatch):
    db, client, project, shots = setup
    project.aspect_ratio = '16:9'
    shot = shots[0]
    shot.image_prompt = 'Wide view of an empty room'
    shot.video_prompt = 'Camera pans slowly across the room'
    db.commit()

    seen_image_calls = []
    chained_clips = []

    def fake_request(provider, prompt, aspect, progress=None, references=None, conversation_url=None):
        seen_image_calls.append((prompt, aspect, list(references or [])))
        return png('cyan' if aspect == '16:9' else 'magenta'), 'gpt-image', {}

    def fake_enqueue_clip(shot_id, job_id, aspect_ratio=None, prompt_override=None, include_cast_references=False):
        chained_clips.append((shot_id, job_id, aspect_ratio, include_cast_references))
        return type('Task', (), {'id': f'task-{aspect_ratio}'})()

    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', fake_request)
    monkeypatch.setattr(tasks, 'enqueue_shot_clip', fake_enqueue_clip)

    res = client.post(
        f'/api/shots/{shot.id}/image/generate?aspect_ratio=16:9',
        json={
            'both_aspects': True,
            'generate_video': True,
            'include_cast_references': True,
            'use_default_image_reference': False,
        },
    )
    assert res.status_code == 202, res.text
    job_id = res.json()['id']
    tasks._generate_shot_image(shot.id, job_id, '16:9')
    db.expire_all()

    assert len(seen_image_calls) == 2
    assert seen_image_calls[0][1] == '16:9'
    assert seen_image_calls[1][1] == '9:16'
    assert len(seen_image_calls[1][2]) == 1
    assert seen_image_calls[1][2][0].reference_type == 'edit_target'

    assert len(chained_clips) == 2
    assert chained_clips[0][2] == '16:9'
    assert chained_clips[0][3] is True
    assert chained_clips[1][2] == '9:16'
    assert chained_clips[1][3] is True


def test_both_aspects_reuses_conversation_thread(setup, monkeypatch):
    db, client, project, shots = setup
    project.aspect_ratio = '16:9'
    shot = shots[0]
    shot.image_prompt = 'A beach at sunrise'
    db.commit()

    seen_calls = []

    def fake_request(provider, prompt, aspect, progress=None, references=None, conversation_url=None):
        seen_calls.append({
            'prompt': prompt,
            'aspect': aspect,
            'refs': list(references or []),
            'conversation_url': conversation_url,
        })
        if progress:
            progress({'provider': provider, 'phase': 'saving', 'conversation_url': 'https://chatgpt.com/c/beach-thread'})
        return png('cyan' if aspect == '16:9' else 'magenta'), 'gpt-image', {'conversation_url': 'https://chatgpt.com/c/beach-thread'}

    monkeypatch.setattr(settings, 'image_providers', 'chatgpt_web')
    monkeypatch.setattr(media, 'image_provider_statuses', lambda: [{'id': 'chatgpt_web', 'available': True}])
    monkeypatch.setattr(media, '_request_provider_image', fake_request)

    res = client.post(
        f'/api/shots/{shot.id}/image/generate?aspect_ratio=16:9',
        json={'both_aspects': True, 'use_default_image_reference': False},
    )
    assert res.status_code == 202, res.text
    job_id = res.json()['id']
    tasks._generate_shot_image(shot.id, job_id, '16:9')
    db.expire_all()

    assert len(seen_calls) == 2
    # First call: creates new conversation
    assert seen_calls[0]['aspect'] == '16:9'
    assert seen_calls[0]['conversation_url'] is None

    # Second call: reuses conversation thread with edit_target_fallback (no re-upload needed)
    assert seen_calls[1]['aspect'] == '9:16'
    assert seen_calls[1]['conversation_url'] == 'https://chatgpt.com/c/beach-thread'
    assert len(seen_calls[1]['refs']) == 1
    assert seen_calls[1]['refs'][0].reference_type == 'edit_target_fallback'
    assert 'Aspect ratio conversion: Reframe and expand/outpaint' in seen_calls[1]['prompt']


def test_chatgpt_dual_generates_both_takes(setup, monkeypatch):
    import uuid
    from backend.models import Shot
    db, client, project, shots = setup
    shot = shots[0]

    def fake_dual(prompt, scene_title, framing, progress=None, references=None, conversation_url=None):
        fn_16 = f"dual-16-9-{uuid.uuid4()}.png"
        fn_9 = f"dual-9-16-{uuid.uuid4()}.png"
        (settings.media_dir / fn_16).write_bytes(png("red"))
        (settings.media_dir / fn_9).write_bytes(png("blue"))
        return (fn_16, "image/png"), (fn_9, "image/png"), "chatgpt_web"

    monkeypatch.setattr(media, "generate_storyboard_chatgpt_dual", fake_dual)

    res = client.post(
        f"/api/shots/{shot.id}/image/generate",
        json={"chatgpt_dual": True},
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    tasks._generate_shot_image(shot.id, job_id, "16:9")
    db.expire_all()

    shot_row = db.get(Shot, shot.id)
    assert shot_row.status == "image_ready"
    variants = {v.aspect_ratio: v for v in shot_row.media_variants if v.media_type == "image"}
    assert "16:9" in variants
    assert "9:16" in variants

    takes = [t for t in shot_row.takes if t.media_type == "image"]
    take_aspects = {t.aspect_ratio for t in takes}
    assert "16:9" in take_aspects
    assert "9:16" in take_aspects


def test_expand_color_retouch_prompt():
    assert "Do NOT modify, add, remove, or reposition" in media.expand_color_retouch_prompt("retouch")
    assert "Do NOT modify, add, remove, or reposition" in media.expand_color_retouch_prompt("Retouch this image!")
    assert "Do NOT modify, add, remove, or reposition" in media.expand_color_retouch_prompt("repaint")
    assert "Do NOT modify, add, remove, or reposition" in media.expand_color_retouch_prompt("revive color")
    assert media.expand_color_retouch_prompt("change her blouse to red") == "change her blouse to red"


def test_retouch_shot_take_color_endpoint(setup):
    db, client, project, shots = setup
    shot = shots[0]
    upload_res = client.post(
        f"/api/shots/{shot.id}/image/upload",
        files={"file": ("faint.png", png("gray"), "image/png")},
    )
    assert upload_res.status_code == 200, upload_res.text
    image_takes = [t for t in upload_res.json()["takes"] if t["media_type"] == "image"]
    assert len(image_takes) >= 1
    base_take = image_takes[0]

    res = client.post(f"/api/shots/{shot.id}/takes/{base_take['id']}/retouch-color")
    assert res.status_code == 200, res.text
    body = res.json()
    new_takes = [t for t in body["takes"] if t["media_type"] == "image" and t["id"] != base_take["id"]]
    assert len(new_takes) == 1
    retouched = new_takes[0]
    assert retouched["provider"] == "color-boost"
    assert retouched["selected"] is True
    assert retouched["settings"]["parent_take_id"] == base_take["id"]
    assert "Instant Color Boost" in retouched["settings"]["edit_prompt"]


def test_retouch_and_repaint_shot_reference_endpoint(setup, monkeypatch):
    db, client, project, shots = setup
    shot1, shot2 = shots[0], shots[1]
    sources = client.get(f"/api/shots/{shot2.id}/image-reference/sources").json()
    src = next(item for item in sources[0]["media"] if item["source_media_type"] == "image")

    save_res = client.post(
        f"/api/shots/{shot2.id}/image-reference",
        data={
            "source_shot_id": shot1.id,
            "source_media_id": src["source_media_id"],
            "source_media_kind": src["source_media_kind"],
            "aspect_ratio": "16:9",
            "mode": "visual_continuity",
        },
    )
    assert save_res.status_code == 200, save_res.text
    original_ref_url = save_res.json()["image_references"][0]["url"]

    retouch_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9")
    assert retouch_res.status_code == 200, retouch_res.text
    ref_after_boost = retouch_res.json()["image_references"][0]
    updated_ref_url = ref_after_boost["url"]
    assert updated_ref_url != original_ref_url
    assert "image-reference-retouched-" in updated_ref_url
    assert ref_after_boost["color_boost_strength"] == 1.0
    assert ref_after_boost["can_undo_color"] is True
    assert ref_after_boost["can_redo_color"] is False

    inc_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9&action=increase")
    assert inc_res.status_code == 200, inc_res.text
    ref_after_inc = inc_res.json()["image_references"][0]
    assert ref_after_inc["color_boost_strength"] == 1.25
    assert ref_after_inc["can_undo_color"] is True

    dec_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9&action=decrease")
    assert dec_res.status_code == 200, dec_res.text
    ref_after_dec = dec_res.json()["image_references"][0]
    assert ref_after_dec["color_boost_strength"] == 1.0

    undo_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9&action=undo")
    assert undo_res.status_code == 200, undo_res.text
    ref_after_undo = undo_res.json()["image_references"][0]
    assert ref_after_undo["color_boost_strength"] == 1.25
    assert ref_after_undo["can_redo_color"] is True

    redo_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9&action=redo")
    assert redo_res.status_code == 200, redo_res.text
    ref_after_redo = redo_res.json()["image_references"][0]
    assert ref_after_redo["color_boost_strength"] == 1.0

    reset_res = client.post(f"/api/shots/{shot2.id}/image-reference/retouch-color?aspect_ratio=16:9&action=reset")
    assert reset_res.status_code == 200, reset_res.text
    ref_after_reset = reset_res.json()["image_references"][0]
    assert ref_after_reset["color_boost_strength"] == 0.0
    assert ref_after_reset["url"] == original_ref_url

    captured_prompts = []
    def fake_request(provider, prompt, aspect, progress=None, references=None, **kwargs):
        captured_prompts.append(prompt)
        return png("orange"), "test", {}

    monkeypatch.setattr(settings, "image_providers", "chatgpt_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [{"id": "chatgpt_web", "available": True}])
    monkeypatch.setattr(media, "_request_provider_image", fake_request)

    repaint_res = client.post(
        f"/api/shots/{shot2.id}/image-reference/repaint",
        data={"aspect_ratio": "16:9", "prompt": "repaint this image"},
    )
    assert repaint_res.status_code == 202, repaint_res.text
    job_id = repaint_res.json()["id"]
    tasks._generate_shot_image(shot2.id, job_id, "16:9")
    db.expire_all()
    assert len(captured_prompts) == 1
    assert "Do NOT modify, add, remove, or reposition" in captured_prompts[0]
    shot2_read = client.get(f"/api/shots/{shot2.id}").json()
    repainted_ref_url = shot2_read["image_references"][0]["url"]
    assert "image-reference-repainted-" in repainted_ref_url



