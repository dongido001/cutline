"""Release kits: timeline frames, AI title/description/tag suggestions and AI cover art for publishing.

A kit is scoped to one episode (or one compilation, or the whole project when neither is set) and one
variant, so landscape and Shorts packaging stay independent.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from PIL import Image, ImageEnhance, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .jobs import JobCancelled, preserve_cancelled, raise_if_cancelled
from .media import IMAGE_REFERENCE_PROVIDERS, VisualReferenceImage, _request_provider_image, image_provider_statuses
from .models import Compilation, Episode, Job, Project, ReleaseKit, ScriptVersion, Shot
from .providers import CompatibleScriptProvider, OllamaScriptProvider, get_script_provider, loads_model_json
from .schemas import TimelineSource, TimelineVariantGeometry
from .settings import settings

VARIANTS = ("landscape", "shorts")
COVER_SIZES = {"landscape": (1280, 720), "shorts": (1080, 1920)}
VARIANT_ASPECT = {"landscape": "16:9", "shorts": "9:16"}
MAX_FRAMES = 8
MAX_COVERS = 24
MAX_TITLE_CHARS = 100
MAX_TAGS_CHARS = 480
YOUTUBE_THUMBNAIL_BYTES = 2 * 1024 * 1024
TITLE_ANGLES = ("curiosity", "emotional", "stakes", "question", "direct", "custom")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _media_url(filename: str | None) -> str | None:
    return f"/media/{filename}" if filename else None


# --------------------------------------------------------------------------- kit storage


def get_or_create_kit(
    db: Session,
    project: Project,
    variant: str,
    episode_id: str | None = None,
    compilation_id: str | None = None,
) -> ReleaseKit:
    if variant not in VARIANTS:
        raise ValueError("Variant must be landscape or shorts")
    if compilation_id:
        compilation = db.get(Compilation, compilation_id)
        if not compilation or compilation.project_id != project.id:
            raise LookupError("Compilation not found")
        episode_id = None
    elif episode_id:
        episode = db.get(Episode, episode_id)
        if not episode or episode.project_id != project.id:
            raise LookupError("Episode not found")
    kit = db.scalar(
        select(ReleaseKit).where(
            ReleaseKit.project_id == project.id,
            ReleaseKit.variant == variant,
            ReleaseKit.episode_id.is_(None) if episode_id is None else ReleaseKit.episode_id == episode_id,
            ReleaseKit.compilation_id.is_(None) if compilation_id is None else ReleaseKit.compilation_id == compilation_id,
        )
    )
    if kit:
        return kit
    kit = ReleaseKit(
        project_id=project.id,
        episode_id=episode_id,
        compilation_id=compilation_id,
        variant=variant,
        frames=[],
        title_options=[],
        tags=[],
        cover_ideas=[],
        covers=[],
    )
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return kit


def scope_label(db: Session, kit: ReleaseKit) -> str:
    if kit.compilation_id:
        compilation = db.get(Compilation, kit.compilation_id)
        return compilation.title if compilation else "Compilation"
    if kit.episode_id:
        episode = db.get(Episode, kit.episode_id)
        if episode:
            return f"Episode {episode.episode_number}: {episode.title}" if episode.title else f"Episode {episode.episode_number}"
    project = db.get(Project, kit.project_id)
    return project.title if project else "Project"


def serialize_kit(db: Session, kit: ReleaseKit) -> dict[str, Any]:
    return {
        "id": kit.id,
        "project_id": kit.project_id,
        "episode_id": kit.episode_id,
        "compilation_id": kit.compilation_id,
        "variant": kit.variant,
        "scope_label": scope_label(db, kit),
        "brief": kit.brief or "",
        "frames": [{**frame, "url": _media_url(frame.get("filename"))} for frame in kit.frames or []],
        "title_options": list(kit.title_options or []),
        "selected_title_id": kit.selected_title_id,
        "description": kit.description or "",
        "tags": list(kit.tags or []),
        "cover_ideas": list(kit.cover_ideas or []),
        "covers": [{**cover, "url": _media_url(cover.get("filename"))} for cover in kit.covers or []],
        "selected_cover_id": kit.selected_cover_id,
        "copy_provider": kit.copy_provider,
        "created_at": kit.created_at,
        "updated_at": kit.updated_at,
    }


def clean_tags(values: list[Any]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    total = 0
    for value in values:
        tag = re.sub(r"\s+", " ", str(value).replace(",", " ").strip().lstrip("#")).strip()[:60]
        key = tag.lower()
        if not tag or key in seen:
            continue
        if total + len(tag) > MAX_TAGS_CHARS:
            break
        seen.add(key)
        tags.append(tag)
        total += len(tag)
    return tags


def _clean_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().strip('"').strip()[:MAX_TITLE_CHARS]


def update_kit(kit: ReleaseKit, patch: dict[str, Any]) -> None:
    if "brief" in patch:
        kit.brief = str(patch["brief"] or "")[:4000]
    if "title_options" in patch:
        options = []
        for item in patch["title_options"] or []:
            text = _clean_title(item.get("text"))
            if not text:
                continue
            angle = item.get("angle") if item.get("angle") in TITLE_ANGLES else "custom"
            options.append({"id": str(item.get("id") or _short_id()), "text": text, "angle": angle, "custom": bool(item.get("custom"))})
        kit.title_options = options
        if kit.selected_title_id not in {item["id"] for item in options}:
            kit.selected_title_id = options[0]["id"] if options else None
    if "selected_title_id" in patch:
        selected = patch["selected_title_id"]
        if selected is not None and selected not in {item["id"] for item in kit.title_options or []}:
            raise ValueError("Selected title is not one of the kit's options")
        kit.selected_title_id = selected
    if "description" in patch:
        kit.description = str(patch["description"] or "")[:5000]
    if "tags" in patch:
        kit.tags = clean_tags(list(patch["tags"] or []))
    if "cover_ideas" in patch:
        kit.cover_ideas = [_normalize_idea(item) for item in patch["cover_ideas"] or [] if isinstance(item, dict)][:6]
    if "selected_cover_id" in patch:
        selected = patch["selected_cover_id"]
        if selected is not None:
            cover = next((item for item in kit.covers or [] if item["id"] == selected), None)
            if not cover or cover.get("status") != "ready":
                raise ValueError("Only a finished cover can be selected")
        kit.selected_cover_id = selected


# --------------------------------------------------------------------------- frames


def _shot_belongs_to_project(shot: Shot, project: Project) -> bool:
    scene = shot.scene
    return bool(scene and scene.script_version and scene.script_version.project_id == project.id)


def capture_frame(
    db: Session,
    project: Project,
    kit: ReleaseKit,
    source: TimelineSource,
    source_ms: int,
    timeline_ms: int,
    label: str = "",
) -> dict[str, Any]:
    from .timeline_render import resolve_source

    if len(kit.frames or []) >= MAX_FRAMES:
        raise ValueError(f"A release kit holds up to {MAX_FRAMES} frames; remove one first")
    if source.kind == "shot_audio":
        raise ValueError("Audio items have no picture to capture")
    if source.kind in {"shot_clip", "shot_image"}:
        shot = db.get(Shot, source.ref_id)
        if not shot or not _shot_belongs_to_project(shot, project):
            raise LookupError("Shot not found")
    media = resolve_source(source, project, db, VARIANT_ASPECT[kit.variant], TimelineVariantGeometry())
    if not media.path.is_file():
        raise RuntimeError("The media under the playhead is missing")
    filename = f"release-frame-{uuid.uuid4()}.jpg"
    destination = settings.media_dir / filename
    if media.is_clip:
        _extract_video_frame(media.path, max(0, int(source_ms)), destination)
    else:
        with Image.open(media.path) as image:
            still = ImageOps.exif_transpose(image).convert("RGB")
            still.thumbnail((1280, 1280))
            still.save(destination, "JPEG", quality=90)
    frame = {
        "id": _short_id(),
        "filename": filename,
        "timeline_ms": max(0, int(timeline_ms)),
        "source_ms": max(0, int(source_ms)),
        "label": str(label or "")[:120],
        "created_at": _stamp(),
    }
    kit.frames = [*(kit.frames or []), frame]
    return frame


def _extract_video_frame(path: Path, source_ms: int, destination: Path) -> None:
    def run(seek_ms: int) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{seek_ms / 1000:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3",
                str(destination),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

    try:
        run(source_ms)
        if not destination.is_file() and source_ms > 0:
            # Seeking past the last decodable frame yields no output; step back slightly.
            run(max(0, source_ms - 250))
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not read a frame from the clip: {exc.stderr.decode(errors='ignore')[-300:]}") from exc
    if not destination.is_file():
        raise RuntimeError("Could not read a frame at the playhead")


def delete_frame(kit: ReleaseKit, frame_id: str) -> None:
    frames = list(kit.frames or [])
    frame = next((item for item in frames if item["id"] == frame_id), None)
    if not frame:
        raise LookupError("Frame not found")
    kit.frames = [item for item in frames if item["id"] != frame_id]
    (settings.media_dir / frame["filename"]).unlink(missing_ok=True)


def promote_frame_to_cover(
    kit: ReleaseKit,
    frame_id: str,
    headline: str | None = None,
    style: str | None = "punch",
) -> dict[str, Any]:
    frame = next((item for item in kit.frames or [] if item["id"] == frame_id), None)
    if not frame:
        raise LookupError("Frame not found")
    source_path = settings.media_dir / frame["filename"]
    if not source_path.is_file():
        raise RuntimeError("Frame image file is missing")

    target_size = COVER_SIZES.get(kit.variant, (1280, 720))
    with Image.open(source_path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.fit(rgb, target_size, Image.Resampling.LANCZOS)

        clean_style = (style or "punch").strip().lower()
        if clean_style in {"punch", "vivid", "pop"}:
            fitted = ImageEnhance.Contrast(fitted).enhance(1.18)
            fitted = ImageEnhance.Color(fitted).enhance(1.25)
            fitted = ImageEnhance.Sharpness(fitted).enhance(1.2)
        elif clean_style == "drama":
            fitted = ImageEnhance.Contrast(fitted).enhance(1.28)
            fitted = ImageEnhance.Color(fitted).enhance(1.08)
            fitted = ImageEnhance.Brightness(fitted).enhance(0.96)
        elif clean_style == "warm":
            fitted = ImageEnhance.Color(fitted).enhance(1.2)
            fitted = ImageEnhance.Brightness(fitted).enhance(1.04)

        filename = f"release-cover-{uuid.uuid4()}.jpg"
        destination = settings.media_dir / filename
        fitted.save(destination, "JPEG", quality=92)

    cover = {
        "id": _short_id(),
        "idea_id": None,
        "concept": f"Frame: {frame.get('label') or 'Timeline'}",
        "headline": (headline or "").strip()[:60],
        "prompt": f"Direct frame promotion ({clean_style})",
        "provider": "frame_promote",
        "status": "ready",
        "filename": filename,
        "error": None,
        "job_id": None,
        "created_at": _stamp(),
    }
    kit.covers = [cover, *(kit.covers or [])]
    kit.selected_cover_id = cover["id"]
    return cover


def auto_import_shot_frames(db: Session, kit: ReleaseKit) -> list[dict[str, Any]]:
    project = db.get(Project, kit.project_id)
    if not project:
        return []
    version = _latest_version(db, project.id, kit.episode_id)
    if not version:
        return []

    existing_filenames = {f.get("filename") for f in (kit.frames or []) if f.get("filename")}
    imported = []
    target_aspect = VARIANT_ASPECT.get(kit.variant, "16:9")

    for scene in sorted(version.scenes or [], key=lambda s: s.order_index):
        for shot in sorted(scene.shots or [], key=lambda sh: sh.order_index):
            if len(kit.frames or []) + len(imported) >= MAX_FRAMES:
                break

            candidate_path: Path | None = None
            source_kind = "shot_image"

            ready_takes = [t for t in shot.takes or [] if t.aspect_ratio == target_aspect and t.filename]
            selected_take = next((t for t in ready_takes if t.id in (shot.selected_image_take_id, shot.selected_clip_take_id)), None)
            take = selected_take or (ready_takes[0] if ready_takes else None)

            if take:
                take_p = settings.media_dir / take.filename
                if take_p.is_file():
                    candidate_path = take_p
                    source_kind = "shot_clip" if take.media_type == "clip" else "shot_image"
            elif shot.shot_images:
                img = shot.shot_images[0]
                img_p = settings.media_dir / img.filename
                if img_p.is_file():
                    candidate_path = img_p
                    source_kind = "shot_image"

            if not candidate_path or candidate_path.name in existing_filenames:
                continue

            frame_filename = f"release-frame-{uuid.uuid4()}.jpg"
            dest = settings.media_dir / frame_filename
            try:
                if source_kind == "shot_clip":
                    _extract_video_frame(candidate_path, 1000, dest)
                else:
                    with Image.open(candidate_path) as im:
                        still = ImageOps.exif_transpose(im).convert("RGB")
                        still.thumbnail((1280, 1280))
                        still.save(dest, "JPEG", quality=90)

                frame = {
                    "id": _short_id(),
                    "filename": frame_filename,
                    "timeline_ms": shot.start_ms,
                    "source_ms": 0,
                    "label": f"{shot.title or 'Shot'} ({scene.title or 'Scene'})",
                    "created_at": _stamp(),
                }
                imported.append(frame)
                existing_filenames.add(frame_filename)
            except Exception:
                continue

    if imported:
        kit.frames = [*(kit.frames or []), *imported]
    return imported


def _frame_images(kit: ReleaseKit) -> list[tuple[str, bytes]]:
    images = []
    for frame in kit.frames or []:
        path = settings.media_dir / frame["filename"]
        if path.is_file():
            images.append(("image/jpeg", path.read_bytes()))
    return images


# --------------------------------------------------------------------------- copy suggestions


def _latest_version(db: Session, project_id: str, episode_id: str | None) -> ScriptVersion | None:
    query = select(ScriptVersion).where(ScriptVersion.project_id == project_id)
    query = query.where(ScriptVersion.episode_id.is_(None) if episode_id is None else ScriptVersion.episode_id == episode_id)
    return db.scalar(query.order_by(ScriptVersion.version_number.desc()))


def _story_summary(version: ScriptVersion | None, scene_limit: int = 12) -> list[dict[str, Any]]:
    if not version:
        return []
    scenes = []
    for scene in sorted(version.scenes, key=lambda item: item.order_index)[:scene_limit]:
        dialogue = []
        for line in (scene.dialogue or [])[:4]:
            if isinstance(line, dict) and (line.get("text") or line.get("line")):
                dialogue.append(f"{line.get('character') or line.get('speaker') or 'Someone'}: {line.get('text') or line.get('line')}"[:160])
        scenes.append({
            "title": scene.title,
            "location": scene.location,
            "action": (scene.action or "")[:360],
            "dialogue": dialogue,
        })
    return scenes


def _episode_context(db: Session, project: Project, episode: Episode, scene_limit: int = 12) -> dict[str, Any]:
    return {
        "number": episode.episode_number,
        "title": episode.title,
        "synopsis": (episode.synopsis or "")[:800],
        "scenes": _story_summary(_latest_version(db, project.id, episode.id), scene_limit),
    }


def build_copy_context(db: Session, kit: ReleaseKit) -> dict[str, Any]:
    project = db.get(Project, kit.project_id)
    context: dict[str, Any] = {
        "series_title": project.title if project else "",
        "platform_format": "YouTube Shorts / vertical 9:16" if kit.variant == "shorts" else "YouTube long-form landscape 16:9",
        "creator_brief": kit.brief or "",
        "frames_attached": len(kit.frames or []),
    }
    if kit.compilation_id:
        compilation = db.get(Compilation, kit.compilation_id)
        if compilation:
            context["compilation"] = {"title": compilation.title, "description": compilation.description or ""}
            if compilation.episode_id:
                ep = db.get(Episode, compilation.episode_id)
                if ep:
                    context["episode"] = _episode_context(db, project, ep)
            episodes = [db.get(Episode, episode_id) for episode_id in compilation.episode_ids or []]
            if episodes:
                context["episodes"] = [_episode_context(db, project, episode, 4) for episode in episodes if episode][:10]
    elif kit.episode_id:
        episode = db.get(Episode, kit.episode_id)
        if episode:
            context["episode"] = _episode_context(db, project, episode)
    elif project:
        context["story"] = _story_summary(_latest_version(db, project.id, None))
    if project and project.characters:
        context["characters"] = [
            {"name": c.name, "notes": (c.notes or "")[:200]}
            for c in project.characters if c.name
        ][:6]
    return context


COPY_SYSTEM_PROMPT = (
    "You are a YouTube packaging strategist for scripted drama series. You write honest, high click-through "
    "titles, descriptions and tags, and you design thumbnail concepts. Return only valid JSON, no markdown."
)


def build_copy_prompt(context: dict[str, Any], variant: str) -> str:
    shorts = variant == "shorts"
    with_frames = bool(context.get("frames_attached"))
    return "\n".join([
        "Package this video for publishing."
        + (" The attached images are frames from the video, in timeline order." if with_frames else ""),
        ("Use the frames, the story context" if with_frames else "Use the story context")
        + " and especially the creator brief to understand what actually happens.",
        "",
        "Rules:",
        "- 5 title options, each with a different angle: curiosity, emotional, stakes, question, direct.",
        f"- Titles at most {'60' if shorts else '70'} characters, no ALL-CAPS sentences, no misleading clickbait, no spoilers of the ending.",
        "- Description: first line is a hook that works in search previews; "
        + ("1-2 short lines total, end with 3 hashtags including #Shorts." if shorts else "then 2-3 short paragraphs, a subscribe call to action, and 3-5 hashtags at the end."),
        "- 12-20 tags mixing series name, characters, genre and search phrases.",
        "- 3 cover ideas. Each has a concept (what the image shows), a headline of 2-5 punchy words to paint on the cover, "
        "and a detailed image-generation prompt describing composition, emotion, lighting and where the headline sits. "
        + ("Covers are vertical 9:16; keep the subject and text in the middle 70% height." if shorts else "Covers are 16:9 YouTube thumbnails readable at small size."),
        "",
        "Return JSON exactly in this shape:",
        '{"titles":[{"text":"...","angle":"curiosity"}],"description":"...","tags":["..."],'
        '"cover_ideas":[{"concept":"...","headline":"...","prompt":"..."}]}',
        "",
        "Context:",
        json.dumps(context, ensure_ascii=False, indent=1)[:9000],
    ])


def _normalize_idea(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or _short_id()),
        "concept": str(item.get("concept") or "").strip()[:400],
        "headline": re.sub(r"\s+", " ", str(item.get("headline") or "")).strip()[:60],
        "prompt": str(item.get("prompt") or "").strip()[:2000],
    }


def normalize_copy(raw: Any, variant: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("The AI reply was not a JSON object")
    titles = []
    seen: set[str] = set()
    for item in raw.get("titles") or raw.get("title_options") or []:
        text = _clean_title(item.get("text") if isinstance(item, dict) else item)
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        angle = item.get("angle") if isinstance(item, dict) and item.get("angle") in TITLE_ANGLES else "direct"
        titles.append({"id": _short_id(), "text": text, "angle": angle, "custom": False})
    if not titles:
        raise RuntimeError("The AI reply did not include any titles")
    description = str(raw.get("description") or "").strip()[:5000]
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    ideas = [_normalize_idea(item) for item in raw.get("cover_ideas") or [] if isinstance(item, dict)]
    ideas = [idea for idea in ideas if idea["concept"] or idea["prompt"]][:4]
    return {"titles": titles[:6], "description": description, "tags": clean_tags(list(tags)), "cover_ideas": ideas}


def mock_copy(context: dict[str, Any], variant: str) -> dict[str, Any]:
    series = context.get("series_title") or "Our series"
    focus = context.get("compilation") or context.get("episode") or {}
    name = focus.get("title") or series
    brief = (context.get("creator_brief") or "").strip()
    hook = brief.split(".")[0][:80] if brief else f"Everything changes in {name}"
    shorts = variant == "shorts"
    series_tag = "#" + re.sub(r"\W", "", series)
    return {
        "titles": [
            {"text": f"{hook}…", "angle": "curiosity"},
            {"text": f"{name} | {series}", "angle": "direct"},
            {"text": f"Nobody saw this coming in {name}", "angle": "stakes"},
            {"text": f"Would you forgive them? {name}", "angle": "question"},
            {"text": f"The moment that broke us | {series}", "angle": "emotional"},
        ],
        "description": (
            f"{hook}. #Shorts {series_tag} #Drama"
            if shorts
            else f"{hook}.\n\n{brief or 'A new chapter of ' + series + '.'}\n\n"
            f"Subscribe so you don't miss the next episode.\n\n{series_tag} #Drama #Series"
        ),
        "tags": [series, name, "drama", "web series", "short film", "storytelling"],
        "cover_ideas": [
            {"concept": f"Close-up of the lead character's face at the turning point of {name}", "headline": "IT'S OVER", "prompt": "Tight emotional close-up, dramatic rim light, shallow depth of field."},
            {"concept": "Two characters facing off, tension between them", "headline": "WHO LIED?", "prompt": "Split composition, cool vs warm light, intense eye contact."},
            {"concept": "The key location at night with one silhouetted figure", "headline": "NO WAY BACK", "prompt": "Moody wide shot, strong backlight, cinematic haze."},
        ],
    }


def _image_part(mime: str, data: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def _chat_accepts_images(provider_name: str, provider: CompatibleScriptProvider) -> bool:
    """Older ChatGPT bridge builds flatten list content into prompt text, so only attach when advertised."""
    if provider_name != "chatgpt_web":
        return True
    try:
        response = httpx.get(
            f"{provider.base_url.rstrip('/')}/provider/capabilities",
            headers={"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {},
            timeout=5,
        )
        response.raise_for_status()
        return bool((response.json().get("chat_attachments") or {}).get("images"))
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def request_copy(provider_name: str, context: dict[str, Any], variant: str, images: list[tuple[str, bytes]]) -> dict[str, Any]:
    provider = get_script_provider(provider_name)
    if images and (not isinstance(provider, CompatibleScriptProvider) or not _chat_accepts_images(provider_name, provider)):
        images = []
    context = {**context, "frames_attached": len(images)}
    prompt = build_copy_prompt(context, variant)
    if isinstance(provider, CompatibleScriptProvider):
        content: Any = [{"type": "text", "text": prompt}, *[_image_part(mime, data) for mime, data in images]] if images else prompt
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": [{"role": "system", "content": COPY_SYSTEM_PROMPT}, {"role": "user", "content": content}],
        }
        if provider.new_session:
            payload["new_session"] = True
        headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
        response = httpx.post(
            f"{provider.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.deep_review_timeout_seconds,
        )
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]["content"]
        return normalize_copy(loads_model_json(reply if isinstance(reply, str) else json.dumps(reply)), variant)
    if isinstance(provider, OllamaScriptProvider):
        response = httpx.post(
            f"{provider.base_url.rstrip('/')}/api/chat",
            json={
                "model": provider.model,
                "stream": False,
                "format": "json",
                "messages": [{"role": "system", "content": COPY_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            },
            timeout=settings.deep_review_timeout_seconds,
        )
        response.raise_for_status()
        return normalize_copy(loads_model_json(response.json()["message"]["content"]), variant)
    return normalize_copy(mock_copy(context, variant), variant)


def apply_copy(kit: ReleaseKit, copy: dict[str, Any], provider_name: str) -> None:
    custom = [item for item in kit.title_options or [] if item.get("custom")]
    kit.title_options = [*copy["titles"], *custom]
    if kit.selected_title_id not in {item["id"] for item in custom}:
        kit.selected_title_id = copy["titles"][0]["id"]
    if copy["description"]:
        kit.description = copy["description"]
    if copy["tags"]:
        kit.tags = copy["tags"]
    if copy["cover_ideas"]:
        kit.cover_ideas = copy["cover_ideas"]
    kit.copy_provider = provider_name


def resolve_copy_provider(db: Session, project: Project, requested: str | None) -> str:
    from .deep_review import _resolve_deep_review_provider_name

    if requested:
        return requested
    return _resolve_deep_review_provider_name(project.text_provider)


def run_copy_job(kit_id: str, job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        kit = db.get(ReleaseKit, kit_id)
        if not job or job.status == "cancelled":
            return
        if not kit:
            job.status, job.error, job.message = "failed", "Release kit was deleted", "Suggestion failed"
            db.commit()
            return
        try:
            raise_if_cancelled(db, job_id)
            provider_name = (job.attempts or [{}])[0].get("provider") or "mock"
            job.status, job.progress, job.provider, job.provider_phase = "running", 0.1, provider_name, "submitting"
            job.message = f"Asking {provider_name} for titles, description and cover ideas"
            db.commit()
            context = build_copy_context(db, kit)
            copy = request_copy(provider_name, context, kit.variant, _frame_images(kit))
            raise_if_cancelled(db, job_id)
            db.refresh(kit)
            apply_copy(kit, copy, provider_name)
            job.status, job.progress, job.provider_phase = "completed", 1, "completed"
            job.message = f"{len(copy['titles'])} titles, {len(copy['tags'])} tags, {len(copy['cover_ideas'])} cover ideas"
            job.error = None
            db.commit()
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job) or not job:
                return
            job.status, job.message, job.provider_phase = "failed", "Suggestion failed", "failed"
            job.error = _error_text(exc)
            db.commit()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        return f"{exc.response.status_code}: {detail or exc.response.text[:300]}"
    if isinstance(exc, httpx.TimeoutException):
        return "The AI provider timed out"
    return str(exc)[:500] or exc.__class__.__name__


# --------------------------------------------------------------------------- covers


def suggest_thumbnail_hooks(prompt: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Generate high-CTR, punchy viral YouTube thumbnail text hooks from a prompt or story context."""
    text = (prompt or "").strip()
    clean = re.sub(r"[^\w\s']", " ", text).strip()
    words = clean.split()
    lower = clean.lower()

    hooks: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_hook(headline: str, secondary: str = "", tag: str = "Viral Hook") -> None:
        headline = headline.strip().upper()[:40]
        secondary = secondary.strip().upper()[:30]
        if not headline:
            return
        full = f"{headline} // {secondary}" if secondary else headline
        if full.lower() in seen:
            return
        seen.add(full.lower())
        hooks.append({"headline": headline, "secondary": secondary, "full": full, "tag": tag})

    # Direct extraction if prompt is concise (1-5 words)
    if 1 <= len(words) <= 5:
        direct = " ".join(words).upper()
        cleaned_direct = re.sub(r"^(THEY|HE|SHE|WE|I)\s+", "", direct)
        if cleaned_direct and cleaned_direct != direct:
            secondary_clutch = "I CLUTCHED" if any(k in lower for k in ["push", "rush", "fight", "game", "clutch", "early"]) else "BIG MISTAKE"
            add_hook(cleaned_direct, secondary_clutch, "Viral Dual Hook")
            add_hook(direct, "", "Direct Punch")
        else:
            add_hook(direct, "I CLUTCHED" if any(k in lower for k in ["push", "rush", "fight", "clutch", "early"]) else "", "Direct Punch")

    # Domain / theme specific triggers
    if any(k in lower for k in ["push", "rush", "early", "clutch", "flank", "attack", "kill", "shot"]):
        add_hook("PUSHED TOO EARLY", "I CLUTCHED", "Gamer / Action")
        add_hook("THEY PUSHED TOO EARLY", "", "High CTR")
        add_hook("I CLUTCHED", "", "Punchline")
        add_hook("BIG MISTAKE", "NO MERCY", "Stakes")
        add_hook("INSTANT REGRET", "", "Emotion")

    if any(k in lower for k in ["lie", "lied", "truth", "secret", "hide", "cheated", "fake", "betray"]):
        add_hook("WHO LIED?", "THE TRUTH", "Suspense")
        add_hook("CAUGHT RED HANDED", "", "Drama")
        add_hook("BETRAYED", "NO ESCAPE", "Stakes")
        add_hook("THE REAL TRUTH", "", "Curiosity")

    if any(k in lower for k in ["argue", "fight", "confront", "break", "broke", "end", "leave", "crying"]):
        add_hook("IT'S OVER", "NO WAY BACK", "High Stakes")
        add_hook("HE SAID WHAT?!", "", "Shock")
        add_hook("POINT OF NO RETURN", "", "Drama")
        add_hook("CAN'T FORGIVE THIS", "", "Emotion")

    if any(k in lower for k in ["money", "steal", "stole", "rich", "loss", "cost", "scam"]):
        add_hook("LOST EVERYTHING", "", "Stakes")
        add_hook("WAS IT WORTH IT?", "", "Question")

    if context:
        ep_title = (context.get("episode") or {}).get("title") or ""
        if ep_title:
            add_hook(f"WHAT HAPPENED IN {ep_title.upper()[:20]}", "", "Episode Hook")

    fallbacks = [
        ("PUSHED TOO EARLY", "I CLUTCHED", "Action Hook"),
        ("IT'S OVER", "NO WAY BACK", "High Stakes"),
        ("BIG MISTAKE", "THEY REGRETTED IT", "Stakes"),
        ("WHO LIED?", "", "Suspense"),
        ("NO ESCAPE", "", "Urgency"),
        ("HOW COULD THEY?", "", "Curiosity"),
        ("THE TRUTH", "", "Mystery"),
    ]
    for h, s, t in fallbacks:
        if len(hooks) >= 6:
            break
        add_hook(h, s, t)

    return hooks[:6]


