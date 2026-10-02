import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.api import app
from backend.database import Base, get_db
from backend.models import Job, Shot, Scene, ScriptVersion, Project, ShotTake
from backend.media import delete_media_file
from backend.lipsync import SyncLabsEngine, FalLipSyncEngine, NativeFFmpegEngine
from backend.settings import settings


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Fix test", status="shot_plan")
    session.add(project)
    session.commit()

    version = ScriptVersion(project_id=project.id, version_number=1, source_text="test", structured_data={})
    session.add(version)
    session.commit()

    scene = Scene(script_version_id=version.id, order_index=0, title="Scene 1")
    session.add(scene)
    session.commit()

    shot = Shot(scene_id=scene.id, order_index=0, title="Shot 1", dialogue=[{"character": "Amaka", "text": "Hello"}])
    session.add(shot)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session, project, shot


def test_retry_final_cut_variant_supported(tmp_path, monkeypatch):
    client, session, project, shot = _client(tmp_path, monkeypatch)
    job = Job(
        project_id=project.id,
        kind="final_cut:landscape",
        status="failed",
        attempts=[{"variant": "landscape"}],
    )
    session.add(job)
    session.commit()

    with patch("backend.api.enqueue_final_cut") as mock_enqueue:
        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_enqueue.return_value = mock_task
        res = client.post(f"/api/jobs/{job.id}/retry")
        assert res.status_code == 200
        session.refresh(job)
        assert job.status == "queued"
        mock_enqueue.assert_called_once_with(project.id, job.id, "landscape")


def test_delete_media_file_preserves_takes(tmp_path, monkeypatch):
    client, session, project, shot = _client(tmp_path, monkeypatch)
    filename = "test-take-file.mp4"
    media_file = settings.media_dir / filename
    media_file.write_text("dummy video content")

    take = ShotTake(
        shot_id=shot.id,
        media_type="clip",
        filename=filename,
        mime_type="video/mp4",
        source="generated",
        provider="test",
    )
    session.add(take)
    session.commit()

    # Attempt to delete file passing db session
    delete_media_file(filename, db=session)
    assert media_file.is_file(), "File referenced by ShotTake must NOT be deleted"

    # Clean up test take
    session.delete(take)
    session.commit()
    # Now that it is orphaned, it should delete
    delete_media_file(filename, db=session)
    assert not media_file.is_file(), "Orphaned media file should be deleted"


def test_flush_queue_cancels_pending_jobs(tmp_path, monkeypatch):
    client, session, project, shot = _client(tmp_path, monkeypatch)
    job1 = Job(project_id=project.id, kind="shot_image", status="queued")
    job2 = Job(project_id=project.id, kind="shot_clip", status="queued")
    session.add_all([job1, job2])
    session.commit()

    res = client.post("/api/system/queues/image/flush")
    assert res.status_code == 200
    session.refresh(job1)
    session.refresh(job2)
    assert job1.status == "cancelled"
    assert job2.status == "queued"  # video queue was not flushed


def test_lipsync_engine_does_not_crash(tmp_path):
    sync_engine = SyncLabsEngine()
    fal_engine = FalLipSyncEngine()

    dummy_vis = tmp_path / "vis.jpg"
    dummy_vis.write_text("fake visual")
    dummy_aud = tmp_path / "aud.wav"
    dummy_aud.write_text("fake audio")
    out_path = tmp_path / "out.mp4"

    with patch.object(NativeFFmpegEngine, "generate", return_value=out_path) as mock_native:
        sync_engine.generate(dummy_vis, dummy_aud, out_path)
        mock_native.assert_called_once()

    with patch.object(NativeFFmpegEngine, "generate", return_value=out_path) as mock_native:
        fal_engine.generate(dummy_vis, dummy_aud, out_path)
        mock_native.assert_called_once()
