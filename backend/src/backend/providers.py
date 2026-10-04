import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .settings import settings
from .timing import estimate_dialogue_duration_ms


class ScriptProvider(ABC):
    @abstractmethod
    def analyze(self, script: str, target_duration_seconds: int) -> dict[str, Any]:
        raise NotImplementedError

    def review(
        self, structured_script: dict[str, Any], target_duration_seconds: int
    ) -> dict[str, Any]:
        return {"suggestions": []}


def loads_model_json(content: str) -> dict[str, Any]:
    """Parse model JSON, tolerating fences and leading/trailing prose."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Model returned an empty response")
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("Model response did not contain a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON root must be an object")
    return parsed


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_dialogue(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        lines = value
    elif isinstance(value, str):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
    else:
        return []

    dialogue: list[dict[str, str]] = []
    for line in lines:
        if isinstance(line, dict):
            character = str(line.get("character") or line.get("speaker") or "Unknown").strip() or "Unknown"
            text = str(line.get("text") or line.get("line") or line.get("dialogue") or "").strip()
        elif isinstance(line, str):
            character, separator, text = line.partition(":")
            if not separator:
                character, text = "Unknown", line
            character = character.strip() or "Unknown"
            text = text.strip()
        else:
            continue
        if text:
            dialogue.append({"character": character, "text": text})
    return dialogue


def normalize_analysis_result(
    result: dict[str, Any], target_duration_seconds: int
) -> dict[str, Any]:
    raw_scenes = result.get("scenes")
    if not isinstance(raw_scenes, list):
        raw_scenes = []
    scene_count = max(1, len(raw_scenes))
    default_duration = max(2000, target_duration_seconds * 1000 // scene_count)
    cursor = 0
    scenes: list[dict[str, Any]] = []

    for scene_index, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            continue
        start_ms = max(0, _as_int(raw_scene.get("start_ms"), cursor))
        end_ms = _as_int(raw_scene.get("end_ms"), start_ms + default_duration)
        end_ms = max(start_ms + 1000, end_ms)
        cursor = end_ms
        title = str(raw_scene.get("title") or f"Scene {scene_index + 1}").strip()
        action = str(raw_scene.get("action") or "").strip()

        raw_shots = raw_scene.get("shots")
        if not isinstance(raw_shots, list):
            raw_shots = []
        shots: list[dict[str, Any]] = []
        shot_cursor = start_ms
        for shot_index, raw_shot in enumerate(raw_shots):
            if not isinstance(raw_shot, dict):
                continue
            dialogue = _normalize_dialogue(raw_shot.get("dialogue"))
            authored_duration = _as_int(raw_shot.get("duration_ms"), 0)
            spoken = estimate_dialogue_duration_ms(dialogue)
            if authored_duration > 0:
                duration_ms = max(2000, min(10000, authored_duration))
                if spoken is not None:
                    duration_ms = max(duration_ms, spoken)
            elif spoken is not None:
                duration_ms = spoken
            else:
                duration_ms = 5000
            duration_ms = max(2000, min(10000, duration_ms))
            shot_start_ms = max(
                start_ms, _as_int(raw_shot.get("start_ms"), shot_cursor)
            )
            shot_end_ms = max(
                shot_start_ms + 1000,
                _as_int(raw_shot.get("end_ms"), shot_start_ms + duration_ms),
            )
            shot_cursor = shot_end_ms
            shots.append(
                {
                    "order_index": shot_index,
                    "title": str(raw_shot.get("title") or f"{title} - Shot {shot_index + 1}"),
                    "start_ms": shot_start_ms,
                    "end_ms": shot_end_ms,
                    "action": str(raw_shot.get("action") or action),
                    "dialogue": dialogue,
                    "sound": str(raw_shot.get("sound") or ""),
                    "duration_ms": duration_ms,
                    "framing": str(raw_shot.get("framing") or "Medium"),
                    "image_prompt": str(
                        raw_shot.get("image_prompt")
                        or f"Cinematic frame, {title}. {action}"
                    ),
                    "video_prompt": str(
                        raw_shot.get("video_prompt")
                        or "Natural movement and controlled cinematic camera motion."
                    ),
                }
            )
        if not shots:
            shots.append(
                {
                    "order_index": 0,
                    "title": f"{title} - Shot 1",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "action": action,
                    "dialogue": _normalize_dialogue(raw_scene.get("dialogue")),
                    "sound": "",
                    "duration_ms": min(10000, max(2000, end_ms - start_ms)),
                    "framing": "Medium",
                    "image_prompt": f"Cinematic frame, {title}. {action}",
                    "video_prompt": "Natural movement and controlled cinematic camera motion.",
                }
            )

        scenes.append(
            {
                "title": title,
                "location": str(raw_scene.get("location") or "Unspecified"),
                "time_of_day": str(raw_scene.get("time_of_day") or "Unspecified"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "action": action,
                "dialogue": _normalize_dialogue(raw_scene.get("dialogue")),
                "shots": shots,
                "set_notes": str(raw_scene.get("set_notes") or ""),
                "lighting_notes": str(raw_scene.get("lighting_notes") or ""),
                "wardrobe_notes": str(raw_scene.get("wardrobe_notes") or ""),
                "props_notes": str(raw_scene.get("props_notes") or ""),
            }
        )

    severity_map = {
        "critical": "blocking",
        "high": "blocking",
        "medium": "warning",
        "low": "optional",
        "info": "optional",
    }
    suggestions: list[dict[str, Any]] = []
    raw_suggestions = result.get("suggestions")
    if not isinstance(raw_suggestions, list):
        raw_suggestions = []
    for raw_suggestion in raw_suggestions:
        if not isinstance(raw_suggestion, dict):
            continue
        raw_target = raw_suggestion.get("target")
        target = (
            raw_target
            if isinstance(raw_target, dict)
            else {"description": str(raw_target or "General script")}
        )
        raw_severity = str(raw_suggestion.get("severity") or "warning").lower()
        suggestions.append(
            {
                "category": str(raw_suggestion.get("category") or "script"),
                "severity": severity_map.get(raw_severity, raw_severity)
                if raw_severity in {"blocking", "warning", "optional", *severity_map}
                else "warning",
                "title": str(raw_suggestion.get("title") or "Review suggestion"),
                "reason": str(raw_suggestion.get("reason") or "Review this detail."),
                "target": target,
                "before_value": raw_suggestion.get("before_value"),
                "after_value": raw_suggestion.get("after_value"),
            }
        )

    return {"scenes": scenes, "suggestions": suggestions}


def _timecode_to_ms(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = parts
    return (hours * 3600 + minutes * 60 + seconds) * 1000


class MockScriptProvider(ScriptProvider):
    heading_pattern = re.compile(
        r"^(?:\[(?P<start>\d{1,2}:\d{2})\s*[-–]\s*(?P<end>\d{1,2}:\d{2})\]\s*)?(?P<title>(?:INT\.|EXT\.|INT/EXT\.|##\s+(?:SCENE|OPENING|FINAL|[A-Z0-9]).*|SCENE\s+\d+).*)$",
        re.I,
    )

    def analyze(self, script: str, target_duration_seconds: int) -> dict[str, Any]:
        lines = [line.rstrip() for line in script.splitlines()]
        chunks: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("---") or line.startswith("# "):
                continue
            if "MAIN CHARACTERS" in line.upper() or re.match(r"^\*\*[A-Z ]+\*\*\s*[—–\-]", line):
                continue
            if line.startswith("**FINAL MESSAGE"):
                continue

            heading = self.heading_pattern.match(line)
            if heading:
                if current:
                    chunks.append(current)
                title = heading.group("title").lstrip("#").strip()
                location, _, time_of_day = title.partition(" - ")
                current = {
                    "title": title,
                    "location": location.strip(),
                    "time_of_day": time_of_day.strip() or "Unspecified",
                    "start_ms": _timecode_to_ms(heading.group("start")) if heading.group("start") else None,
                    "end_ms": _timecode_to_ms(heading.group("end")) if heading.group("end") else None,
                    "action_lines": [],
                    "dialogue": [],
                }
                continue

            if current is None:
                continue

            # Check for character cue (e.g. **AMAKA:** or AMAKA: or AMAKA)
            cue_cand = line.replace("*", "").strip().rstrip(":")
            if cue_cand.isupper() and 2 <= len(cue_cand) <= 40 and not cue_cand.startswith("#"):
                current["pending_character"] = cue_cand
            elif current.get("pending_character"):
                text = line.strip().strip("\"\x27\u201c\u201d")
                if text:
                    char = current.pop("pending_character")
                    current["dialogue"].append({
                        "character": char,
                        "speaker": char,
                        "text": text,
                        "line": text,
                    })
            else:
                current["action_lines"].append(line)

        if current:
            chunks.append(current)
        if not chunks:
            chunks.append(
                {
                    "title": "SCENE 1 - UNSPECIFIED",
                    "location": "Unspecified",
                    "time_of_day": "Unspecified",
                    "start_ms": None,
                    "end_ms": None,
                    "action_lines": [script.strip()],
                    "dialogue": [],
                }
            )

        default_scene_ms = max(5000, target_duration_seconds * 1000 // len(chunks))
        scenes: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        cursor = 0
        for index, chunk in enumerate(chunks):
            start_ms = chunk["start_ms"]
            end_ms = chunk["end_ms"]
            if start_ms is None or end_ms is None:
                suggested_start = cursor
                suggested_end = cursor + default_scene_ms
                suggestions.append(
                    {
                        "category": "timing",
                        "severity": "warning",
                        "title": f"Add timing for Scene {index + 1}",
                        "reason": "Every scene needs a timeline range before shot planning.",
                        "target": {"scene_index": index, "field": "timing"},
                        "before_value": None,
                        "after_value": {"start_ms": suggested_start, "end_ms": suggested_end},
                    }
                )
                start_ms, end_ms = suggested_start, suggested_end
            cursor = end_ms
            action = " ".join(chunk["action_lines"]).strip()
            if not action:
                suggestions.append(
                    {
                        "category": "production_detail",
                        "severity": "blocking",
                        "title": f"Describe the action in Scene {index + 1}",
                        "reason": "The scene has dialogue but no visual action for image and video prompting.",
                        "target": {"scene_index": index, "field": "action"},
                        "before_value": "",
                        "after_value": "Show the characters' physical actions and reactions during the exchange.",
                    }
                )
            duration = max(2000, end_ms - start_ms)
            shot_count = max(1, (duration + 7999) // 8000)
            shot_duration = duration // shot_count
            scenes.append(
                {
                    "title": chunk["title"],
                    "location": chunk["location"],
                    "time_of_day": chunk["time_of_day"],
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "action": action,
                    "dialogue": chunk["dialogue"],
                    "shots": [
                        {
                            "order_index": shot_index,
                            "duration_ms": shot_duration,
                            "framing": "Medium" if shot_index == 0 else "Close-up",
                            "image_prompt": f"Cinematic frame, {chunk['title']}. {action or 'Character reaction and visual storytelling.'}",
                            "video_prompt": "Natural character movement, controlled camera motion, preserve identity and continuity.",
                            "dialogue": chunk["dialogue"] if shot_index == 0 else [],
                        }
                        for shot_index in range(shot_count)
                    ],
                }
            )

        return {"scenes": scenes, "suggestions": suggestions}


class OllamaScriptProvider(ScriptProvider):
    def analyze(self, script: str, target_duration_seconds: int) -> dict[str, Any]:
        prompt = f"""Analyze this screenplay for a movie creator. Return JSON with arrays named scenes and suggestions.
