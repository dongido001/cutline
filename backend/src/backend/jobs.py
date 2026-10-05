"""Job cancellation helpers shared by the API and Huey workers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job
from .queue import chatgpt_huey, gemini_huey, general_huey, image_huey, video_huey


class JobCancelled(Exception):
    """Raised when a worker notices the job was cancelled mid-run."""


def raise_if_cancelled(db: Session, job_id: str) -> None:
    # Read through the database: workers retain Job objects while providers run.
    with db.no_autoflush:
        status = db.scalar(select(Job.status).where(Job.id == job_id))
    if status == "cancelled":
        raise JobCancelled()


def mark_job_cancelled(job: Job) -> None:
    job.status = "cancelled"
    job.message = "Cancelled"
    job.error = None
    job.provider_phase = "cancelled"


def revoke_job_task(job: Job) -> None:
    """Best-effort Huey revoke so queued (and retrying) work is skipped."""
    if not job.task_id:
        return
    kind = job.kind or ""
    if kind in {"shot_image", "cast_reference"}:
        targets = [image_huey]
    elif kind == "shot_clip" or kind.startswith("final_cut") or kind == "create_draft" or kind == "publish":
        targets = [video_huey]
    elif kind in {"script_analysis", "script_continuation"}:
        targets = [chatgpt_huey, gemini_huey, general_huey]
    else:
        targets = [image_huey, video_huey, chatgpt_huey, gemini_huey, general_huey]
    for huey in targets:
        try:
            huey.revoke_by_id(job.task_id)
        except Exception:
            continue


def preserve_cancelled(job: Job | None) -> bool:
    return job is not None and job.status == "cancelled"