def build_cover_prompt(context: dict[str, Any], variant: str, idea: dict[str, Any], headline: str, frame_count: int) -> str:
    shorts = variant == "shorts"
    focus = context.get("compilation") or context.get("episode") or {}
    story = " — ".join(part for part in [context.get("series_title"), focus.get("title")] if part)
    lines = [
        f"Create a {'vertical 9:16 YouTube Shorts cover' if shorts else '16:9 YouTube thumbnail'} that makes people want to click.",
        f"Concept: {(idea.get('concept') or 'The most dramatic moment of the story').rstrip('. ')}.",
    ]
    if idea.get("prompt"):
        lines.append(f"Art direction: {idea['prompt']}")
    if headline:
        if " // " in headline:
            top_part, bottom_part = headline.split(" // ", 1)
            lines.append(
                f'Paint high-impact viral YouTube thumbnail typography into the image with dual text hooks: '
                f'Top text: huge distressed bold block lettering "{top_part.strip()}" with glowing stroke or blood-red spray/outline. '
                f'Bottom text: punchy bold distressed lettering "{bottom_part.strip()}" in neon yellow with dark drop shadow or paint brush underline. '
                f'Ensure all text is massive, high-contrast, instantly readable on mobile devices, and never covers the main character\'s face.'
            )
        else:
            lines.append(
                f'Paint high-impact viral YouTube thumbnail typography into the image, spelled exactly "{headline}" with no other words: '
                f'huge bold distressed block lettering with heavy contrast (thick black outline, vivid red or neon glow, or deep drop shadow), '
                f'placed at the top or bottom safe zone, never covering the character\'s eyes or face.'
            )
    else:
        lines.append("Do not put any text or letters in the image.")
    if frame_count:
        lines.append(
            f"The {frame_count} attached image{'s are frames' if frame_count != 1 else ' is a frame'} from the video: keep the same "
            "characters (faces, hair, wardrobe) and the same world, but recompose them as a striking poster-like thumbnail."
        )
    elif context.get("characters"):
        chars_text = "; ".join(f"{c['name']} ({c['notes']})" for c in context["characters"])
        lines.append(f"Character continuity: Preserve the visual identity and appearance of: {chars_text}.")
    if shorts:
        lines.append("Keep the subject and the headline in the middle 70% of the height; the top and bottom get covered by app UI.")
    lines.append(
        "Style: cinematic, one clear focal point, strong facial emotion, dramatic lighting, rich contrast, saturated but natural "
        "color, clean background separation. No logos, watermarks, borders, play buttons or YouTube interface."
    )
    if context.get("creator_brief"):
        lines.append(f"Creator brief: {context['creator_brief'][:600]}")
    if story:
        lines.append(f"Story: {story}.")
    return "\n".join(lines)


