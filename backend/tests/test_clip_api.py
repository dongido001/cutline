from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import (
    Job,
    Project,
    Scene,
    ScriptVersion,
    Shot,
    ShotClip,
    ShotContinuityFrame,
)
from backend.settings import settings


def test_upload_and_delete_shot_clip(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'clips.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Clip test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(
        script_version=version,
        order_index=0,
        title="INT. ROOM - DAY",
    )
    shot = Shot(scene=scene, order_index=0, title="Test shot", video_prompt="Push in")
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.post(
            f"/api/shots/{shot.id}/clip/upload",
            files={"file": ("shot.webm", b"test-video", "video/webm")},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["clip"]["source"] == "uploaded"
        assert payload["clip"]["mime_type"] == "video/webm"
        filename = payload["clip"]["url"].removeprefix("/media/")
        assert (tmp_path / "media" / filename).read_bytes() == b"test-video"

        response = client.delete(f"/api/shots/{shot.id}/clip")

        assert response.status_code == 204
        assert not (tmp_path / "media" / filename).exists()
        session.expire_all()
        assert session.get(Shot, shot.id).status == "planned"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_save_and_remove_shot_continuity_frame(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'continuity-frames.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Continuity test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    previous = Shot(scene=scene, order_index=0, title="Doorway")
    following = Shot(scene=scene, order_index=1, title="Mara enters", video_prompt="She enters.")
    previous.clip = ShotClip(
        filename="previous.mp4",
        mime_type="video/mp4",
        source="uploaded",
        provider="test",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.post(
            f"/api/shots/{following.id}/continuity-frame",
            data={
                "source_shot_id": previous.id,
                "source_time_ms": "2350",
                "aspect_ratio": "16:9",
            },
            files={"file": ("frame.png", b"captured-frame", "image/png")},
        )

        assert response.status_code == 200
        frame = response.json()["continuity_frames"][0]
        assert frame["source_shot_id"] == previous.id
        assert frame["source_shot_title"] == "Doorway"
        assert frame["source_time_ms"] == 2350
        filename = frame["url"].split("/media/", 1)[1].split("?", 1)[0]
        assert (tmp_path / "media" / filename).read_bytes() == b"captured-frame"

        response = client.delete(
            f"/api/shots/{following.id}/continuity-frame?aspect_ratio=16:9"
        )
        assert response.status_code == 204
        assert not (tmp_path / "media" / filename).exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_continuity_frame_must_come_from_immediately_previous_shot(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'continuity-order.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Continuity order", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    previous = Shot(scene=scene, order_index=0, title="Previous")
    following = Shot(scene=scene, order_index=1, title="Current")
    previous.clip = ShotClip(
        filename="previous.mp4",
        mime_type="video/mp4",
        source="uploaded",
        provider="test",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.post(
            f"/api/shots/{following.id}/continuity-frame",
            data={
                "source_shot_id": "not-the-previous-shot",
                "source_time_ms": "0",
                "aspect_ratio": "16:9",
            },
            files={"file": ("frame.png", b"captured-frame", "image/png")},
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_continuity_frame_can_start_video_without_storyboard_image(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'continuity-generation.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Continuity generation", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=1, title="Mara enters", video_prompt="She enters.")
    shot.continuity_frames.append(
        ShotContinuityFrame(
            aspect_ratio="16:9",
            filename="continuity.png",
            mime_type="image/png",
            source_shot_id="previous-shot",
            source_shot_title="Doorway",
            source_time_ms=1500,
        )
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    class QueuedTask:
        id = "continuity-job-task"

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("backend.api.video_provider_status", lambda: ("test", "Test", True, True, "model", "ready"))
    monkeypatch.setattr("backend.api.enqueue_shot_clip", lambda *args: QueuedTask())
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.post(f"/api/shots/{shot.id}/clip/generate?aspect_ratio=16:9")
        assert response.status_code == 202
        assert response.json()["kind"] == "shot_clip"
        assert session.query(Job).filter_by(kind="shot_clip").count() == 1
        duplicate = client.post(f"/api/shots/{shot.id}/clip/generate?aspect_ratio=16:9")
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == response.json()["id"]
        assert session.query(Job).filter_by(kind="shot_clip").count() == 1
        assert client.post(f"/api/shots/{shot.id}/clip/generate?aspect_ratio=9:16").status_code == 409
        assert client.post(f"/api/shots/{shot.id}/clip/generate?aspect_ratio=1:1").status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_cross_project_video_queue_positions(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'cross_project.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    # Project 1
    p1 = Project(title="Project 1", status="shot_plan")
    v1 = ScriptVersion(project=p1, source_text="test1")
    s1 = Scene(script_version=v1, order_index=0, title="SCENE 1")
    shot1 = Shot(scene=s1, order_index=0, title="Shot 1", video_prompt="Action 1")
    shot1.continuity_frames.append(
        ShotContinuityFrame(
            aspect_ratio="16:9",
            filename="frame1.png",
            mime_type="image/png",
            source_shot_id="prev",
            source_shot_title="Prev",
            source_time_ms=0,
        )
    )

    # Project 2
    p2 = Project(title="Project 2", status="shot_plan")
    v2 = ScriptVersion(project=p2, source_text="test2")
    s2 = Scene(script_version=v2, order_index=0, title="SCENE 2")
    shot2 = Shot(scene=s2, order_index=0, title="Shot 2", video_prompt="Action 2")
    shot2.continuity_frames.append(
        ShotContinuityFrame(
            aspect_ratio="16:9",
            filename="frame2.png",
            mime_type="image/png",
            source_shot_id="prev",
            source_shot_title="Prev",
            source_time_ms=0,
        )
    )

    session.add_all([p1, p2])
    session.commit()

    def override_db():
        yield session

    class DummyTask:
        def __init__(self, tid):
            self.id = tid

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("backend.api.video_provider_status", lambda: ("test", "Test", True, True, "model", "ready"))
    task_counter = 0
    def dummy_enqueue(*args):
        nonlocal task_counter
        task_counter += 1
        return DummyTask(f"task-{task_counter}")

    monkeypatch.setattr("backend.api.enqueue_shot_clip", dummy_enqueue)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        # Queue video in Project 1
        res1 = client.post(f"/api/shots/{shot1.id}/clip/generate?aspect_ratio=16:9")
        assert res1.status_code == 202
        job1_id = res1.json()["id"]

        # Queue video in Project 2
        res2 = client.post(f"/api/shots/{shot2.id}/clip/generate?aspect_ratio=16:9")
        assert res2.status_code == 202
        job2_id = res2.json()["id"]

        # Verify Job 1 queue position is 1
        j1 = client.get(f"/api/jobs/{job1_id}").json()
        assert j1["project_id"] == p1.id
        assert j1["status"] == "queued"
        assert j1["queue_position"] == 1

        # Verify Job 2 queue position is 2 across projects
        j2 = client.get(f"/api/jobs/{job2_id}").json()
        assert j2["project_id"] == p2.id
        assert j2["status"] == "queued"
        assert j2["queue_position"] == 2

        # Verify list_project_jobs returns proper positions per project
        p1_jobs = client.get(f"/api/projects/{p1.id}/jobs").json()
        assert len(p1_jobs) == 1
        assert p1_jobs[0]["id"] == job1_id
        assert p1_jobs[0]["queue_position"] == 1

        p2_jobs = client.get(f"/api/projects/{p2.id}/jobs").json()
        assert len(p2_jobs) == 1
        assert p2_jobs[0]["id"] == job2_id
        assert p2_jobs[0]["queue_position"] == 2
    finally:
        app.dependency_overrides.clear()
        session.close()
