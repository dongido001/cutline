import re
from typing import Any


SHOT_HEADING = re.compile(
    r"^SHOT\s+(?P<number>\d+)\s*[\u2014\u2013-]\s*(?P<title>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FIELD_HEADING = re.compile(
    r"^(?P<field>Scene|Location|Time of day|Time|Framing|Action|Dialogue|(?:Final )?Sound|Image prompt|Video prompt):\s*(?P<value>.*)$",
    re.IGNORECASE,
)
TIME_RANGE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*[\u2014\u2013-]\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?)"
)


def _timecode_to_ms(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = parts
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def _is_character_cue(value: str) -> bool:
    candidate = value.strip().rstrip(":").strip()
    letters = [character for character in candidate if character.isalpha()]
    return bool(letters) and all(character.isupper() for character in letters)


def _clean_dialogue_text(value: str) -> str:
    return value.strip().strip('"\'\u201c\u201d\u2018\u2019').strip()


def _extract_dialogue(lines: list[str]) -> list[dict[str, str]]:
    if not lines or " ".join(lines).strip().lower().rstrip(".") == "none":
        return []
    dialogue: list[dict[str, str]] = []
    character: str | None = None
    text_lines: list[str] = []

    def flush() -> None:
        nonlocal text_lines
        text_value = _clean_dialogue_text(" ".join(text_lines))
        if character and text_value:
            dialogue.append({"character": character, "text": text_value})
        text_lines = []

    for line in lines:
        if _is_character_cue(line):
            flush()
            character = line.strip().rstrip(":").strip()
        elif line.strip():
            text_lines.append(line.strip())
    flush()
    return dialogue


def _split_action_and_dialogue(
    action_lines: list[str], dialogue_lines: list[str]
) -> tuple[str, list[dict[str, str]]]:
    first_cue = next(
        (index for index, line in enumerate(action_lines) if _is_character_cue(line)),
        None,
    )
    if first_cue is not None:
        dialogue_lines = action_lines[first_cue:] + dialogue_lines
        action_lines = action_lines[:first_cue]
    action = "\n".join(line for line in action_lines if line.strip()).strip()
    return action, _extract_dialogue(dialogue_lines)


def _field_text(fields: dict[str, list[str]], name: str) -> str:
    return "\n".join(fields.get(name, [])).strip()


def _suggestion(
    scene_index: int,
    field: str,
    title: str,
    reason: str,
    before_value: Any = None,
    after_value: Any = None,
) -> dict[str, Any]:
    return {
        "category": "structure",
        "severity": "blocking",
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
    preamble_lines = [line.strip() for line in script[: matches[0].start()].splitlines()]
    film_title = next(
        (
            line
            for line in preamble_lines
            if line
            and not re.match(
                r"^(Genre|Duration|Format|Aspect ratio|Character consistency|Location):",
                line,
                re.IGNORECASE,
            )
            and line.lower() != "character consistency"
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
        duration_ms = (
            end_ms - start_ms
            if start_ms is not None and end_ms is not None
            else 5000
        )
        searchable = f"{title}\n{block}".lower()
        time_of_day = next(
            (value.title() for value in ("night", "morning", "afternoon", "evening", "day") if re.search(rf"\b{value}\b", searchable)),
            "Unspecified",
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
            suggestions.append(
                _suggestion(index, "timing", f"Add timing for Shot {index + 1}", "Every shot needs an explicit start and end time.")
            )
        elif duration_ms < 2000 or duration_ms > 10000:
            suggestions.append(
                _suggestion(index, "duration_ms", f"Adjust Shot {index + 1} duration", "Generated video shots must be between 2 and 10 seconds.", duration_ms)
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
        if not grouped_scenes or grouped_scenes[-1]["title"] != group_title:
            grouped_scenes.append(
                {
                    "title": group_title,
                    "location": source_scene["location"],
                    "time_of_day": source_scene["time_of_day"],
                    "start_ms": source_scene["start_ms"],
                    "end_ms": source_scene["end_ms"],
                    "action": source_scene["action"],
                    "dialogue": list(source_scene["dialogue"]),
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
        "parser": "structured-shot-v2",
    }
