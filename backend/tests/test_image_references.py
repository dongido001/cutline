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
        if with_reference or path == 'manual'
        else None
    )
    if path == 'manual':
        response = client.post(
            f'/api/shots/{target.id}/image/generate',
            json={'image_reference_id': first['id'] if with_reference else None},
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
        assert references == []
        assert 'Visual continuity reference' not in prompt
        assert 'image_reference' not in take.settings
    choose(client, target, shots[1])
    client.delete(f'/api/shots/{target.id}/image-reference?aspect_ratio=16:9')
    db.refresh(take)
    if with_reference:
        assert take.settings.get('image_reference') == first
    else:
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
