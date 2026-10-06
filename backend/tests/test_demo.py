import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import app
from backend.database import Base, get_db
from backend.demo import load_demo_project
from backend.models import Shot
from backend.settings import settings


def test_demo_is_independent_and_loads_optional_media(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        try:
            response = TestClient(app).post("/api/demo-project")
            assert response.status_code == 201
            first_id = response.json()["id"]
            demo = json.loads((Path(__file__).parents[1] / "src/backend/demo.json").read_text())
            media = demo["scenes"][0]["shots"][0]["image"]
            source = tmp_path / "demo-media" / media["filename"]
            source.parent.mkdir()
            source.write_bytes(b"sample media")
            project = load_demo_project(db)
            assert project.id != first_id
            assert project.status == "shot_plan"
            assert len(project.episodes) == 1
            assert len(project.characters) == 3
            scenes = project.versions[0].scenes
            assert len(scenes) == 8
            assert sum(len(scene.shots) for scene in scenes) == 16
            assert all(shot.character_ids for scene in scenes for shot in scene.shots)
            shot = scenes[0].shots[0]
            assert shot.image is not None
            assert (settings.media_dir / shot.image.filename).read_bytes() == b"sample media"
            assert source.is_file()
            assert db.query(Shot).count() == 32
        finally:
            app.dependency_overrides.clear()
