from __future__ import annotations

import re
from typing import Any

from .timing import estimate_dialogue_duration_ms, resolve_shot_duration_ms


SHOT_HEADING = re.compile(
    r"^SHOT\s+(?P<number>\d+)\s*[\u2014\u2013-]\s*(?P<title>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FIELD_HEADING = re.compile(
    r"^(?P<field>Scene|Location|Set|Props|Time of day|Time|Framing|Cast|Characters|Action|Dialogue|(?:Final )?Sound|Image prompt|Video prompt):\s*(?P<value>.*)$",
    re.IGNORECASE,
)


def parse_explicit_cast_names(raw: str) -> list[str]:
    """Parse comma/and separated character names from a 'Cast:' or 'Characters:' line."""
    cleaned = raw.strip()
    if not cleaned or cleaned.lower() in {"none", "none.", "n/a", "nobody", "no one", "-", "empty"}:
        return []
    parts = re.split(r",|\band\b|&|/|;", cleaned, flags=re.IGNORECASE)
    names: list[str] = []
    for part in parts:
        name = part.strip().strip("\"'").strip()
        if name and name.lower() not in {"none", "n/a", "nobody", "no one"}:
            names.append(name)
    return names
TIME_RANGE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*[\u2014\u2013-]\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?)"
)
CHARACTER_SECTION = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?:Character consistency|MAIN CHARACTERS|Characters)\s*:?\s*$"
)
CHARACTER_SECTION_STOP = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?:Location|Genre|Duration|Format|Aspect ratio|SHOT\s+\d+|SCENE|OPENING|---)\b"
)
CHARACTER_LINE = re.compile(
    r"^(?:\*\*)?(?P<name>[A-Z][A-Z0-9_' .\-,/\"\u201c\u201d\u2018\u2019\(\)]{0,80}?)(?:\*\*)?"
    r"(?:\s*\((?P<role>[^)]+)\))?"
    r"\s*[—–\-:]\s*(?P<notes>.+)$"
)
TRANSITION_LINE = re.compile(
    r"^(?:CUT TO|FADE IN|FADE OUT|FADE TO|SMASH CUT|MATCH CUT|DISSOLVE TO|WIPE TO|END OF\b|THE END\b)",
    re.IGNORECASE,
)
OPEN_QUOTES = ('"', "'", "\u201c", "\u2018")
CLOSE_QUOTES = ('"', "'", "\u201d", "\u2019")


def canonical_character_name(name: str) -> str:
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.upper()


def expand_character_cue_names(name: str) -> list[str]:
    """Expand compound dialogue cues like 'JASMINE AND SKYLAR' into individual canonical character names."""
    canon = canonical_character_name(name)
    if not canon:
        return []
    if " AND " in canon:
        parts = [part.strip() for part in re.split(r"\s+AND\s+", canon) if part.strip()]
        if len(parts) >= 2 and all(len(part) >= 2 for part in parts):
            return parts
    return [canon]


