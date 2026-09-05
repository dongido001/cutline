from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot, ShotMediaVariant
from backend.settings import settings
from backend.timeline import promote_available_clips


def test_timeline_persists_split_and_aspect_media_without_replacing_legacy(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Timeline test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Test shot",
        duration_ms=2000,
        image_prompt="A room",
        video_prompt="Push in",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        landscape = client.post(
            f"/api/shots/{shot.id}/image/upload?aspect_ratio=16%3A9",
            files={"file": ("wide.png", b"wide", "image/png")},
        )
        vertical = client.post(
            f"/api/shots/{shot.id}/image/upload?aspect_ratio=9%3A16",
            files={"file": ("vertical.png", b"vertical", "image/png")},
        )
        assert landscape.status_code == 200
        assert vertical.status_code == 200
        assert vertical.json()["image"]["url"] == landscape.json()["image"]["url"]
        variants = vertical.json()["media_variants"]
        assert [(item["media_type"], item["aspect_ratio"]) for item in variants] == [("image", "9:16")]

        response = client.get(f"/api/projects/{project.id}/timeline")
        assert response.status_code == 200
        timeline = response.json()
        original = timeline["document"]["primary"][0]
        first = {**original, "duration_ms": 800, "source_out_ms": 800}
        second = {
            **original,
            "id": "split-b",
            "label": "Test shot B",
            "source_in_ms": 800,
            "duration_ms": 1200,
            "source_out_ms": 2000,
        }
        timeline["document"]["primary"] = [first, second]
        timeline["document"]["text_overlays"] = [
            {
                "id": "text-1",
                "text": "Lower third",
                "start_ms": 900,
                "duration_ms": 500,
                "presentation": "overlay",
                "size": "medium",
                "color": "white",
                "background": "shadow",
                "variant": {},
            }
        ]
        saved = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"]},
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == timeline["revision"] + 1
        assert len(saved.json()["document"]["primary"]) == 2

        conflict = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"]},
        )
        assert conflict.status_code == 409
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_available_clip_promotes_existing_storyboard_timeline_block(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'promotion.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Promotion test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Test shot", duration_ms=2000, image_prompt="A room", video_prompt="Push in")
    session.add(project)
    session.commit()

    from backend.timeline import ensure_timeline

    timeline = ensure_timeline(project, session)
    session.add(ShotMediaVariant(shot=shot, media_type="clip", aspect_ratio="9:16", filename="vertical.mp4", mime_type="video/mp4", source="uploaded", provider="manual-upload"))
    session.flush()

    assert promote_available_clips(project, session) == 1
    document = timeline.document
    item = document["primary"][0]
    assert item["kind"] == "video"
    assert item["source"]["kind"] == "shot_clip"
    assert item["source_out_ms"] == 2000
    session.close()
