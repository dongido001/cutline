from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend import api
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot


def test_shot_notes_keep_history_attachments_and_counts(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'notes.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Notes", status="shot_plan")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="Scene")
    shot = Shot(scene=scene, order_index=0, title="Fix this")
    session.add(project)
    session.commit()
    monkeypatch.setattr(api.settings, "data_dir", tmp_path)

    def override_db():
        yield session

    api.app.dependency_overrides[get_db] = override_db
    client = TestClient(api.app)
    try:
        created = client.post(
            f"/api/shots/{shot.id}/notes",
            data={"body": "Remove the boom mic"},
            files=[("files", ("reference.png", b"png-data", "image/png"))],
        )
        assert created.status_code == 201
        note = created.json()
        assert note["resolved"] is False
        assert note["attachments"][0]["original_name"] == "reference.png"
        att_path = api.settings.media_dir / note["attachments"][0]["url"].split("/")[-1]
        assert att_path.exists()

        # Add second note (e.g. reminder)
        second_resp = client.post(
            f"/api/shots/{shot.id}/notes",
            data={"body": "Check color grading on Zara's blouse"},
        )
        assert second_resp.status_code == 201
        second_note = second_resp.json()

        # Project level count should be 2
        projects = client.get("/api/projects").json()
        assert projects[0]["unresolved_note_count"] == 2

        # Project detail should also have unresolved_note_count == 2
        project_detail = client.get(f"/api/projects/{project.id}").json()
        assert project_detail["unresolved_note_count"] == 2

        # Add extra attachment to second note
        extra_att = client.post(
            f"/api/shot-notes/{second_note['id']}/attachments",
            files=[("files", ("screenshot.jpg", b"jpg-bytes", "image/jpeg"))],
        )
        assert extra_att.status_code == 200
        assert len(extra_att.json()["attachments"]) == 1

        # Resolve the first note
        resolved = client.patch(f"/api/shot-notes/{note['id']}", json={"resolved": True})
        assert resolved.status_code == 200
        assert resolved.json()["body"] == "Remove the boom mic"
        assert resolved.json()["resolved"] is True

        # PATCH must reject blank and oversized note bodies.
        for invalid_body in ('   ', 'x' * 10001):
            invalid = client.patch('/api/shot-notes/' + note['id'], json={'body': invalid_body})
            assert invalid.status_code == 422
            unchanged = client.get('/api/shots/' + shot.id + '/notes').json()
            assert next(n for n in unchanged if n['id'] == note['id'])['body'] == 'Remove the boom mic'

        valid_edit = client.patch('/api/shot-notes/' + note['id'], json={'body': '  Remove the boom mic  '})
        assert valid_edit.status_code == 200
        assert valid_edit.json()['body'] == 'Remove the boom mic'

        # Now unresolved count should drop to 1
        projects = client.get("/api/projects").json()
        assert projects[0]["unresolved_note_count"] == 1
        project_detail = client.get(f"/api/projects/{project.id}").json()
        assert project_detail["unresolved_note_count"] == 1

        # Delete second note
        del_resp = client.delete(f"/api/shot-notes/{second_note['id']}")
        assert del_resp.status_code == 204

        # Since first note is resolved and second is deleted, unresolved count should be 0
        projects = client.get("/api/projects").json()
        assert projects[0]["unresolved_note_count"] == 0

        # Note history remains: the resolved note is still queryable
        shot_notes = client.get(f"/api/shots/{shot.id}/notes").json()
        assert len(shot_notes) == 1
        assert shot_notes[0]["id"] == note["id"]
        assert shot_notes[0]["resolved"] is True
    finally:
        api.app.dependency_overrides.clear()
        session.close()
