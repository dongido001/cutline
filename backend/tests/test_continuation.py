from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Project, Scene, ScriptVersion, Shot
from backend.tasks import _offset_continuation
from backend.timeline import append_missing_shots, ensure_timeline


def test_continuation_timecodes_are_offset_and_suggestion_targets_are_global() -> None:
    result = {
        "scenes": [
            {
                "start_ms": 0,
                "end_ms": 6000,
                "shots": [{"start_ms": 0, "end_ms": 6000}],
            }
        ],
        "suggestions": [{"target": {"scene_index": 0, "shot_index": 0}}],
    }

    adjusted = _offset_continuation(result, cursor_ms=48_000, scene_offset=3)

    assert adjusted["scenes"][0]["start_ms"] == 48_000
    assert adjusted["scenes"][0]["end_ms"] == 54_000
    assert adjusted["scenes"][0]["shots"][0]["start_ms"] == 48_000
    assert adjusted["suggestions"][0]["target"]["scene_index"] == 3


def test_new_shots_append_without_rewriting_existing_timeline(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'continuation.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title="Continuation", status="shot_plan")
        version = ScriptVersion(project=project, source_text="Original")
        first_scene = Scene(script_version=version, order_index=0, title="Opening")
        first_shot = Shot(scene=first_scene, order_index=0, title="Original shot", duration_ms=2000)
        db.add(project)
        db.commit()

        timeline = ensure_timeline(project, db)
        original_id = timeline.document["primary"][0]["id"]
        edited_document = deepcopy(timeline.document)
        edited_document["primary"][0]["label"] = "User-edited label"
        timeline.document = edited_document
        db.commit()

        second_scene = Scene(script_version=version, order_index=1, title="Continuation")
        second_shot = Shot(scene=second_scene, order_index=0, title="New shot", duration_ms=3000)
        db.add(second_scene)
        db.commit()
        db.refresh(project)

        assert append_missing_shots(project, db) == 1
        db.commit()
        db.refresh(project.timeline)

        primary = project.timeline.document["primary"]
        assert primary[0]["id"] == original_id
        assert primary[0]["label"] == "User-edited label"
        assert primary[0]["source"]["ref_id"] == first_shot.id
        assert primary[1]["source"]["ref_id"] == second_shot.id
        assert append_missing_shots(project, db) == 0
from copy import deepcopy
