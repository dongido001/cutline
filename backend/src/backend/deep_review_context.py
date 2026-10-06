"""Read-only review context, evidence validation, and production preflight checks."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Character, Episode, Scene, ScriptVersion
from .screenplay_parser import SHOT_HEADING, TRANSITION_LINE, canonical_character_name, expand_character_cue_names
from .timing import estimate_dialogue_duration_ms


SHOT_FIELDS = (
    "title", "start_ms", "end_ms", "duration_ms", "framing", "action", "dialogue",
    "sound", "image_prompt", "video_prompt", "title_card_text", "character_ids",
)
SCENE_FIELDS = (
    "title", "location", "time_of_day", "start_ms", "end_ms", "action", "dialogue",
    "set_notes", "lighting_notes", "wardrobe_notes", "props_notes",
)
PATCH_FIELDS = {"title", "framing", "action", "dialogue", "sound", "image_prompt", "video_prompt"}
KINDS = {"confirmed_inconsistency", "possible_gap", "editorial_suggestion"}


def _episode_context(version: ScriptVersion, *, draft: bool = False) -> dict[str, Any]:
    episode = version.episode
    result: dict[str, Any] = {
        "version_id": version.id,
        "episode_id": version.episode_id,
        "episode_number": episode.episode_number if episode else None,
        "title": episode.title if episode else version.project.title,
        "source_text": version.source_text or "",
        "draft": draft,
        "scenes": [],
    }
    number = 0
    for scene in sorted(version.scenes, key=lambda item: item.order_index):
        entry = {"id": scene.id, "order_index": scene.order_index,
                 **{field: getattr(scene, field) for field in SCENE_FIELDS}, "shots": []}
        for shot in sorted(scene.shots, key=lambda item: item.order_index):
            number += 1
            entry["shots"].append({
                "id": shot.id, "order_index": shot.order_index, "shot_number": number,
                **{field: getattr(shot, field) for field in SHOT_FIELDS},
            })
        result["scenes"].append(entry)
    return result


def build_context(db: Session, version: ScriptVersion) -> dict[str, Any]:
    """Prefer approved history; unparsed and future episodes are never included."""
    previous = []
    if version.episode:
        episodes = db.scalars(select(Episode).where(
            Episode.project_id == version.project_id,
            Episode.episode_number < version.episode.episode_number,
        ).order_by(Episode.episode_number)).all()
        for episode in episodes:
            versions = db.scalars(select(ScriptVersion).where(
                ScriptVersion.episode_id == episode.id,
                ScriptVersion.project_id == version.project_id,
            ).options(selectinload(ScriptVersion.scenes).selectinload(Scene.shots))
              .order_by(ScriptVersion.version_number.desc(), ScriptVersion.created_at.desc())).all()
            parsed = [item for item in versions if item.scenes]
            approved = [item for item in parsed if item.status == "approved"]
            selected = (approved or parsed)
            if selected:
                previous.append(_episode_context(selected[0], draft=not approved))
    cast = db.scalars(select(Character).where(Character.project_id == version.project_id)
                      .order_by(Character.name, Character.id)).all()
    return {
        "project": {"id": version.project_id, "title": version.project.title,
                    "aspect_ratio": version.project.aspect_ratio,
                    "target_duration_seconds": version.project.target_duration_seconds},
        "current": _episode_context(version),
        "previous": previous,
        "cast": [
            {
                "id": item.id,
                "name": item.name,
                "notes": item.notes or "",
                "voice_description": getattr(item, "voice_description", "") or "",
            }
            for item in cast
        ],
    }


def fingerprint(context: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_text(item)}" for key, item in value.items())
    return json.dumps(value, ensure_ascii=False)


def context_for_prompt(context: dict[str, Any]) -> dict[str, Any]:
    """Prior source appears once; retain source indexes and every unmatched saved value.

    This is lossless deduplication, not an AI summary. Original source remains intact,
    including prose the parser missed. Full internal context remains the evidence registry.
    """
    previous = []
    for episode in context.get("previous", []):
        source = _normalized(episode["source_text"])
        compact = {key: value for key, value in episode.items() if key != "scenes"}
        compact["scenes"] = []
        for scene in episode["scenes"]:
            entry = {"id": scene["id"], "order_index": scene["order_index"], "shots": []}
            for field in SCENE_FIELDS:
                value = scene.get(field)
                if value not in (None, "", []) and (
                    not isinstance(value, str) or _normalized(value) not in source
                ):
                    entry[field] = value
            for shot in scene["shots"]:
                item = {key: shot[key] for key in ("id", "order_index", "shot_number", "title")}
                for field in SHOT_FIELDS:
                    value = shot.get(field)
                    if value not in (None, "", []) and (
                        not isinstance(value, str) or _normalized(value) not in source
                    ):
                        item[field] = value
                entry["shots"].append(item)
            compact["scenes"].append(entry)
        previous.append(compact)
    return {**context, "previous": previous}


def _shots(episode: dict[str, Any]) -> list[dict[str, Any]]:
    return [shot for scene in episode.get("scenes", []) for shot in scene.get("shots", [])]


def _source_shot(episode: dict[str, Any], shot: dict[str, Any]) -> str:
    source = episode.get("source_text", "")
    headings = list(SHOT_HEADING.finditer(source))
    index = shot.get("shot_number", 0) - 1
    if index < 0 or index >= len(headings):
        return ""
    start = headings[index].start()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
    return source[start:end]


def _validate_evidence(raw: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("quote"), str):
        return None
    quote = raw["quote"].strip()
    if not quote:
        return None
    episodes = [context["current"], *context.get("previous", [])]
    episode = next((item for item in episodes if item["version_id"] == raw.get("version_id")), None)
    if not episode:
        return None
    shot_id, field = raw.get("shot_id"), raw.get("field")
    shot = None
    if shot_id:
        shot = next((item for item in _shots(episode) if item["id"] == shot_id), None)
        if not shot:
            return None
        if field == "source_text":
            text = _source_shot(episode, shot)
        elif field:
            if field not in SHOT_FIELDS:
                return None
            text = _text(shot.get(field))
        else:
            text = _text({key: shot.get(key) for key in SHOT_FIELDS}) + "\n" + _source_shot(episode, shot)
    else:
        if field and field != "source_text":
            return None
        text = episode["source_text"]
    if _normalized(quote) not in _normalized(text):
        return None
    label = f"Episode {episode['episode_number']}" if episode.get("episode_number") else episode["title"]
    if shot:
        label += f" · Shot {shot['shot_number']}"
    return {"version_id": episode["version_id"], "episode_id": episode.get("episode_id"),
            "shot_id": shot_id or None, "field": field or None, "quote": quote, "label": label}


def validate_patches(raw: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Only current-shot, nonstructural, exact-value replacements are executable."""
    if not isinstance(raw, list):
        raise ValueError("Proposed changes must be a list")
    shots = {shot["id"]: shot for shot in _shots(context["current"])}
    patches, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each proposed change must identify a shot and field")
        shot_id, field = item.get("shot_id"), item.get("field")
        if not isinstance(shot_id, str) or not isinstance(field, str):
            raise ValueError("Each proposed change must identify a shot and field")
        shot = shots.get(shot_id)
        if not shot or field not in PATCH_FIELDS or "before" not in item or "after" not in item:
            raise ValueError("Proposed changes may only replace supported current-shot fields")
        if (shot_id, field) in seen or item["before"] != shot.get(field):
            raise ValueError("A proposed change is duplicated or its original value no longer matches")
        value = item["after"]
        if field == "dialogue":
            if not isinstance(value, list) or any(
                not isinstance(line, dict)
                or not isinstance(line.get("character") or line.get("speaker"), str)
                or not isinstance(line.get("text"), str) for line in value
            ):
                raise ValueError("Dialogue changes must contain character and text entries")
        elif not isinstance(value, str):
            raise ValueError("Text changes must be strings")
        if (field == "title" and len(value) > 200) or (field == "framing" and len(value) > 80):
            raise ValueError("Proposed title or framing exceeds its supported length")
        seen.add((shot_id, field))
        patches.append({key: item[key] for key in ("shot_id", "field", "before", "after")})
    return patches


