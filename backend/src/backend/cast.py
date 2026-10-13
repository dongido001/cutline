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
    parse_structured_screenplay,
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
    """Build a locked identity-sheet, 3D turnaround sheet, scene, or prop prompt."""
    notes = (character.notes or "").strip()
    name = canonical_character_name(character.name) or "CHARACTER"
    kind = (getattr(character, "kind", None) or "character").strip().lower()
    look_key = (look or "").strip().lower()

    if kind == "prop" or look_key == "prop":
        desc = notes or f"Hero prop object named {name}."
        return (
            f"Isolated studio prop reference photograph of {name} centered on a clean pure white studio background. "
            "Sharp focus on the object, accurate materials, surface textures, and proportions, even studio lighting, "
            f"photoreal cinematic quality. Prop details: {desc} "
            "No people, no hands, no text, no labels, no logos, no watermark."
        )

    if kind == "scene" or look_key == "scene":
        desc = notes or f"Cinematic location and set environment for {name}."
        return (
            f"Cinematic location and set environment reference photograph for {name}. "
            "Wide architectural establishing view showing the room or outdoor layout, key furniture, props, textures, "
            f"and lighting atmosphere. Location details: {desc} "
            "No people in frame, no text, no logos, no watermark."
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

    if look_key in {"3d_view", "turnaround", "3d"}:
        return (
            f"Character 3D turnaround reference sheet for {name} in 16:9 horizontal layout on a clean pure white studio background. "
            "Left side: close-up head-and-shoulders portrait facing the camera with a natural calm expression and sharp facial detail. "
            "Right side: three full-body standing views side-by-side (front view facing camera, side profile view, and back view) "
            "showing the exact same character, facial identity, hairstyle, outfit, footwear, and accessories across all views. "
            f"Appearance notes: {appearance} "
            f"{adult_clause}No text, no labels, no logos, no watermark, no extra people."
        )

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
    elif look_key == "full_body":
        look_instruction = (
            "Full-body standing character Main View facing the camera directly on a clean pure white studio background, "
            "showing complete head-to-toe outfit, shoes, and posture."
        )
    elif notes:
        look_instruction = (
            "If the notes describe multiple wardrobe looks, use a neutral plain shirt and "
            "focus only on locking the face, hair, skin, and distinguishing features."
        )
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


def _is_character_non_appearing_in_notes(character: Character) -> bool:
    notes_upper = (character.notes or "").upper()
    return bool(
        re.search(
            r"\bDOES\s+NOT\s+APPEAR\s+(?:PHYSICALLY|AS\s+A\s+LIVING)\b|\bVISIBLE\s+ONLY\s+IN(?:SIDE)?\s+THE\s+(?:SAME\s+|EXACT\s+)?(?:APPROVED\s+)?(?:FADED\s+)?(?:OLD\s+)?PHOTOGRAPH\b",
            notes_upper,
        )
    )


def _is_explicitly_absent(name_token: str, haystack: str) -> bool:
    escaped = re.escape(name_token)
    if re.search(
        rf"\b{escaped}(?:\s+[A-Z]+)?\s+(?:HAS\s+ALREADY\s+[^.\n]{{0,50}}?\s+AND\s+|IS\s+ALREADY\s+[^.\n]{{0,50}}?\s+AND\s+)?(?:IS\s+)?(?:NOT\s+PHYSICALLY\s+PRESENT|NOT\s+VISIBLE|NEVER\s+APPEARS|MUST\s+NOT\s+APPEAR|DOES\s+NOT\s+APPEAR|OUTSIDE\s+THE\s+VISIBLE\s+FRAME|OUT\s+OF\s+THE\s+MAIN\s+FRAME|GONE|ABSENT)\b",
        haystack,
    ):
        return True
    if re.search(
        rf"\b(?:DO\s+NOT\s+GENERATE|ONCE)\s+{escaped}\b|\bAFTER\s+{escaped}(?:'S|\u2019S)\s+EXIT\b",
        haystack,
    ):
        return True
    return False


def _strip_non_presence_mentions(haystack: str) -> str:
    """Strip prop-owner possessives, photo-subject descriptions, and off-screen references."""
    cleaned = re.sub(
        r"\b[A-Z]+(?:'S|\u2019S)\s+(?:OPENED\s+|CREAM\s+|BUSINESS\s+|SEPARATE\s+|SCHEMATIC\s+|PUBLIC\s+|DISABLED\s+|BLACK\s+|LUXURY\s+|DEATH|FUNERAL|HISTORY|PAST|ACCOUNT|DECISION|ARRIVAL)*(?:ENVELOPE|NOTICE|SUV|DEATH|FUNERAL|HISTORY|PAST|ACCOUNT)\b",
        " ",
        haystack,
    )
    cleaned = re.sub(
        r"\b(?:PHOTOGRAPH|PHOTO)\s+(?:OF|SHOWING)\s+[^.\n]+",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:BROUGHT|FIND|AFTER|WARNED\s+ABOUT|WHAT)\s+[A-Z]+(?:\s+DIED|\s+HAD\s+BEEN\s+WARNED|\s+TO\s+SAN\s+AURELIO)?\b",
        " ",
        cleaned,
    )
    return cleaned


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


