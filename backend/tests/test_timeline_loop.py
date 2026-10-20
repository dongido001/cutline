import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Project, Scene, ScriptVersion, Shot
from backend.schemas import TimelineDocument
from backend.timeline import validate_timeline


def _document(loop: bool) -> TimelineDocument:
    return TimelineDocument.model_validate({
        "version": 1,
        "primary": [{
            "id": "p1", "kind": "video", "label": "Clip", "source": {"kind": "shot_clip", "ref_id": "shot-1"},
            "duration_ms": 9000, "source_in_ms": 1000, "source_out_ms": 4000, "loop": loop,
        }],
        "image_overlays": [], "text_overlays": [],
    })


def test_looping_clip_may_outlast_its_trim_range() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title="Loop", status="shot_plan")
        scene = Scene(script_version=ScriptVersion(project=project, source_text="x"), order_index=0, title="S")
        Shot(id="shot-1", scene=scene, order_index=0, title="Shot", duration_ms=3000)
        db.add(project)
        db.commit()

        validate_timeline(_document(loop=True), project, db)
        with pytest.raises(ValueError, match="duration must match its trim range"):
            validate_timeline(_document(loop=False), project, db)
