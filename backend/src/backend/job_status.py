"""Shared job status helpers used by the HTTP API and WebSocket manager."""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .models import Job


def queue_kinds_for_job(job: Job) -> set[str]:
    if job.kind in {"shot_image", "cast_reference"}:
        return {"shot_image", "cast_reference"}
    if job.kind in {"shot_clip", "shot_lipsync"}:
        return {"shot_clip", "shot_lipsync"}
    return set()


def compute_queue_position(db: Session, job: Job) -> int | None:
    """1-based position among queued jobs in the same lane (COUNT, not full scan)."""
    if job.status != "queued":
        return None
    kinds = queue_kinds_for_job(job)
    if not kinds:
        return None
    ahead = db.scalar(
        select(func.count(Job.id)).where(
            Job.kind.in_(kinds),
            Job.status == "queued",
            or_(
                Job.created_at < job.created_at,
                and_(Job.created_at == job.created_at, Job.id <= job.id),
            ),
        )
    )
    return int(ahead or 0)


def compute_global_queue_position(db: Session, job: Job) -> int | None:
    """1-based position among all queued jobs (for the admin queue list)."""
    if job.status != "queued":
        return None
    ahead = db.scalar(
        select(func.count(Job.id)).where(
            Job.status == "queued",
            or_(
                Job.created_at < job.created_at,
                and_(Job.created_at == job.created_at, Job.id <= job.id),
            ),
        )
    )
    return int(ahead or 0)