def extract_mention_element_ids(
    characters: Iterable[Character],
    *texts: str | None,
) -> list[str]:
    """Match explicit @Element mentions (e.g. @MAYA, @CAFE_REPAIR, @OLD_PHOTO) to project Character rows."""
    combined = "\n".join(t for t in texts if t).upper()
    if "@" not in combined:
        return []
    char_list = list(characters)
    matched: list[str] = []
    for char in char_list:
        key = canonical_character_name(char.name)
        if not key:
            continue
        slug = "_".join(key.split())
        candidates = [slug, key]
        parts = key.split()
        if len(parts) > 1:
            candidates.append(parts[0])
        for cand in candidates:
            pattern = rf"@(?<![A-Z0-9_]){re.escape(cand)}(?![A-Z0-9_])"
            if re.search(pattern, combined):
                if char.id not in matched:
                    matched.append(char.id)
                break
    return matched


def match_character_ids(
    characters: Iterable[Character],
    dialogue_names: list[str],
    haystack: str,
) -> list[str]:
    char_list = list(characters)
    explicit_at_ids = set(extract_mention_element_ids(char_list, haystack))
    dialogue_set = {canonical_character_name(name) for name in dialogue_names}
    by_exact_key = {
        canonical_character_name(c.name): c
        for c in char_list
        if canonical_character_name(c.name) and (getattr(c, "kind", "character") or "character") == "character"
    }
    first_word_counts: dict[str, int] = {}
    for character in char_list:
        if (getattr(character, "kind", "character") or "character") != "character":
            continue
        key = canonical_character_name(character.name)
        parts = key.split()
        if parts:
            if len(parts) > 1 and parts[0] in by_exact_key:
                continue
            first_word_counts[parts[0]] = first_word_counts.get(parts[0], 0) + 1

    presence_haystack = _strip_non_presence_mentions(haystack)

    matched: list[str] = []
    for character in char_list:
        if character.id in explicit_at_ids:
            matched.append(character.id)
            continue
        if (getattr(character, "kind", "character") or "character") != "character":
            continue
        if _is_character_non_appearing_in_notes(character):
            continue
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
            re.search(pattern, presence_haystack)
            or (first_pattern and re.search(first_pattern, presence_haystack))
            or any(re.search(alias, presence_haystack) for alias in aliases)
        ):
            matched.append(character.id)
    return matched


def match_explicit_character_ids(
    characters: Iterable[Character],
    explicit_names: list[str],
) -> list[str]:
    """Match explicitly listed character names to project Character rows."""
    if not explicit_names:
        return []
    char_list = list(characters)
    matched_ids: list[str] = []
    for raw_name in explicit_names:
        key = canonical_character_name(raw_name)
        if not key:
            continue
        found_id: str | None = None
        # 1. Exact canonical match
        for char in char_list:
            c_key = canonical_character_name(char.name)
            if c_key == key:
                found_id = char.id
                break
        # 2. First-name or prefix match if unique
        if not found_id:
            for char in char_list:
                c_key = canonical_character_name(char.name)
                c_parts = c_key.split()
                k_parts = key.split()
                if (
                    (len(c_parts) > 1 and key == c_parts[0])
                    or (len(k_parts) > 1 and k_parts[0] == c_key)
                ):
                    found_id = char.id
                    break
        if found_id and found_id not in matched_ids:
            matched_ids.append(found_id)
    return matched_ids


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

    # Build lookup of explicit cast per shot if source_text is present
    explicit_cast_by_shot_idx: dict[int, list[str]] = {}
    explicit_cast_by_shot_title: dict[str, list[str]] = {}
    if source_text:
        try:
            parsed = parse_structured_screenplay(source_text)
            if parsed and parsed.get("scenes"):
                all_parsed_shots = [sh for sc in parsed["scenes"] for sh in sc.get("shots", [])]
                for idx, ps in enumerate(all_parsed_shots):
                    if ps.get("explicit_cast") is not None:
                        explicit_cast_by_shot_idx[idx] = ps["explicit_cast"]
                        if ps.get("title"):
                            explicit_cast_by_shot_title[ps["title"].strip().upper()] = ps["explicit_cast"]
        except Exception:
            pass

    global_shot_idx = 0
    for scene in _latest_version_scenes(project, db):
        for shot in scene.shots:
            dialogue_names, haystack = shot_mentioned_names(shot)
            for name in dialogue_names:
                _ensure_character(db, project.id, by_name, name)

            explicit = None
            if getattr(shot, "explicit_cast", None) is not None:
                explicit = shot.explicit_cast
            elif shot.title and shot.title.strip().upper() in explicit_cast_by_shot_title:
                explicit = explicit_cast_by_shot_title[shot.title.strip().upper()]
            elif global_shot_idx in explicit_cast_by_shot_idx:
                explicit = explicit_cast_by_shot_idx[global_shot_idx]

            if explicit is not None:
                for name in explicit:
                    _ensure_character(db, project.id, by_name, name)
                if tag_shots:
                    shot.character_ids = match_explicit_character_ids(
                        unique_chars(), explicit
                    )
            elif tag_shots:
                shot.character_ids = match_character_ids(
                    unique_chars(), dialogue_names, haystack
                )
            global_shot_idx += 1

    db.flush()
    return unique_chars()


