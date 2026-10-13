import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .final_cut import FinalCutResult, _run, export_encode_args, probe_duration_ms
from .models import Project, Shot, ShotMediaVariant, TimelineAsset
from .schemas import (
    TimelineDocument,
    TimelineImageOverlay,
    TimelinePrimaryItem,
    TimelineTextOverlay,
    TimelineVariantGeometry,
)
from .luts import build_lut_ffmpeg_filter
from .settings import settings


def export_variants() -> dict[str, tuple[str, int, int]]:
    return {
        "landscape": ("16:9", settings.export_width_landscape, settings.export_height_landscape),
        "shorts": ("9:16", settings.export_width_shorts, settings.export_height_shorts),
    }


VARIANTS = export_variants()


@dataclass(frozen=True)
class ResolvedMedia:
    path: Path
    is_clip: bool
    aspect_ratio: str
    native: bool
    dialogue_audio: Path | None = None
    color_boost_strength: float = 0.0


def _legacy_media(shot: Shot, kind: str):
    return shot.clip if kind == "shot_clip" else shot.image


def resolve_shot_media(
    shot: Shot,
    kind: str,
    desired_aspect: str,
    preferred_aspect: str,
) -> ResolvedMedia | None:
    media_type = "clip" if kind == "shot_clip" else "image"
    requested = desired_aspect if preferred_aspect == "auto" else preferred_aspect
    variants = {item.aspect_ratio: item for item in shot.media_variants if item.media_type == media_type}
    legacy = _legacy_media(shot, kind)
    candidates: list[tuple[object, str]] = []
    if requested in variants:
        candidates.append((variants[requested], requested))
    project_aspect = shot.scene.script_version.project.aspect_ratio
    if legacy and project_aspect == requested:
        candidates.append((legacy, project_aspect))
    if desired_aspect in variants and desired_aspect != requested:
        candidates.append((variants[desired_aspect], desired_aspect))
    if legacy:
        candidates.append((legacy, project_aspect))
    candidates.extend((item, aspect) for aspect, item in variants.items())
    if not candidates:
        fallback_type = "image" if media_type == "clip" else "clip"
        fallback_legacy = shot.image if fallback_type == "image" else shot.clip
        fallback_variants = [item for item in shot.media_variants if item.media_type == fallback_type]
        matching = next((item for item in fallback_variants if item.aspect_ratio == desired_aspect), None)
        if matching:
            return ResolvedMedia(
                settings.media_dir / matching.filename,
                fallback_type == "clip",
                desired_aspect,
                True,
                color_boost_strength=float(getattr(matching, "color_boost_strength", 0.0) or 0.0),
            )
        if fallback_legacy:
            return ResolvedMedia(
                settings.media_dir / fallback_legacy.filename,
                fallback_type == "clip",
                project_aspect,
                project_aspect == desired_aspect,
                color_boost_strength=float(getattr(fallback_legacy, "color_boost_strength", 0.0) or 0.0),
            )
        if fallback_variants:
            item = fallback_variants[0]
            return ResolvedMedia(
                settings.media_dir / item.filename,
                fallback_type == "clip",
                item.aspect_ratio,
                item.aspect_ratio == desired_aspect,
                color_boost_strength=float(getattr(item, "color_boost_strength", 0.0) or 0.0),
            )
        return None
    media, aspect = candidates[0]
    return ResolvedMedia(
        path=settings.media_dir / media.filename,
        is_clip=media_type == "clip",
        aspect_ratio=aspect,
        native=aspect == desired_aspect,
        color_boost_strength=float(getattr(media, "color_boost_strength", 0.0) or 0.0),
    )


