from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot
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