def sync_project_elements(project: Project, db: Session) -> list[Character]:
    """Extract Scene (location/set) and Prop elements from the project's scenes."""
    existing = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .options(selectinload(Character.references))
            .order_by(Character.name)
        )
    )
    by_name = {canonical_character_name(item.name): item for item in existing}

    for scene in _latest_version_scenes(project, db):
        raw_loc = (scene.location or scene.title or "").strip()
        cleaned_loc = re.sub(
            r"^(?:SCENE\s*\d+\s*[:\-–—]?\s*)?(?:INT\.?/EXT\.?|INT\.?|EXT\.?)\s*",
            "",
            raw_loc,
            flags=re.IGNORECASE,
        )
        cleaned_loc = re.sub(
            r"\s*[-–—]\s*(?:DAY|NIGHT|MORNING|EVENING|DAWN|DUSK|CONTINUOUS|LATER|SAME(?:\s+TIME)?).*$",
            "",
            cleaned_loc,
            flags=re.IGNORECASE,
        ).strip()
        loc_key = canonical_character_name(cleaned_loc)
        if loc_key and len(loc_key) >= 3:
            set_desc_parts = [
                part.strip()
                for part in (scene.set_notes, scene.lighting_notes, scene.action)
                if (part or "").strip()
            ]
            set_desc = " ".join(set_desc_parts)
            if loc_key not in by_name:
                scene_elem = Character(
                    project_id=project.id,
                    kind="scene",
                    name=loc_key,
                    notes=set_desc or f"Cinematic set and environment for {loc_key}.",
                    voice_description="",
                )
                db.add(scene_elem)
                db.flush()
                by_name[loc_key] = scene_elem
            elif by_name[loc_key].kind == "scene" and set_desc and not by_name[loc_key].notes:
                by_name[loc_key].notes = set_desc

        raw_props = (scene.props_notes or "").strip()
        if raw_props:
            segments = [
                seg.strip(" .,-–—")
                for seg in re.split(r"[;\n]+|,\s+(?=[A-Z0-9])", raw_props)
                if seg.strip(" .,-–—")
            ]
            for seg in segments[:6]:
                words = seg.split()
                prop_title = " ".join(words[:4]) if len(words) > 4 else seg
                prop_key = canonical_character_name(prop_title)
                if not prop_key or len(prop_key) < 3 or prop_key in by_name:
                    continue
                prop_elem = Character(
                    project_id=project.id,
                    kind="prop",
                    name=prop_key,
                    notes=seg,
                    voice_description="",
                )
                db.add(prop_elem)
                db.flush()
                by_name[prop_key] = prop_elem

    db.flush()
    return list({char.id: char for char in by_name.values()}.values())


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
        ordered_refs = sorted(
            character.references,
            key=lambda r: 0 if getattr(r, "view_type", "main") == "3d_view" else 1,
        )
        for ref in ordered_refs:
            v_type = getattr(ref, "view_type", "main")
            if (
                selected_reference_ids is not None
                and ref.id not in selected_reference_ids
                and v_type != "3d_view"
            ):
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
                    view_type=v_type,
                ))
        if not usable:
            missing.append(character.name)
        references.extend(usable)
    if missing:
        raise RuntimeError("Add a usable reference image for: " + ", ".join(missing))
    return references
