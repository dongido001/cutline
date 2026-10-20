import shutil
import subprocess

import pytest
from PIL import Image

from backend.schemas import TimelineDocument, TimelinePrimaryItem, TimelineVariantGeometry
from backend.video_frames import frame_metrics, has_frame, render_frame_assets


def _geometry(**kwargs) -> TimelineVariantGeometry:
    return TimelineVariantGeometry(**kwargs)


def test_no_frame_renders_nothing(tmp_path):
    assert not has_frame(_geometry())
    assert render_frame_assets(_geometry(), 200, 100, 1080, tmp_path, "x") is None


def test_rounded_corners_only_produce_mask(tmp_path):
    assets = render_frame_assets(_geometry(frame_radius=0.05), 200, 100, 1000, tmp_path, "x")
    assert assets is not None and assets.frame is None and assets.pad == 0
    mask = Image.open(assets.mask)
    assert mask.size == (200, 100)
    assert mask.getpixel((0, 0)) < 20
    assert mask.getpixel((100, 50)) == 255


def test_radius_is_clamped_to_half_the_short_side():
    radius, width = frame_metrics(_geometry(frame_radius=0.25, frame_style="solid"), 120, 60, 1080)
    assert radius == 30
    assert width >= 1


@pytest.mark.parametrize("style", ["solid", "double", "dashed", "glow", "neon"])
def test_each_style_renders_a_frame(tmp_path, style):
    geometry = _geometry(frame_style=style, frame_radius=0.03, frame_width=0.01, frame_color="#FF0000")
    assets = render_frame_assets(geometry, 300, 200, 1000, tmp_path, style)
    assert assets is not None and assets.frame is not None
    frame = Image.open(assets.frame).convert("RGBA")
    assert frame.size == (300 + 2 * assets.pad, 200 + 2 * assets.pad)
    if style in {"glow", "neon"}:
        assert assets.pad > 0
    else:
        assert assets.pad == 0
    # The middle of the element stays transparent so the video shows through.
    assert frame.getpixel((frame.width // 2, frame.height // 2))[3] == 0
    # Some opaque red lands on the left edge band (any row, so dash gaps don't matter).
    left_band = [
        frame.getpixel((x, y))
        for x in range(assets.pad, assets.pad + 12)
        for y in range(assets.pad + 40, frame.height - assets.pad - 40, 3)
    ]
    assert any(px[3] > 128 and px[0] > 150 for px in left_band)


def test_invalid_frame_color_is_rejected():
    with pytest.raises(ValueError):
        TimelineVariantGeometry(frame_color="red")


def _make_clip(path, color="blue", size="128x128", seconds="1"):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30",
         "-t", seconds, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _first_frame(video, out_png) -> Image.Image:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(video), "-frames:v", "1", str(out_png)],
        check=True,
    )
    return Image.open(out_png).convert("RGB")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_primary_export_rounds_corners_and_draws_border(tmp_path):
    from backend.timeline_render import ResolvedMedia, _normalize_primary

    source = tmp_path / "blue.mp4"
    _make_clip(source)
    item = TimelinePrimaryItem.model_validate({
        "id": "p", "kind": "video", "label": "Bordered", "duration_ms": 1000,
        "variant": {"landscape": {"frame_style": "solid", "frame_radius": 0.2, "frame_width": 0.04, "frame_color": "#FF0000"}},
    })
    output = tmp_path / "out.mp4"
    _normalize_primary(item, ResolvedMedia(source, True, "16:9", True), output, 128, 128, "landscape")
    frame = _first_frame(output, tmp_path / "f.png")
    corner = frame.getpixel((1, 1))
    assert max(corner) < 40, corner
    edge = frame.getpixel((2, 64))
    assert edge[0] > 150 and edge[2] < 100, edge
    center = frame.getpixel((64, 64))
    assert center[2] > 150 and center[0] < 80, center


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_primary_export_frames_the_layout_window(tmp_path):
    from backend.layout_templates import resolve_template, video_rect_px
    from backend.schemas import TimelineCanvasLayout
    from backend.timeline_render import ResolvedMedia, _normalize_primary

    source = tmp_path / "blue.mp4"
    _make_clip(source, size="160x90")
    item = TimelinePrimaryItem.model_validate({
        "id": "p", "kind": "video", "label": "Framed window", "duration_ms": 1000,
        "variant": {"shorts": {"frame_style": "dashed", "frame_radius": 0.05, "frame_width": 0.02, "frame_color": "#FFFF00"}},
    })
    output = tmp_path / "shorts.mp4"
    layout = TimelineCanvasLayout(template="center_stage", matte="solid")
    _normalize_primary(item, ResolvedMedia(source, True, "16:9", False), output, 180, 320, "shorts", layout)
    frame = _first_frame(output, tmp_path / "s.png")
    vx, vy, vw, vh = video_rect_px(resolve_template("center_stage", "shorts"), 180, 320)
    assert frame.getpixel((vx + vw // 2, vy + vh // 2))[2] > 150
    top_edge = [frame.getpixel((x, vy + 1)) for x in range(vx + 20, vx + vw - 20)]
    assert any(px[0] > 150 and px[1] > 150 and px[2] < 100 for px in top_edge)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_overlay_export_applies_frame(tmp_path, monkeypatch):
    from backend import timeline_render
    from backend.timeline_render import ResolvedMedia, _composite_overlays

    base = tmp_path / "base.mp4"
    _make_clip(base, color="green", size="320x180")
    layer = tmp_path / "layer.mp4"
    _make_clip(layer, color="blue", size="160x90")
    monkeypatch.setattr(timeline_render, "resolve_source", lambda *a, **k: ResolvedMedia(layer, True, "16:9", True))
    document = TimelineDocument.model_validate({
        "primary": [],
        "video_overlays": [{
            "id": "o", "source": {"kind": "asset", "ref_id": "a"}, "start_ms": 0, "duration_ms": 1000,
            "variant": {"landscape": {
                "position": "center", "scale": 1, "frame_style": "neon",
                "frame_radius": 0.03, "frame_width": 0.01, "frame_color": "#FF00FF",
            }},
        }],
        "image_overlays": [],
        "text_overlays": [],
    })
    output = tmp_path / "composited.mp4"
    _composite_overlays(base, output, document, None, None, "landscape", 320, 180, tmp_path)
    frame = _first_frame(output, tmp_path / "o.png")
    assert frame.getpixel((160, 90))[2] > 150
    # Somewhere along the overlay's left edge the magenta border is visible.
    left_edge = [frame.getpixel((x, 90)) for x in range(80, 100)]
    assert any(px[0] > 150 and px[2] > 150 and px[1] < 120 for px in left_edge), left_edge


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_primary_export_loops_trim_range_when_stretched(tmp_path):
    from backend.timeline_render import ResolvedMedia, _normalize_primary

    source = tmp_path / "red-blue.mp4"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=64x64:d=0.5:r=30",
            "-f", "lavfi", "-i", "color=blue:s=64x64:d=0.5:r=30",
            "-f", "lavfi", "-i", "color=green:s=64x64:d=1:r=30",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
    )
    item = TimelinePrimaryItem.model_validate({
        "id": "p", "kind": "video", "label": "Looped", "duration_ms": 2500,
        "source_in_ms": 0, "source_out_ms": 1000, "loop": True,
    })
    output = tmp_path / "loop.mp4"
    _normalize_primary(item, ResolvedMedia(source, True, "16:9", True), output, 64, 64, "landscape")

    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout)
    assert abs(duration - 2.5) < 0.15

    def color_at(seconds: float) -> tuple[int, int, int]:
        png = tmp_path / f"f{seconds}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(seconds), "-i", str(output), "-frames:v", "1", str(png)], check=True)
        return Image.open(png).convert("RGB").getpixel((32, 32))

    red, blue, red_again = color_at(0.2), color_at(0.7), color_at(1.2)
    assert red[0] > 150 and red[2] < 100, red
    assert blue[2] > 150 and blue[0] < 100, blue
    assert red_again[0] > 150 and red_again[1] < 100, red_again
