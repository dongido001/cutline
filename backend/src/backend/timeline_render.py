import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .final_cut import FinalCutResult, _run, probe_duration_ms
from .models import Project, Shot, ShotMediaVariant, TimelineAsset
from .schemas import (
    TimelineDocument,
    TimelineImageOverlay,
    TimelinePrimaryItem,
    TimelineTextOverlay,
    TimelineVariantGeometry,
)
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
            return ResolvedMedia(settings.media_dir / matching.filename, fallback_type == "clip", desired_aspect, True)
        if fallback_legacy:
            return ResolvedMedia(settings.media_dir / fallback_legacy.filename, fallback_type == "clip", project_aspect, project_aspect == desired_aspect)
        if fallback_variants:
            item = fallback_variants[0]
            return ResolvedMedia(settings.media_dir / item.filename, fallback_type == "clip", item.aspect_ratio, item.aspect_ratio == desired_aspect)
        return None
    media, aspect = candidates[0]
    return ResolvedMedia(
        path=settings.media_dir / media.filename,
        is_clip=media_type == "clip",
        aspect_ratio=aspect,
        native=aspect == desired_aspect,
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
            raise RuntimeError("A timeline image asset is missing")
        return ResolvedMedia(settings.media_dir / asset.filename, False, desired_aspect, True)
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
    )


def _geometry(item, variant: str) -> TimelineVariantGeometry:
    return item.variant.get(variant, TimelineVariantGeometry())


def _video_filter(width: int, height: int, geometry: TimelineVariantGeometry) -> str:
    if geometry.fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
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
        geometry = geometry.model_copy(update={"fit": "contain", "zoom": 1})
    trim_start = item.source_in_ms / 1000 if media.is_clip else 0
    trim = f"trim=start={trim_start}:duration={duration}," if media.is_clip else f"trim=duration={duration},"
    video = (
        f"[0:v]{trim}setpts=PTS-STARTPTS,{_video_filter(width, height, geometry)},"
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
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
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


def _overlay_position(position: str, width: int, height: int, overlay_width: int) -> tuple[str, str]:
    margin = max(20, width // 32)
    x = str(margin) if position.endswith("left") else f"W-w-{margin}" if position.endswith("right") else "(W-w)/2"
    y = str(margin) if position.startswith("top") else f"H-h-{margin}" if position.startswith("bottom") else "(H-h)/2"
    return x, y


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
    specs: list[tuple[str, object, Path, TimelineVariantGeometry]] = []
    for overlay in document.image_overlays:
        geometry = _geometry(overlay, variant)
        media = resolve_source(overlay.source, project, db, desired_aspect, geometry)
        command.extend(["-loop", "1", "-t", str(total_seconds), "-i", str(media.path)])
        specs.append(("image", overlay, media.path, geometry))
    for index, overlay in enumerate(document.text_overlays):
        geometry = _geometry(overlay, variant)
        rendered = workspace / f"text-{index:04d}.png"
        _render_text(
            overlay.text, rendered, width, height, overlay.size, overlay.color,
            overlay.background, geometry.position, overlay.presentation == "full-frame",
        )
        command.extend(["-loop", "1", "-t", str(total_seconds), "-i", str(rendered)])
        specs.append(("text", overlay, rendered, geometry))
    if not specs:
        _run(["ffmpeg", "-y", "-i", str(base), "-c", "copy", str(destination)])
        return

    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    for index, (kind, overlay, _path, geometry) in enumerate(specs, start=1):
        prepared = f"o{index}"
        if kind == "image":
            target_width = max(32, round(width * 0.3 * geometry.scale))
            filters.append(f"[{index}:v]scale={target_width}:-2,format=rgba,colorchannelmixer=aa={overlay.opacity}[{prepared}]")
            x, y = _overlay_position(geometry.position, width, height, target_width)
        else:
            filters.append(f"[{index}:v]scale={width}:{height},format=rgba[{prepared}]")
            x, y = "0", "0"
        next_label = f"v{index}"
        start = overlay.start_ms / 1000
        end = (overlay.start_ms + overlay.duration_ms) / 1000
        filters.append(f"[{current}][{prepared}]overlay={x}:{y}:enable='between(t,{start},{end})'[{next_label}]")
        current = next_label
    command.extend(
        [
            "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?",
            "-t", str(total_seconds),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
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
