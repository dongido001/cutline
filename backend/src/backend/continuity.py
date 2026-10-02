"""Build and resolve the persisted visual continuity package for a shot."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cast import load_cast_references
from .models import Character, Project, Scene, Shot
from .settings import settings


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    scene = _scene_package(shot.scene)
    framing = _text(shot.framing)
    action = _text(shot.action)
    base_prompt = _text(prompt_override if prompt_override is not None else shot.image_prompt)
    return {
        "version": 1,
        "character_ids": selected_ids,
        "characters": cast,
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
    scene = _scene_package(shot.scene)
    return {
        "version": 1, "character_ids": selected_ids, "characters": characters,
        "scene": scene, "framing": _text(shot.framing), "action": _text(shot.action),
        "video_prompt": _text(shot.video_prompt), "aspect_ratio": aspect_ratio,
        "image_prompt_base": _text(shot.image_prompt),
        "effective_image_prompt": _image_prompt(
            _text(shot.image_prompt), characters, scene, _text(shot.framing), _text(shot.action),
        ),
    }


def video_continuity_prompt(snapshot: dict[str, Any] | None, video_prompt: str, aspect_ratio: str) -> str:
    anchor = "Approved storyboard frame: use the supplied image as the exact opening frame and visual anchor; preserve its composition, faces, wardrobe, set, props, and lighting throughout the clip."
    if not snapshot:
        return "\n\n".join(part for part in (video_prompt.strip(), anchor) if part)
    scene = snapshot.get("scene") or {}
    characters = snapshot.get("characters") or []
    cast_names = ", ".join(item.get("name", "") for item in characters if item.get("name"))
    package = [
        anchor,
        f"Shot framing: {snapshot.get('framing') or 'preserve the supplied frame'}. Aspect ratio: {aspect_ratio}.",
        f"Shot action: {snapshot.get('action') or 'continue the action naturally from the supplied frame'}.",
    ]
    if cast_names:
        package.append(f"Character identity continuity: {cast_names}; preserve canonical identities without face, age, hair, or body changes.")
    for key, label in (
        ("location", "Location"), ("time_of_day", "Time of day"),
        ("set_notes", "Set"), ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"), ("props_notes", "Props"),
    ):
        value = _text(scene.get(key))
        if value and value.lower() != "unspecified":
            package.append(f"{label} continuity: {value}.")
    return "\n\n".join([video_prompt.strip(), *package]).strip()


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
