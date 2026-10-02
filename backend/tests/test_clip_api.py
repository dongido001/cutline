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
    finally:
        app.dependency_overrides.clear()
        session.close()
