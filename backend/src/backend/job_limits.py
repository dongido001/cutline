"""Per-project active-job caps to keep one film from starving the machine."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Job
from .settings import settings

IMAGE_KINDS = frozenset({"shot_image", "cast_reference", "release_cover"})
VIDEO_KINDS = frozenset({"shot_clip", "shot_lipsync", "create_draft", "publish"})
TEXT_KINDS = frozenset(
    {
        "script_analysis",
        "script_continuation",
        "deep_review",
        "deep_review_draft",
        "release_copy",
    }
)


class JobCapacityExceeded(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _lane_for_kind(kind: str) -> str | None:
    if kind in IMAGE_KINDS:
        return "image"
    if kind in VIDEO_KINDS or kind.startswith("final_cut"):
        return "video"
    if kind in TEXT_KINDS:
        return "text"
    return None


def _limit_for_lane(lane: str) -> int:
    if lane == "image":
        return settings.max_active_image_jobs_per_project
    if lane == "video":
        return settings.max_active_video_jobs_per_project
    if lane == "text":
        return settings.max_active_text_jobs_per_project
    return 0


def _active_count(db: Session, project_id: str, lane: str) -> int:
    query = select(func.count(Job.id)).where(
        Job.project_id == project_id,
        Job.status.in_(["queued", "running"]),
    )
    if lane == "image":
        query = query.where(Job.kind.in_(IMAGE_KINDS))
    elif lane == "video":
        query = query.where(
            or_(Job.kind.in_(VIDEO_KINDS), Job.kind.startswith("final_cut"))
        )
    elif lane == "text":
        query = query.where(Job.kind.in_(TEXT_KINDS))
    return int(db.scalar(query) or 0)


def raise_if_over_capacity(db: Session, project_id: str, kind: str) -> None:
    """Raise JobCapacityExceeded when this project is already at its lane cap."""
    lane = _lane_for_kind(kind)
    if lane is None:
        return
    limit = _limit_for_lane(lane)
    if limit <= 0:
        return
    active = _active_count(db, project_id, lane)
    if active >= limit:
        raise JobCapacityExceeded(
            f"Project already has {active} active {lane} job(s) "
            f"(limit {limit}). Wait for one to finish or cancel it."
        )


def ensure_project_job_capacity(db: Session, project_id: str, kind: str) -> None:
    """API helper: raise HTTP 429 when the project lane is full."""
    try:
        raise_if_over_capacity(db, project_id, kind)
    except JobCapacityExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.detail) from exc