def start_cover(
    kit: ReleaseKit,
    idea_id: str | None,
    headline: str | None,
    prompt: str | None,
    provider: str | None,
) -> dict[str, Any]:
    if len([item for item in kit.covers or [] if item.get("status") != "failed"]) >= MAX_COVERS:
        raise ValueError(f"A release kit holds up to {MAX_COVERS} covers; delete some first")
    if provider and provider not in IMAGE_REFERENCE_PROVIDERS:
        raise ValueError("Covers are generated by the ChatGPT or Gemini bridge")
    idea = next((item for item in kit.cover_ideas or [] if item["id"] == idea_id), None) if idea_id else None
    if idea_id and not idea:
        raise LookupError("Cover idea not found")
    if not idea and not (prompt or "").strip():
        raise ValueError("Pick a cover idea or describe the cover")
    concept = dict(idea or {"id": None, "concept": "", "headline": "", "prompt": ""})
    if prompt and prompt.strip():
        concept["prompt"] = prompt.strip()[:2000]
        if not idea:
            concept["concept"] = prompt.strip()[:400]

    # Resolve headline: if omitted or empty, auto-suggest viral hook from prompt/concept
    final_headline = ""
    raw_headline = (headline if headline is not None else "").strip()
    if raw_headline.upper() in {"NO_TEXT", "NONE", "NO TEXT", "__NONE__"}:
        final_headline = ""
    elif raw_headline:
        final_headline = raw_headline[:60]
    elif idea and (idea.get("headline") or "").strip():
        final_headline = idea["headline"].strip()[:60]
    else:
        concept_text = (concept.get("prompt") or concept.get("concept") or kit.brief or "").strip()
        if concept_text:
            hooks = suggest_thumbnail_hooks(concept_text)
            if hooks:
                final_headline = hooks[0]["full"][:60]

    cover = {
        "id": _short_id(),
        "idea_id": idea["id"] if idea else None,
        "concept": concept.get("concept") or "",
        "headline": final_headline,
        "prompt": concept.get("prompt") or "",
        "provider": provider,
        "status": "generating",
        "filename": None,
        "error": None,
        "job_id": None,
        "created_at": _stamp(),
    }
    kit.covers = [cover, *(kit.covers or [])]
    return cover