Each scene must include title, location, time_of_day, start_ms, end_ms, action, dialogue, and shots.
Each shot must be 2-10 seconds and include order_index, duration_ms, framing, image_prompt, video_prompt.
Suggestions must include category, severity, title, reason, target, before_value, after_value.
Timing reference: approximately {target_duration_seconds} seconds. This is guidance only; preserve every supplied scene, shot, and timestamp.

SCREENPLAY:\n{script}"""
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "stream": False,
                "format": "json",
                "think": False,
                "keep_alive": "10m",
                "messages": [
                    {"role": "system", "content": "You are a meticulous screenplay editor and shot planner."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=180,
        )
        response.raise_for_status()
        return loads_model_json(response.json()["message"]["content"])

    def review(
        self, structured_script: dict[str, Any], target_duration_seconds: int
    ) -> dict[str, Any]:
        prompt = f"""Review this already-parsed screenplay shot plan. Return JSON with one array named suggestions.
Do not add, remove, merge, reorder, or rewrite scenes or shots.
Only flag continuity, timing, dialogue, sound, feasibility, and prompt-quality problems.
Each suggestion needs category, severity, title, reason, target, before_value, and after_value.
Target must be an object with scene_index, shot_index, and field when it applies to one shot.
Use blocking only when production cannot proceed. Return an empty suggestions array when no changes are needed.
Timing reference: approximately {target_duration_seconds} seconds. This is guidance only; do not flag or alter valid supplied material solely to match it.

