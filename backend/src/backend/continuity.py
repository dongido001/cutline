"""Build and resolve the persisted visual continuity package for a shot."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cast import load_cast_references
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


def _resolve_character_voice(character: Character) -> str:
    explicit = _text(getattr(character, "voice_description", ""))
    if explicit:
        return explicit
    notes = _text(getattr(character, "notes", ""))
    if notes:
        return infer_voice_description(character.name, notes)
    return ""


def _scene_package(scene: Scene) -> dict[str, str]:
    return {
        "title": _text(scene.title),
        "location": _text(scene.location),
        "time_of_day": _text(scene.time_of_day),
        "set_notes": _text(scene.set_notes),
        "lighting_notes": _text(scene.lighting_notes),
        "wardrobe_notes": _text(scene.wardrobe_notes),
        "props_notes": _text(scene.props_notes),
    }


def _image_prompt(
    base_prompt: str, characters: list[dict[str, Any]], scene: dict[str, str],
    framing: str, action: str,
) -> str:
    prompt = _text(base_prompt)
    constraints: list[str] = []
    if framing:
        constraints.append(f"Shot framing: {framing}.")
    if action:
        constraints.append(f"Visible shot action: {action}.")
    if characters:
        names = ", ".join(character["name"] for character in characters)
        constraints.append(f"Character identity references: {names}. Preserve each canonical face and identity.")
    for key, label in (
        ("location", "Location"),
        ("time_of_day", "Time of day"),
        ("set_notes", "Set"),
        ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"),
        ("props_notes", "Props"),
    ):
        value = scene[key]
        if value and value.lower() != "unspecified":
            constraints.append(f"{label}: {value}.")
    return "\n".join(part for part in (prompt, "Continuity constraints:\n" + "\n".join(constraints) if constraints else "") if part)


def create_shot_snapshot(
    db: Session,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    character_ids: list[str] | None = None,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    selected_ids = list(dict.fromkeys(shot.character_ids if character_ids is None else character_ids))
    refs = load_cast_references(db, project.id, selected_ids)
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
        if character_id in by_id
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
) -> dict[str, Any]:
    saved = shot.continuity_snapshot
    explicit_changes = character_ids is not None or prompt_override is not None
    if (
        saved and not refresh and not explicit_changes
        and saved.get("aspect_ratio") == aspect_ratio
    ):
        return saved
    snapshot = create_shot_snapshot(
        db, project, shot, aspect_ratio, character_ids=character_ids,
        prompt_override=prompt_override,
    )
    shot.character_ids = snapshot["character_ids"]
    shot.continuity_snapshot = snapshot
    return snapshot


def current_shot_snapshot(shot: Shot, aspect_ratio: str) -> dict[str, Any]:
    project = shot.scene.script_version.project
    selected_ids = list(shot.character_ids or [])
    by_id = {character.id: character for character in project.characters}
    characters = [
        {
            "id": character_id,
            "name": by_id[character_id].name,
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
        for character_id in selected_ids if character_id in by_id
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
_OFFSCREEN_CALLER_QUOTE_RE = re.compile(
    r"(?:Over the (?:filtered\s+)?telephone\s+earpiece,\s+)?(?:unseen\s+caller\s+)?(?P<caller>[A-Z][a-zA-Z0-9_\s\x27-]+?)(?:\x27s\s+[^,\n]+?)?\s+(?:says|delivers[^,\n]*),\s*[\"\u201c](?P<quote>[^\"\u201d]+)[\"\u201d]",
    re.IGNORECASE,
)


def _sanitize_phone_video_prompt(video_prompt: str) -> str:
    """Rewrite clauses that place '<Listener>'s mouth' directly before an off-screen caller's quote,
    and sanitize off-screen caller quoted speech so video models do not lip-sync the visible actor."""
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
        return f"Over the filtered telephone earpiece, unseen caller {caller} speaks while the listener remains completely silent with mouth closed and jaw motionless."

    text = _OFFSCREEN_CALLER_QUOTE_RE.sub(_replace_offscreen_quote, text)
    return text



def _on_camera_canon_set(snapshot: dict[str, Any], shot: Shot | None = None) -> set[str]:
    canons: set[str] = set()
    for item in snapshot.get("characters") or []:
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
    characters = snapshot.get("characters") or []
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
        for item in snapshot.get("characters") or []:
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
    for item in snapshot.get("characters") or []:
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


def _collect_visual_profiles(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
) -> list[str]:
    characters = snapshot.get("characters") or []
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
    sanitized_video_prompt = _sanitize_phone_video_prompt(video_prompt)
    anchor = "Approved storyboard frame: use the supplied image as the exact opening frame and visual anchor; preserve its composition, faces, wardrobe, set, and lighting throughout the clip, moving or removing props only as explicitly performed in the shot action."
    if not snapshot:
        return "\n\n".join(part for part in (sanitized_video_prompt.strip(), anchor) if part)
    scene = snapshot.get("scene") or {}
    characters = snapshot.get("characters") or []
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
    phone_routing = _phone_routing_instruction(snapshot, sanitized_video_prompt, shot=shot)
    if phone_routing:
        package.append(phone_routing)
    for key, label in (
        ("location", "Location"), ("time_of_day", "Time of day"),
        ("set_notes", "Set"), ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"), ("props_notes", "Props"),
    ):
        value = _text(scene.get(key))
        if value and value.lower() != "unspecified":
            package.append(f"{label} continuity: {value}.")
    return "\n\n".join([sanitized_video_prompt.strip(), *package]).strip()


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
    }