def resolve_source(
    source,
    project: Project,
    db: Session,
    desired_aspect: str,
    geometry: TimelineVariantGeometry,
) -> ResolvedMedia:
    if source.kind == "asset":
        asset = db.get(TimelineAsset, source.ref_id)
        if not asset or asset.project_id != project.id:
            raise RuntimeError("A timeline media asset is missing")
        is_clip = (asset.mime_type or "").lower().startswith("video/")
        return ResolvedMedia(settings.media_dir / asset.filename, is_clip, desired_aspect, True)
    shot = db.get(Shot, source.ref_id)
    if not shot:
        raise RuntimeError("A timeline shot is missing")
    media = resolve_shot_media(shot, source.kind, desired_aspect, geometry.source_aspect)
    if not media or not media.path.is_file():
        raise RuntimeError(f"{shot.title or 'A shot'} has no usable media")
    dialogue = None
    if shot.dialogue_audio_filename:
        candidate = settings.media_dir / shot.dialogue_audio_filename
        if candidate.is_file():
            dialogue = candidate
    return ResolvedMedia(
        path=media.path,
        is_clip=media.is_clip,
        aspect_ratio=media.aspect_ratio,
        native=media.native,
        dialogue_audio=dialogue,
        color_boost_strength=media.color_boost_strength,
    )


def _geometry(item, variant: str) -> TimelineVariantGeometry:
    return item.variant.get(variant, TimelineVariantGeometry())


def _video_overlay_geometry(item, variant: str) -> TimelineVariantGeometry:
    if variant in item.variant:
        return item.variant[variant]
    return TimelineVariantGeometry(position="full-frame", fit="cover")


def _color_boost_ffmpeg_filter(strength: float) -> str:
    if abs(float(strength or 0.0)) < 1e-4:
        return ""
    s = max(-1.0, min(3.0, float(strength)))
    contrast = max(0.3, 1.0 + 0.14 * s)
    saturation = max(0.1, 1.0 + 0.32 * s)
    brightness = max(-0.15, min(0.15, 0.015 * s))
    flt = f",eq=contrast={contrast:.4f}:saturation={saturation:.4f}:brightness={brightness:.4f}"
    if s > 0:
        flt += f",unsharp=5:5:{min(1.5, 0.35 * s):.3f}:5:5:0.0"
    return flt


def _edge_crop_fractions(geometry: TimelineVariantGeometry) -> tuple[float, float, float, float]:
    c_top = max(0.0, min(0.85, float(getattr(geometry, "crop_top", 0.0) or 0.0)))
    c_bottom = max(0.0, min(0.85, float(getattr(geometry, "crop_bottom", 0.0) or 0.0)))
    c_left = max(0.0, min(0.85, float(getattr(geometry, "crop_left", 0.0) or 0.0)))
    c_right = max(0.0, min(0.85, float(getattr(geometry, "crop_right", 0.0) or 0.0)))
    if c_top + c_bottom > 0.9:
        scale_v = 0.9 / (c_top + c_bottom)
        c_top *= scale_v
        c_bottom *= scale_v
    if c_left + c_right > 0.9:
        scale_h = 0.9 / (c_left + c_right)
        c_left *= scale_h
        c_right *= scale_h
    return c_top, c_bottom, c_left, c_right


def _video_filter(width: int, height: int, geometry: TimelineVariantGeometry) -> str:
    if geometry.fit == "contain":
        base = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        if geometry.zoom > 1.001:
            scaled_w = max(width, round(width * geometry.zoom))
            scaled_h = max(height, round(height * geometry.zoom))
            return (
                f"{base},scale={scaled_w}:{scaled_h},"
                f"crop={width}:{height}:(iw-ow)*{geometry.focal_x}:(ih-oh)*{geometry.focal_y}"
            )
        return base
    scaled_w = max(width, round(width * geometry.zoom))
    scaled_h = max(height, round(height * geometry.zoom))
    return (
        f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)*{geometry.focal_x}:(ih-oh)*{geometry.focal_y}"
    )


