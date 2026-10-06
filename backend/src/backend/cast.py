"""Project cast helpers: sync Character rows and tag shots."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .media import FaceReferenceImage
from .settings import settings
from .models import Character, Project, Scene, ScriptVersion, Shot
from .screenplay_parser import (
    canonical_character_name,
    expand_character_cue_names,
    infer_voice_description,
    parse_character_consistency,
)


CHARACTER_MENTION_ALIASES = {
    "WOMEN OF OLOKO": (r"\bWOMEN\b", r"\bCROWD\b"),
    "BRITISH COLONIAL TROOPS": (r"\bCOLONIAL SOLDIERS\b",),
}


def is_usable_reference_image(data: bytes) -> bool:
    """Return whether image bytes contain visible pixels suitable for a cast reference."""
    if not data:
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.width < 2 or image.height < 2:
                return False
            if "A" in image.getbands():
                alpha = image.getchannel("A")
                if alpha.getbbox() is None:
                    return False
            return True
    except (UnidentifiedImageError, OSError, ValueError):
        # Preserve existing support for non-empty legacy/test reference bytes;
        # the bug we need to reject is a valid image with no visible pixels.
        return bool(data)


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
    lower_notes = notes.lower()
    age_match = re.search(r"\b(\d{1,2})\s*(?:years?\s*old|yo|y/o)\b", lower_notes)
    is_minor_or_mascot = (
        (age_match is not None and int(age_match.group(1)) < 18)
        or bool(
            re.search(
                r"\b(child|childlike|toddler|preschool(?:er)?|magical\s+animated|rainbow\s+character|animated\s+character)\b",
                lower_notes,
            )
        )
    )
    appearance = notes or f"Adult character named {name}."
    adult_clause = "" if is_minor_or_mascot else "All people are adults age 18 or older. "
    return (
        f"Character identity reference sheet for {name}. "
        "Head-and-shoulders portrait, front-facing, neutral calm expression, eyes toward camera, "
        "plain seamless light-grey studio background, even soft key light, sharp facial detail, "
        "photoreal cinematic still, no crop through the chin or forehead. "
        f"{look_instruction} "
        f"Appearance notes: {appearance} "
        f"{adult_clause}No text, no logos, no watermark, no extra people."
    )


QUOTED_SPEECH_RE = re.compile(r'"[^"\n]+"|\u201c[^\u201d\n]+\u201d')


def _is_explicitly_absent(name_token: str, haystack: str) -> bool:
    escaped = re.escape(name_token)
    if re.search(
        rf"\b{escaped}(?:\s+[A-Z]+)?\s+(?:HAS\s+ALREADY\s+[^.\n]{{0,50}}?\s+AND\s+|IS\s+ALREADY\s+[^.\n]{{0,50}}?\s+AND\s+)?(?:IS\s+)?(?:NOT\s+PHYSICALLY\s+PRESENT|NOT\s+VISIBLE|NEVER\s+APPEARS|MUST\s+NOT\s+APPEAR|DOES\s+NOT\s+APPEAR|OUTSIDE\s+THE\s+VISIBLE\s+FRAME|OUT\s+OF\s+THE\s+MAIN\s+FRAME|GONE)\b",
        haystack,
    ):
        return True
    if re.search(
        rf"\b(?:DO\s+NOT\s+GENERATE|ONCE)\s+{escaped}\b|\bAFTER\s+{escaped}(?:'S|\u2019S)\s+EXIT\b",
        haystack,
    ):
        return True
    return False


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
        raw = str(line.get("character") or line.get("speaker") or "").strip()
        for key in expand_character_cue_names(raw):
            if key and key not in seen:
                seen.add(key)
                names.append(key)

    visual_text = "\n".join(
        (
            title,
            QUOTED_SPEECH_RE.sub(" ", action),
            QUOTED_SPEECH_RE.sub(" ", image_prompt),
            QUOTED_SPEECH_RE.sub(" ", video_prompt),
        )
    )
    haystack = visual_text.upper()
    return names, haystack


def match_character_ids(
    characters: Iterable[Character],
    dialogue_names: list[str],
    haystack: str,
) -> list[str]:
    char_list = list(characters)
    dialogue_set = {canonical_character_name(name) for name in dialogue_names}
    by_exact_key = {canonical_character_name(c.name): c for c in char_list if canonical_character_name(c.name)}
    first_word_counts: dict[str, int] = {}
    for character in char_list:
        key = canonical_character_name(character.name)
        parts = key.split()
        if parts:
            if len(parts) > 1 and parts[0] in by_exact_key:
                continue
            first_word_counts[parts[0]] = first_word_counts.get(parts[0], 0) + 1

    matched: list[str] = []
    for character in char_list:
        key = canonical_character_name(character.name)
        if not key:
            continue
        parts = key.split()
        if len(parts) > 1 and parts[0] in by_exact_key:
            continue
        first_word = parts[0] if len(parts) > 1 and first_word_counts.get(parts[0]) == 1 else None
        if key in dialogue_set or (first_word and first_word in dialogue_set):
            matched.append(character.id)
            continue
        if _is_explicitly_absent(key, haystack) or (
            first_word and _is_explicitly_absent(first_word, haystack)
        ):
            continue
        pattern = rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])"
        first_pattern = (
            rf"(?<![A-Z0-9]){re.escape(first_word)}(?![A-Z0-9])" if first_word else None
        )
        aliases = CHARACTER_MENTION_ALIASES.get(key, ())
        if (
            re.search(pattern, haystack)
            or (first_pattern and re.search(first_pattern, haystack))
            or any(re.search(alias, haystack) for alias in aliases)
        ):
            matched.append(character.id)
    return matched


def _find_first_name_alias(
    by_name: dict[str, Character], key: str
) -> Character | None:
    parts = key.split()
    if len(parts) == 1:
        matches = [
            char
            for existing_key, char in by_name.items()
            if existing_key.split() and existing_key.split()[0] == key
        ]
        unique = {char.id: char for char in matches}
        if len(unique) == 1:
            return next(iter(unique.values()))
    elif len(parts) > 1 and parts[0] in by_name:
        return by_name[parts[0]]
    return None


def _ensure_character(
    db: Session,
    project_id: str,
    by_name: dict[str, Character],
    name: str,
    notes: str = "",
    voice_description: str = "",
) -> Character:
    key = canonical_character_name(name)
    existing = by_name.get(key) or _find_first_name_alias(by_name, key)
    resolved_voice = (voice_description or "").strip() or infer_voice_description(key, notes)
    if existing:
        if notes and (not existing.notes or len(notes) > len(existing.notes)):
            existing.notes = notes
            if key not in by_name or by_name[key].id == existing.id:
                existing.name = key
        if not (existing.voice_description or "").strip():
            existing.voice_description = resolved_voice or infer_voice_description(
                existing.name, existing.notes
            )
        by_name[key] = existing
        return existing
    character = Character(
        project_id=project_id,
        name=key,
        notes=notes or "",
        voice_description=resolved_voice,
    )
    db.add(character)
    db.flush()
    by_name[key] = character
    return character


def _latest_version_scenes(project: Project, db: Session) -> list[Scene]:
    versions = list(
        db.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project.id)
            .order_by(ScriptVersion.version_number.desc(), ScriptVersion.created_at.desc())
        )
    )
    if not versions:
        return []
    seen_episodes: set[str | None] = set()
    latest_version_ids: list[str] = []
    for version in versions:
        ep_key = version.episode_id
        if ep_key not in seen_episodes:
            seen_episodes.add(ep_key)
            latest_version_ids.append(version.id)
    return list(
        db.scalars(
            select(Scene)
            .where(Scene.script_version_id.in_(latest_version_ids))
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
    for item in characters:
        if not (item.voice_description or "").strip() and (item.notes or "").strip():
            item.voice_description = infer_voice_description(item.name, item.notes)
    by_name = {canonical_character_name(item.name): item for item in characters}

    for entry in parse_character_consistency(source_text or ""):
        _ensure_character(
            db,
            project.id,
            by_name,
            entry["name"],
            entry["notes"],
            entry.get("voice_description", ""),
        )

    unique_chars = lambda: list({char.id: char for char in by_name.values()}.values())

    for scene in _latest_version_scenes(project, db):
        for shot in scene.shots:
            dialogue_names, haystack = shot_mentioned_names(shot)
            for name in dialogue_names:
                _ensure_character(db, project.id, by_name, name)
            if tag_shots:
                shot.character_ids = match_character_ids(
                    unique_chars(), dialogue_names, haystack
                )

    db.flush()
    return unique_chars()


def load_cast_references(
    db: Session, project_id: str, character_ids: Iterable[str],
    reference_ids: Iterable[str] | None = None,
) -> list[FaceReferenceImage]:
    """Resolve the complete selected cast, without silently dropping identities."""
    selected_ids = list(dict.fromkeys(character_ids))
    if not selected_ids:
        return []
    characters = db.scalars(
        select(Character)
        .where(Character.project_id == project_id, Character.id.in_(selected_ids))
        .options(selectinload(Character.references))
    ).all()
    by_id = {character.id: character for character in characters}
    if any(character_id not in by_id for character_id in selected_ids):
        raise RuntimeError("One or more selected cast characters were not found in this project")
    selected_reference_ids = set(reference_ids) if reference_ids is not None else None
    references: list[FaceReferenceImage] = []
    missing: list[str] = []
    for character_id in selected_ids:
        character = by_id[character_id]
        usable = []
        for ref in character.references:
            if selected_reference_ids is not None and ref.id not in selected_reference_ids:
                continue
            path = settings.media_dir / ref.filename
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if is_usable_reference_image(data):
                usable.append(FaceReferenceImage(
                    character_name=character.name, mime_type=ref.mime_type, data=data,
                    reference_id=ref.id, character_id=character.id,
                ))
        if not usable:
            missing.append(character.name)
        references.extend(usable)
    if missing:
        raise RuntimeError("Add a usable reference image for: " + ", ".join(missing))
    return references
