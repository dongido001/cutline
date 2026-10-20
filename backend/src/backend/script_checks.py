"""Deterministic craft and continuity checks for parsed screenplays.

These run on the flat, one-shot-per-entry scene list produced by the structured
parser (before shots are grouped into scenes) so suggestion targets can be remapped
the same way as the structural ones. Everything here is advisory: severity is always
"warning" and the target field is empty, so accepting a suggestion only acknowledges
it and never rewrites the script, and nothing here blocks approval.
"""

from __future__ import annotations

import re
from typing import Any

from .screenplay_parser import canonical_character_name, expand_character_cue_names

CATEGORY_CONTINUITY = "continuity"
CATEGORY_CRAFT = "craft"

MIN_ACTION_WORDS = 4
SAME_FRAMING_RUN = 4
MAX_SILENT_STRETCH_MS = 20_000

_VAGUE_ACTION = re.compile(
    r"^(?:\w+\s+){0,2}(?:talks?|talking|speaks?|speaking|chats?|chatting|discuss(?:es)?|converse[sd]?)\.?$",
    re.IGNORECASE,
)
# Cues that are not on-screen people, so they never need a cast entry.
_OFFSCREEN_CUE = re.compile(r"\b(?:voice|narrator|narration|v\.?o\.?|o\.?s\.?|announcer|phone|radio|tv|speaker)\b", re.IGNORECASE)


