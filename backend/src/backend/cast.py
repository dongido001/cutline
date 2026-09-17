"""Project cast helpers: sync Character rows and tag shots."""

from __future__ import annotations

import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Character, Project, Scene, ScriptVersion, Shot
from .screenplay_parser import canonical_character_name, parse_character_consistency


def cast_portrait_prompt(character: Character, *, look: str | None = None) -> str:
    """Build a locked identity-sheet prompt from cast notes."""
    notes = (character.notes or "").strip()
    name = canonical_character_name(character.name) or "CHARACTER"
    look_key = (look or "").strip().lower()
    look_instruction = (
        "Prioritize an exact, reusable facial likeness. "
        "Use a simple solid dark crew-neck shirt so clothing does not become part of the identity lock."
    )
    if look_key in {"poor", "wealthy", "rich"}:
        # Prefer the matching wardrobe paragraph when present in notes.
        look_instruction = (
            f"Use the character's {look_key} wardrobe when described in the notes. "
            "Keep the exact same face as the identity lock."
        )
    elif notes:
        look_instruction = (
            "If the notes describe multiple wardrobe looks, use a neutral plain shirt and "
            "focus only on locking the face, hair, skin, and distinguishing features."
        )
    appearance = notes or f"Adult character named {name}."
    return (
        f"Character identity reference sheet for {name}. "
        "Head-and-shoulders portrait, front-facing, neutral calm expression, eyes toward camera, "
        "plain seamless light-grey studio background, even soft key light, sharp facial detail, "
        "photoreal cinematic still, no crop through the chin or forehead. "
        f"{look_instruction} "
        f"Appearance notes: {appearance} "
        "All people are adults age 18 or older. No text, no logos, no watermark, no extra people."
    )


def shot_mentioned_names(shot: Shot | dict[str, Any]) -> tuple[list[str], str]:
    if isinstance(shot, Shot):
        dialogue = shot.dialogue or []
        action = shot.action or ""
        image_prompt = shot.image_prompt or ""
        video_prompt = shot.video_prompt or ""
        title = shot.title or ""
    else:
        dialogue = shot.get("dialogue") or []
        action = str(shot.get("action") or "")
        image_prompt = str(shot.get("image_prompt") or "")
        video_prompt = str(shot.get("video_prompt") or "")
        title = str(shot.get("title") or "")

    names: list[str] = []
    seen: set[str] = set()
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        raw = str(line.get("character") or "").strip()
        key = canonical_character_name(raw)
        if key and key not in seen:
            seen.add(key)
            names.append(key)

    haystack = f"{title}\n{action}\n{image_prompt}\n{video_prompt}".upper()
    return names, haystack


def match_character_ids(
    characters: Iterable[Character],
    dialogue_names: list[str],
    haystack: str,
) -> list[str]:
    dialogue_set = {canonical_character_name(name) for name in dialogue_names}
    matched: list[str] = []
    for character in characters:
        key = canonical_character_name(character.name)
        if not key:
            continue
        if key in dialogue_set:
            matched.append(character.id)
            continue
        pattern = rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])"
        if re.search(pattern, haystack):
            matched.append(character.id)
    return matched


def _ensure_character(
    db: Session,
    project_id: str,
    by_name: dict[str, Character],
    name: str,
    notes: str = "",
) -> Character:
    key = canonical_character_name(name)
    existing = by_name.get(key)
    if existing:
        if notes and (not existing.notes or len(notes) > len(existing.notes)):
            existing.notes = notes
            existing.name = key
        return existing
    character = Character(project_id=project_id, name=key, notes=notes or "")
    db.add(character)
    db.flush()
    by_name[key] = character
    return character


def _latest_version_scenes(project: Project, db: Session) -> list[Scene]:
    version = db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project.id)
        .order_by(ScriptVersion.version_number.desc())
    )
    if not version:
        return []
    return list(
        db.scalars(
            select(Scene)
            .where(Scene.script_version_id == version.id)
            .options(selectinload(Scene.shots))
            .order_by(Scene.order_index)
        )
    )


def sync_project_cast(
    project: Project,
    db: Session,
    *,
    source_text: str | None = None,
    tag_shots: bool = True,
) -> list[Character]:
    """Create/update cast from the script and optionally tag every shot."""
    characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .options(selectinload(Character.references))
            .order_by(Character.name)
        )
    )
    by_name = {canonical_character_name(item.name): item for item in characters}

    for entry in parse_character_consistency(source_text or ""):
        _ensure_character(db, project.id, by_name, entry["name"], entry["notes"])

    for scene in _latest_version_scenes(project, db):
        for shot in scene.shots:
            dialogue_names, haystack = shot_mentioned_names(shot)
            for name in dialogue_names:
                _ensure_character(db, project.id, by_name, name)
            if tag_shots:
                shot.character_ids = match_character_ids(
                    by_name.values(), dialogue_names, haystack
                )

    db.flush()
    return list(by_name.values())