def _normalize_primary(
    item: TimelinePrimaryItem,
    media: ResolvedMedia | None,
    destination: Path,
    width: int,
    height: int,
    variant: str,
) -> None:
    duration = item.duration_ms / 1000
    if item.kind == "title":
        card = destination.with_suffix(".png")
        _render_text(item.text, card, width, height, "large", "white", "solid", "center", True)
        media = ResolvedMedia(card, False, VARIANTS[variant][0], True)
    if not media:
        raise RuntimeError(f"{item.label} has no media")

    command = ["ffmpeg", "-y"]
    if media.is_clip:
        command.extend(["-i", str(media.path)])
    else:
        command.extend(["-loop", "1", "-i", str(media.path)])
    command.extend(["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])

    geometry = _geometry(item, variant)
    if media and not media.native and variant not in item.variant:
        geometry = geometry.model_copy(update={"fit": "cover", "zoom": 1})
    trim_start = item.source_in_ms / 1000 if media.is_clip else 0
    trim = f"trim=start={trim_start}:duration={duration}," if media.is_clip else f"trim=duration={duration},"
    boost_chain = _color_boost_ffmpeg_filter(media.color_boost_strength) if media.is_clip else ""
    lut_chain = build_lut_ffmpeg_filter(
        getattr(geometry, "lut", "none"),
        float(getattr(geometry, "lut_strength", 1.0) or 1.0),
    )
    c_top, c_bottom, c_left, c_right = _edge_crop_fractions(geometry)
    dx = round(width * max(-0.9, min(0.9, float(getattr(geometry, "offset_x", 0.0) or 0.0))))
    dy = round(height * max(-0.9, min(0.9, float(getattr(geometry, "offset_y", 0.0) or 0.0))))
    edge_crop_chain = ""
    if c_top > 1e-4 or c_bottom > 1e-4 or c_left > 1e-4 or c_right > 1e-4 or dx != 0 or dy != 0:
        src_x0 = round(width * c_left)
        src_y0 = round(height * c_top)
        src_x1 = max(src_x0 + 16, round(width * (1.0 - c_right)))
        src_y1 = max(src_y0 + 16, round(height * (1.0 - c_bottom)))
        dst_x0 = max(0, min(width - 16, src_x0 + dx))
        dst_y0 = max(0, min(height - 16, src_y0 + dy))
        dst_x1 = max(dst_x0 + 16, min(width, src_x1 + dx))
        dst_y1 = max(dst_y0 + 16, min(height, src_y1 + dy))
        vis_w = dst_x1 - dst_x0
        vis_h = dst_y1 - dst_y0
        vis_src_x = max(0, min(width - vis_w, dst_x0 - dx))
        vis_src_y = max(0, min(height - vis_h, dst_y0 - dy))
        edge_crop_chain = f",crop={vis_w}:{vis_h}:{vis_src_x}:{vis_src_y},pad={width}:{height}:{dst_x0}:{dst_y0}:color=black"
    video = (
        f"[0:v]{trim}setpts=PTS-STARTPTS,{_video_filter(width, height, geometry)}{boost_chain}{lut_chain}{edge_crop_chain},"
        f"setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={duration},"
        f"trim=duration={duration}[v]"
    )
    mute_audio = getattr(item, "mute_audio", False)
    has_audio = (not mute_audio) and media.is_clip and _has_audio(media.path)
    dialogue = media.dialogue_audio if (not mute_audio) and media.dialogue_audio and media.dialogue_audio.is_file() else None
    if has_audio:
        audio = (
            f";[0:a]atrim=start={trim_start}:duration={duration},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={duration}[a]"
        )
        audio_map = ["-map", "[a]"]
    elif dialogue:
        command.extend(["-i", str(dialogue)])
        audio = (
            f";[2:a]atrim=duration={duration},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={duration}[a]"
        )
        audio_map = ["-map", "[a]"]
    else:
        audio = ""
        audio_map = ["-map", "1:a:0"]
    command.extend(
        [
            "-filter_complex", video + audio,
            "-map", "[v]", *audio_map,
            "-t", str(duration),
            *export_encode_args(),
            str(destination),
        ]
    )
    _run(command)


def _has_audio(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_text(
    text: str,
    path: Path,
    width: int,
    height: int,
    size: str,
    color: str,
    background: str,
    position: str,
    full_frame: bool = False,
) -> None:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255 if full_frame or background == "solid" else 0))
    draw = ImageDraw.Draw(canvas)
    font_size = {"small": 30, "medium": 46, "large": 68}[size] * width // 1280
    font = _font(max(20, font_size))
    max_width = int(width * 0.82)
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    spacing = max(8, font_size // 4)
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines or [""]]
    text_width = max((box[2] for box in boxes), default=0)
    text_height = sum(box[3] - box[1] for box in boxes) + spacing * max(0, len(boxes) - 1)
    x = (width - text_width) // 2
    margin = int(height * 0.08)
    y = margin if position.startswith("top") else height - text_height - margin if position.startswith("bottom") else (height - text_height) // 2
    if position.endswith("left"):
        x = margin
    elif position.endswith("right"):
        x = width - text_width - margin
    padding = max(12, font_size // 3)
    if background == "translucent":
        draw.rounded_rectangle((x - padding, y - padding, x + text_width + padding, y + text_height + padding), radius=8, fill=(0, 0, 0, 170))
    fill = (255, 255, 255, 255) if color == "white" else (15, 15, 15, 255)
    cursor = y
    for line, box in zip(lines, boxes):
        line_width = box[2]
        line_x = x + (text_width - line_width) // 2
        if background == "shadow":
            draw.text((line_x + 3, cursor + 3), line, font=font, fill=(0, 0, 0, 210))
        draw.text((line_x, cursor), line, font=font, fill=fill)
        cursor += box[3] - box[1] + spacing
    canvas.save(path)


def _probe_dimensions(path: Path) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                w = int(streams[0].get("width") or 0)
                h = int(streams[0].get("height") or 0)
                if w > 0 and h > 0:
                    return w, h
    except Exception:
        pass
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        pass
    return 1920, 1080


def _overlay_position(position: str, width: int, height: int, overlay_width: int) -> tuple[str, str]:
    margin_x = round(width * 0.08)
    margin_y = round(height * 0.08)
    x = str(margin_x) if position.endswith("left") else f"W-w-{margin_x}" if position.endswith("right") else "(W-w)/2"
    y = str(margin_y) if position.startswith("top") else f"H-h-{margin_y}" if position.startswith("bottom") else "(H-h)/2"
    return x, y


def _compute_overlay_layout(
    geometry: TimelineVariantGeometry,
    media_path: Path,
    width: int,
    height: int,
    is_video_layer: bool = True,
) -> tuple[str, str, str]:
    c_top, c_bottom, c_left, c_right = _edge_crop_fractions(geometry)
    has_edge_crop = c_top > 1e-4 or c_bottom > 1e-4 or c_left > 1e-4 or c_right > 1e-4
    position = getattr(geometry, "position", "full-frame" if is_video_layer else "center")

    off_x = round(width * max(-1.0, min(1.0, float(getattr(geometry, "offset_x", 0.0) or 0.0))))
    off_y = round(height * max(-1.0, min(1.0, float(getattr(geometry, "offset_y", 0.0) or 0.0))))

    if position == "full-frame":
        scale_chain = _video_filter(width, height, geometry)
        if has_edge_crop:
            crop_x = round(width * c_left)
            crop_y = round(height * c_top)
            crop_w = max(16, (round(width * (1.0 - c_left - c_right)) // 2) * 2)
            crop_h = max(16, (round(height * (1.0 - c_top - c_bottom)) // 2) * 2)
            scale_chain = f"{scale_chain},crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
            final_x = crop_x + off_x
            final_y = crop_y + off_y
        else:
            final_x = off_x
            final_y = off_y
        return scale_chain, str(final_x), str(final_y)

    src_w, src_h = _probe_dimensions(media_path)
    aspect = max(0.1, src_w / max(1, src_h))

    scale_factor = max(0.2, min(5.0, float(getattr(geometry, "scale", 1.0) or 1.0)))
    base_width_ratio = 0.45 if is_video_layer else 0.30
    max_w = max(32, round(width * base_width_ratio * scale_factor))
    max_h = max(32, round(height * 0.60 * scale_factor))

    if max_w / aspect <= max_h:
        el_w = max_w
        el_h = max(16, round(el_w / aspect))
    else:
        el_h = max_h
        el_w = max(16, round(el_h * aspect))
    el_w = max(16, (el_w // 2) * 2)
    el_h = max(16, (el_h // 2) * 2)

    margin_x = round(width * 0.08)
    margin_y = round(height * 0.08)

    if position == "top":
        el_x = (width - el_w) // 2
        el_y = margin_y
    elif position == "bottom":
        el_x = (width - el_w) // 2
        el_y = height - margin_y - el_h
    elif position == "top-left":
        el_x = margin_x
        el_y = margin_y
    elif position == "top-right":
        el_x = width - margin_x - el_w
        el_y = margin_y
    elif position == "bottom-left":
        el_x = margin_x
        el_y = height - margin_y - el_h
    elif position == "bottom-right":
        el_x = width - margin_x - el_w
        el_y = height - margin_y - el_h
    else:
        el_x = (width - el_w) // 2
        el_y = (height - el_h) // 2

    scale_chain = f"scale={el_w}:{el_h}"
    if geometry.zoom > 1.001:
        scale_chain = (
            f"crop=iw/{geometry.zoom:.4f}:ih/{geometry.zoom:.4f}:"
            f"(iw-ow)*{geometry.focal_x:.4f}:(ih-oh)*{geometry.focal_y:.4f},"
            f"scale={el_w}:{el_h}"
        )

    delta_x = 0
    delta_y = 0
    if has_edge_crop:
        crop_x = round(el_w * c_left)
        crop_y = round(el_h * c_top)
        crop_w = max(16, (round(el_w * (1.0 - c_left - c_right)) // 2) * 2)
        crop_h = max(16, (round(el_h * (1.0 - c_top - c_bottom)) // 2) * 2)
        scale_chain = f"{scale_chain},crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
        delta_x = crop_x
        delta_y = crop_y

    final_x = el_x + delta_x + off_x
    final_y = el_y + delta_y + off_y
    return scale_chain, str(final_x), str(final_y)


def resolve_audio_source(
    source,
    project: Project,
    db: Session,
    desired_aspect: str,
) -> Path:
    if source.kind == "asset":
        asset = db.get(TimelineAsset, source.ref_id)
        if not asset or asset.project_id != project.id:
            raise RuntimeError("A timeline audio asset is missing")
        path = settings.media_dir / asset.filename
        if not path.is_file():
            raise RuntimeError("A timeline audio asset file is missing")
        return path
    shot = db.get(Shot, source.ref_id)
    if not shot:
        raise RuntimeError("A timeline shot is missing")
    if source.kind == "shot_audio" and shot.dialogue_audio_filename:
        candidate = settings.media_dir / shot.dialogue_audio_filename
        if candidate.is_file():
            return candidate
    media = resolve_shot_media(shot, "shot_clip", desired_aspect, "auto")
    if media and media.path.is_file():
        return media.path
    raise RuntimeError(f"{shot.title or 'A shot'} has no usable audio")


def _composite_overlays(
    base: Path,
    destination: Path,
    document: TimelineDocument,
    project: Project,
    db: Session,
    variant: str,
    width: int,
    height: int,
    workspace: Path,
) -> None:
    desired_aspect = VARIANTS[variant][0]
    total_seconds = probe_duration_ms(base) / 1000
    command = ["ffmpeg", "-y", "-i", str(base)]
    specs: list[tuple[str, object, Path, TimelineVariantGeometry, float]] = []
    audio_specs: list[tuple[int, object, Path]] = []

    for overlay in getattr(document, "video_overlays", []) or []:
        geometry = _video_overlay_geometry(overlay, variant)
        media = resolve_source(overlay.source, project, db, desired_aspect, geometry)
        if not media.native and variant not in overlay.variant:
            geometry = geometry.model_copy(update={"fit": "cover", "zoom": 1})
        if media.is_clip:
            command.extend(["-i", str(media.path)])
            input_idx = 1 + len(specs)
            specs.append(("video", overlay, media.path, geometry, media.color_boost_strength))
            if (not getattr(overlay, "mute_audio", False)) and getattr(overlay, "volume", 1.0) > 0 and _has_audio(media.path):
                audio_specs.append((input_idx, overlay, media.path))
        else:
            command.extend(["-loop", "1", "-t", str(total_seconds), "-i", str(media.path)])
            specs.append(("image", overlay, media.path, geometry, 0.0))

    for overlay in document.image_overlays:
        geometry = _geometry(overlay, variant)
        media = resolve_source(overlay.source, project, db, desired_aspect, geometry)
        command.extend(["-loop", "1", "-t", str(total_seconds), "-i", str(media.path)])
        specs.append(("image", overlay, media.path, geometry, 0.0))
    for index, overlay in enumerate(document.text_overlays):
        geometry = _geometry(overlay, variant)
        rendered = workspace / f"text-{index:04d}.png"
        _render_text(
            overlay.text, rendered, width, height, overlay.size, overlay.color,
            overlay.background, geometry.position, overlay.presentation == "full-frame",
        )
        command.extend(["-loop", "1", "-t", str(total_seconds), "-i", str(rendered)])
        specs.append(("text", overlay, rendered, geometry, 0.0))

    next_input_index = 1 + len(specs)
    for overlay in getattr(document, "audio_overlays", []) or []:
        if getattr(overlay, "mute_audio", False) or getattr(overlay, "volume", 1.0) <= 0:
            continue
        audio_path = resolve_audio_source(overlay.source, project, db, desired_aspect)
        if not _has_audio(audio_path):
            continue
        command.extend(["-i", str(audio_path)])
        audio_specs.append((next_input_index, overlay, audio_path))
        next_input_index += 1

    if not specs and not audio_specs:
        _run(["ffmpeg", "-y", "-i", str(base), "-c", "copy", str(destination)])
        return

    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    for index, (kind, overlay, _path, geometry, boost_strength) in enumerate(specs, start=1):
        prepared = f"o{index}"
        start = overlay.start_ms / 1000
        end = (overlay.start_ms + overlay.duration_ms) / 1000
        dur = max(0.1, overlay.duration_ms / 1000)
        if kind == "video":
            trim_start = max(0.0, getattr(overlay, "source_in_ms", 0) / 1000)
            opacity = getattr(overlay, "opacity", 1.0)
            scale_chain, x, y = _compute_overlay_layout(geometry, _path, width, height, is_video_layer=True)
            boost_chain = _color_boost_ffmpeg_filter(boost_strength)
            lut_chain = build_lut_ffmpeg_filter(
                getattr(geometry, "lut", "none"),
                float(getattr(geometry, "lut_strength", 1.0) or 1.0),
            )
            alpha_chain = f",format=rgba,colorchannelmixer=aa={opacity}" if opacity < 0.999 else ",format=rgba"
            filters.append(
                f"[{index}:v]trim=start={trim_start}:duration={dur},setpts=PTS-STARTPTS,"
                f"{scale_chain}{boost_chain}{lut_chain},setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={dur},"
                f"trim=duration={dur},setpts=PTS-STARTPTS+{start}/TB{alpha_chain}[{prepared}]"
            )
            next_label = f"v{index}"
            filters.append(
                f"[{current}][{prepared}]overlay={x}:{y}:eof_action=pass:enable='between(t,{start},{end})'[{next_label}]"
            )
            current = next_label
            continue
        if kind == "image":
            opacity = getattr(overlay, "opacity", 1.0)
            scale_chain, x, y = _compute_overlay_layout(geometry, _path, width, height, is_video_layer=False)
            lut_chain = build_lut_ffmpeg_filter(
                getattr(geometry, "lut", "none"),
                float(getattr(geometry, "lut_strength", 1.0) or 1.0),
            )
            alpha_chain = f",format=rgba,colorchannelmixer=aa={opacity}" if opacity < 0.999 else ",format=rgba"
            filters.append(
                f"[{index}:v]{scale_chain}{lut_chain},setsar=1,fps=30{alpha_chain}[{prepared}]"
            )
            next_label = f"v{index}"
            filters.append(
                f"[{current}][{prepared}]overlay={x}:{y}:enable='between(t,{start},{end})'[{next_label}]"
            )
            current = next_label
            continue
        else:
            filters.append(f"[{index}:v]scale={width}:{height},format=rgba[{prepared}]")
            x, y = "0", "0"
            next_label = f"v{index}"
            filters.append(f"[{current}][{prepared}]overlay={x}:{y}:enable='between(t,{start},{end})'[{next_label}]")
            current = next_label

    if audio_specs:
        for aud_idx, (input_index, overlay, _audio_path) in enumerate(audio_specs, start=1):
            trim_start = max(0.0, getattr(overlay, "source_in_ms", 0) / 1000)
            dur = max(0.1, overlay.duration_ms / 1000)
            vol = max(0.0, min(2.0, float(getattr(overlay, "volume", 1.0))))
            delay_ms = max(0, int(overlay.start_ms))
            filters.append(
                f"[{input_index}:a]atrim=start={trim_start}:duration={dur},asetpts=PTS-STARTPTS,"
                f"volume={vol},aformat=sample_rates=48000:channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}[aud{aud_idx}]"
            )
        mix_inputs = "[0:a]" + "".join(f"[aud{i}]" for i in range(1, len(audio_specs) + 1))
        filters.append(
            f"{mix_inputs}amix=inputs={1 + len(audio_specs)}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        audio_map = ["-map", "[aout]"]
    else:
        audio_map = ["-map", "0:a?"]

    command.extend(
        [
            "-filter_complex", ";".join(filters), "-map", f"[{current}]", *audio_map,
            "-t", str(total_seconds),
            *export_encode_args(include_audio_rate=False),
            str(destination),
        ]
    )
    _run(command)


def assemble_timeline(
    project: Project,
    document: TimelineDocument,
    variant: str,
    db: Session,
    progress: Callable[[int, int], None] | None = None,
) -> FinalCutResult:
    desired_aspect, width, height = export_variants()[variant]
    filename = f"final-cut-{variant}-{uuid.uuid4()}.mp4"
    output = settings.media_dir / filename
    clip_count = 0
    fallback_count = 0
    with tempfile.TemporaryDirectory(prefix="cutline-timeline-") as directory:
        workspace = Path(directory)
        segments: list[Path] = []
        for index, item in enumerate(document.primary):
            media = None
            if item.source:
                media = resolve_source(item.source, project, db, desired_aspect, _geometry(item, variant))
                clip_count += int(media.is_clip)
                fallback_count += int(not media.is_clip or not media.native)
            segment = workspace / f"segment-{index:04d}.mp4"
            _normalize_primary(item, media, segment, width, height, variant)
            segments.append(segment)
            if progress:
                progress(index + 1, len(document.primary) + 1)
        concat_file = workspace / "concat.txt"
        concat_file.write_text("".join(f"file '{item.as_posix()}'\n" for item in segments))
        base = workspace / "base.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(base)])
        composed = workspace / "composed.mp4"
        _composite_overlays(base, composed, document, project, db, variant, width, height, workspace)
        composed.replace(output)
        if progress:
            progress(len(document.primary) + 1, len(document.primary) + 1)
    return FinalCutResult(
        filename=filename,
        duration_ms=probe_duration_ms(output),
        width=width,
        height=height,
        clip_count=clip_count,
        fallback_count=fallback_count,
    )
