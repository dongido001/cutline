import array
import math
import shutil
import subprocess
import wave

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Project, Scene, ScriptVersion, Shot, ShotImage, TimelineAsset
from backend.schemas import TimelineDocument
from backend.settings import settings
from backend.timeline_render import assemble_timeline

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")


def _peak(path, start: float, seconds: float) -> int:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{seconds}", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    samples = array.array("h", raw)
    return max((abs(value) for value in samples), default=0)


def _render(tmp_path, monkeypatch, loop: bool):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "export_width_landscape", 160)
    monkeypatch.setattr(settings, "export_height_landscape", 90)
    engine = create_engine(f"sqlite:///{tmp_path / 'loop.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    (media / "frame.ppm").write_text("P3\n2 2\n255\n30 90 150  30 90 150\n30 90 150  30 90 150\n")
    with wave.open(str(media / "beep.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        tone = [int(12000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(6400)]
        wf.writeframes(array.array("h", tone + [0] * 9600).tobytes())  # 0.4s beep, 0.6s silence

    project = Project(title="Loop", status="shot_plan", aspect_ratio="16:9")
    scene = Scene(script_version=ScriptVersion(project=project, source_text="t"), order_index=0, title="INT. ROOM")
    shot = Shot(scene=scene, order_index=0, title="Still", duration_ms=3000)
    shot.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.flush()
    asset = TimelineAsset(project_id=project.id, filename="beep.wav", mime_type="audio/wav", label="Beep")
    session.add(asset)
    session.commit()
    document = TimelineDocument.model_validate({
        "primary": [{"id": "s", "kind": "image", "source": {"kind": "shot_image", "ref_id": shot.id}, "duration_ms": 3000}],
        "audio_overlays": [{
            "id": "a", "label": "Beep", "source": {"kind": "asset", "ref_id": asset.id},
            "start_ms": 0, "duration_ms": 3000, "source_in_ms": 0, "loop": loop,
        }],
    })
    result = assemble_timeline(project, document, "landscape", session)
    session.close()
    return media / result.filename


def test_audio_longer_than_its_file_repeats_until_the_clip_ends(tmp_path, monkeypatch) -> None:
    output = _render(tmp_path, monkeypatch, loop=True)
    assert _peak(output, 0.05, 0.3) > 3000
    assert _peak(output, 0.55, 0.35) < 300
    assert _peak(output, 1.05, 0.3) > 3000
    assert _peak(output, 2.05, 0.3) > 3000
    assert _peak(output, 2.55, 0.35) < 300


def test_audio_loop_off_leaves_silence_after_the_file_ends(tmp_path, monkeypatch) -> None:
    output = _render(tmp_path, monkeypatch, loop=False)
    assert _peak(output, 0.05, 0.3) > 3000
    assert _peak(output, 1.05, 0.3) < 300
    assert _peak(output, 2.05, 0.3) < 300


def test_audio_loop_defaults_on() -> None:
    document = TimelineDocument.model_validate({"primary": [], "audio_overlays": [{
        "id": "a", "source": {"kind": "asset", "ref_id": "x"}, "start_ms": 0, "duration_ms": 1000,
    }]})
    assert document.audio_overlays[0].loop is True
