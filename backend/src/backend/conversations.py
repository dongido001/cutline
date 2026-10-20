"""Delete linked provider conversations from Cutline while preserving remote chat history and local media."""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job, Project, Scene, ScriptVersion, Shot, ShotTake
from .media import normalize_conversation_url


def clear_conversation(value, url: str):
    if isinstance(value, dict):
        return {key: None if key.endswith("conversation_url") and normalize_conversation_url(item) == url else clear_conversation(item, url)
                for key, item in value.items()}
    if isinstance(value, list):
        return [clear_conversation(item, url) for item in value]
    return value


def delete_project_conversation(db: Session, project_id: str, url: str) -> None:
    url = normalize_conversation_url(url)
    if not url or not re.match(r"^https?://(?:chatgpt\.com/c/|gemini\.google\.com/app/)[^\s]+", url.strip(), re.IGNORECASE):
        raise HTTPException(400, "Only linked ChatGPT or Gemini conversations can be removed")
    shots = list(db.scalars(select(Shot).join(Scene, Shot.scene_id == Scene.id).join(ScriptVersion, Scene.script_version_id == ScriptVersion.id).where(ScriptVersion.project_id == project_id)))
    takes = list(db.scalars(select(ShotTake).where(ShotTake.shot_id.in_([shot.id for shot in shots]))))
    jobs = list(db.scalars(select(Job).where(Job.project_id == project_id)))
    project = db.get(Project, project_id)
    records = [(row, "conversation_url") for row in [*shots, *jobs]]
    if project:
        records.append((project, "image_conversation_url"))
    records += [(row, "settings") for row in takes]
    records += [(row, "attempts") for row in jobs]
    linked = any(normalize_conversation_url(getattr(row, field)) == url if field.endswith("conversation_url")
                 else clear_conversation(getattr(row, field), url) != getattr(row, field)
                 for row, field in records)
    if not linked:
        raise HTTPException(404, "This conversation is not linked to this project")
    # A pending edit may refer to its shot before it has acquired a conversation URL.
    linked_shot_ids = {shot.id for shot in shots if normalize_conversation_url(shot.conversation_url) == url}
    linked_shot_ids.update(take.shot_id for take in takes if clear_conversation(take.settings, url) != take.settings)
    if any(
        job.status in {"queued", "running"} and (
            normalize_conversation_url(job.conversation_url) == url
            or clear_conversation(job.attempts, url) != job.attempts
            or any(isinstance(attempt, dict) and attempt.get("shot_id") in linked_shot_ids
                   for attempt in (job.attempts or []))
        )
        for job in jobs
    ):
        raise HTTPException(409, "This chat is in use by a queued or running job. Wait for that job to finish or cancel it, then delete the chat.")

    # Clear internal Cutline links while preserving the remote chat history in ChatGPT/Gemini
    for row, field in records:
        value = getattr(row, field)
        updated = (None if normalize_conversation_url(value) == url else value) if field.endswith("conversation_url") else clear_conversation(value, url)
        if updated != value:
            setattr(row, field, updated)
    db.commit()