def _set_cover(db: Session, kit_id: str, cover_id: str, **changes: Any) -> ReleaseKit | None:
    kit = db.get(ReleaseKit, kit_id)
    if not kit:
        return None
    db.refresh(kit)
    kit.covers = [{**item, **changes} if item["id"] == cover_id else item for item in kit.covers or []]
    return kit


def generate_cover_image(
    prompt: str,
    variant: str,
    references: list[VisualReferenceImage],
    provider: str | None,
    progress: Callable[[dict], None] | None = None,
) -> tuple[str, str]:
    """Return (filename, provider) for a cover rendered entirely by an image bridge."""
    candidates = [provider] if provider else [item for item in settings.image_provider_order if item in IMAGE_REFERENCE_PROVIDERS]
    if not candidates:
        raise RuntimeError("Covers need the ChatGPT or Gemini image bridge; none is configured")
    statuses = {item["id"]: item for item in image_provider_statuses()}
    failures = []
    for candidate in candidates:
        status = statuses.get(candidate, {})
        label = status.get("label", candidate)
        if not status.get("available"):
            failures.append(f"{label}: offline or missing image capability")
            continue
        if progress:
            progress({"provider": candidate, "phase": "submitting", "message": f"Painting cover with {label}"})
        try:
            image, _model, _metadata = _request_provider_image(
                candidate, prompt, VARIANT_ASPECT[variant], progress, references=references
            )
        except Exception as exc:  # noqa: BLE001 - try the next bridge
            failures.append(f"{label}: {_error_text(exc)}")
            continue
        return save_cover(image, variant), candidate
    raise RuntimeError("; ".join(failures) or "No image bridge produced a cover")


