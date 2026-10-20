import shutil
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend import timeline_render
from backend.database import Base
from backend.final_cut import probe_duration_ms
from backend.models import Project, TimelineAsset
from backend.schemas import TimelineDocument
from backend.settings import settings
from backend.timeline_render import assemble_timeline, effective_transitions

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")


def _clip(path, color: str, seconds: float = 1.5) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )


def _pixel(path, seconds: float) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{seconds}", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def _setup(tmp_path, monkeypatch, colors=("red", "blue", "green")):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "export_width_landscape", 160)
    monkeypatch.setattr(settings, "export_height_landscape", 90)
    monkeypatch.setattr(settings, "export_video_preset", "ultrafast")
    engine = create_engine(f"sqlite:///{tmp_path / 'render.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Transitions", status="shot_plan", aspect_ratio="16:9")
    session.add(project)
    session.commit()
    items = []
    for index, color in enumerate(colors):
        filename = f"{index}-{color}.mp4"
        _clip(settings.media_dir / filename, color)
        asset = TimelineAsset(project_id=project.id, filename=filename, mime_type="video/mp4", label=color)
        session.add(asset)
        session.commit()
        items.append({
            "id": f"clip-{index}", "kind": "video", "label": color,
            "source": {"kind": "asset", "ref_id": asset.id},
            "duration_ms": 1000, "source_in_ms": 0, "source_out_ms": 1000, "text": "", "variant": {},
        })
    return session, project, items


def test_effective_transitions_resolve_default_override_and_cap() -> None:
    document = TimelineDocument.model_validate({
        "primary": [
            {"id": "a", "kind": "video", "duration_ms": 2000},
            {"id": "b", "kind": "video", "duration_ms": 300},
            {"id": "c", "kind": "video", "duration_ms": 2000, "transition": {"kind": "cut"}},
            {"id": "d", "kind": "image", "duration_ms": 2000, "transition": {"kind": "wipeleft", "duration_ms": 1500}},
        ],
        "default_transition": {"kind": "dissolve", "duration_ms": 800},
    })
    assert effective_transitions(document) == [("dissolve", 0.3), ("cut", 0.0), ("wipeleft", 1.5)]
    document.default_transition = None
    assert effective_transitions(document) == [("cut", 0.0), ("cut", 0.0), ("wipeleft", 1.5)]


def test_transition_keeps_length_and_blends_at_the_cut(tmp_path, monkeypatch) -> None:
    session, project, items = _setup(tmp_path, monkeypatch)
    items[2]["transition"] = {"kind": "cut"}
    document = TimelineDocument.model_validate({
        "primary": items,
        "default_transition": {"kind": "fade", "duration_ms": 500},
    })
    result = assemble_timeline(project, document, "landscape", session)
    output = settings.media_dir / result.filename

    assert abs(probe_duration_ms(output) - 3000) <= 80
    red, _g, blue = _pixel(output, 1.0)
    assert red > 60 and blue > 60, (red, blue)
    r, _g, b = _pixel(output, 0.4)
    assert r > 200 and b < 40
    r, _g, b = _pixel(output, 1.6)
    assert b > 200 and r < 40
    r, g, b = _pixel(output, 2.1)
    assert g > 100 and r < 40 and b < 40
    session.close()


def test_cut_followed_by_a_transition_renders(tmp_path, monkeypatch) -> None:
    session, project, items = _setup(tmp_path, monkeypatch)
    items[1]["transition"] = {"kind": "cut"}
    items[2]["transition"] = {"kind": "wipeleft", "duration_ms": 400}
    document = TimelineDocument.model_validate({"primary": items})
    output = settings.media_dir / assemble_timeline(project, document, "landscape", session).filename
    assert abs(probe_duration_ms(output) - 3000) <= 80
    session.close()


def test_clip_without_spare_head_holds_its_first_frame(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    source = tmp_path / "blue.mp4"
    _clip(source, "blue", seconds=1.0)
    item = TimelineDocument.model_validate({"primary": [{
        "id": "b", "kind": "video", "duration_ms": 1000, "source_in_ms": 0, "source_out_ms": 1000,
    }]}).primary[0]
    output = tmp_path / "segment.mp4"
    timeline_render._normalize_primary(
        item, timeline_render.ResolvedMedia(source, True, "16:9", True), output, 64, 36, "landscape",
        head_s=0.25, tail_s=0.25,
    )
    assert abs(probe_duration_ms(output) - 1500) <= 60
    r, _g, b = _pixel(output, 0.05)
    assert b > 200 and r < 40


def test_all_cuts_keep_the_fast_copy_concat(tmp_path, monkeypatch) -> None:
    session, project, items = _setup(tmp_path, monkeypatch)

    def fail(*_args):
        raise AssertionError("transition join should not run for hard cuts")

    monkeypatch.setattr(timeline_render, "_join_with_transitions", fail)
    document = TimelineDocument.model_validate({"primary": items})
    result = assemble_timeline(project, document, "landscape", session)
    assert abs(result.duration_ms - 3000) <= 80
    session.close()


def _stream_seconds(path, kind: str) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip())


def test_audio_stays_in_sync_across_many_crossfades(tmp_path, monkeypatch) -> None:
    session, project, items = _setup(tmp_path, monkeypatch, colors=("red", "blue") * 4)
    document = TimelineDocument.model_validate({
        "primary": items,
        "default_transition": {"kind": "dissolve", "duration_ms": 300},
    })
    commands: list[list[str]] = []
    real_run = timeline_render._run

    def record(command, *args, **kwargs):
        commands.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(timeline_render, "_run", record)
    output = settings.media_dir / assemble_timeline(project, document, "landscape", session).filename
    graph = next(c[c.index("-filter_complex") + 1] for c in commands if "-filter_complex" in c and "acrossfade" in " ".join(c))
    for index in range(len(items)):
        assert f"[{index}:a]apad,atrim=duration=" in graph
    assert abs(_stream_seconds(output, "a") - 8.0) <= 0.05
    assert abs(_stream_seconds(output, "v") - 8.0) <= 0.1
    session.close()


def test_frame_rounding_does_not_accumulate_across_joins(tmp_path, monkeypatch) -> None:
    session, project, items = _setup(tmp_path, monkeypatch, colors=("red", "blue") * 4)
    for index, item in enumerate(items):
        item["duration_ms"] = item["source_out_ms"] = 1010
        if index % 2 == 0 and index:
            item["transition"] = {"kind": "cut"}
    document = TimelineDocument.model_validate({
        "primary": items,
        "default_transition": {"kind": "fadeblack", "duration_ms": 300},
    })
    output = settings.media_dir / assemble_timeline(project, document, "landscape", session).filename
    assert abs(_stream_seconds(output, "v") - 8.08) <= 0.04
    assert abs(_stream_seconds(output, "a") - 8.08) <= 0.04
    session.close()
