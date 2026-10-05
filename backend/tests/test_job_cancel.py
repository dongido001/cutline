from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Job, Project
from backend.settings import settings


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cancel.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Cancel test", status="shot_plan")
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session, project


def test_cancel_queued_job(tmp_path, monkeypatch) -> None:
    client, session, project = _client(tmp_path, monkeypatch)
    try:
        job = Job(project_id=project.id, kind="shot_image", status="queued", message="Queued", task_id="fake-task")
        session.add(job)
        session.commit()

        response = client.post(f"/api/jobs/{job.id}/cancel")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "cancelled"
        assert payload["message"] == "Cancelled"

        session.refresh(job)
        assert job.status == "cancelled"

        again = client.post(f"/api/jobs/{job.id}/cancel")
        assert again.status_code == 200
        assert again.json()["status"] == "cancelled"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_cancel_rejects_completed_job_quietly(tmp_path, monkeypatch) -> None:
    client, session, project = _client(tmp_path, monkeypatch)
    try:
        job = Job(project_id=project.id, kind="shot_image", status="completed", message="Done", progress=1)
        session.add(job)
        session.commit()
        response = client.post(f"/api/jobs/{job.id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_worker_detects_cancellation_from_another_session(tmp_path):
    import pytest
    from backend.jobs import JobCancelled, raise_if_cancelled
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-cancel.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as worker:
        project = Project(title='Cancel running clip')
        worker.add(project)
        worker.flush()
        job = Job(project_id=project.id, kind='shot_clip', status='running')
        worker.add(job)
        worker.commit()
        with Session(engine) as api:
            api.get(Job, job.id).status = 'cancelled'
            api.commit()
        assert job.status == 'running'
        with pytest.raises(JobCancelled):
            raise_if_cancelled(worker, job.id)