def save_cover(image_bytes: bytes, variant: str) -> str:
    with Image.open(io.BytesIO(image_bytes)) as image:
        fitted = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), COVER_SIZES[variant], Image.Resampling.LANCZOS)
    filename = f"release-cover-{uuid.uuid4()}.jpg"
    fitted.save(settings.media_dir / filename, "JPEG", quality=92)
    return filename


def run_cover_job(kit_id: str, job_id: str, cover_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        kit = db.get(ReleaseKit, kit_id)
        if not job or job.status == "cancelled":
            if kit:
                _set_cover(db, kit_id, cover_id, status="failed", error="Cancelled")
                db.commit()
            return
        if not kit:
            job.status, job.error, job.message = "failed", "Release kit was deleted", "Cover failed"
            db.commit()
            return
        cover = next((item for item in kit.covers or [] if item["id"] == cover_id), None)
        if not cover:
            job.status, job.error, job.message = "failed", "Cover was deleted", "Cover failed"
            db.commit()
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status, job.progress, job.message = "running", 0.1, "Preparing cover prompt"
            db.commit()
            context = build_copy_context(db, kit)
            frames = _frame_images(kit)
            prompt = build_cover_prompt(context, kit.variant, cover, cover.get("headline") or "", len(frames))
            references = [
                VisualReferenceImage(mime_type=mime, data=data, character_name=f"Video frame {index}", reference_type="video_frame")
                for index, (mime, data) in enumerate(frames, start=1)
            ]
            if not references:
                project = db.get(Project, kit.project_id)
                if project:
                    for char in project.characters or []:
                        for ref in char.references or []:
                            ref_path = settings.media_dir / ref.filename
                            if ref_path.is_file():
                                references.append(
                                    VisualReferenceImage(
                                        mime_type=ref.mime_type or "image/png",
                                        data=ref_path.read_bytes(),
                                        character_name=char.name,
                                        reference_type="character_reference",
                                    )
                                )
                                break
                        if len(references) >= 4:
                            break

            def progress(event: dict) -> None:
                raise_if_cancelled(db, job_id)
                job.provider = event.get("provider") or job.provider
                job.provider_phase = event.get("phase") or job.provider_phase
                if event.get("message"):
                    job.message = str(event["message"])[:240]
                job.progress = min(0.9, (job.progress or 0.1) + 0.1)
                db.commit()

            filename, provider = generate_cover_image(prompt, kit.variant, references, cover.get("provider"), progress)
            kit = _set_cover(db, kit_id, cover_id, status="ready", filename=filename, provider=provider, error=None, final_prompt=prompt)
            if kit and not kit.selected_cover_id:
                kit.selected_cover_id = cover_id
            job.status, job.progress, job.provider_phase, job.message, job.error = "completed", 1, "completed", "Cover ready", None
            db.commit()
        except JobCancelled:
            db.rollback()
            _set_cover(db, kit_id, cover_id, status="failed", error="Cancelled")
            db.commit()
        except Exception as exc:
            db.rollback()
            message = _error_text(exc)
            _set_cover(db, kit_id, cover_id, status="failed", error=message)
            job = db.get(Job, job_id)
            if job and not preserve_cancelled(job):
                job.status, job.message, job.provider_phase, job.error = "failed", "Cover failed", "failed", message
            db.commit()


def delete_cover(kit: ReleaseKit, cover_id: str) -> None:
    covers = list(kit.covers or [])
    cover = next((item for item in covers if item["id"] == cover_id), None)
    if not cover:
        raise LookupError("Cover not found")
    kit.covers = [item for item in covers if item["id"] != cover_id]
    if kit.selected_cover_id == cover_id:
        kit.selected_cover_id = next((item["id"] for item in kit.covers if item.get("status") == "ready"), None)
    if cover.get("filename"):
        (settings.media_dir / cover["filename"]).unlink(missing_ok=True)


def import_cover(kit: ReleaseKit, image_bytes: bytes, label: str = "") -> dict[str, Any]:
    try:
        filename = save_cover(image_bytes, kit.variant)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("That file is not a readable image") from exc
    cover = {
        "id": _short_id(),
        "idea_id": None,
        "concept": label[:400] or "Uploaded cover",
        "headline": "",
        "prompt": "",
        "provider": "upload",
        "status": "ready",
        "filename": filename,
        "error": None,
        "job_id": None,
        "created_at": _stamp(),
    }
    kit.covers = [cover, *(kit.covers or [])]
    if not kit.selected_cover_id:
        kit.selected_cover_id = cover["id"]
    return cover


def thumbnail_for_upload(kit: ReleaseKit, cover_id: str) -> Path:
    """JPEG under YouTube's 2 MB thumbnail limit for a ready cover."""
    cover = next((item for item in kit.covers or [] if item["id"] == cover_id), None)
    if not cover or cover.get("status") != "ready" or not cover.get("filename"):
        raise ValueError("The selected cover is not ready")
    source = settings.media_dir / cover["filename"]
    if not source.is_file():
        raise ValueError("The selected cover file is missing")
    if source.stat().st_size <= YOUTUBE_THUMBNAIL_BYTES:
        return source
    destination = settings.media_dir / f"{source.stem}-upload.jpg"
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        for quality in (85, 75, 65, 55):
            rgb.save(destination, "JPEG", quality=quality)
            if destination.stat().st_size <= YOUTUBE_THUMBNAIL_BYTES:
                break
    return destination