def infer_voice_description(name: str, notes: str, age: int | None = None) -> str:
    """Extract an explicit Voice: clause or synthesize a deterministic Veo voice profile from character notes."""
    cleaned_notes = (notes or "").strip()
    if not cleaned_notes and age is None:
        return ""

    explicit = re.search(
        r"(?:speaking\s+)?voice(?:\s+profile)?\s*:\s*([^\n]+)",
        cleaned_notes,
        re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1).strip().rstrip(".")

    lower = cleaned_notes.lower()
    if age is None:
        age_match = re.search(r"\b(\d{1,2})\s*(?:years?\s*old|yo|y/o)\b", lower)
        if age_match:
            age = int(age_match.group(1))

    is_child = (age is not None and age < 13) or bool(
        re.search(
            r"\b(child|childlike|toddler|preschool(?:er)?|little\s+girl|little\s+boy|young\s+girl|young\s+boy|kid)\b",
            lower,
        )
    )
    is_non_human_mascot = bool(
        re.search(
            r"\b(magical\s+animated|friendly\s+animated|animated\s+(?:yellow\s+)?sun|rainbow\s+character|talking\s+rainbow|animated\s+character|talking\s+animal)\b",
            lower,
        )
    )

    is_female = bool(
        re.search(
            r"\b(woman|female|mother|matriarch|sister|daughter|girl|wife|widow|aunt|grandmother|blouse|ponytail|ponytails|dress|bun|she|her)\b",
            lower,
        )
    )
    is_male = bool(
        re.search(
            r"\b(man|male|father|patriarch|brother|son|boy|husband|uncle|grandfather|jaw|stubble|beard|baritone|tenor|he|him|his)\b",
            lower,
        )
    )

    is_older = not is_child and (
        (age is not None and age >= 50)
        or bool(
            re.search(
                r"\b(older\s+(?:man|woman|adult|person)|elderly|matriarch|patriarch|50s|60s|70s|silver|graying|grey)\b",
                lower,
            )
        )
    )

    if is_non_human_mascot:
        register = "Bright, friendly, musical animated character voice"
    elif is_child:
        if is_female and not is_male:
            register = f"Bright, cheerful {f'{age}-year-old ' if age else ''}young girl voice"
        elif is_male and not is_female:
            register = f"Bright, cheerful {f'{age}-year-old ' if age else ''}young boy voice"
        else:
            register = f"Bright, cheerful {f'{age}-year-old ' if age else ''}child voice"
    elif is_female and not is_male:
        if is_older:
            register = f"Firm, seasoned {f'{age}-year-old ' if age else ''}contralto"
        else:
            register = f"Warm, grounded {f'{age}-year-old ' if age else ''}mezzo-soprano"
    elif is_male and not is_female:
        if is_older:
            register = f"Deep, weathered {f'{age}-year-old ' if age else ''}baritone"
        else:
            register = f"Steady, resonant {f'{age}-year-old ' if age else ''}baritone"
    else:
        register = f"Clear, grounded {f'{age}-year-old ' if age else ''}speaking voice"

    traits: list[str] = [register]
    if "filipino" in lower:
        traits.append("subtle Filipino-English cadence")
    elif "nigerian" in lower:
        traits.append("natural Nigerian-English cadence")
    elif "british" in lower:
        traits.append("natural British-English cadence")

    delivery: list[str] = []
    if re.search(r"\b(composed|controlled|guarded|calculating|calm|measured|unhurried)\b", lower):
        delivery.append("composed, controlled, unhurried delivery")
    elif re.search(r"\b(authoritative|commanding|stern|sharp|matriarch|patriarch)\b", lower):
        delivery.append("authoritative, measured delivery")
    elif re.search(r"\b(weathered|salt-aged|quiet|observant|working-class|rough)\b", lower):
        delivery.append("low-key, weathered, unhurried delivery")
    elif re.search(r"\b(curious|playful|energetic|giggle|bubbly)\b", lower):
        delivery.append("playful, curious, expressive delivery")
    elif re.search(r"\b(cheerful|encouraging|patient|friendly|musical)\b", lower):
        delivery.append("warm, cheerful, encouraging delivery")
    elif re.search(r"\b(practical|alert|determined|capable|direct|focused)\b", lower):
        delivery.append("clear, natural, earnest delivery")
    else:
        delivery.append("natural, steady conversational delivery")

    traits.extend(delivery)
    return ", ".join(traits)


def parse_character_consistency(script: str) -> list[dict[str, str]]:
    """Extract named cast descriptions from a structured screenplay preamble."""
    if not script or not script.strip():
        return []

    lines = script.replace("\r\n", "\n").split("\n")
    start: int | None = None
    for index, line in enumerate(lines):
        if CHARACTER_SECTION.match(line.strip()):
            start = index + 1
            break
    if start is None:
        return []

    dialogue_cues = {
        canonical_character_name(line.strip().rstrip(":"))
        for line in lines[start:]
        if line.strip().endswith(":") and _is_character_cue(line)
    }
    first_shot_match = SHOT_HEADING.search(script)
    shots_text = script[first_shot_match.start() :] if first_shot_match else ""

    def _resolve_base_name(base: str) -> str:
        parts = base.split()
        if len(parts) <= 1 or base in dialogue_cues:
            return base
        if parts[0] in dialogue_cues:
            return parts[0]
        if shots_text and re.search(
            rf"\b{re.escape(parts[0])}\b(?!\s+{re.escape(parts[1])})",
            shots_text,
            re.IGNORECASE,
        ):
            return parts[0]
        return base

    found: list[dict[str, str]] = []
    seen: set[str] = set()
    blank_streak = 0
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            blank_streak += 1
            if found and blank_streak >= 2:
                break
            continue
        blank_streak = 0
        if CHARACTER_SECTION_STOP.match(stripped):
            break
        if stripped.lower() in {"character consistency", "main characters", "characters"}:
            continue
        match = CHARACTER_LINE.match(stripped)
        if not match:
            if found:
                break
            continue
        raw_name = match.group("name").strip()
        notes = match.group("notes").strip()
        if re.search(
            r"\bnot\s+a\s+separate\s+(?:voice\s+or\s+)?character\b|\bwhen\s+this\s+cue\s+appears\b",
            notes,
            re.IGNORECASE,
        ):
            continue
        age_match = re.search(r",\s*(\d{1,3})\b", raw_name)
        age = int(age_match.group(1)) if age_match else None
        nickname_match = re.search(
            r'["\'\u201c\u2018]([^"\'\u201d\u2019]+)["\'\u201d\u2019]', raw_name
        )
        if nickname_match:
            name = canonical_character_name(nickname_match.group(1))
        elif "," in raw_name:
            # Drop trailing age e.g. "MAYA SANTOS, 25" -> "MAYA SANTOS"
            base = canonical_character_name(raw_name.split(",")[0].strip())
            name = _resolve_base_name(base)
        else:
            base = canonical_character_name(raw_name)
            name = _resolve_base_name(base)
        if not name or name in seen or len(expand_character_cue_names(name)) > 1:
            continue
        role = (match.group("role") or "").strip()
        if age is not None and not re.search(rf"\b{age}\b", notes):
            notes = f"{age} years old. {notes}".strip()
        if role:
            notes = f"({role}) {notes}".strip()
        voice_description = infer_voice_description(name, notes, age=age)
        seen.add(name)
        found.append(
            {
                "name": name,
                "notes": notes,
                "voice_description": voice_description,
            }
        )
    return found


