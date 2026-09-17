"""Helpers for selectable shot takes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import Shot, ShotTake, now


def record_take(
    db: Session,
    shot: Shot,
    *,
    media_type: str,
    aspect_ratio: str,
    filename: str,
    mime_type: str,
    source: str,
    provider: str,
    prompt: str = "",
    settings: dict[str, Any] | None = None,
    select: bool = True,
) -> ShotTake:
    if select:
        for take in shot.takes:
            if take.media_type == media_type and take.aspect_ratio == aspect_ratio:
                take.selected = False
    take = ShotTake(
        shot_id=shot.id,
        media_type=media_type,
        aspect_ratio=aspect_ratio,
        filename=filename,
        mime_type=mime_type,
        source=source,
        provider=provider,
        prompt=prompt,
        settings=settings or {},
        selected=select,
        created_at=now(),
    )
    db.add(take)
    db.flush()
    if select:
        if media_type == "image":
            shot.selected_image_take_id = take.id
        elif media_type == "clip":
            shot.selected_clip_take_id = take.id
        elif media_type == "audio":
            shot.selected_audio_take_id = take.id
            shot.dialogue_audio_filename = filename
            shot.dialogue_audio_mime = mime_type
    return take


def select_take(db: Session, shot: Shot, take_id: str) -> ShotTake:
    take = next((item for item in shot.takes if item.id == take_id), None)
    if take is None:
        raise LookupError("Take not found")
    for item in shot.takes:
        if item.media_type == take.media_type and item.aspect_ratio == take.aspect_ratio:
            item.selected = item.id == take.id
    if take.media_type == "image":
        shot.selected_image_take_id = take.id
    elif take.media_type == "clip":
        shot.selected_clip_take_id = take.id
    elif take.media_type == "audio":
        shot.selected_audio_take_id = take.id
        shot.dialogue_audio_filename = take.filename
        shot.dialogue_audio_mime = take.mime_type
    db.flush()
    return take


def continuity_prompt_suffix(scene) -> str:
    parts = []
    if getattr(scene, "set_notes", None):
        parts.append(f"Set: {scene.set_notes.strip()}")
    if getattr(scene, "lighting_notes", None):
        parts.append(f"Lighting: {scene.lighting_notes.strip()}")
    if getattr(scene, "wardrobe_notes", None):
        parts.append(f"Wardrobe: {scene.wardrobe_notes.strip()}")
    if getattr(scene, "props_notes", None):
        parts.append(f"Props: {scene.props_notes.strip()}")
    location = getattr(scene, "location", None)
    if location and location != "Unspecified":
        parts.append(f"Location: {location}")
    time_of_day = getattr(scene, "time_of_day", None)
    if time_of_day and time_of_day != "Unspecified":
        parts.append(f"Time of day: {time_of_day}")
    if not parts:
        return ""
    return " Continuity: " + "; ".join(parts) + "."
