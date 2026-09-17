import shutil

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Project, Scene, ScriptVersion, Shot, ShotImage
from backend.schemas import TimelineDocument
from backend.settings import settings
from backend.timeline_render import assemble_timeline


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_timeline_renders_both_variants_with_text_overlay(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'render.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    frame = tmp_path / "media" / "frame.ppm"
    frame.parent.mkdir()
    frame.write_text("P3\n2 2\n255\n120 20 20  120 20 20\n120 20 20  120 20 20\n")
    project = Project(title="Render test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Still", duration_ms=500)
    shot.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.commit()
    document = TimelineDocument.model_validate(
        {
            "version": 1,
            "primary": [
                {
                    "id": "still-1",
                    "kind": "image",
                    "label": "Still",
                    "source": {"kind": "shot_image", "ref_id": shot.id},
                    "duration_ms": 500,
                    "source_in_ms": 0,
                    "source_out_ms": None,
                    "text": "",
                    "variant": {},
                }
            ],
            "image_overlays": [],
            "text_overlays": [
                {
                    "id": "text-1",
                    "text": "HELLO",
                    "start_ms": 100,
                    "duration_ms": 300,
                    "presentation": "overlay",
                    "size": "medium",
                    "color": "white",
                    "background": "shadow",
                    "variant": {"shorts": {"position": "bottom"}},
                }
            ],
        }
    )
    landscape = assemble_timeline(project, document, "landscape", session)
    shorts = assemble_timeline(project, document, "shorts", session)
    assert (tmp_path / "media" / landscape.filename).is_file()
    assert (tmp_path / "media" / shorts.filename).is_file()
    assert (landscape.width, landscape.height) == (1920, 1080)
    assert (shorts.width, shorts.height) == (1080, 1920)
    assert 450 <= landscape.duration_ms <= 600
    assert 450 <= shorts.duration_ms <= 600
    session.close()
