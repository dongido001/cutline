from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend import api
from backend.database import Base, get_db
from backend.media import _assert_safe_image_url, delete_media_file
from backend.models import Episode, Job, Project, ProjectVariantExport, Scene, ScriptVersion, Shot
from backend.queue_admin import job_matches_queue
from backend.settings import settings
from backend.system_guard import require_local_system
from backend.timeline import latest_shots
from backend.token_cipher import decrypt_secret, encrypt_secret


def _session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "token_secret", "test-token-secret")
    monkeypatch.setattr(settings, "system_token", "")
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    api.app.dependency_overrides[get_db] = lambda: session
    return engine, session, TestClient(api.app)


def test_latest_shots_does_not_fall_back_across_episodes(tmp_path, monkeypatch):
    engine, session, _client = _session(tmp_path, monkeypatch)
    try:
        project = Project(title="Series")
        ep1 = Episode(project=project, episode_number=1, title="One")
        ep2 = Episode(project=project, episode_number=2, title="Two")
        v1 = ScriptVersion(project=project, episode=ep1, source_text="one")
        scene = Scene(script_version=v1, order_index=0, title="Kitchen")
        Shot(scene=scene, order_index=0, title="A")
        session.add(project)
        session.add(ep2)
        session.commit()
        assert latest_shots(project.id, session, episode_id=ep1.id)
        assert latest_shots(project.id, session, episode_id=ep2.id) == []
        assert latest_shots(project.id, session, episode_id="missing-ep") == []
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_chatgpt_flush_cancels_analysis_not_drafts(tmp_path, monkeypatch):
    engine, session, client = _session(tmp_path, monkeypatch)
    try:
        project = Project(title="Flush")
        session.add(project)
        session.commit()
        analysis = Job(project_id=project.id, kind="script_analysis", status="queued")
        draft = Job(project_id=project.id, kind="create_draft", status="queued")
        image = Job(project_id=project.id, kind="shot_image", status="queued")
        session.add_all([analysis, draft, image])
        session.commit()
        response = client.post("/api/system/queues/chatgpt/flush")
        assert response.status_code == 200
        session.refresh(analysis)
        session.refresh(draft)
        session.refresh(image)
        assert analysis.status == "cancelled"
        assert draft.status == "queued"
        assert image.status == "queued"
        assert job_matches_queue("script_continuation", "chatgpt")
        assert not job_matches_queue("create_draft", "chatgpt")
        assert job_matches_queue("create_draft", "video")
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_publish_shots_requires_selection(tmp_path, monkeypatch):
    engine, session, client = _session(tmp_path, monkeypatch)
    try:
        project = Project(title="Publish", status="shot_plan")
        session.add(project)
        session.commit()
        response = client.post(
            f"/api/projects/{project.id}/publish",
            json={
                "platforms": ["youtube"],
                "scope": "shots",
                "variant": "landscape",
                "title": "Launch",
                "selected_asset_ids": [],
                "privacy": {"youtube": "private"},
            },
        )
        assert response.status_code == 422
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_delete_media_file_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("keep")
    delete_media_file("../outside.txt")
    delete_media_file(str(outside))
    assert outside.read_text() == "keep"
    with pytest.raises(RuntimeError):
        _assert_safe_image_url("http://127.0.0.1:8000/media/secret.png")
    _assert_safe_image_url("https://lh3.googleusercontent.com/abc.png")


def test_delete_final_cut_is_episode_scoped(tmp_path, monkeypatch):
    engine, session, client = _session(tmp_path, monkeypatch)
    try:
        project = Project(title="Cuts")
        ep1 = Episode(project=project, episode_number=1, title="One")
        ep2 = Episode(project=project, episode_number=2, title="Two")
        session.add(project)
        session.flush()
        keep = ProjectVariantExport(
            project_id=project.id,
            episode_id=ep1.id,
            variant="landscape",
            filename="keep.mp4",
            duration_ms=1000,
            width=1920,
            height=1080,
            clip_count=1,
            fallback_count=0,
        )
        drop = ProjectVariantExport(
            project_id=project.id,
            episode_id=ep2.id,
            variant="landscape",
            filename="drop.mp4",
            duration_ms=1000,
            width=1920,
            height=1080,
            clip_count=1,
            fallback_count=0,
        )
        session.add_all([keep, drop])
        session.commit()
        response = client.delete(
            f"/api/projects/{project.id}/final-cut?variant=landscape&episode_id={ep2.id}"
        )
        assert response.status_code == 204
        remaining = session.scalars(select(ProjectVariantExport)).all()
        assert [item.filename for item in remaining] == ["keep.mp4"]
    finally:
        api.app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_system_controls_reject_non_loopback_and_bad_token(monkeypatch):
    monkeypatch.setattr(settings, "system_token", "")
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"), headers={})
    with pytest.raises(HTTPException) as error:
        require_local_system(request)
    assert error.value.status_code == 403

    monkeypatch.setattr(settings, "system_token", "secret")
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    with pytest.raises(HTTPException):
        require_local_system(local)
    ok = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"X-Cutline-System-Token": "secret"},
    )
    require_local_system(ok)


def test_social_tokens_round_trip_encrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "token_secret", "unit-secret")
    packed = encrypt_secret("ya29.access")
    assert packed != "ya29.access"
    assert packed.startswith("enc:v1:")
    assert decrypt_secret(packed) == "ya29.access"
    assert decrypt_secret("legacy-plaintext") == "legacy-plaintext"