def _advisory(
    scene_index: int,
    category: str,
    title: str,
    reason: str,
    *,
    before_value: Any = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "severity": "warning",
        "title": title,
        "reason": reason,
        "target": {"scene_index": scene_index, "shot_index": 0, "field": ""},
        "before_value": before_value,
        "after_value": None,
    }


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _speakers(shot: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for line in shot.get("dialogue") or []:
        for name in expand_character_cue_names(str(line.get("character", ""))):
            if name and name not in names:
                names.append(name)
    return names


def _is_onscreen_person(name: str) -> bool:
    return not _OFFSCREEN_CUE.search(name)


def script_quality_suggestions(
    scenes: list[dict[str, Any]],
    characters: list[dict[str, str]],
    shot_text: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return advisory suggestions for a flat list of one-shot scenes.

    `characters` is the parsed "Character consistency" list (may be empty, in which
    case checks that depend on a declared cast are skipped).
    """
    out: list[dict[str, Any]] = []
    known = {canonical_character_name(c["name"]) for c in characters if c.get("name")}
    announced: set[str] = set()
    appeared: set[str] = set()

    framing_run = 0
    previous_framing = ""
    silent_ms = 0
    silent_flagged = False

    for index, scene in enumerate(scenes):
        shot = (scene.get("shots") or [{}])[0]
        label = f"Shot {index + 1}"
        action = str(shot.get("action") or "")
        sound = str(shot.get("sound") or "").strip()
        dialogue = shot.get("dialogue") or []
        duration = int(shot.get("duration_ms") or 0)
        speakers = _speakers(shot)
        for cast_name in shot.get("explicit_cast") or []:
            appeared.update(expand_character_cue_names(str(cast_name)))

        # --- Characters -------------------------------------------------------
        for name in speakers:
            if not _is_onscreen_person(name):
                continue
            appeared.add(name)
            if known and name not in known and name not in announced:
                announced.add(name)
                out.append(
                    _advisory(
                        index,
                        CATEGORY_CONTINUITY,
                        f"{name.title()} speaks in {label} but is never introduced",
                        "This speaker has no entry under Character consistency, so their look and voice "
                        "may change between shots. Add them to the cast list.",
                        before_value=name,
                    )
                )
            cast = shot.get("explicit_cast")
            if cast is not None:
                cast_names = {canonical_character_name(c) for c in cast}
                if name not in cast_names:
                    out.append(
                        _advisory(
                            index,
                            CATEGORY_CONTINUITY,
                            f"{name.title()} speaks in {label} but is not in its cast list",
                            "The shot's Cast line leaves this speaker out, so they may not be drawn or voiced.",
                            before_value=name,
                        )
                    )

        # --- Scene-to-scene continuity ---------------------------------------
        if index > 0:
            prev = scenes[index - 1]
            same_place = (scene.get("location") or "").strip().lower() == (prev.get("location") or "").strip().lower()
            prev_tod = (prev.get("time_of_day") or "Unspecified").strip()
            tod = (scene.get("time_of_day") or "Unspecified").strip()
            if same_place and "Unspecified" not in (prev_tod, tod) and prev_tod.lower() != tod.lower():
                out.append(
                    _advisory(
                        index,
                        CATEGORY_CONTINUITY,
                        f"Time of day jumps from {prev_tod.lower()} to {tod.lower()} at {label}",
                        "The location is unchanged, so the jump may read as a mistake. Add a transition shot "
                        "or confirm it is intentional.",
                        before_value=prev_tod,
                    )
                )
            if action and _norm(action) == _norm(str(((prev.get("shots") or [{}])[0]).get("action") or "")):
                out.append(
                    _advisory(
                        index,
                        CATEGORY_CRAFT,
                        f"{label} repeats the action of the previous shot",
                        "Two shots in a row describe the same action. Merge them or give this one a new beat.",
                    )
                )

        # --- Shot quality ------------------------------------------------------
        if action and (_words(action) < MIN_ACTION_WORDS or _VAGUE_ACTION.match(action.strip())):
            out.append(
                _advisory(
                    index,
                    CATEGORY_CRAFT,
                    f"{label} action is too vague to direct",
                    "Describe who does what, and how, so the image and clip have something concrete to show.",
                    before_value=action,
                )
            )
        framing = str(shot.get("framing") or "").strip()
        if not framing or framing.lower() == "unspecified":
            out.append(
                _advisory(
                    index,
                    CATEGORY_CRAFT,
                    f"{label} has no framing",
                    "Name the shot size or angle (wide, medium close-up, over-the-shoulder) so coverage stays varied.",
                )
            )
            framing_run = 0
            previous_framing = ""
        else:
            same = framing.lower() == previous_framing
            framing_run = framing_run + 1 if same else 1
            previous_framing = framing.lower()
            if framing_run == SAME_FRAMING_RUN:
                out.append(
                    _advisory(
                        index,
                        CATEGORY_CRAFT,
                        f"{SAME_FRAMING_RUN} shots in a row use the same framing",
                        f"Shots {index - SAME_FRAMING_RUN + 2}-{index + 1} are all \"{framing}\". "
                        "Vary the shot size to keep the pacing alive.",
                        before_value=framing,
                    )
                )

        # --- Pacing and hook -------------------------------------------------
        if index == 0 and not dialogue and not sound:
            out.append(
                _advisory(
                    index,
                    CATEGORY_CRAFT,
                    "The opening shot has no sound or dialogue",
                    "Give the first shot a sound or a line so the film has a hook from the first second.",
                )
            )
        if dialogue or sound:
            silent_ms = 0
            silent_flagged = False
        else:
            silent_ms += duration
            if silent_ms > MAX_SILENT_STRETCH_MS and not silent_flagged:
                silent_flagged = True
                out.append(
                    _advisory(
                        index,
                        CATEGORY_CRAFT,
                        f"Over {MAX_SILENT_STRETCH_MS // 1000}s without sound or dialogue by {label}",
                        "A long silent stretch can feel like dead air. Add ambience, music or a line.",
                    )
                )

    # --- Cast declared but never used --------------------------------------
    if known and scenes:
        haystack = _norm(" ".join(shot_text or []))
        for character in characters:
            name = canonical_character_name(character.get("name", ""))
            first = _norm(name.split(" ")[0]) if name else ""
            if not name or not _is_onscreen_person(name) or name in appeared:
                continue
            if first and re.search(rf"\b{re.escape(first)}\b", haystack):
                continue
            out.append(
                _advisory(
                    0,
                    CATEGORY_CONTINUITY,
                    f"{name.title()} is in the cast list but never appears",
                    "No shot mentions or voices this character. Remove them or give them a moment on screen.",
                    before_value=name,
                )
            )
    return out