def _timecode_to_ms(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = parts
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def _is_character_cue(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith(OPEN_QUOTES) or (
        not stripped.endswith(":") and stripped.endswith(CLOSE_QUOTES)
    ):
        return False
    if not stripped.endswith(":") and stripped.endswith((".", "!", "?")):
        return False
    candidate = stripped.rstrip(":").strip()
    if TRANSITION_LINE.match(candidate):
        return False
    letters = [character for character in candidate if character.isalpha()]
    return bool(letters) and all(character.isupper() for character in letters)


def _clean_dialogue_text(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(
        r"\s*\(\s*Alternative\b[^)]*\)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    lead_paren = re.match(
        r"^\([^)]+\)\s*[\"\'\u201c\u2018]+(.+?)[\"\'\u201d\u2019]*$",
        cleaned,
    )
    if lead_paren:
        cleaned = lead_paren.group(1).strip()
    return cleaned.strip('"\'\u201c\u201d\u2018\u2019').strip()


def _split_inline_quote_and_action(value: str) -> tuple[str, str]:
    """If a dialogue string has a closing quote followed by narrative action, split them."""
    cleaned = value.strip()
    cleaned = re.sub(
        r"\s*\(\s*Alternative\b[^)]*\)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    match = re.match(
        r"^(?:\([^)]+\)\s*)?[\"\u201c]?(.+?)[\"\u201d]\s+(.+)$",
        cleaned,
    )
    if not match:
        return _clean_dialogue_text(cleaned), ""
    speech = _clean_dialogue_text(match.group(1))
    tail = match.group(2).strip()
    tail = re.sub(
        r"\s*None after [^.]+(?:\.|$)",
        "",
        tail,
        flags=re.IGNORECASE,
    ).strip()
    return speech, tail


def _is_none_marker(value: str) -> bool:
    return value.strip().lower().rstrip(".") == "none"


def _split_dialogue_and_interleaved_action(
    lines: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    if not lines or _is_none_marker(" ".join(lines)):
        return [], []
    dialogue: list[dict[str, str]] = []
    extra_action: list[str] = []
    character: str | None = None
    text_lines: list[str] = []
    in_quoted_dialogue = False

    def flush() -> None:
        nonlocal character, text_lines, in_quoted_dialogue
        raw_joined = " ".join(text_lines)
        text_value, trailing_action = _split_inline_quote_and_action(raw_joined)
        if character and text_value and not _is_none_marker(text_value):
            dialogue.append({"character": character, "text": text_value})
        if trailing_action and not _is_none_marker(trailing_action):
            extra_action.append(trailing_action)
        character = None
        text_lines = []
        in_quoted_dialogue = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if character and text_lines and not in_quoted_dialogue:
                flush()
            continue
        if _is_none_marker(stripped):
            continue
        if _is_character_cue(stripped):
            flush()
            character = stripped.rstrip(":").strip()
            continue
        if character is not None:
            cleaned_line = re.sub(
                r"\s*\(\s*Alternative\b[^)]*\)\s*$",
                "",
                stripped,
                flags=re.IGNORECASE,
            ).strip()
            starts_quoted = cleaned_line.startswith(OPEN_QUOTES) or bool(
                re.match(r"^\([^)]+\)\s*[\"\'\u201c\u2018]", cleaned_line)
            )
            ends_quoted = cleaned_line.endswith(CLOSE_QUOTES) and len(cleaned_line) > 1
            if not text_lines:
                in_quoted_dialogue = starts_quoted
                text_lines.append(cleaned_line)
                if in_quoted_dialogue and ends_quoted:
                    flush()
            elif in_quoted_dialogue:
                text_lines.append(cleaned_line)
                if ends_quoted:
                    flush()
            else:
                text_lines.append(cleaned_line)
        else:
            extra_action.append(stripped)

    flush()
    return dialogue, extra_action


def _extract_dialogue(lines: list[str]) -> list[dict[str, str]]:
    dialogue, _ = _split_dialogue_and_interleaved_action(lines)
    return dialogue


def _split_action_and_dialogue(
    action_lines: list[str], dialogue_lines: list[str]
) -> tuple[str, list[dict[str, str]]]:
    first_cue = next(
        (index for index, line in enumerate(action_lines) if _is_character_cue(line)),
        None,
    )
    if first_cue is not None:
        combined_dialogue_lines = action_lines[first_cue:] + dialogue_lines
        leading_action_lines = action_lines[:first_cue]
    else:
        combined_dialogue_lines = dialogue_lines
        leading_action_lines = action_lines
    dialogue, interleaved_action = _split_dialogue_and_interleaved_action(
        combined_dialogue_lines
    )
    all_action = [
        line.strip()
        for line in [*leading_action_lines, *interleaved_action]
        if line.strip() and not _is_none_marker(line)
    ]
    action = "\n".join(all_action).strip()
    return action, dialogue


def _field_text(fields: dict[str, list[str]], name: str) -> str:
    return "\n".join(fields.get(name, [])).strip()


def _suggestion(
    scene_index: int,
    field: str,
    title: str,
    reason: str,
    before_value: Any = None,
    after_value: Any = None,
    *,
    severity: str = "blocking",
) -> dict[str, Any]:
    return {
        "category": "structure",
        "severity": severity,
        "title": title,
        "reason": reason,
        "target": {"scene_index": scene_index, "shot_index": 0, "field": field},
        "before_value": before_value,
        "after_value": after_value,
    }


def parse_structured_screenplay(script: str) -> dict[str, Any] | None:
    matches = list(SHOT_HEADING.finditer(script))
    if not matches:
        return None

    location_match = re.search(r"^Location:\s*(.+)$", script, re.IGNORECASE | re.MULTILINE)
    location = location_match.group(1).strip() if location_match else "Unspecified"

    set_match = re.search(
        r"^(?:Set(?: notes)?|Set / location):\s*(.+?)(?=\n\s*(?:Lighting|Wardrobe|Props|Character consistency|SHOT\s+\d+|SCENE|$))",
        script,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    default_set_notes = set_match.group(1).strip() if set_match else ""
    if not default_set_notes and location and location != "Unspecified":
        default_set_notes = location

    lighting_match = re.search(
        r"^(?:Lighting(?: notes)?):\s*(.+?)(?=\n\s*(?:Set|Wardrobe|Props|Character consistency|SHOT\s+\d+|SCENE|$))",
        script,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    default_lighting_notes = lighting_match.group(1).strip() if lighting_match else ""

    wardrobe_match = re.search(
        r"^(?:Wardrobe(?: notes)?):\s*(.+?)(?=\n\s*(?:Set|Lighting|Props|Character consistency|SHOT\s+\d+|SCENE|$))",
        script,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    default_wardrobe_notes = wardrobe_match.group(1).strip() if wardrobe_match else ""

    props_match = re.search(
        r"^(?:Props(?: notes)?):\s*(.+?)(?=\n\s*(?:Set|Lighting|Wardrobe|Character consistency|SHOT\s+\d+|SCENE|$))",
        script,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    default_props_notes = props_match.group(1).strip() if props_match else ""
    preamble_lines = [line.strip() for line in script[: matches[0].start()].splitlines()]
    film_title = next(
        (
            line
            for line in preamble_lines
            if line
            and not re.match(
                r"^(Genre|Duration|Format|Aspect ratio|Character consistency|Location|Set|Lighting|Wardrobe|Props|Continuity):",
                line,
                re.IGNORECASE,
            )
            and line.lower() not in ("character consistency", "continuity notes", "continuity")
        ),
        "MAIN SEQUENCE",
    )
    format_match = re.search(
        r"^Format:\s*(\d+)\s+shots?\b", script, re.IGNORECASE | re.MULTILINE
    )
    expected_count = int(format_match.group(1)) if format_match else None
    duration_match = re.search(
        r"^Duration:\s*(\d+)\s*(seconds?|secs?|minutes?|mins?)\b",
        script,
        re.IGNORECASE | re.MULTILINE,
    )
    declared_duration_ms: int | None = None
    if duration_match:
        multiplier = 60_000 if duration_match.group(2).lower().startswith("m") else 1000
        declared_duration_ms = int(duration_match.group(1)) * multiplier

    scenes: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    previous_end: int | None = None

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        block = script[match.end():end].strip()
        fields: dict[str, list[str]] = {}
        current_field: str | None = None
        for raw_line in block.splitlines():
            line = raw_line.strip()
            heading = FIELD_HEADING.match(line)
            if heading:
                current_field = heading.group("field").lower().replace(" ", "_")
                if current_field == "final_sound":
                    current_field = "sound"
                fields.setdefault(current_field, [])
                if heading.group("value").strip():
                    fields[current_field].append(heading.group("value").strip())
            elif re.match(
                r"^(?:[=\-*_]{4,}|END\s+OF\s+(?:EPISODE|SCRIPT|ACT|PART|SCENE)\b.*|THE\s+END\.?)$",
                line,
                re.IGNORECASE,
            ):
                current_field = None
            elif current_field is not None:
                fields[current_field].append(line)

        time_value = _field_text(fields, "time")
        time_match = TIME_RANGE.search(time_value)
        start_ms = _timecode_to_ms(time_match.group("start")) if time_match else None
        end_ms = _timecode_to_ms(time_match.group("end")) if time_match else None
        action, dialogue = _split_action_and_dialogue(
            fields.get("action", []), fields.get("dialogue", [])
        )
        framing = _field_text(fields, "framing")
        image_prompt = _field_text(fields, "image_prompt")
        video_prompt = _field_text(fields, "video_prompt")
        sound = _field_text(fields, "sound")
        title = match.group("title").strip()
        scene_title = _field_text(fields, "scene") or film_title
        shot_location = _field_text(fields, "location") or location
        duration_ms, used_dialogue_estimate = resolve_shot_duration_ms(
            start_ms=start_ms,
            end_ms=end_ms,
            dialogue=dialogue,
        )
        if used_dialogue_estimate and start_ms is not None and end_ms is None:
            end_ms = start_ms + duration_ms
        elif used_dialogue_estimate and start_ms is None and end_ms is None and previous_end is not None:
            start_ms = previous_end
            end_ms = start_ms + duration_ms
        elif used_dialogue_estimate and start_ms is None and end_ms is None and index == 0:
            start_ms = 0
            end_ms = duration_ms
        searchable = f"{title}\n{block}".lower()
        explicit_tod = _field_text(fields, "time_of_day").strip()
        time_of_day = (
            explicit_tod.title()
            if explicit_tod
            else next(
                (
                    value.title()
                    for value in ("night", "morning", "afternoon", "evening", "day")
                    if re.search(rf"\b{value}\b", searchable)
                ),
                "Unspecified",
            )
        )

        explicit_cast_field = _field_text(fields, "cast") or _field_text(fields, "characters")
        explicit_cast = (
            parse_explicit_cast_names(explicit_cast_field)
            if ("cast" in fields or "characters" in fields)
            else None
        )

        shot = {
            "order_index": 0,
            "title": title,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "action": action,
            "dialogue": dialogue,
            "sound": sound,
            "duration_ms": duration_ms,
            "framing": framing or "Unspecified",
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
            "explicit_cast": explicit_cast,
        }
        scenes.append(
            {
                "title": title,
                "location": shot_location,
                "time_of_day": time_of_day,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "action": action,
                "dialogue": dialogue,
                "shots": [shot],
                "_group_title": scene_title,
            }
        )

        if not time_match or start_ms is None or end_ms is None:
            if used_dialogue_estimate:
                suggestions.append(
                    _suggestion(
                        index,
                        "timing",
                        f"Estimated timing for Shot {index + 1} from dialogue",
                        "No Time: range found; duration was estimated from spoken dialogue (~2.5 words/sec).",
                        duration_ms,
                        severity="warning",
                    )
                )
            else:
                suggestions.append(
                    _suggestion(index, "timing", f"Add timing for Shot {index + 1}", "Every shot needs an explicit start and end time.")
                )
        elif duration_ms < 2000 or duration_ms > 10000:
            suggestions.append(
                _suggestion(index, "duration_ms", f"Adjust Shot {index + 1} duration", "Generated video shots must be between 2 and 10 seconds.", duration_ms)
            )
        else:
            spoken = estimate_dialogue_duration_ms(dialogue)
            if spoken is not None and spoken > duration_ms + 500:
                suggestions.append(
                    _suggestion(
                        index,
                        "duration_ms",
                        f"Shot {index + 1} may be short for its dialogue",
                        f"Dialogue needs about {spoken / 1000:.1f}s at a natural speech rate; timed duration is {duration_ms / 1000:.1f}s.",
                        duration_ms,
                        spoken,
                        severity="warning",
                    )
                )
        if previous_end is not None and start_ms is not None and start_ms != previous_end:
            suggestions.append(
                _suggestion(index, "start_ms", f"Fix the timeline before Shot {index + 1}", "Shot timings must be continuous without gaps or overlaps.", start_ms, previous_end)
            )
        if end_ms is not None:
            previous_end = end_ms
        for field, value, label in (
            ("action", action, "action"),
            ("image_prompt", image_prompt, "image prompt"),
            ("video_prompt", video_prompt, "video prompt"),
        ):
            if not value:
                suggestions.append(
                    _suggestion(index, field, f"Add {label} for Shot {index + 1}", f"Every structured shot needs a {label}.")
                )

    shot_count = len(scenes)
    if expected_count is not None and expected_count != shot_count:
        suggestions.append(
            _suggestion(0, "shot_count", "Shot count does not match", f"The script declares {expected_count} shots but {shot_count} shot blocks were parsed.", shot_count, expected_count)
        )
    if declared_duration_ms is not None and previous_end != declared_duration_ms:
        suggestions.append(
            _suggestion(0, "timeline", "Timeline does not match declared duration", "The final shot must end at the screenplay's declared duration.", previous_end, declared_duration_ms)
        )

    grouped_scenes: list[dict[str, Any]] = []
    shot_targets: dict[int, tuple[int, int]] = {}
    for source_index, source_scene in enumerate(scenes):
        group_title = source_scene.pop("_group_title")
        group_location = (source_scene.get("location") or "Unspecified").strip()
        group_time = (source_scene.get("time_of_day") or "Unspecified").strip()
        group_key = (group_title.strip().lower(), group_location.lower())
        previous_key = None
        if grouped_scenes:
            previous = grouped_scenes[-1]
            previous_key = (
                previous["title"].strip().lower(),
                (previous.get("location") or "Unspecified").strip().lower(),
            )
        if not grouped_scenes or previous_key != group_key:
            grouped_scenes.append(
                {
                    "title": group_title,
                    "location": group_location,
                    "time_of_day": group_time,
                    "start_ms": source_scene["start_ms"],
                    "end_ms": source_scene["end_ms"],
                    "action": source_scene["action"],
                    "dialogue": list(source_scene["dialogue"]),
                    "set_notes": default_set_notes,
                    "lighting_notes": default_lighting_notes,
                    "wardrobe_notes": default_wardrobe_notes,
                    "props_notes": default_props_notes,
                    "shots": [],
                }
            )
        group = grouped_scenes[-1]
        shot_index = len(group["shots"])
        shot = source_scene["shots"][0]
        shot["order_index"] = shot_index
        group["shots"].append(shot)
        group["end_ms"] = source_scene["end_ms"]
        if source_scene["action"] and source_scene["action"] != group["action"]:
            group["action"] += f"\n\n{source_scene['action']}"
        if shot_index > 0:
            group["dialogue"].extend(source_scene["dialogue"])
        if group["time_of_day"] == "Unspecified" and source_scene["time_of_day"] != "Unspecified":
            group["time_of_day"] = source_scene["time_of_day"]
        shot_targets[source_index] = (len(grouped_scenes) - 1, shot_index)

    for suggestion in suggestions:
        target = suggestion.get("target", {})
        source_index = target.get("scene_index")
        if isinstance(source_index, int) and source_index in shot_targets:
            scene_index, shot_index = shot_targets[source_index]
            target["scene_index"] = scene_index
            target["shot_index"] = shot_index

    return {
        "scenes": grouped_scenes,
        "suggestions": suggestions,
        "characters": parse_character_consistency(script),
        "parser": "structured-shot-v2",
    }
