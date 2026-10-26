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
from .models import Character, CharacterReference, Project, Scene, ScriptVersion, Shot
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


def element_mention_slug(name: str) -> str:
    """Derive the @MENTION slug shown in the UI / matched in shot prompts."""
    raw = (name or "").strip().lstrip("@")
    # Title is the left side of an em-dash description: "BISCUIT PACK — bright red…"
    title = re.split(r"\s*[—–]\s*", raw, maxsplit=1)[0].strip()
    key = canonical_character_name(title) or ""
    key = key.lstrip("@")
    slug = re.sub(r"[^A-Z0-9]+", "_", key).strip("_")
    return slug


def location_key(location: str) -> str:
    """Same physical set across screenplay headings, @tags and time-of-day changes."""
    name = re.sub(r"^(?:INT\.?/EXT\.?|INT\.?|EXT\.?)\s+", "", (location or "").strip(), flags=re.I)
    name = re.sub(r"\s*[-–—]\s*(?:DAY|NIGHT|MORNING|AFTERNOON|EVENING|DAWN|DUSK|CONTINUOUS|LATER|SAME(?:\s+TIME)?).*$", "", name, flags=re.I)
    key = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    return "" if key in {"", "UNSPECIFIED", "NONE", "N A"} else key


def matching_set_element(elements: Iterable[Character], location: str) -> Character | None:
    key = location_key(location)
    matches = [element for element in elements if element.kind == "scene" and key and location_key(element.name) == key]
    # Preserve existing references when a legacy project already has naming variants.
    return next((element for element in matches if element.references), matches[0] if matches else None)


def shot_element_ids(elements: Iterable[Character], shot: Shot, selected_ids: Iterable[str]) -> list[str]:
    """Cast/props plus the current location, never a stale set from another room."""
    elements = list(elements)
    scene_ids = {element.id for element in elements if element.kind == "scene"}
    ids = [item for item in dict.fromkeys(selected_ids) if item not in scene_ids]
    location = matching_set_element(elements, shot.scene.location)
    if location:
        ids.append(location.id)
    return ids


def prompt_element_title(name: str, *, fallback: str = "ELEMENT") -> str:
    """Human-readable element title for provider prompts (never a raw @tag)."""
    slug = element_mention_slug(name)
    if not slug:
        return fallback
    return re.sub(r"\s+", " ", slug.replace("_", " ")).strip() or fallback


