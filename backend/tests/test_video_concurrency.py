from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from sqlalchemy.orm import Session

from backend.models import Job, Scene, Shot, ShotImage, Project, ProjectFinalCut
from backend.settings import settings
from test_video_generation_regressions import setup_shot


def test_worker_concurrency_only_changes_video_queue(monkeypatch):
    from backend import cli
    calls = []
    class Process:
        returncode = 0
        def poll(self):
            return 0
        def wait(self):
            return 0
    monkeypatch.setattr(cli.subprocess, 'Popen', lambda command: calls.append(command) or Process())
    monkeypatch.setattr(settings, 'video_worker_concurrency', 3)
    cli.run_worker()
    assert len(calls) == 5
    for command in calls:
        assert command[-1] == ('3' if command[3] == 'backend.tasks.video_huey' else '1')


@pytest.mark.parametrize("with_failures", [False, True])
def test_three_jobs_overlap_and_fourth_waits_with_independent_results(tmp_path, monkeypatch, with_failures):
    from backend import tasks
    from backend.timeline import ensure_timeline
    engine, sid, pid = setup_shot(tmp_path)
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    with Session(engine) as db:
        first = db.get(Shot, sid)
        shots = [first]
        for index in range(1, 4):
            scene = first.scene
            if index == 3:
                other = Project(title='Other project', status='shot_plan', aspect_ratio='16:9')
                from backend.models import ScriptVersion
                scene = Scene(script_version=ScriptVersion(project=other, source_text='test'), order_index=0, title='Other')
            shot = Shot(scene=scene, order_index=index, video_prompt=str(index), duration_ms=4000)
            shot.image = ShotImage(filename=f'{index}.png', mime_type='image/png', source='uploaded', provider='test')
            db.add(shot)
            shots.append(shot)
        db.flush()
        project = db.get(Project, pid)
        ensure_timeline(project, db)
        project.final_cut = ProjectFinalCut(filename='old-final.mp4', duration_ms=12000, width=1280, height=720, clip_count=0, fallback_count=3)
        (settings.media_dir / 'old-final.mp4').write_bytes(b'old final')
        jobs = [Job(project_id=shot.scene.script_version.project.id, kind='shot_clip', status='queued') for shot in shots]
        db.add_all(jobs)
        db.commit()
        pairs = [(shot.id, job.id) for shot, job in zip(shots, jobs)]
    monkeypatch.setattr(tasks, 'SessionLocal', lambda: Session(engine, expire_on_commit=False))
    release, three_started = Event(), Event()
    lock = Lock()
    started = []
    def render(*args, **kwargs):
        with lock:
            index = 0 if args[0].name == 'frame.png' else int(args[0].stem)
            started.append(index)
            if len(started) == 3:
                three_started.set()
        assert release.wait(10)
        if with_failures and index == 1:
            raise RuntimeError('one provider failed')
        name = f'result-{index}.mp4'
        (settings.media_dir / name).write_bytes(b'video')
        return name, 'video/mp4', 'test'
    monkeypatch.setattr(tasks, 'render_shot_clip', render)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(tasks._generate_shot_clip, shot, job, '16:9') for shot, job in pairs]
        try:
            assert three_started.wait(10)
            with Session(engine) as db:
                assert db.get(Job, pairs[3][1]).status == 'queued'
                assert sum(db.get(Job, job).status == 'running' for _, job in pairs) == 3
                if with_failures:
                    db.get(Job, pairs[2][1]).status = 'cancelled'
                    db.commit()
        finally:
            release.set()
        errors = []
        for future in futures:
            try:
                future.result(timeout=15)
            except RuntimeError as exc:
                errors.append(str(exc))
    assert errors == (['one provider failed'] if with_failures else [])
    with Session(engine) as db:
        statuses = [db.get(Job, job).status for _, job in pairs]
        assert statuses.count('completed') == (2 if with_failures else 4)
        assert statuses.count('failed') == (1 if with_failures else 0)
        assert statuses.count('cancelled') == (1 if with_failures else 0)
        project = db.get(Project, pid)
        assert project.final_cut is None
        assert not (settings.media_dir / 'old-final.mp4').exists()
        assert sum(item['kind'] == 'video' for item in project.timeline.document['primary']) == (1 if with_failures else 3), project.timeline.document
        for shot_id, job_id in pairs:
            shot = db.get(Shot, shot_id)
            if db.get(Job, job_id).status == 'completed':
                assert shot.clip and (settings.media_dir / shot.clip.filename).exists()
            else:
                assert shot.clip is None


def test_flowkit_parallel_calls_keep_provider_context_separate(tmp_path, monkeypatch):
    from backend import video
    from threading import Barrier
    barrier = Barrier(2)
    class Response:
        headers = {'content-type': 'video/mp4'}
        content = b'video'
        def __init__(self, payload=None):
            self.payload = payload
        def json(self):
            return self.payload
        def raise_for_status(self):
            pass
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, json):
            if url.endswith('/upload-image'):
                self.key = json['file_name']
                barrier.wait(timeout=5)
                return Response({'media_id': f'image-{self.key}', 'project_id': f'project-{self.key}'})
            assert json['project_id'] == f'project-{self.key}'
            if url.endswith('/generate-video'):
                assert json['start_image_media_id'] == f'image-{self.key}'
                return Response({'operations': [{'operation': {'name': f'op-{self.key}'}}]})
            assert json['operations'][0]['operation']['name'] == f'op-{self.key}'
            return Response({'operations': [{'status': 'MEDIA_GENERATION_STATUS_SUCCESSFUL', 'operation': {
                'name': f'op-{self.key}', 'metadata': {'video': {'fifeUrl': f'https://example.test/{self.key}.mp4'}}
            }}]})
        def get(self, url):
            assert url == f'https://example.test/{self.key}.mp4'
            return Response()
    monkeypatch.setattr(video.httpx, 'Client', lambda **kwargs: Client())
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    paths = [tmp_path / f'{index}.png' for index in range(2)]
    for path in paths:
        path.write_bytes(b'png')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: video.generate_flowkit_clip(path, 'Wave', 4000, '16:9', scene_id=path.stem), paths))
    assert results[0][0] != results[1][0]
    assert all((settings.media_dir / result[0]).exists() for result in results)
