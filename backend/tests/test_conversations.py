import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.conversations import delete_project_conversation
from backend.database import Base
from backend.models import Job, Project, Scene, ScriptVersion, Shot, ShotTake

URL = "https://chatgpt.com/c/6ad6839a-5588-83ea-a162-39585ae451aa"


@pytest.fixture
def linked(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chats.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title="Film")
        version = ScriptVersion(project=project, source_text="script")
        scene = Scene(script_version=version, order_index=0, title="Office")
        shot = Shot(scene=scene, order_index=0, conversation_url=URL)
        take = ShotTake(shot=shot, media_type="image", filename="keep.png", mime_type="image/png",
                        source="generated", provider="chatgpt_web", settings={"conversation_url": URL, "seed": 1})
        job = Job(project=project, kind="shot_image", status="completed", conversation_url=URL,
                  attempts=[{"conversation_url": URL}])
        db.add_all([project, version, scene, shot, take, job])
        db.commit()
        yield db, project, shot, take, job


def test_clears_links_from_cutline_and_preserves_take_without_remote_deletion(linked):
    db, project, shot, take, job = linked
    delete_project_conversation(db, project.id, URL)
    assert shot.conversation_url is None
    assert take.settings == {"conversation_url": None, "seed": 1}
    assert take.filename == "keep.png"
    assert job.conversation_url is None
    assert job.attempts == [{"conversation_url": None}]


@pytest.mark.parametrize("case,code", [("invalid", 400), ("unlinked", 404), ("running", 409)])
def test_rejects_unsafe_deletion(linked, case, code):
    db, project, shot, take, job = linked
    url = URL
    if case == "invalid":
        url = "https://example.com/c/chat"
    elif case == "unlinked":
        url = URL.replace("6ad6839a", "aaaaaaaa")
    else:
        job.status = "running"
        db.commit()
    with pytest.raises(HTTPException) as error:
        delete_project_conversation(db, project.id, url)
    assert error.value.status_code == code


def test_unrelated_running_job_does_not_block_removal(linked):
    db, project, shot, take, job = linked
    db.add(Job(project=project, kind="shot_image", status="running",
               attempts=[{"shot_id": "another-shot"}]))
    db.commit()
    delete_project_conversation(db, project.id, URL)
    assert shot.conversation_url is None


@pytest.mark.parametrize("status", ["queued", "running"])
def test_linked_shot_job_blocks_removal_before_it_has_a_url(linked, status):
    db, project, shot, take, job = linked
    job.status = status
    job.conversation_url = None
    job.attempts = [{"shot_id": shot.id}]
    db.commit()
    with pytest.raises(HTTPException) as error:
        delete_project_conversation(db, project.id, URL)
    assert error.value.status_code == 409
    assert shot.conversation_url == URL


def test_removes_legacy_gemini_url_using_displayed_url(linked):
    db, project, shot, take, job = linked
    legacy = "https://gemini.google.com/app/c_123abc"
    shot.conversation_url = legacy
    take.settings = {"conversation_url": legacy}
    job.conversation_url = legacy
    job.attempts = [{"edit_conversation_url": legacy}]
    db.commit()
    delete_project_conversation(db, project.id, "https://gemini.google.com/app/123abc")
    assert shot.conversation_url is None
    assert take.settings["conversation_url"] is None
    assert job.attempts == [{"edit_conversation_url": None}]
