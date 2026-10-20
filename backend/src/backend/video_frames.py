"""Border / rounded-corner decoration for timeline video windows.

Rendered as two stills that FFmpeg composites per clip:
- an alpha mask (rounded corners) merged into the video before overlaying it;
- an RGBA frame image (border ring, optional glow) overlaid on top, padded so glows can spill outside.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .schemas import TimelineVariantGeometry

_SUPERSAMPLE = 4


@dataclass(frozen=True)
class FrameAssets:
    mask: Path | None
    frame: Path | None
    pad: int


def frame_metrics(geometry: TimelineVariantGeometry, el_w: int, el_h: int, canvas_w: int) -> tuple[int, int]:
    """Corner radius and border width in output pixels for an element of size el_w x el_h."""
    radius = round(max(0.0, float(geometry.frame_radius or 0.0)) * canvas_w)
    radius = max(0, min(radius, min(el_w, el_h) // 2))
    if geometry.frame_style == "none":
        return radius, 0
    width = max(1, round(max(0.0, float(geometry.frame_width or 0.0)) * canvas_w))
    width = min(width, max(1, min(el_w, el_h) // 4))
    return radius, width


def has_frame(geometry: TimelineVariantGeometry | None) -> bool:
    if geometry is None:
        return False
    return geometry.frame_style != "none" or float(geometry.frame_radius or 0.0) > 1e-4


def _hex_rgb(value: str) -> tuple[int, int, int]:
    raw = (value or "#FFFFFF").lstrip("#")
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _rounded_rect_path(x0: float, y0: float, x1: float, y1: float, r: float) -> list[tuple[float, float]]:
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    steps = max(2, int(r / 2)) if r > 0 else 1
    corners = [
        (x1 - r, y0 + r, -90.0, 0.0),
        (x1 - r, y1 - r, 0.0, 90.0),
        (x0 + r, y1 - r, 90.0, 180.0),
        (x0 + r, y0 + r, 180.0, 270.0),
    ]
    points: list[tuple[float, float]] = []
    for cx, cy, a0, a1 in corners:
        for k in range(steps + 1):
            angle = math.radians(a0 + (a1 - a0) * k / steps)
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    points.append(points[0])
    return points


def _dash_polylines(points: list[tuple[float, float]], dash: float, gap: float) -> list[list[tuple[float, float]]]:
    period = dash + gap
    lines: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    travelled = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 1e-9:
            continue
        t = 0.0
        while t < seg - 1e-9:
            phase = travelled % period
            on = phase < dash
            remaining = (dash - phase) if on else (period - phase)
            step = max(1e-6, min(remaining, seg - t))
            if on:
                p0 = (ax + (bx - ax) * t / seg, ay + (by - ay) * t / seg)
                p1 = (ax + (bx - ax) * (t + step) / seg, ay + (by - ay) * (t + step) / seg)
                if not current:
                    current = [p0]
                current.append(p1)
            elif current:
                lines.append(current)
                current = []
            t += step
            travelled += step
    if current:
        lines.append(current)
    return lines


def _ring(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    radius: float,
    inset: float,
    width: float,
    color: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(
        (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
        radius=max(0.0, radius - inset),
        outline=color,
        width=max(1, round(width)),
    )


def _render_mask(el_w: int, el_h: int, radius: int, path: Path) -> None:
    s = _SUPERSAMPLE
    big = Image.new("L", (el_w * s, el_h * s), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, el_w * s - 1, el_h * s - 1), radius=radius * s, fill=255)
    big.resize((el_w, el_h), Image.Resampling.LANCZOS).save(path)


def _render_frame(
    geometry: TimelineVariantGeometry,
    el_w: int,
    el_h: int,
    radius: int,
    width: int,
    canvas_w: int,
    path: Path,
) -> int:
    style = geometry.frame_style
    glowing = style in {"glow", "neon"}
    pad = round(width * 3 + canvas_w * 0.012) if glowing else 0
    s = _SUPERSAMPLE
    canvas = Image.new("RGBA", ((el_w + 2 * pad) * s, (el_h + 2 * pad) * s), (0, 0, 0, 0))
    box = (pad * s, pad * s, (pad + el_w) * s - 1, (pad + el_h) * s - 1)
    rgb = _hex_rgb(geometry.frame_color)
    solid = (*rgb, 255)
    w = width * s
    r = radius * s

    if glowing:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        _ring(ImageDraw.Draw(glow), box, r, 0, w * 2, solid)
        blur_radius = (width * 2 + canvas_w * 0.006) * s
        glow = glow.filter(ImageFilter.GaussianBlur(blur_radius))
        for _ in range(3 if style == "neon" else 2):
            canvas = Image.alpha_composite(canvas, glow)

    draw = ImageDraw.Draw(canvas)
    if style == "solid":
        _ring(draw, box, r, 0, w, solid)
    elif style == "double":
        if width < 3:
            _ring(draw, box, r, 0, w, solid)
        else:
            t = max(s, round(w / 3))
            _ring(draw, box, r, 0, t, solid)
            _ring(draw, box, r, w - t, t, solid)
    elif style == "dashed":
        half = w / 2
        x0, y0, x1, y1 = box
        centerline = _rounded_rect_path(x0 + half, y0 + half, x1 - half, y1 - half, max(0.0, r - half))
        dash = max(w * 3.0, canvas_w * 0.02 * s)
        for line in _dash_polylines(centerline, dash, dash * 0.6):
            draw.line(line, fill=solid, width=max(1, round(w)), joint="curve")
    elif style == "glow":
        _ring(draw, box, r, 0, max(s, w // 2), solid)
    elif style == "neon":
        _ring(draw, box, r, 0, w, solid)
        core = tuple(round(c + (255 - c) * 0.65) for c in rgb)
        _ring(draw, box, r, w / 3, max(s, w / 3), (*core, 255))

    canvas.resize((el_w + 2 * pad, el_h + 2 * pad), Image.Resampling.LANCZOS).save(path)
    return pad


def render_frame_assets(
    geometry: TimelineVariantGeometry | None,
    el_w: int,
    el_h: int,
    canvas_w: int,
    out_dir: Path,
    stem: str,
) -> FrameAssets | None:
    """Render mask/frame stills for an element, or None when the geometry has no frame."""
    if geometry is None or not has_frame(geometry) or el_w < 4 or el_h < 4:
        return None
    radius, width = frame_metrics(geometry, el_w, el_h, canvas_w)
    mask_path: Path | None = None
    frame_path: Path | None = None
    pad = 0
    if radius >= 1:
        mask_path = out_dir / f"{stem}-mask.png"
        _render_mask(el_w, el_h, radius, mask_path)
    if width >= 1:
        frame_path = out_dir / f"{stem}-frame.png"
        pad = _render_frame(geometry, el_w, el_h, radius, width, canvas_w, frame_path)
    if mask_path is None and frame_path is None:
        return None
    return FrameAssets(mask=mask_path, frame=frame_path, pad=pad)