def validate_report(raw: Any, context: dict[str, Any],
                    preflight_findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("assessment"), str):
        raise ValueError("The reviewer must return an assessment and findings")
    findings = raw.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("Review findings must be a list")
    report = {"assessment": raw["assessment"], "strengths": [], "priorities": [], "findings": []}
    for field in ("strengths", "priorities"):
        values = raw.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"Review {field} must be a list of strings")
        report[field] = values
    for item in [*(preflight_findings or []), *findings]:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        if any(not isinstance(item.get(field), str) or not item[field].strip()
               for field in ("title", "reason", "recommendation")):
            continue
        entries = item.get("evidence", [])
        if not isinstance(entries, list):
            entries = []
        evidence = [value for entry in entries if (value := _validate_evidence(entry, context))]
        # Unsupported claims cannot appear as evidence-backed findings of any kind.
        if not evidence:
            continue
        kind = item["kind"]
        incomplete = len(evidence) != len(entries)
        if kind == "confirmed_inconsistency" and incomplete:
            kind = "possible_gap"
        try:
            patches = validate_patches(item.get("patches", []), context)
        except ValueError:
            patches = []
        report["findings"].append({
            "id": str(uuid.uuid4()), "kind": kind,
            "category": str(item.get("category") or "story"),
            "title": item["title"], "reason": item["reason"],
            "recommendation": item["recommendation"], "evidence": evidence,
            "evidence_incomplete": incomplete, "patches": patches, "status": "open",
        })
    return report