PARSED SHOT PLAN:
{json.dumps(structured_script, ensure_ascii=False)}"""
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "stream": False,
                "format": "json",
                "think": False,
                "keep_alive": "10m",
                "messages": [
                    {
                        "role": "system",
                        "content": "You review parsed shot plans without changing their structure.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=180,
        )
        response.raise_for_status()
        return loads_model_json(response.json()["message"]["content"])


class CompatibleScriptProvider(ScriptProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        new_session: bool = False,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.new_session = new_session

    def analyze(self, script: str, target_duration_seconds: int) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. You are a meticulous screenplay editor and shot planner.",
                },
                {
                    "role": "user",
                    "content": f"""Analyze this screenplay and return an object with scenes and suggestions arrays.
Scenes need title, location, time_of_day, start_ms, end_ms, action, dialogue, and 2-10 second shots.
Shots need order_index, duration_ms, framing, image_prompt, and video_prompt.
Suggestions need category, severity, title, reason, target, before_value, and after_value.
Timing reference: approximately {target_duration_seconds} seconds. This is guidance only; preserve every supplied scene, shot, and timestamp.\n\n{script}""",
                },
            ],
        }
        if self.new_session:
            payload["new_session"] = True
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        return loads_model_json(response.json()["choices"][0]["message"]["content"])

    def review(
        self, structured_script: dict[str, Any], target_duration_seconds: int
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. Review parsed shot plans without changing their structure.",
                },
                {
                    "role": "user",
                    "content": f"""Return an object with one suggestions array for this already-parsed shot plan.
