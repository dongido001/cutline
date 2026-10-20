import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import app
from backend.database import Base, get_db
from backend.final_cut import probe_duration_ms
from backend.models import LibraryAudio, Project, TimelineAsset
from backend.settings import settings

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'library.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _asset(db, project: Project, filename: str, lavfi: str, mime: str) -> TimelineAsset:
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", lavfi, str(settings.media_dir / filename)], check=True, capture_output=True)
    asset = TimelineAsset(project_id=project.id, filename=filename, mime_type=mime, label=filename)
    db.add(asset)
    db.commit()
    return asset


def _project(db, title: str = "Film") -> Project:
    project = Project(title=title, status="shot_plan", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    return project


def test_save_trimmed_audio_to_the_library_and_list_it(client, db) -> None:
    project = _project(db)
    asset = _asset(db, project, "song.wav", "sine=frequency=440:duration=6", "audio/wav")

    response = client.post(f"/api/projects/{project.id}/library-audio", json={
        "source": {"kind": "asset", "ref_id": asset.id}, "source_in_ms": 1000, "duration_ms": 2500, "label": "  Tension piano  ",
    })

    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["label"] == "Tension piano"
    assert saved["mime_type"] == "audio/mp4"
    assert abs(saved["duration_ms"] - 2500) <= 80
    assert saved["url"].startswith("/media/library-") and saved["url"].endswith(".m4a")
    assert abs(probe_duration_ms(settings.media_dir / saved["url"].removeprefix("/media/")) - 2500) <= 80

    other = _project(db, "Another film")
    assert other.id != project.id
    listed = client.get("/api/library-audio").json()
    assert [item["id"] for item in listed] == [saved["id"]]


def test_library_rejects_other_projects_and_silent_clips(client, db) -> None:
    project = _project(db)
    other = _project(db, "Other")
    foreign = _asset(db, other, "foreign.wav", "sine=duration=1", "audio/wav")
    silent = _asset(db, project, "silent.mp4", "color=c=black:s=32x32:d=1", "video/mp4")

    def save(source):
        return client.post(f"/api/projects/{project.id}/library-audio", json={"source": source, "duration_ms": 500, "label": "x"})

    assert save({"kind": "asset", "ref_id": foreign.id}).status_code == 404
    assert save({"kind": "asset", "ref_id": silent.id}).status_code == 422
    assert save({"kind": "shot_image", "ref_id": "nope"}).status_code == 422
    assert db.query(LibraryAudio).count() == 0


def test_delete_removes_library_entry_and_file(client, db) -> None:
    project = _project(db)
    asset = _asset(db, project, "song.wav", "sine=duration=2", "audio/wav")
    saved = client.post(f"/api/projects/{project.id}/library-audio", json={
        "source": {"kind": "asset", "ref_id": asset.id}, "duration_ms": 1000, "label": "Loop",
    }).json()
    path = settings.media_dir / saved["url"].removeprefix("/media/")
    assert path.is_file()

    assert client.delete(f"/api/library-audio/{saved['id']}").status_code == 204
    assert not path.is_file()
    assert client.get("/api/library-audio").json() == []
    assert client.delete(f"/api/library-audio/{saved['id']}").status_code == 404