def preflight(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic facts and explicitly advisory production heuristics."""
    episode = context["current"]
    cast = {canonical_character_name(item["name"]) for item in context.get("cast", [])}
    findings = []

    def add(shot, category, title, reason, recommendation, field, quote, *, kind="possible_gap", extra=None):
        evidence = [{"version_id": episode["version_id"], "shot_id": shot["id"],
                     "field": field, "quote": quote}]
        if extra:
            evidence.append(extra)
        findings.append({"kind": kind, "category": category, "title": title,
                         "reason": reason, "recommendation": recommendation,
                         "evidence": evidence, "patches": []})

    previous = None
    for shot in _shots(episode):
        start, end, duration = shot.get("start_ms"), shot.get("end_ms"), shot.get("duration_ms")
        if start is not None and end is not None and end <= start:
            add(shot, "timing", "Shot ends before it starts", "The saved time range is not positive.",
                "Correct the saved time range before production.", "end_ms", str(end), kind="confirmed_inconsistency")
        elif start is not None and end is not None and duration != end - start:
            add(shot, "timing", "Saved duration differs from the time range",
                "The saved duration and explicit start/end range disagree.",
                "Reconcile the duration with the intended time range.", "duration_ms", str(duration),
                kind="confirmed_inconsistency")
        if previous and previous.get("end_ms") is not None and start is not None and start != previous["end_ms"]:
            add(shot, "timing", "Timeline gap or overlap", "Adjacent explicit shot time ranges are not continuous.",
                "Check whether the timeline gap or overlap is intentional.", "start_ms", str(start), extra={
                    "version_id": episode["version_id"], "shot_id": previous["id"],
                    "field": "end_ms", "quote": str(previous["end_ms"]),
                })
        if isinstance(duration, int) and not 2000 <= duration <= 10000:
            add(shot, "timing", "Check generated shot duration",
                "The saved duration falls outside the import check's 2–10 second generation range.",
                "Check the selected generator's duration limits.", "duration_ms", str(duration))
        dialogue = shot.get("dialogue") or []
        estimate = estimate_dialogue_duration_ms(dialogue, max_ms=3600000)
        if estimate and isinstance(duration, int) and estimate > duration + 500:
            add(shot, "timing", "Dialogue may exceed the shot duration",
                f"Estimated speech duration is {estimate / 1000:.1f}s versus {duration / 1000:.1f}s; this is an estimate.",
                "Read the dialogue aloud and adjust the wording or shot timing if needed.", "dialogue", _text(dialogue))
        speakers = set()
        for line in dialogue:
            if not isinstance(line, dict):
                continue
            cue = str(line.get("character") or line.get("speaker") or "")
            cue_parts = expand_character_cue_names(cue)
            names = [canonical_character_name(part) for part in (cue_parts or [cue])]
            names = [n for n in names if n]
            if names:
                for name in names:
                    speakers.add(name)
                if any(
                    len([item for item in cast if item == name or (" " not in name and item.split()[0] == name)]) != 1
                    for name in names
                ):
                    add(shot, "cast", f"Check dialogue speaker {cue}",
                        "The cue has no unique canonical cast match; first-name aliases are allowed when unambiguous.",
                        "Confirm the intended speaker and update the cast or cue.", "dialogue", cue)
            text = str(line.get("text") or "")
            for segment in text.splitlines():
                if TRANSITION_LINE.match(segment.strip()):
                    add(shot, "parser", "Possible stage direction in spoken dialogue",
                        "A transition appears inside the saved dialogue and may be sent to speech generation.",
                        "Move the transition into action unless it is intentionally spoken.", "dialogue", segment.strip())
                    break
        sound = shot.get("sound") or ""
        # ponytail: lexical heuristics remain advisory; use explicit audio roles if the schema adds them.
        if len(speakers) > 1 and re.search(r"filtered|phone\s+(?:voice|audio)|off[- ]screen", sound, re.IGNORECASE):
            add(shot, "production", "Check phone or off-screen speaker routing",
                "Multiple speakers share a shot with phone/off-screen audio; lip-sync and visible cast need explicit routing.",
                "Identify which speaker is visible and which is heard remotely.", "sound", sound)
        for field in ("framing", "video_prompt"):
            value = shot.get(field) or ""
            if re.search(r"alternating\s+close[- ]?ups|multi[- ]angle\s+cuts", value, re.IGNORECASE):
                add(shot, "production", "Multiple camera cuts inside one generated shot",
                    "The requested camera switches may be difficult to produce in a single continuous generated clip.",
                    "Use one composition or review the proposed cuts as separate production shots.", field, value)
                break
        previous = shot
    return findings