Do not add, remove, merge, reorder, or rewrite scenes or shots. Only flag continuity, timing, dialogue, sound, feasibility, and prompt-quality problems.
Suggestions need category, severity, title, reason, target, before_value, and after_value. Target should contain scene_index, shot_index, and field when applicable.
Timing reference: approximately {target_duration_seconds} seconds. This is guidance only; do not flag or alter valid supplied material solely to match it.

{json.dumps(structured_script, ensure_ascii=False)}""",
                },
            ],
        }
        if self.new_session:
            payload["new_session"] = True
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        return loads_model_json(response.json()["choices"][0]["message"]["content"])


def get_script_provider(provider_id: str | None = None) -> ScriptProvider:
    selected = provider_id or settings.text_provider
    if selected == "ollama":
        return OllamaScriptProvider()
    if selected == "gemini_web":
        return CompatibleScriptProvider(
            settings.gemini_web_base_url,
            settings.gemini_web_model,
        )
    if selected == "chatgpt_web":
        return CompatibleScriptProvider(
            settings.chatgpt_web_base_url,
            settings.chatgpt_web_model,
            settings.chatgpt_web_api_key,
            new_session=True,
        )
    if selected == "compatible":
        return CompatibleScriptProvider(
            settings.compatible_base_url,
            settings.compatible_model,
            settings.compatible_api_key,
        )
    return MockScriptProvider()
