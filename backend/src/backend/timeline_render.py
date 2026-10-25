from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .final_cut import FinalCutResult, _run, export_encode_args, probe_duration_ms
from .layout_templates import is_full_bleed, resolve_template, video_rect_px
from .models import Project, Shot, ShotMediaVariant, TimelineAsset
from .schemas import (
    TimelineAudioOverlay,
    TimelineCanvasLayout,
    TimelineDocument,
    TimelineImageOverlay,
    TimelinePrimaryItem,
    TimelineTextOverlay,
    TimelineVariantGeometry,
)
from .luts import build_lut_ffmpeg_filter
from .settings import settings
from .video_frames import render_frame_assets


def _hex_to_ffmpeg_color(value: str, default: str = "0x0B0D12") -> str:
    raw = (value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return default
    return f"0x{raw.upper()}"


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


def _video_filter(width: int, height: int, geometry: TimelineVariantGeometry, pad_color: str = "black") -> str:
    if geometry.fit == "contain":
        base = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
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


def _render_band_text_png(
    text: str,
    path: Path,
    *,
    width: int,
    band_h: int,
    fontsize: int,
) -> None:
    canvas = Image.new("RGBA", (width, max(24, band_h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(max(18, fontsize))
    max_width = int(width * 0.86)
    words = (text or "").strip().split()
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
    if not lines:
        canvas.save(path)
        return
    line_h = fontsize + 6
    total_h = line_h * len(lines)
    y = max(0, (band_h - total_h) // 2)
    for row in lines[:4]:
        bbox = draw.textbbox((0, 0), row, font=font)
        tw = bbox[2] - bbox[0]
        x = max(0, (width - tw) // 2)
        # Soft shadow for readability on any matte.
        draw.text((x + 2, y + 2), row, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), row, font=font, fill=(245, 248, 252, 255))
        y += line_h
    canvas.save(path)


def _normalize_primary(
    item: TimelinePrimaryItem,
    media: ResolvedMedia | None,
    destination: Path,
    width: int,
    height: int,
    variant: str,
    layout: TimelineCanvasLayout | None = None,
    head_s: float = 0.0,
    tail_s: float = 0.0,
) -> None:
    # head_s/tail_s are transition handles: extra source time before the in point and after the
    # out point, so a transition can be centred on the cut without changing the timeline length.
    duration = item.duration_ms / 1000 + head_s + tail_s
    looping = False
    if media and media.is_clip and item.loop and item.source_out_ms is not None:
        span_ms = item.source_out_ms - item.source_in_ms
        if 0 < span_ms < item.duration_ms:
            segment = destination.with_name(f"{destination.stem}-loopseg.mp4")
            _run([
                "ffmpeg", "-y", "-ss", str(item.source_in_ms / 1000), "-i", str(media.path),
                "-t", str(span_ms / 1000), "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "256k", str(segment),
            ])
            media = replace(media, path=segment)
            looping = True
    if item.kind == "title":
        card = destination.with_suffix(".png")
        _render_text(item.text, card, width, height, "large", "white", "solid", "center", True)
        media = ResolvedMedia(card, False, VARIANTS[variant][0], True)
    if not media:
        raise RuntimeError(f"{item.label} has no media")

    family = "shorts" if variant == "shorts" else "landscape"
    canvas = layout or TimelineCanvasLayout()
    template = resolve_template(canvas.template, family)
    vx, vy, vw, vh = video_rect_px(template, width, height)
    use_layout = not is_full_bleed(template)

    command = ["ffmpeg", "-y"]
    decoder_flags = _input_video_decoder_flags(media.path) if media else []
    if looping:
        command.extend(decoder_flags)
        command.extend(["-stream_loop", "-1", "-i", str(media.path)])
    elif media.is_clip:
        command.extend(decoder_flags)
        command.extend(["-i", str(media.path)])
    else:
        command.extend(decoder_flags)
        command.extend(["-loop", "1", "-i", str(media.path)])
    command.extend(["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])

    geometry = _geometry(item, variant)
    if media and not media.native and variant not in item.variant:
        geometry = geometry.model_copy(update={"fit": "cover", "zoom": 1})
    source_start = item.source_in_ms / 1000 if media.is_clip and not looping else 0
    head_missing = max(0.0, head_s - source_start) if media.is_clip else 0.0
    trim_start = max(0.0, source_start - head_s) if media.is_clip else 0
    trim = (
        f"trim=start={trim_start}:duration={duration - head_missing},"
        if media.is_clip else f"trim=duration={duration},"
    )
    head_pad = f"tpad=start_mode=clone:start_duration={head_missing}," if head_missing > 0 else ""
    trim_chain = f"{trim}setpts=PTS-STARTPTS,{head_pad}"
    boost_chain = _color_boost_ffmpeg_filter(media.color_boost_strength) if media.is_clip else ""
    lut_chain = build_lut_ffmpeg_filter(
        getattr(geometry, "lut", "none"),
        float(getattr(geometry, "lut_strength", 1.0) or 1.0),
    )
    # Framing crops apply inside the video window when a template is active.
    frame_w, frame_h = (vw, vh) if use_layout else (width, height)
    c_top, c_bottom, c_left, c_right = _edge_crop_fractions(geometry)
    dx = round(frame_w * max(-0.9, min(0.9, float(getattr(geometry, "offset_x", 0.0) or 0.0))))
    dy = round(frame_h * max(-0.9, min(0.9, float(getattr(geometry, "offset_y", 0.0) or 0.0))))
    edge_crop_chain = ""
    if c_top > 1e-4 or c_bottom > 1e-4 or c_left > 1e-4 or c_right > 1e-4 or dx != 0 or dy != 0:
        src_x0 = round(frame_w * c_left)
        src_y0 = round(frame_h * c_top)
        src_x1 = max(src_x0 + 16, round(frame_w * (1.0 - c_right)))
        src_y1 = max(src_y0 + 16, round(frame_h * (1.0 - c_bottom)))
        dst_x0 = max(0, min(frame_w - 16, src_x0 + dx))
        dst_y0 = max(0, min(frame_h - 16, src_y0 + dy))
        dst_x1 = max(dst_x0 + 16, min(frame_w, src_x1 + dx))
        dst_y1 = max(dst_y0 + 16, min(frame_h, src_y1 + dy))
        vis_w = dst_x1 - dst_x0
        vis_h = dst_y1 - dst_y0
        vis_src_x = max(0, min(frame_w - vis_w, dst_x0 - dx))
        vis_src_y = max(0, min(frame_h - vis_h, dst_y0 - dy))
        edge_crop_chain = (
            f",crop={vis_w}:{vis_h}:{vis_src_x}:{vis_src_y},"
            f"pad={frame_w}:{frame_h}:{dst_x0}:{dst_y0}:color=black"
        )

    pad_color = _hex_to_ffmpeg_color(canvas.matte_color, default="black")
    fg_chain = (
        f"{trim_chain}{_video_filter(frame_w, frame_h, geometry, pad_color=pad_color)}"
        f"{boost_chain}{lut_chain}{edge_crop_chain},setsar=1,fps=30,"
        f"tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}"
    )

    decoration = render_frame_assets(geometry, frame_w, frame_h, width, destination.parent, destination.stem + "-border")
    next_input = 2  # 0=media, 1=anullsrc
    mask_input: int | None = None
    frame_input: int | None = None
    if decoration and decoration.mask:
        command.extend(["-loop", "1", "-framerate", "30", "-t", str(duration), "-i", str(decoration.mask)])
        mask_input = next_input
        next_input += 1
    if decoration and decoration.frame:
        command.extend(["-loop", "1", "-framerate", "30", "-t", str(duration), "-i", str(decoration.frame)])
        frame_input = next_input
        next_input += 1
    if mask_input is not None:
        fg_parts = [
            f"[0:v]{fg_chain},format=rgba[fgraw]",
            f"[{mask_input}:v]format=gray[fgmask]",
            "[fgraw][fgmask]alphamerge[fg]",
        ]
    else:
        fg_parts = [f"[0:v]{fg_chain}[fg]"]

    def framed(current_label: str, x: int, y: int) -> str:
        if frame_input is None or decoration is None:
            return current_label
        parts.append(
            f"[{frame_input}:v]format=rgba[borderpng];"
            f"[{current_label}][borderpng]overlay={x - decoration.pad}:{y - decoration.pad}[bordered]"
        )
        return "bordered"

    if not use_layout and decoration is None:
        video = f"[0:v]{fg_chain}[v]"
    elif not use_layout:
        parts = [
            *fg_parts,
            f"color=c=black:s={width}x{height}:d={duration}:r=30,setsar=1[bg]",
            "[bg][fg]overlay=0:0:shortest=1[base]",
        ]
        current = framed("base", 0, 0)
        parts.append(f"[{current}]format=yuv420p[v]")
        video = ";".join(parts)
    else:
        color_a = _hex_to_ffmpeg_color(canvas.matte_color)
        color_b = _hex_to_ffmpeg_color(canvas.matte_color_b, default="0x1A2233")
        matte = canvas.matte or template.get("default_matte") or "solid"
        parts = [*fg_parts]
        if matte == "blur":
            parts.append(
                f"[0:v]{trim_chain}"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=28,eq=brightness=-0.12:saturation=0.55,"
                f"setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={duration},"
                f"trim=duration={duration}[bg]"
            )
        elif matte == "gradient":
            parts.append(
                f"color=c={color_a}:s={width}x{height}:d={duration}:r=30[bg0];"
                f"color=c={color_b}:s={width}x{height}:d={duration}:r=30[bg1];"
                f"[bg0][bg1]blend=all_expr='A*(1-Y/{max(height - 1, 1)})+B*(Y/{max(height - 1, 1)})',"
                f"setsar=1[bg]"
            )
        elif matte == "duotone":
            parts.append(
                f"[0:v]{trim_chain}"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=22,hue=s=0,"
                f"colorbalance=rs=0.15:gs=-0.05:bs=0.25:rm=0.1:bm=0.2,"
                f"eq=contrast=1.15:brightness=-0.08,setsar=1,fps=30,"
                f"tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}[bg]"
            )
        else:
            parts.append(f"color=c={color_a}:s={width}x{height}:d={duration}:r=30,setsar=1[bg]")

        parts.append(f"[bg][fg]overlay={vx}:{vy}:shortest=1[base]")
        current = "base"
        if canvas.accent_bar or template.get("accent_bar"):
            accent = _hex_to_ffmpeg_color(canvas.accent_color, default="0x39D0FF")
            bar_h = max(2, round(height * 0.004))
            if vy > 0:
                nxt = "acc_t"
                parts.append(
                    f"color=c={accent}:s={width}x{bar_h}:d={duration}:r=30[bar_t];"
                    f"[{current}][bar_t]overlay=0:{max(0, vy - bar_h)}[{nxt}]"
                )
                current = nxt
            if vy + vh < height:
                nxt = "acc_b"
                parts.append(
                    f"color=c={accent}:s={width}x{bar_h}:d={duration}:r=30[bar_b];"
                    f"[{current}][bar_b]overlay=0:{vy + vh}[{nxt}]"
                )
                current = nxt
        current = framed(current, vx, vy)

        top_text = (canvas.top_text or "").strip()
        bottom_text = (canvas.bottom_text or "").strip()
        brand_text = (canvas.brand_text or "").strip()
        slots = template.get("slots") or {}
        if brand_text and slots.get("bottom") == "brand" and not bottom_text:
            bottom_text = brand_text
        elif brand_text and not top_text and slots.get("top") in {"hook", "brand"}:
            top_text = brand_text

        # Burn band copy via PNG overlays (this FFmpeg build has no drawtext).
        # Band stills follow any border stills and are added before the audio branch.
        band_inputs: list[str] = []
        if vy > 8 and top_text:
            top_png = destination.with_name(destination.stem + "-band-top.png")
            _render_band_text_png(
                top_text,
                top_png,
                width=width,
                band_h=vy,
                fontsize=max(28, width // (18 if family == "shorts" else 28)),
            )
            band_inputs.append(str(top_png))
            nxt = "txt_t"
            parts.append(
                f"[{next_input}:v]format=rgba[{nxt}src];"
                f"[{current}][{nxt}src]overlay=0:0:format=auto[{nxt}]"
            )
            current = nxt
            next_input += 1
        bottom_band_h = height - (vy + vh)
        if bottom_band_h > 8 and bottom_text:
            bottom_png = destination.with_name(destination.stem + "-band-bottom.png")
            _render_band_text_png(
                bottom_text,
                bottom_png,
                width=width,
                band_h=bottom_band_h,
                fontsize=max(24, width // (22 if family == "shorts" else 32)),
            )
            band_inputs.append(str(bottom_png))
            nxt = "txt_b"
            parts.append(
                f"[{next_input}:v]format=rgba[{nxt}src];"
                f"[{current}][{nxt}src]overlay=0:{vy + vh}:format=auto[{nxt}]"
            )
            current = nxt
            next_input += 1
        for band_path in band_inputs:
            command.extend(["-loop", "1", "-t", str(duration), "-i", band_path])

        parts.append(f"[{current}]format=yuv420p[v]")
        video = ";".join(parts)

    mute_audio = getattr(item, "mute_audio", False)
    has_audio = (not mute_audio) and media.is_clip and _has_audio(media.path)
    dialogue = media.dialogue_audio if (not mute_audio) and media.dialogue_audio and media.dialogue_audio.is_file() else None
    # Input 0 = media, 1 = anullsrc, then optional band PNGs already appended above.
    next_av_input = sum(1 for flag in command if flag == "-i")
    if has_audio:
        audio_head = f"adelay={round(head_missing * 1000)}:all=1," if head_missing > 0 else ""
        audio = (
            f";[0:a]atrim=start={trim_start}:duration={duration - head_missing},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,{audio_head}apad,atrim=duration={duration}[a]"
        )
        audio_map = ["-map", "[a]"]
    elif dialogue:
        command.extend(["-i", str(dialogue)])
        dialogue_head = f"adelay={round(head_s * 1000)}:all=1," if head_s > 0 else ""
        audio = (
            f";[{next_av_input}:a]atrim=duration={duration - head_s},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,{dialogue_head}apad,atrim=duration={duration}[a]"
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
            *export_encode_args(fast_intermediate=True),
            str(destination),
        ]
    )
    _run(command)


def _file_sig(path: Path | None) -> str:
    if not path or not path.exists():
        return "none"
    try:
        st = path.stat()
        return f"{path.name}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return path.name


def _segment_cache_path(
    item: TimelinePrimaryItem,
    media: ResolvedMedia | None,
    width: int,
    height: int,
    variant: str,
    layout: TimelineCanvasLayout | None,
    head_s: float,
    tail_s: float,
) -> Path | None:
    try:
        cache_dir = settings.media_dir / ".segment_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "v": 2,
            "item": item.model_dump(mode="json", exclude={"id", "label", "transition"}),
            "media": _file_sig(media.path if media else None),
            "is_clip": bool(media and media.is_clip),
            "native": bool(media and media.native),
            "boost": round(float(media.color_boost_strength), 4) if media else 0.0,
            "dialogue": _file_sig(media.dialogue_audio if media else None),
            "w": width,
            "h": height,
            "variant": variant,
            "layout": layout.model_dump(mode="json") if layout else None,
            "head_s": round(head_s, 4),
            "tail_s": round(tail_s, 4),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]
        return cache_dir / f"seg-{digest}.mp4"
    except Exception:
        return None


def _audio_overlay_chain(input_index: int, overlay: TimelineAudioOverlay, audio_path: Path, label: str) -> str:
    total_dur = probe_duration_ms(audio_path) / 1000.0
    raw_in = max(0.0, overlay.source_in_ms / 1000.0)
    if total_dur > 0 and (overlay.loop or raw_in >= total_dur):
        trim_start = raw_in % total_dur
    else:
        trim_start = raw_in
    dur = max(0.1, overlay.duration_ms / 1000.0)
    vol = max(0.0, min(2.0, float(overlay.volume)))
    delay_ms = max(0, int(overlay.start_ms))
    remaining = (total_dur - trim_start) if overlay.loop else (total_dur - trim_start)
    if overlay.loop and 0.05 < remaining < dur:
        # Repeat the in-point..end portion until the clip's length is filled.
        body = (
            f"atrim=start={trim_start},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"aloop=loop=-1:size={round(remaining * 48000)},"
            f"atrim=duration={dur},asetpts=PTS-STARTPTS,volume={vol},"
        )
    else:
        body = (
            f"atrim=start={trim_start}:duration={dur},asetpts=PTS-STARTPTS,"
            f"volume={vol},aformat=sample_rates=48000:channel_layouts=stereo,"
        )
    return f"[{input_index}:a]{body}adelay={delay_ms}|{delay_ms}[{label}]"


def effective_transitions(document: TimelineDocument) -> list[tuple[str, float]]:
    """(xfade kind, seconds) for each join between primary clips; 0 seconds means a hard cut."""
    default = getattr(document, "default_transition", None)
    joins: list[tuple[str, float]] = []
    for previous, item in zip(document.primary, document.primary[1:]):
        transition = item.transition or default
        if not transition or transition.kind == "cut":
            joins.append(("cut", 0.0))
            continue
        seconds = min(transition.duration_ms, previous.duration_ms, item.duration_ms) / 1000
        joins.append((transition.kind, seconds))
    return joins


def _concat_copy(files: list[Path], destination: Path) -> None:
    if len(files) == 1:
        if files[0] != destination:
            shutil.copyfile(files[0], destination)
        return
    concat_file = destination.with_suffix(".concat.txt")
    concat_file.write_text("".join(f"file '{item.as_posix()}'\n" for item in files))
    try:
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(destination)])
    finally:
        concat_file.unlink(missing_ok=True)


def _join_transition_group(
    segments: list[Path], joins: list[tuple[str, float]], destination: Path, lengths: list[float]
) -> None:
    """Join a small group of segments (<=12) using xfade/acrossfade (or concat for hard cuts)."""
    if len(segments) == 1:
        if segments[0] != destination:
            shutil.copyfile(segments[0], destination)
        return
    if not any(seconds > 0 for _kind, seconds in joins):
        _concat_copy(segments, destination)
        return
    command = ["ffmpeg", "-y"]
    for segment in segments:
        command.extend(["-i", str(segment)])
    parts: list[str] = []
    for index, length in enumerate(lengths):
        # concat outputs a 1/1000000 timebase and xfade rejects mismatched timebases.
        parts.append(f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[w{index}]")
        parts.append(f"[{index}:a]apad,atrim=duration={length:.3f},asetpts=PTS-STARTPTS[s{index}]")
    video, audio = "w0", "s0"
    end = lengths[0]
    for index, (kind, seconds) in enumerate(joins, start=1):
        parts.append(f"[{video}]trim=duration={end:.3f},setpts=PTS-STARTPTS[tv{index}]")
        parts.append(f"[{audio}]apad,atrim=duration={end:.3f},asetpts=PTS-STARTPTS[ta{index}]")
        out_v, out_a = f"v{index}", f"a{index}"
        if seconds > 0:
            parts.append(
                f"[tv{index}][w{index}]xfade=transition={kind}:duration={seconds:.3f}:offset={max(0.0, end - seconds):.3f}[{out_v}]"
            )
            parts.append(f"[ta{index}][s{index}]acrossfade=d={seconds:.3f}[{out_a}]")
            end += lengths[index] - seconds
        else:
            parts.append(f"[tv{index}][ta{index}][w{index}][s{index}]concat=n=2:v=1:a=1[{out_v}][{out_a}]")
            end += lengths[index]
        video, audio = out_v, out_a
    parts.append(f"[{video}]trim=duration={end:.3f},setpts=PTS-STARTPTS[vout]")
    parts.append(f"[{audio}]apad,atrim=duration={end:.3f},asetpts=PTS-STARTPTS[aout]")
    command.extend([
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        *export_encode_args(fast_intermediate=True),
        str(destination),
    ])
    _run(command)


def _join_with_transitions(
    segments: list[Path], joins: list[tuple[str, float]], destination: Path, lengths: list[float]
) -> None:
    """Join segments with xfade/acrossfade (or concat for hard cuts).

    ``lengths`` are the timeline's exact segment lengths in seconds. Encoded segments run slightly
    long (video rounds up to whole frames, AAC adds padding), so the running result is trimmed to
    its exact timeline end before every join; otherwise the error accumulates and later shots drift
    away from overlays placed by absolute time.

    For large timelines (e.g. 190+ compilation clips), opening all inputs in a single FFmpeg
    filtergraph exhausts file descriptors/memory and causes libx264 to fail to initialize. When
    more than ``MAX_GROUP`` segments are present, we first merge contiguous transition clusters in
    bounded batches and then join the resulting super-segments.
    """
    MAX_GROUP = 10
    if len(segments) <= MAX_GROUP:
        _join_transition_group(segments, joins, destination, lengths)
        return

    workspace = destination.parent
    cur_segments = list(segments)
    cur_joins = list(joins)
    cur_lengths = list(lengths)
    pass_idx = 0

    while len(cur_segments) > MAX_GROUP:
        pass_idx += 1
        next_segments: list[Path] = []
        next_joins: list[tuple[str, float]] = []
        next_lengths: list[float] = []
        i = 0
        n = len(cur_segments)
        while i < n:
            # Prefer splitting on a hard cut (seconds == 0) up to MAX_GROUP segments so we don't
            # re-encode across transition boundaries more than necessary.
            limit = min(n, i + MAX_GROUP)
            end_i = limit
            if limit < n:
                # Look backwards from limit-1 for a hard cut join (cur_joins[k-1] is between k-1 and k).
                cut_pos = next((k for k in range(limit - 1, i, -1) if cur_joins[k - 1][1] == 0), None)
                if cut_pos is not None:
                    end_i = cut_pos
            sub_segs = cur_segments[i:end_i]
            sub_joins = cur_joins[i : end_i - 1]
            sub_lens = cur_lengths[i:end_i]
            chunk_path = workspace / f"join-p{pass_idx}-{len(next_segments):04d}.mp4"
            _join_transition_group(sub_segs, sub_joins, chunk_path, sub_lens)
            merged_len = sum(sub_lens) - sum(sec for _k, sec in sub_joins if sec > 0)
            next_segments.append(chunk_path)
            next_lengths.append(merged_len)
            if end_i < n:
                next_joins.append(cur_joins[end_i - 1])
            i = end_i
        cur_segments = next_segments
        cur_joins = next_joins
        cur_lengths = next_lengths

    _join_transition_group(cur_segments, cur_joins, destination, cur_lengths)


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


def _input_count(command: list[str]) -> int:
    return sum(1 for flag in command if flag == "-i")


def _input_video_decoder_flags(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in (".webm", ".mkv"):
        try:
            res = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                streams = data.get("streams", [])
                if streams:
                    codec = streams[0].get("codec_name")
                    if codec == "vp9":
                        return ["-c:v", "libvpx-vp9"]
                    if codec == "vp8":
                        return ["-c:v", "libvpx"]
        except Exception:
            return ["-c:v", "libvpx-vp9"]
        return ["-c:v", "libvpx-vp9"]
    return []


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
) -> tuple[str, str, str, int, int]:
    """Return (filter chain, x, y, output width, output height) for an overlay element."""
    c_top, c_bottom, c_left, c_right = _edge_crop_fractions(geometry)
    has_edge_crop = c_top > 1e-4 or c_bottom > 1e-4 or c_left > 1e-4 or c_right > 1e-4
    position = getattr(geometry, "position", "full-frame" if is_video_layer else "center")

    off_x = round(width * max(-1.0, min(1.0, float(getattr(geometry, "offset_x", 0.0) or 0.0))))
    off_y = round(height * max(-1.0, min(1.0, float(getattr(geometry, "offset_y", 0.0) or 0.0))))

    if position == "full-frame":
        if getattr(geometry, "fit", "cover") == "contain":
            src_w, src_h = _probe_dimensions(media_path)
            aspect = max(0.1, src_w / max(1, src_h))
            if width / aspect <= height:
                el_w = width
                el_h = max(16, (round(width / aspect) // 2) * 2)
            else:
                el_h = height
                el_w = max(16, (round(height * aspect) // 2) * 2)
            final_x = off_x + (width - el_w) // 2
            final_y = off_y + (height - el_h) // 2
            scale_chain = f"scale={el_w}:{el_h}"
            if has_edge_crop:
                crop_x = round(el_w * c_left)
                crop_y = round(el_h * c_top)
                crop_w = max(16, (round(el_w * (1.0 - c_left - c_right)) // 2) * 2)
                crop_h = max(16, (round(el_h * (1.0 - c_top - c_bottom)) // 2) * 2)
                scale_chain = f"{scale_chain},crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
                return scale_chain, str(final_x + crop_x), str(final_y + crop_y), crop_w, crop_h
            return scale_chain, str(final_x), str(final_y), el_w, el_h

        zoom = max(1.0, float(getattr(geometry, "zoom", 1.0) or 1.0))
        fx = float(getattr(geometry, "focal_x", 0.5) or 0.5)
        fy = float(getattr(geometry, "focal_y", 0.5) or 0.5)
        scaled_w = max(16, (round(width * zoom) // 2) * 2)
        scaled_h = max(16, (round(height * zoom) // 2) * 2)
        final_x = off_x - round((scaled_w - width) * fx)
        final_y = off_y - round((scaled_h - height) * fy)
        scale_chain = f"scale={scaled_w}:{scaled_h}"
        if has_edge_crop:
            crop_x = round(scaled_w * c_left)
            crop_y = round(scaled_h * c_top)
            crop_w = max(16, (round(scaled_w * (1.0 - c_left - c_right)) // 2) * 2)
            crop_h = max(16, (round(scaled_h * (1.0 - c_top - c_bottom)) // 2) * 2)
            scale_chain = f"{scale_chain},crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
            return scale_chain, str(final_x + crop_x), str(final_y + crop_y), crop_w, crop_h
        return scale_chain, str(final_x), str(final_y), scaled_w, scaled_h

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
    out_w, out_h = el_w, el_h
    if has_edge_crop:
        crop_x = round(el_w * c_left)
        crop_y = round(el_h * c_top)
        crop_w = max(16, (round(el_w * (1.0 - c_left - c_right)) // 2) * 2)
        crop_h = max(16, (round(el_h * (1.0 - c_top - c_bottom)) // 2) * 2)
        scale_chain = f"{scale_chain},crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
        delta_x = crop_x
        delta_y = crop_y
        out_w, out_h = crop_w, crop_h

    final_x = el_x + delta_x + off_x
    final_y = el_y + delta_y + off_y
    return scale_chain, str(final_x), str(final_y), out_w, out_h


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
            decoder_flags = _input_video_decoder_flags(media.path)
            command.extend([*decoder_flags, "-i", str(media.path)])
            input_idx = 1 + len(specs)
            specs.append(("video", overlay, media.path, geometry, media.color_boost_strength))
            if (not getattr(overlay, "mute_audio", False)) and getattr(overlay, "volume", 1.0) > 0 and _has_audio(media.path):
                audio_specs.append((input_idx, overlay, media.path))
        else:
            decoder_flags = _input_video_decoder_flags(media.path)
            command.extend([*decoder_flags, "-loop", "1", "-t", str(total_seconds), "-i", str(media.path)])
            specs.append(("image", overlay, media.path, geometry, 0.0))

    for overlay in document.image_overlays:
        geometry = _geometry(overlay, variant)
        media = resolve_source(overlay.source, project, db, desired_aspect, geometry)
        decoder_flags = _input_video_decoder_flags(media.path)
        command.extend([*decoder_flags, "-loop", "1", "-t", str(total_seconds), "-i", str(media.path)])
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

    if not specs:
        audio_filters = []
        for aud_idx, (input_index, overlay, audio_path) in enumerate(audio_specs, start=1):
            audio_filters.append(_audio_overlay_chain(input_index, overlay, audio_path, f"aud{aud_idx}"))
        mix_inputs = "[0:a]" + "".join(f"[aud{i}]" for i in range(1, len(audio_specs) + 1))
        audio_filters.append(
            f"{mix_inputs}amix=inputs={1 + len(audio_specs)}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        command.extend(
            [
                "-filter_complex", ";".join(audio_filters),
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "320k",
                "-t", str(total_seconds),
                str(destination),
            ]
        )
        _run(command)
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
            scale_chain, x, y, el_w, el_h = _compute_overlay_layout(geometry, _path, width, height, is_video_layer=True)
            boost_chain = _color_boost_ffmpeg_filter(boost_strength)
            lut_chain = build_lut_ffmpeg_filter(
                getattr(geometry, "lut", "none"),
                float(getattr(geometry, "lut_strength", 1.0) or 1.0),
            )
            alpha_chain = f",format=rgba,colorchannelmixer=aa={opacity}" if opacity < 0.999 else ",format=rgba"
            decoration = render_frame_assets(geometry, el_w, el_h, width, workspace, f"vframe-{index:03d}")
            base_chain = (
                f"[{index}:v]trim=start={trim_start}:duration={dur},setpts=PTS-STARTPTS,"
                f"{scale_chain}{boost_chain}{lut_chain},setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={dur},"
                f"trim=duration={dur}"
            )
            if decoration and decoration.mask:
                mask_input = _input_count(command)
                command.extend(["-loop", "1", "-framerate", "30", "-t", str(dur), "-i", str(decoration.mask)])
                filters.append(f"{base_chain},format=rgba[{prepared}raw]")
                filters.append(f"[{mask_input}:v]format=gray[{prepared}mask]")
                filters.append(
                    f"[{prepared}raw][{prepared}mask]alphamerge,"
                    f"setpts=PTS-STARTPTS+{start}/TB{alpha_chain}[{prepared}]"
                )
            else:
                filters.append(f"{base_chain},setpts=PTS-STARTPTS+{start}/TB{alpha_chain}[{prepared}]")
            next_label = f"v{index}"
            filters.append(
                f"[{current}][{prepared}]overlay={x}:{y}:eof_action=pass:enable='between(t,{start},{end})'[{next_label}]"
            )
            current = next_label
            if decoration and decoration.frame:
                frame_input = _input_count(command)
                command.extend(["-loop", "1", "-framerate", "30", "-t", str(total_seconds), "-i", str(decoration.frame)])
                frame_alpha = f",colorchannelmixer=aa={opacity}" if opacity < 0.999 else ""
                framed_label = f"{next_label}f"
                filters.append(f"[{frame_input}:v]format=rgba{frame_alpha}[{prepared}frame]")
                filters.append(
                    f"[{current}][{prepared}frame]overlay={int(x) - decoration.pad}:{int(y) - decoration.pad}:"
                    f"enable='between(t,{start},{end})'[{framed_label}]"
                )
                current = framed_label
            continue
        if kind == "image":
            opacity = getattr(overlay, "opacity", 1.0)
            scale_chain, x, y, _el_w, _el_h = _compute_overlay_layout(geometry, _path, width, height, is_video_layer=False)
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
        for aud_idx, (input_index, overlay, audio_path) in enumerate(audio_specs, start=1):
            filters.append(_audio_overlay_chain(input_index, overlay, audio_path, f"aud{aud_idx}"))
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
    progress: Callable[..., None] | None = None,
) -> FinalCutResult:
    desired_aspect, width, height = export_variants()[variant]
    filename = f"final-cut-{variant}-{uuid.uuid4()}.mp4"
    output = settings.media_dir / filename
    clip_count = 0
    fallback_count = 0
    with tempfile.TemporaryDirectory(prefix="cutline-timeline-") as directory:
        workspace = Path(directory)
        segments: list[Path] = []
        shot_total = len(document.primary)
        joins = effective_transitions(document)
        segment_lengths: list[float] = []
        layout_doc = getattr(document, "layout", None)
        resolved_items: list[tuple[int, TimelinePrimaryItem, ResolvedMedia | None, Path, float, float]] = []
        for index, item in enumerate(document.primary):
            media = None
            if item.source:
                media = resolve_source(item.source, project, db, desired_aspect, _geometry(item, variant))
                clip_count += int(media.is_clip)
                fallback_count += int(not media.is_clip or not media.native)
            segment = workspace / f"segment-{index:04d}.mp4"
            head_s = joins[index - 1][1] / 2 if index > 0 else 0.0
            tail_s = joins[index][1] / 2 if index < len(joins) else 0.0
            resolved_items.append((index, item, media, segment, head_s, tail_s))
            segments.append(segment)
            segment_lengths.append(item.duration_ms / 1000 + head_s + tail_s)

        def _render_one(entry: tuple[int, TimelinePrimaryItem, ResolvedMedia | None, Path, float, float]) -> int:
            idx, it, med, seg_path, h_s, t_s = entry
            cache_file = _segment_cache_path(it, med, width, height, variant, layout_doc, h_s, t_s)
            if cache_file and cache_file.is_file() and cache_file.stat().st_size > 0:
                shutil.copyfile(cache_file, seg_path)
                return idx
            _normalize_primary(
                it,
                med,
                seg_path,
                width,
                height,
                variant,
                layout=layout_doc,
                head_s=h_s,
                tail_s=t_s,
            )
            if cache_file and seg_path.is_file() and seg_path.stat().st_size > 0:
                try:
                    tmp_cache = cache_file.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
                    shutil.copyfile(seg_path, tmp_cache)
                    tmp_cache.replace(cache_file)
                except OSError:
                    pass
            return idx

        completed_count = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for _done_idx in pool.map(_render_one, resolved_items):
                completed_count += 1
                if progress and shot_total:
                    # Count real primary clips only — do not inflate total with a fake compose "shot".
                    progress(completed_count, shot_total)
        base = workspace / "base.mp4"
        if any(seconds > 0 for _kind, seconds in joins):
            if progress:
                try:
                    progress(max(shot_total, 1), max(shot_total, 1), "Blending transitions…")
                except TypeError:
                    pass
            _join_with_transitions(segments, joins, base, segment_lengths)
        else:
            concat_file = workspace / "concat.txt"
            concat_file.write_text("".join(f"file '{item.as_posix()}'\n" for item in segments))
            _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(base)])
        composed = workspace / "composed.mp4"
        if progress:
            # Final mux/overlay pass — keep denominator as shot count, change the label.
            try:
                progress(max(shot_total, 1), max(shot_total, 1), "Compositing overlays…")
            except TypeError:
                progress(max(shot_total, 1), max(shot_total, 1))
        _composite_overlays(base, composed, document, project, db, variant, width, height, workspace)
        composed.replace(output)
        if progress:
            try:
                progress(max(shot_total, 1), max(shot_total, 1), "Finishing export…")
            except TypeError:
                progress(max(shot_total, 1), max(shot_total, 1))
    return FinalCutResult(
        filename=filename,
        duration_ms=probe_duration_ms(output),
        width=width,
        height=height,
        clip_count=clip_count,
        fallback_count=fallback_count,
    )
