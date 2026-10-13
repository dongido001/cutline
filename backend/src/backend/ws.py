from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .database import SessionLocal
from .job_status import compute_queue_position
from .models import Job, Scene, ScriptVersion, Shot, ShotTake, now
from .schemas import JobRead, ShotRead

logger = logging.getLogger(__name__)


def serialize_job_event(db: Session, job: Job) -> dict[str, Any]:
    queue_position = compute_queue_position(db, job)
    job_read = JobRead.model_validate(job).model_copy(update={"queue_position": queue_position})
    return {
        "type": "job.updated",
        "project_id": job.project_id,
        "episode_id": job.episode_id,
        "job": job_read.model_dump(mode="json"),
    }


def serialize_shot_event(db: Session, shot_id: str) -> dict[str, Any] | None:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.image),
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.takes),
            selectinload(Shot.image_references),
            selectinload(Shot.continuity_frames),
            selectinload(Shot.scene).selectinload(Scene.script_version),
        )
    )
    if not shot or not shot.scene or not shot.scene.script_version:
        return None
    version = shot.scene.script_version
    shot_read = ShotRead.model_validate(shot)
    return {
        "type": "shot.updated",
        "project_id": version.project_id,
        "episode_id": version.episode_id,
        "shot": shot_read.model_dump(mode="json"),
    }


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, str | None] = {}
        self._task: asyncio.Task[None] | None = None
        self._job_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._tracked_job_ids: set[str] = set()
        self._last_job_check: datetime = now()
        self._last_take_check: datetime = now()
        self._seen_take_ids: set[str] = set()

    async def connect(self, websocket: WebSocket, project_id: str | None = None) -> None:
        await websocket.accept()
        was_empty = len(self._connections) == 0
        self._connections[websocket] = project_id
        if was_empty:
            self._prime_cursors()
        await self._send_initial_snapshot(websocket, project_id)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def set_project(self, websocket: WebSocket, project_id: str | None) -> None:
        if websocket in self._connections:
            self._connections[websocket] = project_id
            await self._send_initial_snapshot(websocket, project_id)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._prime_cursors()
            self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _prime_cursors(self) -> None:
        current = now()
        self._last_job_check = current - timedelta(seconds=2)
        self._last_take_check = current
        try:
            with SessionLocal() as db:
                active = db.scalars(
                    select(Job).where(Job.status.in_(["queued", "running"]))
                ).all()
                self._tracked_job_ids = {job.id for job in active}
                for job in active:
                    qpos = compute_queue_position(db, job)
                    self._job_fingerprints[job.id] = self._fingerprint(job, qpos)
                recent_takes = db.scalars(
                    select(ShotTake.id).where(ShotTake.created_at >= current - timedelta(seconds=5))
                ).all()
                self._seen_take_ids = set(recent_takes)
        except Exception:
            pass

    @staticmethod
    def _fingerprint(job: Job, queue_position: int | None) -> tuple[Any, ...]:
        return (
            job.status,
            round(float(job.progress or 0.0), 4),
            job.message or "",
            job.error or "",
            job.provider or "",
            job.provider_phase or "",
            job.conversation_url or "",
            queue_position,
            len(job.attempts or []),
        )

    async def _send_initial_snapshot(self, websocket: WebSocket, project_id: str | None) -> None:
        try:
            events: list[dict[str, Any]] = [{"type": "connected", "project_id": project_id}]
            with SessionLocal() as db:
                stmt = select(Job).where(Job.status.in_(["queued", "running"]))
                if project_id:
                    stmt = stmt.where(Job.project_id == project_id)
                active_jobs = db.scalars(stmt.order_by(Job.created_at)).all()
                for job in active_jobs:
                    self._tracked_job_ids.add(job.id)
                    qpos = compute_queue_position(db, job)
                    self._job_fingerprints[job.id] = self._fingerprint(job, qpos)
                    events.append(serialize_job_event(db, job))
            for event in events:
                await websocket.send_json(event)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        if not self._connections:
            return
        target_project = event.get("project_id")
        stale: list[WebSocket] = []
        for ws, sub_project in list(self._connections.items()):
            if sub_project is not None and target_project is not None and sub_project != target_project:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    async def _watch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.35)
                if not self._connections:
                    continue
                events = await asyncio.to_thread(self._poll_db_changes)
                for event in events:
                    await self.broadcast(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("WebSocket watch loop error: %s", exc)
                await asyncio.sleep(1.0)

    def _poll_db_changes(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        check_started = now()
        cutoff = self._last_job_check - timedelta(seconds=1)
        with SessionLocal() as db:
            # 1. Check active or recently updated jobs
            active_jobs = db.scalars(
                select(Job).where(Job.status.in_(["queued", "running"]))
            ).all()
            recent_jobs = db.scalars(
                select(Job).where(Job.updated_at >= cutoff)
            ).all()
            tracked_jobs = (
                db.scalars(select(Job).where(Job.id.in_(self._tracked_job_ids))).all()
                if self._tracked_job_ids
                else []
            )
            by_id: dict[str, Job] = {}
            for job in (*active_jobs, *recent_jobs, *tracked_jobs):
                by_id[job.id] = job

            updated_shot_ids: list[str] = []

            for job_id, job in by_id.items():
                qpos = compute_queue_position(db, job)
                fp = self._fingerprint(job, qpos)
                prev_fp = self._job_fingerprints.get(job_id)
                if fp != prev_fp:
                    self._job_fingerprints[job_id] = fp
                    events.append(serialize_job_event(db, job))
                    # Whenever a shot generation job completes, guarantee its shot is broadcast
                    if job.status == "completed" and job.kind in {"shot_image", "shot_clip", "shot_lipsync"}:
                        for att in (job.attempts or []):
                            if isinstance(att, dict) and att.get("shot_id"):
                                sid = str(att["shot_id"])
                                if sid not in updated_shot_ids:
                                    updated_shot_ids.append(sid)
                if job.status in {"queued", "running"}:
                    self._tracked_job_ids.add(job_id)
                else:
                    self._tracked_job_ids.discard(job_id)

            if len(self._job_fingerprints) > 500:
                active_set = set(self._tracked_job_ids)
                self._job_fingerprints = {
                    k: v for k, v in self._job_fingerprints.items() if k in active_set
                }

            # 2. Check newly recorded ShotTakes (from image, clip, lipsync, or audio generation)
            take_cutoff = self._last_take_check - timedelta(seconds=2)
            new_takes = db.scalars(
                select(ShotTake)
                .where(ShotTake.created_at >= take_cutoff)
                .order_by(ShotTake.created_at)
            ).all()
            for take in new_takes:
                if take.id in self._seen_take_ids:
                    continue
                self._seen_take_ids.add(take.id)
                if take.shot_id not in updated_shot_ids:
                    updated_shot_ids.append(take.shot_id)

            if len(self._seen_take_ids) > 500:
                self._seen_take_ids.clear()

            for shot_id in updated_shot_ids:
                shot_event = serialize_shot_event(db, shot_id)
                if shot_event:
                    events.append(shot_event)

        self._last_job_check = check_started
        self._last_take_check = check_started
        return events


ws_manager = WebSocketManager()
