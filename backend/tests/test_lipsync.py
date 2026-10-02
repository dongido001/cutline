from pathlib import Path
import subprocess
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot, ShotImage
from backend.settings import settings


def test_lipsync_api(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lipsync.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    project = Project(title="LipSync Test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="SCENE 1")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Shot 1",
        dialogue=[{"character": "AMAKA", "text": "Is this what you call clean?"}],
    )
    image_file = media_dir / "frame.jpg"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1", "-update", "1", "-frames:v", "1", str(image_file)], check=True)
    shot_image = ShotImage(shot=shot, filename=image_file.name, mime_type="image/jpeg", source="generated", provider="mock")
    shot.image = shot_image

    session.add(project)
    session.commit()

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    try:
        # Check system lipsync status
        res_status = client.get("/api/system/lipsync/status")
        assert res_status.status_code == 200
        assert "available" in res_status.json()

        # Queue lipsync job
        res = client.post(f"/api/shots/{shot.id}/lipsync")
        assert res.status_code == 202
        data = res.json()
        assert data["kind"] == "shot_lipsync"
        assert data["status"] == "queued"
    finally:
        app.dependency_overrides.clear()

def test_execute_shot_lipsync(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lipsync_exec.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    project = Project(title="Exec Test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="SCENE 1")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Shot 1",
        dialogue=[{"character": "AMAKA", "text": "Testing dialogue audio"}],
    )
    image_file = media_dir / "frame.jpg"
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1", "-update", "1", "-frames:v", "1", str(image_file)], check=True)
    shot_image = ShotImage(shot=shot, filename=image_file.name, mime_type="image/jpeg", source="generated", provider="mock")
    shot.image = shot_image

    # Dummy audio file: 2 seconds of silent aac
    audio_file = media_dir / "speech.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", str(audio_file)], check=True)
    shot.dialogue_audio_filename = audio_file.name
    shot.dialogue_audio_mime = "audio/mpeg"

    session.add(project)
    session.commit()

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    from backend.lipsync import execute_shot_lipsync
    take = execute_shot_lipsync(shot.id, session)

    assert take is not None
    assert take.media_type == "clip"
    assert take.source == "lipsync"
    assert (media_dir / take.filename).is_file()
    assert shot.clip is not None
    assert shot.clip.filename == take.filename
    assert shot.clip.source == "lipsync"
    assert shot.duration_ms >= 1000
