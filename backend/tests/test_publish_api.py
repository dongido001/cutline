from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend import api, tasks
from backend.database import Base, get_db
from backend.models import Job, Project, ProjectVariantExport, Scene, ScriptVersion, Shot, ShotClip, PublishRecord
from backend.settings import settings


def setup_project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "publish_mode", "mock")
    engine = create_engine(f"sqlite:///{tmp_path / 'publish.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    media = settings.media_dir
    (media / "final.mp4").write_bytes(b"final")
    (media / "shot-1.mp4").write_bytes(b"shot1")
    project = Project(title="Release Test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="Opening")
    shot = Shot(scene=scene, order_index=0, title="First shot", duration_ms=4000, image_prompt="x", video_prompt="y")
    shot.clip = ShotClip(filename="shot-1.mp4", mime_type="video/mp4", source="generated", provider="test")
    project.variant_exports.append(ProjectVariantExport(variant="landscape", filename="final.mp4", duration_ms=4000, width=1920, height=1080, clip_count=1, fallback_count=0))
    session.add(project)
    session.commit()
    app = api.app
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(api, "enqueue_publish_project", lambda *args: SimpleNamespace(id="publish-task"))
    return engine, session, TestClient(app), project, shot


def test_publish_plan_lists_final_shots_and_providers(tmp_path, monkeypatch):
    engine, session, client, project, shot = setup_project(tmp_path, monkeypatch)
    try:
        response = client.get(f"/api/projects/{project.id}/publish/plan?variant=landscape")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["final"][0]["id"] == "final:landscape"
        assert data["shots"][0]["id"] == f"shot:{shot.id}"
        assert {item["id"] for item in data["providers"]} == {"youtube", "facebook"}
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_queue_publish_persists_record_and_job_snapshot(tmp_path, monkeypatch):
    engine, session, client, project, shot = setup_project(tmp_path, monkeypatch)
    try:
        response = client.post(f"/api/projects/{project.id}/publish", json={
            "platforms": ["youtube", "facebook"],
            "scope": "shots",
            "variant": "landscape",
            "title": "Launch",
            "description": "A release",
            "tags": ["drama", "launch"],
            "privacy": {"youtube": "private", "facebook": "self"},
            "selected_asset_ids": [f"shot:{shot.id}"],
            "made_for_kids": False,
        })
        assert response.status_code == 202, response.text
        job = session.get(Job, response.json()["id"])
        record = session.query(PublishRecord).one()
        assert job.kind == "publish"
        assert record.title == "Launch"
        assert record.platforms == ["youtube", "facebook"]
        assert job.attempts[0]["publish_record_id"] == record.id
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_publish_worker_records_dry_run_results(tmp_path, monkeypatch):
    engine, session, _client, project, shot = setup_project(tmp_path, monkeypatch)
    try:
        job = Job(project_id=project.id, kind="publish", attempts=[{
            "platforms": ["youtube"], "scope": "shots", "variant": "landscape",
            "title": "Launch", "description": "", "tags": [],
            "privacy": {"youtube": "private"}, "selected_asset_ids": [f"shot:{shot.id}"],
            "made_for_kids": False, "platform_options": {},
        }])
        session.add(job)
        session.flush()
        record = PublishRecord(project_id=project.id, job_id=job.id, scope="shots", variant="landscape", platforms=["youtube"], title="Launch", privacy={"youtube": "private"})
        session.add(record)
        session.commit()
        monkeypatch.setattr(tasks, "SessionLocal", lambda: Session(engine))
        tasks._publish_project(project.id, job.id, record.id)
        session.expire_all()
        record = session.get(PublishRecord, record.id)
        job = session.get(Job, job.id)
        assert record.status == "completed"
        assert record.results[0]["external_url"].startswith("https://youtube.example.local")
        assert job.status == "completed"
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()
