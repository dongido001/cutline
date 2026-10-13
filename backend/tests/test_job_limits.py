from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.job_limits import ensure_project_job_capacity, raise_if_over_capacity, JobCapacityExceeded
from backend.job_status import compute_global_queue_position, compute_queue_position
from backend.models import Job, Project
from backend.settings import settings


def _session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "max_active_image_jobs_per_project", 2)
    monkeypatch.setattr(settings, "max_active_video_jobs_per_project", 1)
    monkeypatch.setattr(settings, "max_active_text_jobs_per_project", 1)
    engine = create_engine(f"sqlite:///{tmp_path / 'limits.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_image_lane_cap_raises_http_429(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project = Project(title="Cap", status="shot_plan")
    db.add(project)
    db.flush()
    for index in range(2):
        db.add(
            Job(
                project_id=project.id,
                kind="shot_image",
                status="queued",
                message=f"q{index}",
            )
        )
    db.commit()

    try:
        ensure_project_job_capacity(db, project.id, "shot_image")
        assert False, "expected 429"
    except HTTPException as exc:
        assert exc.status_code == 429
        assert "limit 2" in str(exc.detail)


def test_worker_helper_raises_domain_error(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project = Project(title="Cap", status="shot_plan")
    db.add(project)
    db.flush()
    db.add(Job(project_id=project.id, kind="shot_clip", status="running", message="r"))
    db.commit()

    try:
        raise_if_over_capacity(db, project.id, "shot_clip")
        assert False, "expected capacity error"
    except JobCapacityExceeded as exc:
        assert "limit 1" in exc.detail


def test_zero_limit_disables_cap(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "max_active_image_jobs_per_project", 0)
    project = Project(title="Open", status="shot_plan")
    db.add(project)
    db.flush()
    for _ in range(5):
        db.add(Job(project_id=project.id, kind="shot_image", status="queued", message="q"))
    db.commit()
    ensure_project_job_capacity(db, project.id, "shot_image")


def test_queue_position_uses_count(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    project = Project(title="Q", status="shot_plan")
    db.add(project)
    db.flush()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    first = Job(project_id=project.id, kind="shot_image", status="queued", created_at=t0)
    second = Job(project_id=project.id, kind="shot_image", status="queued", created_at=t1)
    db.add_all([first, second])
    db.commit()
    db.refresh(first)
    db.refresh(second)
    assert compute_queue_position(db, first) == 1
    assert compute_queue_position(db, second) == 2
    assert compute_global_queue_position(db, second) == 2
    first.status = "running"
    db.commit()
    assert compute_queue_position(db, first) is None
