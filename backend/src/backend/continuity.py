"""Build and resolve the persisted visual continuity package for a shot."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cast import load_cast_references, matching_set_element, shot_element_ids
from .models import Character, Project, Scene, Shot
from .screenplay_parser import (
    canonical_character_name,
    expand_character_cue_names,
    infer_voice_description,
)
from .settings import settings
import re

PHONE_CALL_PATTERN = re.compile(
    r"\b(?:phone|cellphone|smartphone|handset|receiver|caller|on the line)\b",
    re.IGNORECASE,
)
OFFSCREEN_SPEAKER_PATTERN = re.compile(
    r"\((?:O\.?S\.?|V\.?O\.?|ON PHONE|OVER PHONE|THROUGH PHONE|FILTERED)\)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _people(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in snapshot.get("characters") or []
            if isinstance(item, dict) and (item.get("kind") or "character") == "character"]


_PROP_TAG_RE = re.compile(r"@([A-Za-z0-9_]+)")
# Prop definition chunks: "@PROP is …" or "@PROP — …" up to the next @tag.
_PROP_DEFINITION_RE = re.compile(
    r"(@[A-Za-z0-9_]+\s*(?:is\b|[—–-])[^@]*)",
    re.IGNORECASE,
)


def _shot_text_for_prop_filter(
    snapshot: dict[str, Any] | None,
    shot: Shot | None,
    video_prompt: str = "",
) -> str:
    """Collect shot-local text used to decide which scene props apply."""
    snapshot = snapshot or {}
    parts = [
        _text(shot.action) if shot is not None else _text(snapshot.get("action")),
        _text(shot.image_prompt) if shot is not None else _text(snapshot.get("image_prompt_base")),
        _text(video_prompt)
        or (_text(shot.video_prompt) if shot is not None else _text(snapshot.get("video_prompt"))),
        _text(getattr(shot, "sound", "")) if shot is not None else _text(snapshot.get("sound")),
        _text(shot.framing) if shot is not None else _text(snapshot.get("framing")),
    ]
    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    for line in dialogue:
        if isinstance(line, dict):
            parts.append(_text(line.get("text") or line.get("line")))
            parts.append(_text(line.get("character") or line.get("speaker")))
    return "\n".join(part for part in parts if part)


def _filter_wardrobe_notes_for_shot(wardrobe_notes: str, characters: list[dict[str, Any]]) -> str:
    """Keep only wardrobe descriptions for characters present in this shot.

    If no characters are declared or if notes are general/unsegmented, returns notes unchanged.
    """
    notes = _text(wardrobe_notes)
    if not notes or not characters:
        return notes
    char_names = [
        _text(c.get("name")).upper().strip()
        for c in characters
        if (c.get("kind") or "character") == "character" and _text(c.get("name")).strip()
    ]
    if not char_names:
        return notes

    protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", notes, flags=re.I)
    sentences = [
        s.replace("__DOT__", ".").strip()
        for s in re.split(r"\n+|(?<=[.])\s+(?=[A-Z@])", protected)
        if s.strip()
    ]
    if len(sentences) <= 1:
        return notes

    kept: list[str] = []
    has_any_match = False
    for sent in sentences:
        mentioned_this_shot = any(
            re.search(r"\b" + re.escape(name) + r"\b", sent.upper())
            for name in char_names
        )
        if mentioned_this_shot:
            kept.append(sent.rstrip(" ."))
            has_any_match = True
        else:
            is_other_char = bool(
                re.match(r"^@?[A-Za-z]+(?:\s+[A-Za-z]+)?\s*(?:wears\b|is\s+wearing\b|in\b|:)", sent)
                or re.search(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,})?\b\s*(?:wears\b|:)", sent)
            )
            if not is_other_char:
                kept.append(sent.rstrip(" ."))

    if not has_any_match:
        return notes
    return ". ".join(kept) + "."


def _filter_props_notes_for_shot(props_notes: str, shot_text: str) -> str:
    """Keep only prop definitions whose @TAG or key object is mentioned in the shot.

    Tagged episode-wide prop bibles filter by @TAG.
    Plain-text prop bibles filter by object keywords matching the shot text.
    """
    notes = _text(props_notes)
    if not notes or not shot_text:
        return notes
    definitions = [seg.strip() for seg in _PROP_DEFINITION_RE.findall(notes) if seg.strip()]
    if definitions:
        shot_tags = {tag.upper() for tag in _PROP_TAG_RE.findall(shot_text or "")}
        if not shot_tags:
            return ""
        kept: list[str] = []
        for segment in definitions:
            match = _PROP_TAG_RE.match(segment)
            if match and match.group(1).upper() in shot_tags:
                kept.append(segment.rstrip(" ."))
        return " ".join(kept)

    segments = [seg.strip(" .,-–—") for seg in re.split(r"[;\n]+", notes) if seg.strip(" .,-–—")]
    if len(segments) <= 1:
        return notes

    shot_lower = shot_text.lower()
    shot_words = set(re.findall(r"\b[a-z]{3,}\b", shot_lower))
    modifiers = {
        "small", "large", "brown", "white", "black", "blue", "cream", "red", "green", "yellow",
        "metal", "plastic", "plain", "three", "two", "one", "courtyard", "side", "unlettered",
        "closed", "enclosed", "exterior", "the", "and", "with", "from", "for", "are", "etc",
        "near", "beside", "behind", "into", "onto", "over", "under", "all", "each", "some",
        "tobi", "amaka", "bamidele", "zara", "mira", "jun", "ben", "maya", "gabe", "rosa",
    }

    kept_plain: list[str] = []
    for seg in segments:
        base = re.split(
            r"\s+(?:centered\s+on|beneath|initially\s+in|clipped\s+to|beside|inside|with|attached\s+to|held\s+by|worn\s+by|hanging\s+from|placed\s+on|is\s+an?)\b",
            seg,
            flags=re.IGNORECASE,
        )[0].strip()
        words = [w for w in re.findall(r"\b[a-z]{3,}\b", base.lower()) if w not in modifiers]
        if any(w in shot_words for w in words):
            kept_plain.append(seg)
        elif not words and any(w in shot_words for w in re.findall(r"\b[a-z]{3,}\b", seg.lower()) if w not in modifiers):
            kept_plain.append(seg)

    if not kept_plain:
        return ""
    return "; ".join(kept_plain) + "."


def _resolve_character_voice(character: Character) -> str:
    explicit = _text(getattr(character, "voice_description", ""))
    if explicit:
        return explicit
    notes = _text(getattr(character, "notes", ""))
    if notes:
        return infer_voice_description(character.name, notes)
    return ""


def _scene_package(scene: Scene) -> dict[str, str]:
    package = {
        "title": _text(scene.title),
        "location": _text(scene.location),
        "time_of_day": _text(scene.time_of_day),
        "set_notes": _text(scene.set_notes),
        "lighting_notes": _text(scene.lighting_notes),
        "wardrobe_notes": _text(scene.wardrobe_notes),
        "props_notes": _text(scene.props_notes),
    }
    reusable_set = matching_set_element(scene.script_version.project.characters, scene.location)
    if reusable_set and reusable_set.notes:
        package["set_notes"] = f"Canonical set layout: {reusable_set.notes.strip()}"
        staging = _text(scene.set_notes)
        if staging and staging != reusable_set.notes.strip():
            package["set_notes"] += f"\nScene staging (preserve the canonical layout): {staging}"
    return package


def naturalize_screenplay_tags(text: str) -> str:
    """Convert raw screenplay tokens like @NKIRU, @NKIRU_SHOP, @RED_CHAIR into natural English."""
    if not text or "@" not in text:
        return text

    # Specific compound tokens first
    text = re.sub(r"@([A-Za-z0-9]+)_(?:SHOP|STORE)S?\b", lambda m: f"{m.group(1).capitalize()}'s shop", text)
    text = re.sub(r"@([A-Za-z0-9]+)_COUNTER\b", lambda m: f"{m.group(1).capitalize()}'s counter", text)
    text = re.sub(r"@LAGOS_SHOPFRONT\b", "Lagos street provision shopfront", text, flags=re.I)
    text = re.sub(r"@RED_CHAIR\b", "red plastic chair", text, flags=re.I)
    text = re.sub(r"@BLUE_CHAIR\b", "blue plastic chair", text, flags=re.I)
    text = re.sub(r"@BISCUIT_PACK\b", "biscuit packet", text, flags=re.I)
    text = re.sub(r"@EXPIRED_BISCUITS\b", "expired biscuit packet", text, flags=re.I)
    text = re.sub(r"@SHOPPING_BAG\b", "transparent shopping bag", text, flags=re.I)
    text = re.sub(r"@NAIRA_NOTES\b", "Nigerian naira banknotes", text, flags=re.I)
    text = re.sub(r"@BREAD_LOAF\b", "packaged loaf of bread", text, flags=re.I)
    text = re.sub(r"@MILK_TIN\b", "tin of powdered milk", text, flags=re.I)
    text = re.sub(r"@PRODUCT_BASKET\b", "plastic provisions basket", text, flags=re.I)
    text = re.sub(r"@BLACK_HANDBAG\b", "black handbag", text, flags=re.I)
    text = re.sub(r"@CUSTOMER_(\d+)\b", r"Customer \1", text, flags=re.I)

    # General fallback for any remaining @TAG:
    def _clean_tag(m: re.Match) -> str:
        tag = m.group(1)
        if "_" in tag:
            words = tag.split("_")
            return " ".join(words).lower()
        return tag.capitalize()

    return re.sub(r"@([A-Za-z0-9_]+)\b", _clean_tag, text)


def _image_prompt(
    base_prompt: str, characters: list[dict[str, Any]], scene: dict[str, str],
    framing: str, action: str,
) -> str:
    prompt = _text(base_prompt)
    constraints: list[str] = []
    if framing:
        constraints.append(f"Shot framing: {framing.rstrip(' .')}.")
    if action:
        constraints.append(f"Visible shot action: {action.rstrip(' .')}.")
    if characters:
        people = [c["name"] for c in characters if (c.get("kind") or "character") == "character"]
        props = [c["name"] for c in characters if (c.get("kind") or "") == "prop"]
        scenes = [c["name"] for c in characters if (c.get("kind") or "") == "scene"]
        # Back-compat: snapshots without kind treat everyone as characters.
        if not people and not props and not scenes:
            people = [c["name"] for c in characters]
        if people:
            constraints.append(
                f"Character identity references: {', '.join(people)}. Preserve each canonical face and identity."
            )
            constraints.append(
                f"Camera coverage framing: Camera is tightly cropped and angled to frame ONLY {', '.join(people)}; all other scene participants and unoccupied location areas remain off-camera outside the frame."
            )
        if props:
            constraints.append(
                f"Prop object references: {', '.join(props)}. Preserve each prop's shape, materials, and colors."
            )
        if scenes:
            constraints.append(
                f"Scene/set references: {', '.join(scenes)}. Preserve architecture, layout and materials; use this scene's lighting instructions."
            )
    shot_text = "\n".join(part for part in (prompt, framing, action) if part)
    for key, label in (
        ("location", "Location"),
        ("time_of_day", "Time of day"),
        ("set_notes", "Set"),
        ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"),
        ("props_notes", "Props"),
    ):
        value = scene[key]
        if key == "wardrobe_notes":
            value = _filter_wardrobe_notes_for_shot(value, characters)
        elif key == "props_notes":
            value = _filter_props_notes_for_shot(value, shot_text)
        if value and value.lower() != "unspecified":
            constraints.append(f"{label}: {value.rstrip(' .')}.")
    compiled = "\n".join(part for part in (prompt, "Continuity constraints:\n" + "\n".join(constraints) if constraints else "") if part)
    return naturalize_screenplay_tags(compiled)


def create_shot_snapshot(
    db: Session,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    character_ids: list[str] | None = None,
    prompt_override: str | None = None,
    *,
    require_cast_references: bool = False,
) -> dict[str, Any]:
    elements = list(db.scalars(select(Character).where(Character.project_id == project.id)))
    selected_ids = shot_element_ids(elements, shot, shot.character_ids if character_ids is None else character_ids)
    refs = load_cast_references(db, project.id, selected_ids, require_all=require_cast_references)
    by_id = {
        character.id: character
        for character in db.scalars(
            select(Character).where(Character.project_id == project.id, Character.id.in_(selected_ids))
        ).all()
    } if selected_ids else {}
    reference_ids_by_character: dict[str, list[str]] = {character_id: [] for character_id in selected_ids}
    names = {character.id: character.name for character in by_id.values()}
    for ref in refs:
        if ref.character_id in reference_ids_by_character:
            reference_ids_by_character[ref.character_id].append(ref.reference_id)
    cast = [
        {
            "id": character_id,
            "name": names[character_id],
            "kind": (getattr(by_id[character_id], "kind", None) or "character") if character_id in by_id else "character",
            "reference_ids": reference_ids_by_character[character_id],
        }
        for character_id in selected_ids
    ]
    character_voices = [
        {
            "id": character_id,
            "name": names[character_id],
            "notes": _text(by_id[character_id].notes) if character_id in by_id else "",
            "voice_description": _resolve_character_voice(by_id[character_id]) if character_id in by_id else "",
        }
        for character_id in selected_ids
        if character_id in by_id and (by_id[character_id].kind or "character") == "character"
    ]
    scene = _scene_package(shot.scene)
    framing = _text(shot.framing)
    action = _text(shot.action)
    base_prompt = _text(prompt_override if prompt_override is not None else shot.image_prompt)
    return {
        "version": 1,
        "character_ids": selected_ids,
        "characters": cast,
        "character_voices": character_voices,
        "dialogue": list(shot.dialogue or []),
        "scene": scene,
        "framing": framing,
        "action": action,
        "video_prompt": _text(shot.video_prompt),
        "aspect_ratio": aspect_ratio,
        "image_prompt_base": base_prompt,
        "effective_image_prompt": _image_prompt(base_prompt, cast, scene, framing, action),
    }


def get_or_create_shot_snapshot(
    db: Session,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    *,
    refresh: bool = False,
    character_ids: list[str] | None = None,
    prompt_override: str | None = None,
    require_cast_references: bool = False,
) -> dict[str, Any]:
    saved = shot.continuity_snapshot
    explicit_changes = character_ids is not None or prompt_override is not None
    if (
        saved and not refresh and not explicit_changes
        and saved.get("aspect_ratio") == aspect_ratio
        and not require_cast_references
    ):
        return saved
    snapshot = create_shot_snapshot(
        db, project, shot, aspect_ratio, character_ids=character_ids,
        prompt_override=prompt_override,
        require_cast_references=require_cast_references,
    )
    shot.character_ids = snapshot["character_ids"]
    shot.continuity_snapshot = snapshot
    return snapshot


def current_shot_snapshot(shot: Shot, aspect_ratio: str) -> dict[str, Any]:
    project = shot.scene.script_version.project
    selected_ids = shot_element_ids(project.characters, shot, shot.character_ids or [])
    by_id = {character.id: character for character in project.characters}
    characters = [
        {
            "id": character_id,
            "name": by_id[character_id].name,
            "kind": getattr(by_id[character_id], "kind", None) or "character",
            "reference_ids": [reference.id for reference in by_id[character_id].references],
        }
        for character_id in selected_ids if character_id in by_id
    ]
    character_voices = [
        {
            "id": character_id,
            "name": by_id[character_id].name,
            "notes": _text(by_id[character_id].notes),
            "voice_description": _resolve_character_voice(by_id[character_id]),
        }
        for character_id in selected_ids if character_id in by_id and (by_id[character_id].kind or "character") == "character"
    ]
    scene = _scene_package(shot.scene)
    return {
        "version": 1, "character_ids": selected_ids, "characters": characters,
        "character_voices": character_voices,
        "dialogue": list(shot.dialogue or []),
        "scene": scene, "framing": _text(shot.framing), "action": _text(shot.action),
        "video_prompt": _text(shot.video_prompt), "aspect_ratio": aspect_ratio,
        "image_prompt_base": _text(shot.image_prompt),
        "effective_image_prompt": _image_prompt(
            _text(shot.image_prompt), characters, scene, _text(shot.framing), _text(shot.action),
        ),
    }


_MOUTH_STILL_PHONE_QUOTE_RE = re.compile(
    r"Keep\s+(?P<listener>[A-Z][a-zA-Z]+)'s\s+(?:mouth|lips)\s+(?:still|closed|shut|motionless)\s+"
    r"while\s+(?P<caller>[^:\n]+?)\s*:\s*(?P<quote>[\"\u201c][^\"\u201d]+[\"\u201d])",
    re.IGNORECASE,
)
_OFFSCREEN_PHONE_CUE_RE = re.compile(
    r"\b(?:filtered\s+(?:phone\s+|handset\s+)?(?:voice|line|audio)|"
    r"handset\s+audio|off-screen\s+phone\s+voice|"
    r"(?:assistant|caller)\s+says\s*,)",
    re.IGNORECASE,
)
# Require an explicit phone/off-screen cue. Optional prefixes used to match plain
# `@GABE says, "..."` on-camera dialogue and rewrite it into a phone call.
_OFFSCREEN_CALLER_QUOTE_RE = re.compile(
    r"(?:"
    r"Over the (?:filtered\s+)?telephone\s+earpiece,\s+(?:unseen\s+caller\s+)?"
    r"|unseen\s+caller\s+"
    r")"
    r"(?P<caller>[A-Z][a-zA-Z0-9_\s\x27-]+?)(?:\x27s\s+[^,\n]+?)?\s+"
    r"(?:says|delivers[^,\n]*),\s*[\"\u201c](?P<quote>[^\"\u201d]+)[\"\u201d]",
    re.IGNORECASE,
)


_EPISODE_TIMECODE_RE = re.compile(r"\b(?P<prep>at|by|until)\s+(?P<min>\d{1,2}):(?P<sec>\d{2})(?![\d:])", re.IGNORECASE)


def _clip_relative_timecodes(video_prompt: str, shot: Shot | None) -> str:
    """Rewrite episode timecodes ("snap to black at 03:00") as clip-relative time; video models read MM:SS as clip time."""
    if shot is None or shot.start_ms is None or not shot.duration_ms:
        return video_prompt
    start_ms = shot.start_ms
    duration_ms = shot.duration_ms

    def replace(match: re.Match[str]) -> str:
        absolute_ms = (int(match.group("min")) * 60 + int(match.group("sec"))) * 1000
        relative_ms = absolute_ms - start_ms
        if relative_ms < 0 or relative_ms > duration_ms:
            return match.group(0)
        prep = match.group("prep")
        if relative_ms >= duration_ms - 500:
            return f"{prep} the very end of the clip"
        return f"{prep} {relative_ms / 1000:g} seconds into the clip"

    return _EPISODE_TIMECODE_RE.sub(replace, video_prompt)


def _sanitize_phone_video_prompt(video_prompt: str) -> str:
    """Rewrite clauses that place '<Listener>'s mouth' directly before an off-screen caller's quote,
    and sanitize off-screen caller quoted speech so video models do not lip-sync the visible actor.

    Only rewrites speech that is already marked as telephone / unseen-caller audio.
    Plain on-camera cues like ``@GABE says, "I'll help."`` must stay untouched.
    """
    text = _text(video_prompt)
    if not text:
        return text

    text = _MOUTH_STILL_PHONE_QUOTE_RE.sub(
        lambda m: (
            f"{m.group('listener').strip()} listens silently with lips firmly closed and jaw motionless. "
            f"Over the filtered telephone earpiece, unseen caller ({m.group('caller').strip()}): {m.group('quote').strip()}"
        ),
        text,
    )

    def _replace_offscreen_quote(match: re.Match[str]) -> str:
        caller = match.group("caller").strip()
        return (
            f"Over the filtered telephone earpiece, unseen caller {caller} speaks while the listener "
            f"remains completely silent with mouth closed and jaw motionless."
        )

    text = _OFFSCREEN_CALLER_QUOTE_RE.sub(_replace_offscreen_quote, text)
    return text



def _on_camera_canon_set(snapshot: dict[str, Any], shot: Shot | None = None) -> set[str]:
    canons: set[str] = set()
    for item in _people(snapshot):
        if isinstance(item, dict) and _text(item.get("name")):
            canon = canonical_character_name(_text(item.get("name")))
            if canon:
                canons.add(canon)
                canons.add(canon.split()[0])
    if not canons and shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            selected = set(shot.character_ids or [])
            for char in project.characters or []:
                if char.id in selected and char.name:
                    canon = canonical_character_name(char.name)
                    if canon:
                        canons.add(canon)
                        canons.add(canon.split()[0])
    return canons


def _detect_offscreen_phone_characters(
    snapshot: dict[str, Any],
    video_prompt: str,
    shot: Shot | None = None,
) -> list[tuple[str, Character | None, str]]:
    """Return (display_name, Character | None, voice_description) for off-screen phone speakers in this shot."""
    action = _text(shot.action if shot is not None else snapshot.get("action"))
    framing = _text(shot.framing if shot is not None else snapshot.get("framing"))
    sound = _text(getattr(shot, "sound", "") if shot is not None else snapshot.get("sound"))
    combined = f"{action}\n{video_prompt}\n{framing}\n{sound}"
    if not PHONE_CALL_PATTERN.search(combined):
        return []

    on_camera_canons = _on_camera_canon_set(snapshot, shot)
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = list(project.characters or [])
    by_name: dict[str, Character] = {}
    for char in project_chars:
        key = canonical_character_name(char.name)
        if key:
            by_name[key] = char
            first = key.split()[0]
            if first not in by_name:
                by_name[first] = char

    found: list[tuple[str, Character | None, str]] = []
    seen: set[str] = set()

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        raw_speaker = _text(line.get("character") or line.get("speaker"))
        if not raw_speaker:
            continue
        canon = canonical_character_name(raw_speaker)
        if not canon or canon in seen:
            continue
        is_offscreen_cue = bool(OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker))
        is_unselected_speaker = bool(
            on_camera_canons
            and canon not in on_camera_canons
            and canon.split()[0] not in on_camera_canons
        )
        if is_offscreen_cue or is_unselected_speaker:
            live_char = by_name.get(canon) or (by_name.get(canon.split()[0]) if canon.split() else None)
            voice_desc = _resolve_character_voice(live_char) if live_char is not None else ""
            seen.add(canon)
            found.append((canon, live_char, voice_desc))

    search_text = f"{action}\n{video_prompt}\n{sound}"
    # Strip quoted dialogue strings so names mentioned within dialogue (e.g. "Isabel, send a car...") are not treated as offscreen speakers
    clean_search = re.sub(r'["\u201c\u201d][^"\u201c\u201d]*["\u201c\u201d]|\'[^\']*\'', ' ', search_text)
    for char in project_chars:
        canon = canonical_character_name(char.name)
        if not canon or canon in seen:
            continue
        first = canon.split()[0]
        if canon in on_camera_canons or first in on_camera_canons:
            continue
        c_pattern = rf"\b(?:{re.escape(canon)}|{re.escape(first)})\b"
        cue_pattern = re.compile(
            rf"(?:"
            rf"{c_pattern}(?:'s)?\s+(?:filtered|phone|handset|earpiece|voice|line|report|speech|audio|call|speaks|says|replies|talking)\b|"
            rf"(?:listens to|hears|answering|on the (?:phone|line|call) with)\s+{c_pattern}\b|"
            rf"(?:filtered|phone|handset|earpiece)\s+(?:voice|line|speech|audio)\s+(?:of|from)\s+{c_pattern}\b|"
            rf"{c_pattern}\s+on the (?:phone|line|other end|call)\b"
            rf")",
            re.IGNORECASE,
        )
        if cue_pattern.search(clean_search):
            seen.add(canon)
            found.append((canon, char, _resolve_character_voice(char)))

    return found


def _collect_voice_entries(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
    video_prompt: str = "",
) -> list[str]:
    characters = _people(snapshot)
    snapshot_voices = {
        str(item.get("id") or item.get("name") or ""): item
        for item in (snapshot.get("character_voices") or [])
        if isinstance(item, dict)
    }
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = list(project.characters or [])
    by_id = {char.id: char for char in project_chars}
    by_name: dict[str, Character] = {}
    for char in project_chars:
        key = canonical_character_name(char.name)
        if key:
            by_name[key] = char
            first = key.split()[0]
            if first not in by_name:
                by_name[first] = char

    offscreen_phone_chars = _detect_offscreen_phone_characters(
        snapshot,
        video_prompt or _text(getattr(shot, "video_prompt", "") if shot is not None else snapshot.get("video_prompt")),
        shot=shot,
    )
    offscreen_canons = {canon for canon, _, _ in offscreen_phone_chars}

    seen_names: set[str] = set()
    entries: list[str] = []

    def add_voice(
        raw_name: str,
        char_id: str | None = None,
        fallback_voice: str = "",
        fallback_notes: str = "",
        is_offscreen_phone: bool = False,
    ) -> None:
        clean_name = _text(raw_name)
        canon = canonical_character_name(clean_name)
        if not canon or canon in seen_names:
            return
        live_char = (by_id.get(char_id) if char_id else None) or by_name.get(canon) or (
            by_name.get(canon.split()[0]) if canon.split() else None
        )
        voice_desc = ""
        if live_char is not None:
            voice_desc = _resolve_character_voice(live_char)
        if not voice_desc:
            voice_desc = _text(fallback_voice)
        if not voice_desc and fallback_notes:
            voice_desc = infer_voice_description(canon, fallback_notes)
        if voice_desc:
            seen_names.add(canon)
            if is_offscreen_phone or canon in offscreen_canons:
                entries.append(
                    f"{canon} (off-screen phone caller, unseen filtered telephone earpiece voice-over only — never lip-sync on-camera face to this voice): {voice_desc.rstrip('.')}"
                )
            else:
                entries.append(f"{clean_name}: {voice_desc.rstrip('.')}")

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    char_by_canon = {
        canonical_character_name(_text(item.get("name"))): item
        for item in characters
        if isinstance(item, dict) and _text(item.get("name"))
    }
    if dialogue:
        for line in dialogue:
            if not isinstance(line, dict):
                continue
            raw_speaker = _text(line.get("character") or line.get("speaker"))
            if not raw_speaker:
                continue
            speaker_parts = expand_character_cue_names(raw_speaker)
            for speaker in (speaker_parts if len(speaker_parts) > 1 else [raw_speaker]):
                canon = canonical_character_name(speaker)
                item = char_by_canon.get(canon) or {}
                char_id = _text(item.get("id"))
                sv = snapshot_voices.get(char_id) or snapshot_voices.get(canon) or snapshot_voices.get(speaker) or {}
                add_voice(
                    speaker,
                    char_id=char_id or None,
                    fallback_voice=_text(item.get("voice_description") or sv.get("voice_description")),
                    fallback_notes=_text(item.get("notes") or sv.get("notes")),
                    is_offscreen_phone=bool(OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker)),
                )
    elif not re.search(r'\b(?:no\s+(?:spoken\s+words|speech|dialogue|voice|audible)|complete\s+silence|without\s+saying\s+any\s+words)\b', video_prompt, re.IGNORECASE):
        for item in characters:
            if not isinstance(item, dict):
                continue
            char_id = _text(item.get("id"))
            name = _text(item.get("name"))
            sv = snapshot_voices.get(char_id) or snapshot_voices.get(name) or {}
            add_voice(
                name,
                char_id=char_id or None,
                fallback_voice=_text(item.get("voice_description") or sv.get("voice_description")),
                fallback_notes=_text(item.get("notes") or sv.get("notes")),
            )

    for canon, live_char, voice_desc in offscreen_phone_chars:
        if canon not in seen_names:
            add_voice(
                canon,
                char_id=live_char.id if live_char is not None else None,
                fallback_voice=voice_desc,
                fallback_notes=_text(live_char.notes) if live_char is not None else "",
                is_offscreen_phone=True,
            )

    return entries


def _phone_routing_instruction(
    snapshot: dict[str, Any],
    video_prompt: str,
    shot: Shot | None = None,
) -> str:
    action = _text(shot.action if shot is not None else snapshot.get("action"))
    framing = _text(shot.framing if shot is not None else snapshot.get("framing"))
    sound = _text(getattr(shot, "sound", "") if shot is not None else snapshot.get("sound"))
    combined = f"{action}\n{video_prompt}\n{framing}\n{sound}"
    if not PHONE_CALL_PATTERN.search(combined):
        return ""

    on_camera_canons = _on_camera_canon_set(snapshot, shot)
    offscreen_phone_chars = _detect_offscreen_phone_characters(snapshot, video_prompt, shot=shot)
    offscreen_canons = {canon for canon, _, _ in offscreen_phone_chars}

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    on_camera_speakers: list[str] = []
    on_camera_lines: list[str] = []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        raw_speaker = _text(line.get("character") or line.get("speaker"))
        text = _text(line.get("text") or line.get("line"))
        if not raw_speaker or not text:
            continue
        if OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker):
            continue
        canon = canonical_character_name(raw_speaker)
        if canon in offscreen_canons or (
            on_camera_canons
            and canon not in on_camera_canons
            and canon.split()[0] not in on_camera_canons
        ):
            continue
        if canon and canon not in on_camera_speakers:
            on_camera_speakers.append(canon)
        if text not in on_camera_lines:
            on_camera_lines.append(text)

    if not on_camera_speakers and re.search(r'\bspeaks\s*:|["\u201c][^"\u201d]+["\u201d]', video_prompt, re.IGNORECASE):
        for item in _people(snapshot):
            if isinstance(item, dict) and _text(item.get("name")):
                on_camera_speakers.append(_text(item.get("name")))

    if not on_camera_speakers:
        return ""

    speakers_label = ", ".join(on_camera_speakers)
    base_routing = (
        f"Voice routing: {speakers_label} speaks on camera with their natural, direct, unfiltered chest voice "
        "in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech."
    )

    raw_on_camera_names: list[str] = []
    for item in _people(snapshot):
        if isinstance(item, dict) and _text(item.get("name")):
            name = canonical_character_name(_text(item.get("name")))
            if name and name not in raw_on_camera_names:
                raw_on_camera_names.append(name)
    if not raw_on_camera_names and shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            selected = set(shot.character_ids or [])
            for char in project.characters or []:
                if char.id in selected and char.name:
                    name = canonical_character_name(char.name)
                    if name and name not in raw_on_camera_names:
                        raw_on_camera_names.append(name)

    silent_on_camera = [
        name for name in raw_on_camera_names
        if name not in on_camera_speakers and name.split()[0] not in on_camera_speakers
    ]
    silent_clause = (
        f" Silent character lock: {', '.join(silent_on_camera)}'s lips and jaw MUST remain completely closed and motionless at all times — do NOT lip-sync {', '.join(silent_on_camera)} to any speech or audio."
        if silent_on_camera
        else ""
    )

    has_offscreen_phone_speech = bool(
        offscreen_phone_chars or _OFFSCREEN_PHONE_CUE_RE.search(f"{video_prompt}\n{sound}")
    )
    if not has_offscreen_phone_speech:
        return f"{base_routing}{silent_clause}"

    offscreen_label = (
        ", ".join(canon for canon, _, _ in offscreen_phone_chars)
        if offscreen_phone_chars
        else "the off-screen phone caller"
    )
    quoted_lines = ", ".join(f'"{line}"' for line in on_camera_lines)
    lines_clause = f" ({quoted_lines})" if quoted_lines else ""
    return (
        f"{base_routing} "
        f"Lip-sync lock: {offscreen_label} is an unseen off-screen telephone caller heard ONLY as filtered, "
        f"band-limited earpiece voice-over — {speakers_label}'s lips and jaw MUST remain completely closed and "
        f"motionless in silent listening while {offscreen_label}'s phone line plays, and {speakers_label}'s mouth "
        f"must move ONLY when speaking {speakers_label}'s own on-camera dialogue{lines_clause}.{silent_clause}"
    )


_SCREEN_POSITION_RE = re.compile(r"\bscreen[-\s]?(left|right|center|centre)\b", re.IGNORECASE)


def _speaker_position_lock(snapshot: dict[str, Any], shot: Shot | None = None) -> str:
    """Pin each on-camera dialogue line to the screen position its speaker holds in the frame."""
    names = [
        canonical_character_name(_text(item.get("name")))
        for item in _people(snapshot)
        if isinstance(item, dict) and _text(item.get("name"))
    ]
    names = [name for name in dict.fromkeys(names) if name]
    if len(names) < 2:
        return ""
    name_patterns = {
        name: re.compile(r"@?\b" + r"[\s_]".join(map(re.escape, name.split())) + r"\b", re.IGNORECASE)
        for name in names
    }
    sources = [
        _text(shot.framing if shot is not None else snapshot.get("framing")),
        _text(shot.action if shot is not None else snapshot.get("action")),
        _text(getattr(shot, "image_prompt", "") if shot is not None else snapshot.get("image_prompt")),
    ]
    positions: dict[str, str] = {}
    for source in sources:
        for match in _SCREEN_POSITION_RE.finditer(source):
            window_start = max(0, match.start() - 90)
            window = source[window_start:match.start()]
            protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1 ", window, flags=re.I)
            clause = re.split(r"[.;!?]", protected)[-1]
            nearest: tuple[int, str] | None = None
            for name, pattern in name_patterns.items():
                plain_name = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", name, flags=re.I)
                plain_pattern = re.compile(
                    r"@?\b" + r"[\s_]".join(map(re.escape, plain_name.split())) + r"\b",
                    re.IGNORECASE,
                )
                for name_match in (*pattern.finditer(window), *plain_pattern.finditer(clause)):
                    if nearest is None or name_match.start() > nearest[0]:
                        nearest = (name_match.start(), name)
            if nearest is None:
                continue
            side = match.group(1).lower().replace("centre", "center")
            positions.setdefault(nearest[1], f"screen-{side}")
    if len(set(positions.values())) < 2:
        return ""

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    entries: list[str] = []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        speaker = canonical_character_name(_text(line.get("character") or line.get("speaker")))
        text = _text(line.get("text") or line.get("line"))
        position = positions.get(speaker)
        if not position or not text:
            continue
        entries.append(f'{speaker} on {position} speaks "{text}"')
    if not entries:
        return ""
    return (
        f"Speaker lock: {'; '.join(entries)}. Each line is lip-synced only by the character at that screen position; "
        "whoever is not speaking keeps their lips closed while listening."
    )


def _collect_visual_profiles(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
) -> list[str]:
    characters = _people(snapshot)
    snapshot_voices = {
        str(item.get("id") or item.get("name") or ""): item
        for item in (snapshot.get("character_voices") or [])
        if isinstance(item, dict)
    }
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = list(project.characters or [])
    by_id = {char.id: char for char in project_chars}
    by_name = {canonical_character_name(char.name): char for char in project_chars if char.name}

    profiles: list[str] = []
    seen: set[str] = set()
    for item in characters:
        if not isinstance(item, dict):
            continue
        char_id = _text(item.get("id"))
        name = _text(item.get("name"))
        canon = canonical_character_name(name)
        if not canon or canon in seen:
            continue
        live_char = (by_id.get(char_id) if char_id else None) or by_name.get(canon)
        sv = snapshot_voices.get(char_id) or snapshot_voices.get(name) or {}
        notes = _text((live_char.notes if live_char is not None else "") or item.get("notes") or sv.get("notes"))
        if not notes:
            continue
        # Keep the first two sentences (physical identity + wardrobe) so plot notes don't clutter the video prompt.
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", notes) if part.strip()]
        visual_summary = " ".join(sentences[:2]).rstrip(".")
        if visual_summary:
            seen.add(canon)
            profiles.append(f"{name}: {visual_summary}")
    return profiles


def video_continuity_prompt(
    snapshot: dict[str, Any] | None,
    video_prompt: str,
    aspect_ratio: str,
    *,
    shot: Shot | None = None,
) -> str:
    sanitized_video_prompt = _clip_relative_timecodes(_sanitize_phone_video_prompt(video_prompt), shot)
    anchor = (
        "Approved storyboard frame: use the supplied image as the exact opening frame and visual anchor; "
        "preserve its composition, faces, wardrobe, set, and lighting throughout the clip, "
        "moving or removing props only as explicitly performed in the shot action. "
        "No on-screen text, subtitles, UI elements, or location tags."
    )
    if not snapshot:
        return "\n\n".join(part for part in (sanitized_video_prompt.strip(), anchor) if part)
    scene = snapshot.get("scene") or {}
    characters = _people(snapshot)
    cast_names = ", ".join(item.get("name", "") for item in characters if item.get("name"))
    live_framing = _text(shot.framing) if shot is not None else ""
    live_action = _text(shot.action) if shot is not None else ""
    package = [
        anchor,
        f"Shot framing: {live_framing or snapshot.get('framing') or 'preserve the supplied frame'}. Aspect ratio: {aspect_ratio}.",
        f"Shot action: {live_action or snapshot.get('action') or 'continue the action naturally from the supplied frame'}.",
    ]
    if cast_names:
        package.append(
            f"Character identity continuity: {cast_names}; preserve canonical identities without face, age, hair, body, or voice changes."
        )
    visual_profiles = _collect_visual_profiles(snapshot, shot=shot)
    if visual_profiles:
        package.append(f"Character visual profiles: {'; '.join(visual_profiles)}.")
    voice_entries = _collect_voice_entries(snapshot, shot=shot, video_prompt=sanitized_video_prompt)
    if voice_entries:
        package.append(f"Character voices: {'; '.join(voice_entries)}.")
    speaker_lock = _speaker_position_lock(snapshot, shot=shot)
    if speaker_lock:
        package.append(speaker_lock)
    phone_routing = _phone_routing_instruction(snapshot, sanitized_video_prompt, shot=shot)
    if phone_routing:
        package.append(phone_routing)
    shot_text = _shot_text_for_prop_filter(snapshot, shot, sanitized_video_prompt)
    for key, label in (
        ("location", "Location"), ("time_of_day", "Time of day"),
        ("set_notes", "Set"), ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"), ("props_notes", "Props"),
    ):
        value = _text(scene.get(key))
        if key == "wardrobe_notes":
            value = _filter_wardrobe_notes_for_shot(value, snapshot.get("characters", []))
        elif key == "props_notes":
            value = _filter_props_notes_for_shot(value, shot_text)
        if value and value.lower() != "unspecified":
            clean_value = re.sub(
                r"@([A-Z0-9_]+)",
                lambda m: m.group(1).replace("_", " ").title(),
                value,
            )
            package.append(f"{label} continuity: {clean_value}.")
    return "\n\n".join([sanitized_video_prompt.strip(), *package]).strip()


def cast_continuity_warnings(shot: Shot) -> list[str]:
    """Flag unexplained omissions; never silently add people to a composition."""
    people = {c.id: c.name for c in shot.scene.script_version.project.characters if c.kind == "character"}
    established: set[str] = set()
    for previous in sorted(shot.scene.shots, key=lambda item: item.order_index):
        if previous.order_index >= shot.order_index:
            break
        established.update(set(previous.character_ids or []) & people.keys())
        for character_id in list(established):
            name = re.escape(people[character_id])
            if re.search(rf"\b{name}\s+(?:exits|leaves|walks out|steps out)\b", previous.action or "", re.I):
                established.remove(character_id)
    text = "\n".join([shot.framing or "", shot.action or "", shot.image_prompt or "", shot.video_prompt or ""])
    protected_text = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", text, flags=re.I)
    unexplained = []
    for character_id in sorted(established - set(shot.character_ids or []), key=lambda item: people[item]):
        name = people[character_id]
        plain_name = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", name, flags=re.I)
        if re.search(
            rf"\b(?:{re.escape(name)}|{re.escape(plain_name)})\b[^.!?\n]{{0,100}}\b(?:off[- ]camera|off[- ]screen|outside (?:the )?frame|exits|leaves)\b",
            protected_text,
            re.I,
        ):
            continue
        unexplained.append(name)
    if not unexplained:
        return []
    return [f"Earlier in this scene: {', '.join(unexplained)}. They are not tagged here. Include them in the cast and composition, or explicitly describe their exit or off-camera position."]


def shot_continuity_status(shot: Shot) -> dict[str, Any]:
    snapshot = shot.continuity_snapshot
    scene = snapshot.get("scene") if snapshot else _scene_package(shot.scene)
    cast = snapshot.get("characters", []) if snapshot else []
    if not snapshot:
        project = shot.scene.script_version.project
        selected_ids = list(shot.character_ids or [])
        selected = set(selected_ids)
        cast = [
            {"id": character.id, "name": character.name, "reference_ids": [ref.id for ref in character.references]}
            for character in project.characters if character.id in selected
        ]
        known_ids = {item["id"] for item in cast}
        cast.extend({"id": item, "name": "Unknown character", "reference_ids": []}
                    for item in selected_ids if item not in known_ids)
    missing_names: list[str] = []
    characters_status: list[dict[str, Any]] = []
    for character in cast:
        character_id = character.get("id")
        reference_ids = list(character.get("reference_ids") or [])
        available_ids: list[str] = []
        if character_id:
            refs = shot.scene.script_version.project.characters
            owner = next((item for item in refs if item.id == character_id), None)
            if owner:
                for ref in owner.references:
                    if (not reference_ids or ref.id in reference_ids) and (settings.media_dir / ref.filename).is_file():
                        available_ids.append(ref.id)
        has_reference = bool(available_ids)
        if not has_reference:
            missing_names.append(str(character.get("name") or "Unknown character"))
        characters_status.append({
            "character_id": str(character_id or ""),
            "name": str(character.get("name") or "Unknown character"),
            "has_reference": has_reference,
            "reference_ids": available_ids,
        })
    missing_fields: list[str] = []
    for key in ("location", "time_of_day", "set_notes", "lighting_notes", "wardrobe_notes", "props_notes"):
        value = _text((scene or {}).get(key))
        if not value or (key == "location" and value.lower() == "unspecified"):
            missing_fields.append(key)
    return {
        "snapshot": snapshot,
        "characters": characters_status,
        "missing_character_references": missing_names,
        "missing_fields": missing_fields,
        "ready": not missing_names,
        "cast_warnings": cast_continuity_warnings(shot),
    }
