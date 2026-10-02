from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Character, Project, Scene, ScriptVersion, Shot
from backend.settings import settings


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'voice_test.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Voice test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Test shot",
        image_prompt="A person speaking",
        dialogue=[{"speaker": "ALICE", "line": "Hello there."}],
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session, project, shot


def test_get_voice_providers(tmp_path, monkeypatch):
    client, session, project, _shot = _client(tmp_path, monkeypatch)
    try:
        res = client.get("/api/voices")
        assert res.status_code == 200
        providers = res.json()
        assert len(providers) >= 3
        vibevoice = next((p for p in providers if p["id"] == "vibevoice"), None)
        assert vibevoice is not None
        assert vibevoice["available"] is True
        assert len(vibevoice["voices"]) >= 9

        # Verify voice preset structure
        maya = next((v for v in vibevoice["voices"] if v["id"] == "en-Maya_woman"), None)
        assert maya is not None
        assert maya["name"] == "Maya"
        assert maya["gender"] == "female"
        assert maya["preview_url"] == "/api/voices/vibevoice/en-Maya_woman/sample"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_character_voice_assignment(tmp_path, monkeypatch):
    client, session, project, _shot = _client(tmp_path, monkeypatch)
    try:
        created = client.post(
            f"/api/projects/{project.id}/characters",
            json={"name": "MARA", "notes": "lead", "voice_provider": "vibevoice", "voice_id": "en-Alice_woman"},
        )
        assert created.status_code == 201
        char_data = created.json()
        assert char_data["voice_provider"] == "vibevoice"
        assert char_data["voice_id"] == "en-Alice_woman"

        # Update voice assignment
        updated = client.patch(
            f"/api/characters/{char_data['id']}",
            json={"voice_provider": "vibevoice", "voice_id": "en-Maya_woman"},
        )
        assert updated.status_code == 200
        assert updated.json()["voice_id"] == "en-Maya_woman"

        # Verify persisted in database
        session.expire_all()
        db_char = session.get(Character, char_data["id"])
        assert db_char.voice_id == "en-Maya_woman"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_voice_audition_sample(tmp_path, monkeypatch):
    client, session, project, _shot = _client(tmp_path, monkeypatch)
    try:
        # Sample audio route
        sample_res = client.get("/api/voices/vibevoice/en-Maya_woman/sample")
        assert sample_res.status_code == 200
        assert sample_res.headers["content-type"] in ["audio/wav", "audio/x-wav"]

        # Audition endpoint with 'sample' text
        audition_res = client.post(
            "/api/voices/audition",
            json={"provider": "vibevoice", "voice_id": "en-Maya_woman", "text": "sample"},
        )
        assert audition_res.status_code == 200
        data = audition_res.json()
        assert data["provider"] == "vibevoice"
        assert data["voice_id"] == "en-Maya_woman"
        assert data["url"].startswith("/media/audition-")
    finally:
        app.dependency_overrides.clear()
        session.close()

def test_upload_custom_voice(tmp_path, monkeypatch):
    client, session, project, _shot = _client(tmp_path, monkeypatch)
    try:
        # Create dummy wav bytes
        import io, wave
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\x00\x00' * 24000)
        wav_bytes = buf.getvalue()

        res = client.post(
            "/api/voices/custom",
            data={
                "name": "Ngozi",
                "gender": "female",
                "language": "en-NG",
                "description": "Nigerian female voice",
            },
            files={"file": ("sample.wav", wav_bytes, "audio/wav")},
        )
        assert res.status_code == 200
        preset = res.json()
        assert preset["name"] == "Ngozi"
        assert preset["gender"] == "female"
        assert preset["language"] == "en-NG"
        assert preset["id"] == "custom-ngozi_female"
        assert preset["preview_url"] == "/api/voices/vibevoice/custom-ngozi_female/sample"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_shot_audio_generate_with_speaker_line_keys(tmp_path, monkeypatch):
    client, session, project, shot = _client(tmp_path, monkeypatch)
    try:
        # shot has dialogue: [{'speaker': 'ALICE', 'line': 'Hello there.'}]
        res = client.post(f"/api/shots/{shot.id}/audio/generate")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["dialogue_audio_filename"] is not None
        assert data["dialogue_audio_filename"].startswith("dialogue-")
    finally:
        app.dependency_overrides.clear()
        session.close()
