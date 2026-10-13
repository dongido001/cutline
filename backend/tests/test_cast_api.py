from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Character, Project, Scene, ScriptVersion, Shot
from backend.settings import settings


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cast.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Cast test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Test shot",
        image_prompt="MARA stands by the window",
        dialogue=[{"character": "MARA", "text": "Hello"}],
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session, project, shot


def test_cast_crud_and_reference_upload(tmp_path, monkeypatch) -> None:
    client, session, project, _shot = _client(tmp_path, monkeypatch)
    try:
        created = client.post(
            f"/api/projects/{project.id}/characters",
            json={"name": "MARA", "notes": "lead"},
        )
        assert created.status_code == 201
        character = created.json()
        assert character["name"] == "MARA"
        assert character["references"] == []

        detail = client.get(f"/api/projects/{project.id}")
        assert detail.status_code == 200
        assert len(detail.json()["characters"]) == 1

        uploaded = client.post(
            f"/api/characters/{character['id']}/references",
            files={"file": ("face.png", PNG_BYTES, "image/png")},
        )
        assert uploaded.status_code == 200
        payload = uploaded.json()
        assert len(payload["references"]) == 1
        filename = payload["references"][0]["url"].split("?")[0].removeprefix("/media/")
        assert (tmp_path / "media" / filename).read_bytes() == PNG_BYTES

        renamed = client.patch(
            f"/api/characters/{character['id']}",
            json={"name": "MARA ELIAS"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "MARA ELIAS"

        reference_id = payload["references"][0]["id"]
        deleted_ref = client.delete(f"/api/character-references/{reference_id}")
        assert deleted_ref.status_code == 204
        assert not (tmp_path / "media" / filename).exists()

        deleted = client.delete(f"/api/characters/{character['id']}")
        assert deleted.status_code == 204
        session.expire_all()
        assert session.get(Character, character["id"]) is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_generate_requires_reference_images_for_selected_cast(tmp_path, monkeypatch) -> None:
    client, session, project, shot = _client(tmp_path, monkeypatch)
    try:
        character = client.post(
            f"/api/projects/{project.id}/characters",
            json={"name": "MARA"},
        ).json()

        shot.character_ids = [character["id"]]
        session.commit()
        detail = client.get(f"/api/projects/{project.id}")
        status = detail.json()["current_version"]["scenes"][0]["shots"][0]["continuity"]
        assert status["characters"] == [{
            "character_id": character["id"], "name": "MARA", "has_reference": False, "reference_ids": [],
        }]
        assert "MARA" in status["missing_character_references"]
        missing = client.post(
            f"/api/shots/{shot.id}/image/generate",
            json={"character_ids": [character["id"]]},
        )
        assert missing.status_code == 409
        assert "reference image" in missing.json()["detail"].lower()

        client.post(
            f"/api/characters/{character['id']}/references",
            files={"file": ("face.png", PNG_BYTES, "image/png")},
        )

        calls = {}

        def fake_enqueue(shot_id, job_id, aspect_ratio=None, prompt_override=None, character_ids=None):
            calls["character_ids"] = character_ids
            class Task:
                id = "task-1"
            return Task()

        monkeypatch.setattr("backend.api.enqueue_shot_image", fake_enqueue)
        queued = client.post(
            f"/api/shots/{shot.id}/image/generate?aspect_ratio=16%3A9",
            json={"character_ids": [character["id"]]},
        )
        assert queued.status_code == 202
        assert calls["character_ids"] == [character["id"]]
        session.expire_all()
        shot.character_ids = [character["id"]]
        session.commit()
        automatic = client.post(f"/api/shots/{shot.id}/image/generate", json={})
        assert automatic.status_code == 202
        assert calls["character_ids"] == [character["id"]]
        session.expire_all()
        assert shot.continuity_snapshot["character_ids"] == [character["id"]]
        assert shot.continuity_snapshot["characters"][0]["reference_ids"]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_elements_sync_3d_view_mentions_and_r2v_clip_queue(tmp_path, monkeypatch) -> None:
    client, session, project, shot = _client(tmp_path, monkeypatch)
    try:
        scene = shot.scene
        scene.location = "INT. NEON DINER"
        scene.set_notes = "Rainy neon diner booth with chrome table"
        scene.props_notes = "Vintage Revolver, Brass Compass"
        shot.video_prompt = "@MARA_ELIAS picks up @VINTAGE_REVOLVER inside @INT_NEON_DINER"
        session.commit()

        synced = client.post(f"/api/projects/{project.id}/elements/sync")
        assert synced.status_code == 200
        elements = synced.json()
        kinds = {(item["name"], item["kind"]) for item in elements}
        assert ("NEON DINER", "scene") in kinds
        assert ("VINTAGE REVOLVER", "prop") in kinds
        assert ("BRASS COMPASS", "prop") in kinds

        created_char = client.post(
            f"/api/projects/{project.id}/characters",
            json={"name": "MARA ELIAS", "notes": "detective", "kind": "character"},
        )
        assert created_char.status_code == 201
        char_data = created_char.json()
        assert char_data["kind"] == "character"

        # Upload Step 1 main view and Step 2 3D turnaround sheet
        up_main = client.post(
            f"/api/characters/{char_data['id']}/references?view_type=main",
            files={"file": ("main.png", PNG_BYTES, "image/png")},
        )
        assert up_main.status_code == 200
        assert up_main.json()["references"][0]["view_type"] == "main"

        up_3d = client.post(
            f"/api/characters/{char_data['id']}/references?view_type=3d_view",
            files={"file": ("3d.png", PNG_BYTES, "image/png")},
        )
        assert up_3d.status_code == 200
        view_types = [r["view_type"] for r in up_3d.json()["references"]]
        assert view_types == ["main", "3d_view"]

        # Queue R2V clip WITHOUT a pre-rendered shot image
        monkeypatch.setattr("backend.api.video_provider_status", lambda: ("flowkit", "Google Flow", True, True, "veo_3_1_r2v_fast_ultra", "ready"))
        enqueued_calls = {}

        def fake_enqueue_clip(shot_id, job_id, aspect_ratio=None, include_cast_references=False):
            enqueued_calls["shot_id"] = shot_id
            enqueued_calls["include_cast_references"] = include_cast_references
            class DummyTask:
                id = "r2v-task-1"
            return DummyTask()

        monkeypatch.setattr("backend.api.enqueue_shot_clip", fake_enqueue_clip)

        # In I2V mode, missing storyboard image returns 409
        i2v_resp = client.post(
            f"/api/shots/{shot.id}/clip/generate?aspect_ratio=16%3A9",
            json={"video_mode": "i2v"},
        )
        assert i2v_resp.status_code == 409

        # In R2V mode, it succeeds with 202 using attached/mentioned @Elements!
        r2v_resp = client.post(
            f"/api/shots/{shot.id}/clip/generate?aspect_ratio=16%3A9",
            json={
                "video_mode": "r2v",
                "include_cast_references": True,
                "include_scene_reference": True,
                "element_ids": [char_data["id"]],
            },
        )
        assert r2v_resp.status_code == 202
        r2v_job = r2v_resp.json()
        assert enqueued_calls["shot_id"] == shot.id
        assert enqueued_calls["include_cast_references"] is True
        assert r2v_job["attempts"][0]["video_mode"] == "r2v"
        assert r2v_job["attempts"][0]["element_ids"] == [char_data["id"]]
    finally:
        app.dependency_overrides.clear()
        session.close()