def prompt_element_details(notes: str, title: str, *, kind: str) -> str:
    """Notes for provider prompts with leading @SLUG — prefixes removed."""
    desc = (notes or "").strip()
    if desc:
        desc = re.sub(
            r"^@?[A-Z0-9_]+\s*[:—–-]\s*",
            "",
            desc,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        desc = re.sub(
            r"(?:speaking\s+)?voice(?:\s+profile)?\s*:\s*[^\n]+",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip(" .,-–—")
    if not desc:
        if kind == "prop":
            return f"Hero prop object named {title}."
        if kind == "scene":
            return f"Cinematic location and set environment for {title}."
        return f"Named {title}."
    return desc


def cast_portrait_prompt(
    character: Character,
    *,
    look: str | None = None,
    has_attached_reference: bool | None = None,
) -> str:
    """Build a locked identity-sheet, 3D turnaround sheet, scene, or prop prompt."""
    notes = (character.notes or "").strip()
    name = prompt_element_title(character.name, fallback="CHARACTER")
    kind = (getattr(character, "kind", None) or "character").strip().lower()
    look_key = (look or "").strip().lower()
    has_refs = (
        bool(has_attached_reference)
        if has_attached_reference is not None
        else bool(getattr(character, "references", None))
    )

    if kind == "prop" or look_key == "prop":
        desc = prompt_element_details(notes, name, kind="prop")
        ref_note = (
            "The attached image is the prop identity reference. "
            "Preserve its exact shape, proportions, materials, colors, and surface details from that image. "
            if has_refs
            else ""
        )
        return (
            f"Isolated studio prop reference photograph of {name} centered on a clean pure white studio background. "
            f"{ref_note}"
            "Sharp focus on the object, accurate materials, surface textures, and proportions, even studio lighting, "
            f"photoreal cinematic quality. Prop details: {desc.rstrip('.')}. "
            "No people, no hands, no watermark, no UI chrome. "
            "Avoid readable brand logos unless they are essential to recognizing this prop."
        )

    if kind == "scene" or look_key == "scene":
        desc = prompt_element_details(notes, name, kind="scene")
        ref_note = (
            "The attached image is the scene/set identity reference. "
            "Preserve its architecture, layout, key furniture and materials from that image. "
            "Scene lighting may change with the time of day. "
            if has_refs
            else ""
        )
        return (
            f"Cinematic location and set environment reference photograph for {name}. "
            f"{ref_note}"
            "Wide architectural establishing view showing the room or outdoor layout, key furniture, props, textures, "
            f"and lighting atmosphere. Location details: {desc.rstrip('.')}. "
            "No people in frame, no watermark, no UI chrome, no logos burned into the frame."
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
    # Characters keep the stored canonical name for dialogue identity.
    name = canonical_character_name(character.name) or "CHARACTER"
    name = name.lstrip("@") or "CHARACTER"
    clean_notes = re.sub(
        r"(?:speaking\s+)?voice(?:\s+profile)?\s*:\s*[^\n]+",
        "",
        notes or "",
        flags=re.IGNORECASE,
    ).strip(" .,-–—")
    appearance = clean_notes or f"Adult character named {name}."
    adult_clause = "" if is_minor_or_mascot else "All people are adults age 18 or older. "

    ref_note = (
        "The attached image is an identity and facial likeness reference. "
        f"Preserve {name}'s exact facial structure, eye shape, nose, mouth, skin tone, and bone structure from the reference image. "
        "You are free to change and must modify the hairstyle, clothing, and accessories to match the appearance notes below. "
        if has_refs
        else ""
    )

    if look_key in {"3d_view", "turnaround", "3d"}:
        return (
            f"Character 3D turnaround reference sheet for {name} in 16:9 horizontal layout on a clean pure white studio background. "
            f"{ref_note}"
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
        f"{ref_note}"
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
        framing = shot.framing or ""
    else:
        dialogue = shot.get("dialogue") or []
        action = str(shot.get("action") or "")
        image_prompt = str(shot.get("image_prompt") or "")
        video_prompt = str(shot.get("video_prompt") or "")
        framing = str(shot.get("framing") or "")

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
            QUOTED_SPEECH_RE.sub(" ", framing),
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
        slug = element_mention_slug(char.name)
        if not slug:
            continue
        key = canonical_character_name(char.name.lstrip("@") if char.name else "") or slug.replace("_", " ")
        candidates = [slug, key, key.replace(" ", "_")]
        parts = [p for p in key.replace("_", " ").split() if p]
        if len(parts) > 1:
            candidates.append(parts[0])
        seen_cands: set[str] = set()
        for cand in candidates:
            cand = (cand or "").strip().upper().lstrip("@")
            if not cand or cand in seen_cands:
                continue
            seen_cands.add(cand)
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
        pattern = rf"(?<![A-Z0-9_]){re.escape(key)}(?![A-Z0-9_])"
        first_pattern = (
            rf"(?<![A-Z0-9_]){re.escape(first_word)}(?![A-Z0-9_])" if first_word else None
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
        if (getattr(item, "kind", "character") or "character") == "character":
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

    # Each episode's shots must take their Cast: lines from that episode's own script.
    explicit_cast_cache: dict[str, tuple[dict[int, list[str]], dict[str, list[str]]]] = {}

    def explicit_cast_lookup(version: ScriptVersion) -> tuple[dict[int, list[str]], dict[str, list[str]]]:
        if version.id in explicit_cast_cache:
            return explicit_cast_cache[version.id]
        by_idx: dict[int, list[str]] = {}
        by_title: dict[str, list[str]] = {}
        text = version.source_text or source_text or ""
        if text:
            try:
                parsed = parse_structured_screenplay(text)
                if parsed and parsed.get("scenes"):
                    all_parsed_shots = [sh for sc in parsed["scenes"] for sh in sc.get("shots", [])]
                    for idx, ps in enumerate(all_parsed_shots):
                        if ps.get("explicit_cast") is not None:
                            by_idx[idx] = ps["explicit_cast"]
                            if ps.get("title"):
                                by_title[ps["title"].strip().upper()] = ps["explicit_cast"]
            except Exception:
                pass
        explicit_cast_cache[version.id] = (by_idx, by_title)
        return by_idx, by_title

    shot_idx_by_version: dict[str, int] = {}
    for scene in _latest_version_scenes(project, db):
        version = scene.script_version
        explicit_cast_by_shot_idx, explicit_cast_by_shot_title = explicit_cast_lookup(version)
        for shot in scene.shots:
            shot_idx = shot_idx_by_version.get(version.id, 0)
            shot_idx_by_version[version.id] = shot_idx + 1
            dialogue_names, haystack = shot_mentioned_names(shot)
            for name in dialogue_names:
                _ensure_character(db, project.id, by_name, name)

            explicit = None
            if getattr(shot, "explicit_cast", None) is not None:
                explicit = shot.explicit_cast
            elif shot.title and shot.title.strip().upper() in explicit_cast_by_shot_title:
                explicit = explicit_cast_by_shot_title[shot.title.strip().upper()]
            elif shot_idx in explicit_cast_by_shot_idx:
                explicit = explicit_cast_by_shot_idx[shot_idx]

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

    sync_project_elements(project, db)
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
        loc_key = location_key(scene.location)
        if loc_key and len(loc_key) > 80:
            loc_key = re.split(r"[,\n;–—.]", loc_key)[0].strip()
            loc_key = loc_key[:60].strip()
        if loc_key and len(loc_key) >= 3:
            set_desc = (scene.set_notes or "").strip()
            existing_set = matching_set_element(by_name.values(), scene.location)
            if existing_set:
                if set_desc and not existing_set.notes:
                    existing_set.notes = set_desc
            elif loc_key not in by_name:
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

        raw_props = (scene.props_notes or "").strip()
        if raw_props:
            existing_prop_notes = {
                re.sub(r"\W+", " ", (item.notes or "").lower()).strip()
                for item in by_name.values()
                if getattr(item, "kind", None) == "prop" and (item.notes or "").strip()
            }
            def _norm_prop_name(text: str) -> str:
                unprefixed = re.sub(r"^(?:THE|A|AN)\s+", "", text.strip(), flags=re.IGNORECASE)
                return canonical_character_name(unprefixed).strip(" :;,.-–—")

            existing_prop_norm_names = {
                _norm_prop_name(k) for k in by_name.keys()
            }
            segments = [
                seg.strip(" .,-–—")
                for seg in re.split(r"[;\n]+|\.\s+(?=[A-Z@])|,\s+(?=[A-Z0-9@])", raw_props)
                if seg.strip(" .,-–—")
            ]
            for seg in segments[:12]:
                if re.search(
                    r"\b(?:blank\s+or\s+illegible|no\s+generated\s+lettering|separate\s+from\s+the\s+tank)\b",
                    seg,
                    flags=re.IGNORECASE,
                ):
                    continue
                # Skip negative statements (e.g. "No recurring props in this space")
                if re.match(
                    r"^\s*(?:no\s+recurring|no\s+props?|no\s+specific\s+props?|none\b|nothing\b|there\s+are\s+no)\b",
                    seg,
                    flags=re.IGNORECASE,
                ):
                    continue
                # Skip action/stage directions that are not prop definitions
                if re.search(
                    r"\b(?:stays?\s+(?:outside|inside|in|on|here)|move(?:s)?\s+only|specif(?:y|ies)\b|appears?\s+only|dresses\s+the\s+table|open/closed)\b",
                    seg,
                    flags=re.IGNORECASE,
                ):
                    continue

                # "@BISCUIT_PACK — bright red sealed packet" or "BLUE FOLDER — closed blue folder..."
                # Also handles colons: "THREE WHITE PLASTIC CHAIRS: three white molded plastic armchairs"
                mention = re.match(
                    r"^@?([A-Za-z0-9_'\s]{2,40}?)\s*[:—–-]\s*(.+)$",
                    seg.strip(),
                ) or re.match(
                    r"^@([A-Za-z0-9_]+)\s+(?:is|are|has|consists\s+of)\s+(.+)$",
                    seg.strip(),
                    flags=re.IGNORECASE,
                ) or re.match(
                    r"^@([A-Za-z0-9_]+)\s+(.+)$",
                    seg.strip(),
                )
                if mention:
                    prop_key = canonical_character_name(mention.group(1).replace("_", " ")).strip(" :;,.-–—")
                    prop_notes = mention.group(2).strip()
                else:
                    cleaned = seg.lstrip("@").strip()
                    base_title = re.split(
                        r"\s+(?:centered\s+on|beneath|initially\s+in|clipped\s+to|beside|inside|with|attached\s+to|held\s+by|worn\s+by|hanging\s+from|placed\s+on|is\s+an?)\b",
                        cleaned,
                        flags=re.IGNORECASE,
                    )[0].strip()
                    words = base_title.split()
                    # Only treat as prop if it's a clean noun phrase (at most 4 words) without action verbs
                    if len(words) > 4 or re.search(r"\b(?:is|are|was|were|has|have|had|will|should|stays?|moves?|takes?|gives?)\b", base_title, flags=re.IGNORECASE):
                        continue
                    prop_key = canonical_character_name(base_title).strip(" :;,.-–—")
                    prop_notes = cleaned

                norm_key = _norm_prop_name(prop_key)
                norm_notes = re.sub(r"\W+", " ", prop_notes.lower()).strip()
                if (
                    not prop_key
                    or len(prop_key) < 3
                    or prop_key in by_name
                    or norm_key in existing_prop_norm_names
                    or (norm_notes and norm_notes in existing_prop_notes)
                ):
                    continue
                prop_elem = Character(
                    project_id=project.id,
                    kind="prop",
                    name=prop_key,
                    notes=prop_notes,
                    voice_description="",
                )
                db.add(prop_elem)
                db.flush()
                by_name[prop_key] = prop_elem
                existing_prop_norm_names.add(norm_key)
                if norm_notes:
                    existing_prop_notes.add(norm_notes)

    db.flush()
    return list({char.id: char for char in by_name.values()}.values())


def load_cast_references(
    db: Session, project_id: str, character_ids: Iterable[str],
    reference_ids: Iterable[str] | None = None,
    *,
    require_all: bool = True,
    max_per_character: int | None = None,
    prefer_main_portrait: bool = False,
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
        kind = (getattr(character, "kind", None) or "character").strip().lower()

        def _ref_rank(ref: CharacterReference) -> tuple[int, float]:
            v_type = getattr(ref, "view_type", "main") or "main"
            if kind == "prop":
                return (0 if v_type == "prop" else 1, ref.created_at.timestamp() if ref.created_at else 0.0)
            if kind == "scene":
                return (0 if v_type == "scene" else 1, ref.created_at.timestamp() if ref.created_at else 0.0)
            if prefer_main_portrait:
                return (0 if v_type == "main" else 1, ref.created_at.timestamp() if ref.created_at else 0.0)
            # Characters: prefer 3D turnaround sheets when present.
            return (0 if v_type == "3d_view" else 1, ref.created_at.timestamp() if ref.created_at else 0.0)

        ordered_refs = sorted(character.references, key=_ref_rank)
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
        if not usable and kind in ("character", "scene"):
            missing.append(character.name)
        if max_per_character and max_per_character > 0:
            usable = usable[:max_per_character]
        references.extend(usable)
    if missing and require_all:
        raise RuntimeError("Add a usable reference image for: " + ", ".join(missing))
    return references
