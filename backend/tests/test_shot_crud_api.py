from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot
from backend.timeline import ensure_timeline


def test_create_and_delete_shot(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'shot_crud.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    project = Project(title="CRUD Test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="SCENE 1")
    shot0 = Shot(scene=scene, order_index=0, title="Shot 1", action="Action 1", image_prompt="Prompt 1")
    shot1 = Shot(scene=scene, order_index=1, title="Shot 2", action="Action 2", image_prompt="Prompt 2")
    session.add(project)
    session.commit()

    ensure_timeline(project, session)
    session.commit()

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    try:
        # 1. Append a new shot at the end
        res = client.post(
            f"/api/scenes/{scene.id}/shots",
            json={
                "title": "Shot 3",
                "action": "Action 3",
                "framing": "Close-up",
                "duration_ms": 4000,
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Shot 3"
        assert data["order_index"] == 2
        assert data["duration_ms"] == 4000
        assert data["framing"] == "Close-up"
        shot3_id = data["id"]

        # 2. Insert a shot between Shot 1 and Shot 2
        res2 = client.post(
            f"/api/scenes/{scene.id}/shots",
            json={
                "title": "Shot 1.5",
                "action": "Insert reaction",
                "insert_after_shot_id": shot0.id,
            },
        )
        assert res2.status_code == 201
        data2 = res2.json()
        assert data2["order_index"] == 1
        shot1_5_id = data2["id"]

        # Check all shots order in DB
        session.expire_all()
        shots_in_db = session.scalars(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.order_index)).all()
        assert len(shots_in_db) == 4
        assert [s.id for s in shots_in_db] == [shot0.id, shot1_5_id, shot1.id, shot3_id]
        assert [s.order_index for s in shots_in_db] == [0, 1, 2, 3]

        # 3. Delete the inserted shot
        res_del = client.delete(f"/api/shots/{shot1_5_id}")
        assert res_del.status_code == 204

        session.expire_all()
        shots_after_del = session.scalars(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.order_index)).all()
        assert len(shots_after_del) == 3
        assert [s.id for s in shots_after_del] == [shot0.id, shot1.id, shot3_id]
        assert [s.order_index for s in shots_after_del] == [0, 1, 2]
    finally:
        app.dependency_overrides.clear()


def test_read_shot_endpoint(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'read_shot.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)

    project = Project(title="Read Shot Test", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="SCENE 1")
    shot = Shot(scene=scene, order_index=0, title="Shot 1", action="Action 1", image_prompt="Prompt 1")
    session.add(project)
    session.commit()

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    try:
        res = client.get(f"/api/shots/{shot.id}")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == shot.id
        assert data["title"] == "Shot 1"
        assert "takes" in data
        assert isinstance(data["takes"], list)

        res_404 = client.get("/api/shots/non-existent-shot-id")
        assert res_404.status_code == 404
    finally:
        app.dependency_overrides.clear()

