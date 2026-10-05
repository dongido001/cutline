from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Job, Project, Scene, ScriptVersion, Shot, ShotImage, ShotClip
from backend.settings import settings


def setup_shot(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'video.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title='Video regression', status='shot_plan', aspect_ratio='16:9')
        version = ScriptVersion(project=project, source_text='test')
        scene = Scene(script_version=version, order_index=0, title='Room')
        shot = Shot(scene=scene, order_index=0, video_prompt='Wave', duration_ms=4000)
        shot.image = ShotImage(filename='frame.png', mime_type='image/png', source='uploaded', provider='test')
        db.add(project)
        db.commit()
        return engine, shot.id, project.id


def test_concurrent_clip_requests_enqueue_once(tmp_path, monkeypatch):
    from backend import api
    engine, sid, _ = setup_shot(tmp_path)
    queued = []
    monkeypatch.setattr(api, 'video_provider_status', lambda: ('test', 'Test', True, True, '', 'ready'))
    def enqueue(*args):
        queued.append(args)
        return type('Task', (), {'id': 'test-task'})()
    monkeypatch.setattr(api, 'enqueue_shot_clip', enqueue)
    def submit():
        with Session(engine) as db:
            return api.queue_shot_clip(sid, '16:9', db).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    assert len(queued) == 1


def test_cancelled_clip_does_not_replace_existing_media(tmp_path, monkeypatch):
    from backend import tasks
    engine, sid, pid = setup_shot(tmp_path)
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    with Session(engine) as db:
        shot = db.get(Shot, sid)
        shot.clip = ShotClip(filename='old.mp4', mime_type='video/mp4', source='uploaded', provider='test')
        job = Job(project_id=pid, kind='shot_clip', status='queued')
        db.add(job)
        db.commit()
        jid = job.id
    monkeypatch.setattr(tasks, 'SessionLocal', lambda: Session(engine, expire_on_commit=False))
    def render(*args, **kwargs):
        with Session(engine) as api:
            api.get(Job, jid).status = 'cancelled'
            api.commit()
        (settings.media_dir / 'new.mp4').write_bytes(b'new clip')
        return 'new.mp4', 'video/mp4', 'test'
    monkeypatch.setattr(tasks, 'render_shot_clip', render)
    tasks._generate_shot_clip(sid, jid, '16:9')
    with Session(engine) as db:
        assert db.get(Job, jid).status == 'cancelled'
        assert db.get(Shot, sid).clip.filename == 'old.mp4'
    assert not (settings.media_dir / 'new.mp4').exists()


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg unavailable')
def test_export_preserves_duration_when_clip_is_short(tmp_path):
    from backend.timeline_render import _normalize_primary, ResolvedMedia
    from backend.schemas import TimelinePrimaryItem
    from backend.final_cut import probe_duration_ms
    source = tmp_path / 'short.mp4'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=c=blue:s=64x64:r=30', '-t', '0.5', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(source)], check=True)
    item = TimelinePrimaryItem.model_validate({'id': 'short', 'kind': 'video',
                                               'label': 'Short clip', 'duration_ms': 1500})
    output = tmp_path / 'rendered.mp4'
    _normalize_primary(item, ResolvedMedia(source, True, '16:9', True), output, 64, 64, 'landscape')
    assert abs(probe_duration_ms(output) - 1500) <= 50
