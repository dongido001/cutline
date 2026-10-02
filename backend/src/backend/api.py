from datetime import datetime, timezone
import html as html_lib
import json
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from .cast import is_usable_reference_image, sync_project_cast
from .continuity import get_or_create_shot_snapshot
from .database import Base, engine, get_db
from .final_cut import invalidate_final_cut
from .media import delete_media_file, image_provider_statuses
from .migrations import migrate_schema
from .video import video_provider_status
from .draft import enqueue_create_draft, project_readiness
from .takes import record_take, select_take
from .vibevoice_tts import synthesize_dialogue, vibevoice_status
from .models import Character, CharacterReference, Job, Project, ProjectFinalCut, ProjectVariantExport, Scene, ScriptVersion, Shot, ShotClip, ShotContinuityFrame, ShotImage, ShotImageReference, ShotMediaVariant, ShotTake, Suggestion, TimelineAsset, PublishRecord, now
from .schemas import (
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    VoiceAuditionRequest,
    VoiceAuditionResponse,
    VoiceProviderInfo,
    VoicePresetRead,
    CreateDraftRequest,
    JobRead,
    JobQueueItemRead,
    ImageProviderStatus,
    ProjectTimelineRead,
    ProjectApproval,
    ProjectContinuationCreate,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    ProviderStatus,
    PublishCreate,
    PublishPlanRead,
    PublishRecordRead,
    SceneRead,
    SceneUpdate,
    ShotImageGenerateRequest,
    ShotCreate,
    ShotRead,
    ShotUpdate,
    SuggestionDecision,
    SuggestionRead,
    TakeSelectRequest,
    TimelineDocument,
    TimelineUpdate,
)
from .settings import settings
from .publishing import build_publish_plan, provider_statuses as publish_provider_statuses, validate_publish_request
from .oauth import (
    youtube_auth_url, facebook_auth_url,
    handle_youtube_callback, handle_facebook_callback,
    disconnect_platform, all_token_statuses, token_status as oauth_token_status,
)
from .image_references import earlier_shots, media_sources, reference_snapshot, release_reference_asset, snapshot_filenames, validate_reference_bytes
from .jobs import mark_job_cancelled, revoke_job_task
from .tasks import (
    enqueue_cast_reference,
    enqueue_final_cut,
    enqueue_script_analysis,
    enqueue_script_continuation,
    enqueue_shot_clip,
    enqueue_shot_lipsync,
    enqueue_shot_image,
    enqueue_publish_project,
)
from .timeline import append_missing_shots, ensure_timeline, initial_document, promote_available_clips, validate_timeline


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "worker_queues": "chatgpt, gemini, image, general, video",
    }


@app.get("/api/providers", response_model=list[ProviderStatus])
def providers() -> list[ProviderStatus]:
    ollama_available = False
    try:
        response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=1)
        ollama_available = response.is_success
    except httpx.HTTPError:
        pass

    def bridge_available(base_url: str, api_key: str = "") -> bool:
        try:
            response = httpx.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=1,
            )
            return response.is_success
        except httpx.HTTPError:
            return False

    gemini_available = bridge_available(settings.gemini_web_base_url)
    chatgpt_available = bridge_available(
        settings.chatgpt_web_base_url, settings.chatgpt_web_api_key
    )
    compatible_available = bridge_available(
        settings.compatible_base_url, settings.compatible_api_key
    )
    return [
        ProviderStatus(
            id="ollama",
            label="Ollama",
            configured=True,
            available=ollama_available,
            model=settings.ollama_model,
            detail="Local model runtime",
        ),
        ProviderStatus(
            id="mock",
            label="Prototype parser",
            configured=True,
            available=True,
            model="deterministic-v1",
            detail="No credits required",
        ),
        ProviderStatus(
            id="gemini_web",
            label="Gemini Web",
            configured=True,
            available=gemini_available,
            model=settings.gemini_web_model,
            detail="Local Docker bridge",
        ),
        ProviderStatus(
            id="chatgpt_web",
            label="ChatGPT Web",
            configured=bool(settings.chatgpt_web_api_key),
            available=chatgpt_available,
            model=settings.chatgpt_web_model,
            detail="Local browser bridge",
        ),
        ProviderStatus(
            id="compatible",
            label="Custom bridge",
            configured=bool(settings.compatible_base_url),
            available=compatible_available,
            model=settings.compatible_model,
            detail="Other OpenAI-compatible endpoint",
        ),
    ]


@app.get("/api/video-provider", response_model=ProviderStatus)
def video_provider() -> ProviderStatus:
    provider_id, label, configured, available, model, detail = video_provider_status()
    return ProviderStatus(
        id=provider_id,
        label=label,
        configured=configured,
        available=available,
        model=model,
        detail=detail,
    )


@app.get("/api/audio-provider", response_model=ProviderStatus)
def audio_provider() -> ProviderStatus:
    configured, available, detail = vibevoice_status()
    return ProviderStatus(
        id="vibevoice",
        label="VibeVoice",
        configured=configured,
        available=available,
        model=settings.vibevoice_model,
        detail=detail,
    )


@app.get("/api/image-providers", response_model=list[ImageProviderStatus])
def image_providers() -> list[dict]:
    return image_provider_statuses()


@app.post("/api/projects", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Job:
    project = Project(
        title=payload.title,
        aspect_ratio=payload.aspect_ratio,
        target_duration_seconds=payload.target_duration_seconds,
        text_provider=payload.text_provider,
        status="analyzing",
    )
    db.add(project)
    db.flush()
    version = ScriptVersion(project_id=project.id, source_text=payload.script)
    db.add(version)
    db.flush()
    job = Job(project_id=project.id, kind="script_analysis")
    db.add(job)
    db.commit()
    task = enqueue_script_analysis(
        project.text_provider, project.id, version.id, job.id
    )
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.post(
    "/api/projects/{project_id}/continue",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def continue_project(
    project_id: str,
    payload: ProjectContinuationCreate,
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "shot_plan":
        raise HTTPException(
            status_code=409,
            detail="Approve the current script before adding a continuation",
        )
    active_job = db.scalar(
        select(Job).where(
            Job.project_id == project.id,
            Job.kind == "script_continuation",
            Job.status.in_(["queued", "running"]),
        )
    )
    if active_job:
        return active_job
    job = Job(project_id=project.id, kind="script_continuation")
    db.add(job)
    db.commit()
    task = enqueue_script_continuation(
        project.text_provider,
        project.id,
        job.id,
        payload.script,
        payload.target_duration_seconds,
    )
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/projects", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.updated_at.desc())))


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectDetail:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    version = db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .options(
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.image),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.clip),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.media_variants),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.takes),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.image_references),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.continuity_frames),
            selectinload(ScriptVersion.suggestions),
        )
        .order_by(ScriptVersion.version_number.desc())
    )
    data = ProjectSummary.model_validate(project).model_dump()
    data["current_version"] = version
    data["final_cut"] = project.final_cut
    timeline_revision = project.timeline.revision if project.timeline else 0
    data["variant_exports"] = [
        {
            **{
                field: getattr(export, field)
                for field in (
                    "id", "url", "mime_type", "duration_ms", "width", "height",
                    "clip_count", "fallback_count", "created_at", "variant", "timeline_revision",
                )
            },
            "is_stale": export.timeline_revision != timeline_revision,
        }
        for export in project.variant_exports
    ]
    data["publishes"] = db.scalars(
        select(PublishRecord)
        .where(PublishRecord.project_id == project.id)
        .order_by(PublishRecord.created_at.desc())
    ).all()
    characters = db.scalars(
        select(Character)
        .where(Character.project_id == project_id)
        .options(selectinload(Character.references))
        .order_by(Character.name)
    ).all()
    data["characters"] = characters
    return ProjectDetail.model_validate(data)


@app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, db: Session = Depends(get_db)) -> None:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    versions = db.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project.id)
        .options(
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.image),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.clip),
            selectinload(ScriptVersion.scenes)
            .selectinload(Scene.shots)
            .selectinload(Shot.media_variants),
        )
    ).all()
    filenames = {
        media.filename
        for version in versions
        for scene in version.scenes
        for shot in scene.shots
        for media in (shot.image, shot.clip, *shot.media_variants)
        if media
    }
    for version in versions:
        for scene in version.scenes:
            for shot in scene.shots:
                filenames.update(ref.filename for ref in shot.image_references)
                for take in shot.takes:
                    filenames.update(snapshot_filenames(take.settings))
    for attempts in db.scalars(select(Job.attempts).where(Job.project_id == project.id)):
        filenames.update(snapshot_filenames(attempts))
    if project.final_cut:
        filenames.add(project.final_cut.filename)
    filenames.update(item.filename for item in project.variant_exports)
    filenames.update(item.filename for item in project.timeline_assets)
    for character in db.scalars(
        select(Character)
        .where(Character.project_id == project.id)
        .options(selectinload(Character.references))
    ).all():
        filenames.update(ref.filename for ref in character.references)
    db.delete(project)
    db.commit()
    for filename in filenames:
        delete_media_file(filename)


@app.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    queue_position = None
    if job.kind in {"shot_image", "cast_reference"} and job.status == "queued":
        pending = db.scalars(
            select(Job)
            .where(Job.kind.in_(["shot_image", "cast_reference"]), Job.status == "queued")
            .order_by(Job.created_at, Job.id)
        ).all()
        queue_position = next((index for index, item in enumerate(pending, start=1) if item.id == job.id), None)
    result = JobRead.model_validate(job)
    return result.model_copy(update={"queue_position": queue_position})


@app.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in {"completed", "failed", "cancelled"}:
        return JobRead.model_validate(job)
    if job.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel a job in status {job.status}")
    mark_job_cancelled(job)
    db.commit()
    db.refresh(job)
    revoke_job_task(job)
    return JobRead.model_validate(job)


@app.get("/api/jobs", response_model=list[JobQueueItemRead])
def list_all_jobs(
    status: str | None = None,
    kind: str | None = None,
    limit: int = 80,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(Job).order_by(Job.created_at.desc())
    if status:
        if status == "active":
            query = query.where(Job.status.in_(["queued", "running"]))
        else:
            query = query.where(Job.status == status)
    if kind:
        query = query.where(Job.kind == kind)
    query = query.limit(min(limit, 300)).offset(offset)
    jobs = list(db.scalars(query).all())

    project_ids = {j.project_id for j in jobs if j.project_id}
    projects = {p.id: p.title for p in db.scalars(select(Project).where(Project.id.in_(project_ids))).all()} if project_ids else {}

    results = []
    now_ts = now().timestamp()
    for job in jobs:
        queue_pos = None
        if job.status == "queued":
            pending_ids = list(db.scalars(
                select(Job.id).where(Job.status == "queued", Job.created_at <= job.created_at)
            ).all())
            queue_pos = len(pending_ids)
        def _to_utc(dt):
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        data = JobRead.model_validate(job).model_dump(mode="json")
        data["project_title"] = projects.get(job.project_id, "Untitled project")
        created_dt = _to_utc(job.created_at)
        updated_dt = _to_utc(job.updated_at)
        created_ts = created_dt.timestamp() if created_dt else now_ts
        updated_ts = updated_dt.timestamp() if updated_dt else now_ts
        data["elapsed_seconds"] = max(0.0, round((updated_ts if job.status in {"completed", "failed", "cancelled"} else now_ts) - created_ts, 1))
        data["queue_position"] = queue_pos
        results.append(data)
    return results


@app.get("/api/system/queues")
def system_queue_status(db: Session = Depends(get_db)) -> dict:
    from .queue import chatgpt_huey, gemini_huey, general_huey, image_huey, video_huey
    queues = [
        {"id": "image", "name": "Image Worker", "pending": image_huey.pending_count(), "scheduled": image_huey.scheduled_count(), "status": "active"},
        {"id": "video", "name": "Video Worker", "pending": video_huey.pending_count(), "scheduled": video_huey.scheduled_count(), "status": "active"},
        {"id": "chatgpt", "name": "ChatGPT Bridge", "pending": chatgpt_huey.pending_count(), "scheduled": chatgpt_huey.scheduled_count(), "status": "active"},
        {"id": "gemini", "name": "Gemini Bridge", "pending": gemini_huey.pending_count(), "scheduled": gemini_huey.scheduled_count(), "status": "active"},
        {"id": "general", "name": "General Worker", "pending": general_huey.pending_count(), "scheduled": general_huey.scheduled_count(), "status": "active"},
    ]
    active_count = db.scalar(select(func.count(Job.id)).where(Job.status.in_(["queued", "running"]))) or 0
    failed_count = db.scalar(select(func.count(Job.id)).where(Job.status == "failed")) or 0
    completed_count = db.scalar(select(func.count(Job.id)).where(Job.status == "completed")) or 0
    total_count = db.scalar(select(func.count(Job.id))) or 0

    bridges = []
    # ChatGPT Web Bridge
    chatgpt_ok = False
    chatgpt_detail = "Offline"
    try:
        chatgpt_host = settings.chatgpt_web_base_url.replace('/v1', '').rstrip('/')
        res = httpx.get(f"{chatgpt_host}/health", timeout=1.5)
        if res.is_success:
            chatgpt_ok = True
            chatgpt_detail = f"Online · {settings.chatgpt_web_model}"
    except Exception:
        pass
    bridges.append({"id": "chatgpt_web", "name": "ChatGPT Web Bridge", "url": settings.chatgpt_web_base_url, "available": chatgpt_ok, "detail": chatgpt_detail})

    # Gemini Web Bridge
    gemini_ok = False
    gemini_detail = "Offline"
    try:
        gemini_host = settings.gemini_web_base_url.replace('/openai/v1', '').replace('/v1', '').rstrip('/')
        res = httpx.get(f"{gemini_host}/health", timeout=1.5)
        if res.is_success:
            gemini_ok = True
            gemini_detail = f"Online · {settings.gemini_web_model}"
    except Exception:
        pass
    bridges.append({"id": "gemini_web", "name": "Gemini Web Bridge", "url": settings.gemini_web_base_url, "available": gemini_ok, "detail": gemini_detail})

    # Video Flowkit Bridge
    video_ok = False
    video_detail = "Offline"
    try:
        res = httpx.get(f"{settings.flowkit_base_url.rstrip('/')}/health", timeout=1.5)
        if res.is_success:
            video_ok = True
            video_detail = "Online"
    except Exception:
        pass
    bridges.append({"id": "video_provider", "name": "Video Generation Service", "url": settings.flowkit_base_url, "available": video_ok, "detail": video_detail})

    return {
        "queues": queues,
        "active_jobs": active_count,
        "failed_jobs": failed_count,
        "completed_jobs": completed_count,
        "total_jobs": total_count,
        "bridges": bridges,
    }


@app.post("/api/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")

    task = None
    if job.kind == "shot_image":
        shot_id = job.attempts[0].get("shot_id") if job.attempts else None
        aspect_ratio = job.attempts[0].get("aspect_ratio") if job.attempts else "16:9"
        char_ids = job.attempts[0].get("character_ids") if job.attempts else None
        if not shot_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing shot information")
        task = enqueue_shot_image(shot_id, job.id, aspect_ratio, character_ids=char_ids)
    elif job.kind == "shot_clip":
        shot_id = job.attempts[0].get("shot_id") if job.attempts else None
        aspect_ratio = job.attempts[0].get("aspect_ratio") if job.attempts else "16:9"
        if not shot_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing shot information")
        task = enqueue_shot_clip(shot_id, job.id, aspect_ratio)
    elif job.kind == "create_draft":
        variant = job.attempts[0].get("variant", "landscape") if job.attempts else "landscape"
        task = enqueue_create_draft(job.project_id, job.id, variant, retry_failed_only=True)
    elif job.kind == "final_cut" or job.kind.startswith("final_cut:"):
        variant = job.attempts[0].get("variant") if job.attempts else None
        if not variant and ":" in job.kind:
            variant = job.kind.split(":", 1)[1]
        variant = variant or "landscape"
        task = enqueue_final_cut(job.project_id, job.id, variant)
    elif job.kind == "shot_lipsync":
        shot_id = job.attempts[0].get("shot_id") if job.attempts else None
        aspect_ratio = job.attempts[0].get("aspect_ratio") if job.attempts else None
        if not shot_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing shot information")
        task = enqueue_shot_lipsync(shot_id, job.id, aspect_ratio)
    elif job.kind == "cast_reference":
        char_id = job.attempts[0].get("character_id") if job.attempts else None
        if not char_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing character information")
        task = enqueue_cast_reference(char_id, job.id)
    elif job.kind == "publish":
        publish_record_id = job.attempts[0].get("publish_record_id") if job.attempts else None
        if not publish_record_id:
            rec = db.scalar(select(PublishRecord).where(PublishRecord.job_id == job.id))
            publish_record_id = rec.id if rec else None
        if not publish_record_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing publish record")
        task = enqueue_publish_project(job.project_id, job.id, publish_record_id)
    else:
        raise HTTPException(status_code=400, detail=f"Retry is not supported for job kind '{job.kind}'")

    job.status = "queued"
    job.progress = 0.0
    job.message = "Queued for retry"
    job.error = None
    job.task_id = task.id if task else job.task_id
    db.commit()
    db.refresh(job)
    return job


@app.post("/api/jobs/cancel-all")
def cancel_all_active_jobs(db: Session = Depends(get_db)) -> dict:
    active_jobs = list(db.scalars(select(Job).where(Job.status.in_(["queued", "running"]))).all())
    count = 0
    for job in active_jobs:
        mark_job_cancelled(job)
        revoke_job_task(job)
        count += 1
    db.commit()
    return {"cancelled": count}


@app.delete("/api/jobs/cleanup")
def cleanup_finished_jobs(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if status_filter == "completed":
        statuses = ["completed"]
    elif status_filter == "failed":
        statuses = ["failed"]
    elif status_filter == "cancelled":
        statuses = ["cancelled"]
    else:
        statuses = ["completed", "cancelled"]
    deleted = db.execute(delete(Job).where(Job.status.in_(statuses))).rowcount
    db.commit()
    return {"deleted": deleted}


@app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_single_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in {"running", "queued"}:
        revoke_job_task(job)
    db.delete(job)
    db.commit()


@app.post("/api/system/queues/{queue_name}/flush")
def flush_queue(queue_name: str, db: Session = Depends(get_db)) -> dict:
    from .queue import chatgpt_huey, gemini_huey, general_huey, image_huey, video_huey
    queue_map = {
        "image": image_huey,
        "video": video_huey,
        "chatgpt": chatgpt_huey,
        "gemini": gemini_huey,
        "general": general_huey,
    }
    target = queue_map.get(queue_name)
    if not target:
        raise HTTPException(status_code=404, detail="Queue not found")
    target.flush()

    # Cancel phantom/orphaned jobs in DB corresponding to this queue
    kind_map = {
        "image": ["shot_image", "cast_reference"],
        "video": ["shot_clip", "shot_lipsync", "final_cut"],
        "chatgpt": ["create_draft"],
        "gemini": ["create_draft"],
        "general": ["create_draft", "export"],
    }
    kinds = kind_map.get(queue_name, [])
    queued_jobs = list(db.scalars(
        select(Job).where(Job.status == "queued")
    ).all())
    cancelled_count = 0
    for job in queued_jobs:
        if not kinds or any(job.kind == k or job.kind.startswith(f"{k}:") for k in kinds):
            job.status = "cancelled"
            job.message = f"Cancelled: queue '{queue_name}' was flushed"
            cancelled_count += 1
    db.commit()

    return {"status": "ok", "message": f"Queue {queue_name} flushed ({cancelled_count} queued jobs cancelled)"}


@app.get("/api/system/services")
def get_system_services() -> list[dict]:
    from .services_manager import list_services
    return list_services()


@app.post("/api/system/services/{service_id}/start")
def start_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    result = svc.start()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start service"))
    return result


@app.post("/api/system/services/{service_id}/stop")
def stop_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc.stop()


@app.post("/api/system/services/{service_id}/restart")
def restart_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    result = svc.restart()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to restart service"))
    return result


@app.get("/api/system/services/{service_id}/logs")
def get_system_service_logs(service_id: str, lines: int = 80) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": service_id, "logs": svc.get_logs(lines=lines)}


@app.post("/api/scenes/{scene_id}/shots", response_model=ShotRead, status_code=status.HTTP_201_CREATED)
def create_shot(
    scene_id: str,
    payload: ShotCreate,
    db: Session = Depends(get_db),
) -> Shot:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    project = scene.script_version.project
    existing_shots = sorted(scene.shots, key=lambda s: s.order_index)

    insert_idx = len(existing_shots)
    if payload.insert_after_shot_id:
        match_idx = next(
            (i for i, s in enumerate(existing_shots) if s.id == payload.insert_after_shot_id),
            None,
        )
        if match_idx is not None:
            insert_idx = match_idx + 1

    for s in existing_shots[insert_idx:]:
        s.order_index += 1

    title = payload.title.strip() if payload.title else f"Shot {insert_idx + 1}"
    image_prompt = payload.image_prompt.strip()
    if not image_prompt:
        if payload.action.strip():
            image_prompt = f"{scene.title}. {payload.action.strip()}"
        else:
            image_prompt = f"{scene.title}, {title}, cinematic lighting, photorealistic, 8k"

    normalized_dialogue = []
    for item in payload.dialogue:
        if isinstance(item, dict):
            char = str(item.get("character") or item.get("speaker") or "Character").strip() or "Character"
            txt = str(item.get("text") or item.get("line") or item.get("dialogue") or "").strip()
            if txt:
                normalized_dialogue.append({
                    "character": char,
                    "speaker": char,
                    "text": txt,
                    "line": txt,
                })

    new_shot = Shot(
        scene_id=scene.id,
        order_index=insert_idx,
        title=title,
        action=payload.action.strip(),
        dialogue=normalized_dialogue,
        sound=payload.sound.strip(),
        duration_ms=payload.duration_ms,
        framing=payload.framing,
        image_prompt=image_prompt,
        video_prompt=payload.video_prompt.strip(),
        title_card_text=payload.title_card_text.strip(),
        character_ids=list(dict.fromkeys(payload.character_ids)),
        status="planned",
    )
    db.add(new_shot)
    db.flush()

    scene_shots = db.scalars(
        select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.order_index)
    ).all()
    for idx, s in enumerate(scene_shots):
        s.order_index = idx

    if project.timeline:
        try:
            append_missing_shots(project, db)
        except Exception:
            pass

    invalidate_final_cut(project, db)
    db.commit()
    db.refresh(new_shot)
    return new_shot


@app.delete("/api/shots/{shot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shot(shot_id: str, db: Session = Depends(get_db)) -> None:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")

    scene = shot.scene
    project = scene.script_version.project

    files_to_delete: list[str] = []
    if shot.image and shot.image.filename:
        files_to_delete.append(shot.image.filename)
    if shot.clip and shot.clip.filename:
        files_to_delete.append(shot.clip.filename)
    if shot.dialogue_audio_filename:
        files_to_delete.append(shot.dialogue_audio_filename)
    for variant in shot.media_variants:
        if variant.filename:
            files_to_delete.append(variant.filename)
    for take in shot.takes:
        if take.filename:
            files_to_delete.append(take.filename)
    for frame in shot.continuity_frames:
        if frame.filename:
            files_to_delete.append(frame.filename)

    if project.timeline:
        for doc_attr in ("document", "shorts_document"):
            raw_doc = getattr(project.timeline, doc_attr)
            if not raw_doc:
                continue
            document = TimelineDocument.model_validate(raw_doc)
            orig_primary_len = len(document.primary)
            orig_image_overlay_len = len(document.image_overlays)
            document.primary = [
                item for item in document.primary
                if not (item.source and item.source.ref_id == shot_id)
            ]
            document.image_overlays = [
                overlay for overlay in document.image_overlays
                if not (overlay.source and overlay.source.ref_id == shot_id)
            ]
            if (
                len(document.primary) != orig_primary_len
                or len(document.image_overlays) != orig_image_overlay_len
            ):
                setattr(project.timeline, doc_attr, document.model_dump(mode="json"))
                project.timeline.revision += 1

    invalidate_final_cut(project, db)
    scene_id = scene.id
    db.delete(shot)
    db.flush()

    remaining_shots = db.scalars(
        select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order_index)
    ).all()
    for idx, s in enumerate(remaining_shots):
        s.order_index = idx

    db.commit()

    for fname in files_to_delete:
        delete_media_file(fname)


@app.patch("/api/shots/{shot_id}", response_model=ShotRead)
def update_shot(
    shot_id: str, payload: ShotUpdate, db: Session = Depends(get_db)
) -> Shot:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    invalidated_filename = None
    if shot.duration_ms != payload.duration_ms:
        invalidated_filename = invalidate_final_cut(
            shot.scene.script_version.project, db
        )
    if payload.title is not None:
        shot.title = payload.title.strip()
    if payload.action is not None:
        shot.action = payload.action.strip()
    if payload.sound is not None:
        shot.sound = payload.sound.strip()
    shot.framing = payload.framing
    shot.duration_ms = payload.duration_ms
    shot.image_prompt = payload.image_prompt
    shot.video_prompt = payload.video_prompt
    shot.title_card_text = payload.title_card_text
    if payload.character_ids is not None:
        project = shot.scene.script_version.project
        requested = list(dict.fromkeys(payload.character_ids))
        if requested:
            characters = db.scalars(
                select(Character).where(
                    Character.project_id == project.id,
                    Character.id.in_(requested),
                )
            ).all()
            if len(characters) != len(requested):
                raise HTTPException(status_code=404, detail="One or more cast characters were not found")
        shot.character_ids = requested
    if payload.dialogue is not None:
        normalized_dialogue = []
        for item in payload.dialogue:
            if isinstance(item, dict):
                char = str(item.get("character") or item.get("speaker") or "Character").strip() or "Character"
                txt = str(item.get("text") or item.get("line") or item.get("dialogue") or "").strip()
                if txt:
                    normalized_dialogue.append({
                        "character": char,
                        "speaker": char,
                        "text": txt,
                        "line": txt,
                    })
        shot.dialogue = normalized_dialogue
    db.commit()
    db.refresh(shot)
    delete_media_file(invalidated_filename)
    return shot


@app.post(
    "/api/shots/{shot_id}/image/generate",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_shot_image(
    shot_id: str,
    aspect_ratio: str | None = None,
    payload: ShotImageGenerateRequest = Body(default_factory=ShotImageGenerateRequest),
    db: Session = Depends(get_db),
) -> Job:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(
            status_code=409, detail="Approve the script before generating images"
        )
    if not shot.image_prompt.strip():
        raise HTTPException(
            status_code=409, detail="Add an image prompt before generating"
        )
    target_aspect = payload.aspect_ratio or aspect_ratio or project.aspect_ratio
    if target_aspect not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    requested_ids = (
        list(dict.fromkeys(payload.character_ids))
        if payload.character_ids is not None
        else None
    )
    if requested_ids is not None:
        valid_ids = set(db.scalars(
            select(Character.id).where(
                Character.project_id == project.id,
                Character.id.in_(requested_ids),
            )
        ).all()) if requested_ids else set()
        if len(valid_ids) != len(requested_ids):
            raise HTTPException(status_code=404, detail="One or more cast characters were not found")
    image_reference_id = payload.image_reference_id
    if image_reference_id is not None:
        image_reference = reference_snapshot(shot, target_aspect, image_reference_id)
        if image_reference is None:
            raise HTTPException(
                status_code=409,
                detail="Selected image reference is no longer available for this aspect ratio",
            )
    else:
        active_ref = next(
            (ref for ref in shot.image_references if ref.aspect_ratio == target_aspect),
            None,
        )
        if active_ref:
            image_reference = reference_snapshot(shot, target_aspect, active_ref.id)
            image_reference_id = active_ref.id
        else:
            image_reference = None
    try:
        snapshot = get_or_create_shot_snapshot(
            db, project, shot, target_aspect,
            refresh=payload.refresh_continuity,
            character_ids=requested_ids,
        )
    except RuntimeError as exc:
        message = str(exc)
        code = 404 if "not found in this project" in message else 409
        raise HTTPException(status_code=code, detail=message) from exc
    character_ids = list(snapshot.get("character_ids") or [])
    job = Job(
        project_id=project.id,
        kind="shot_image",
        message="Queued",
        attempts=[
            {
                "shot_id": shot.id,
                "aspect_ratio": target_aspect,
                "character_ids": character_ids,
                "continuity_snapshot": snapshot,
                "image_reference_id": image_reference_id,
                "image_reference": image_reference,
            }
        ],
    )
    db.add(job)
    db.commit()
    task = enqueue_shot_image(shot.id, job.id, target_aspect, character_ids=character_ids)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/projects/{project_id}/jobs", response_model=list[JobRead])
def list_project_jobs(
    project_id: str,
    active: bool = True,
    db: Session = Depends(get_db),
) -> list[JobRead]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    query = select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    if active:
        query = query.where(Job.status.in_(["queued", "running"]))
    jobs = list(db.scalars(query).all())
    results: list[JobRead] = []
    for job in jobs:
        queue_position = None
        if job.kind in {"shot_image", "cast_reference"} and job.status == "queued":
            pending = db.scalars(
                select(Job)
                .where(
                    Job.kind.in_(["shot_image", "cast_reference"]),
                    Job.status == "queued",
                )
                .order_by(Job.created_at, Job.id)
            ).all()
            queue_position = next(
                (index for index, item in enumerate(pending, start=1) if item.id == job.id),
                None,
            )
        results.append(JobRead.model_validate(job).model_copy(update={"queue_position": queue_position}))
    return results


@app.get("/api/projects/{project_id}/characters", response_model=list[CharacterRead])
def list_characters(project_id: str, db: Session = Depends(get_db)) -> list[Character]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .options(selectinload(Character.references))
            .order_by(Character.name)
        )
    )


@app.post("/api/projects/{project_id}/characters", response_model=CharacterRead, status_code=status.HTTP_201_CREATED)
def create_character(
    project_id: str,
    payload: CharacterCreate,
    db: Session = Depends(get_db),
) -> Character:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Character name is required")
    existing = db.scalar(
        select(Character).where(
            Character.project_id == project_id,
            Character.name == name,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="A character with that name already exists")
    character = Character(
        project_id=project_id,
        name=name,
        notes=payload.notes.strip(),
        voice_provider=payload.voice_provider or "vibevoice",
        voice_id=payload.voice_id,
        voice_settings=payload.voice_settings or {},
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


@app.patch("/api/characters/{character_id}", response_model=CharacterRead)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
) -> Character:
    character = db.scalar(
        select(Character)
        .where(Character.id == character_id)
        .options(selectinload(Character.references))
    )
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Character name is required")
        conflict = db.scalar(
            select(Character).where(
                Character.project_id == character.project_id,
                Character.name == name,
                Character.id != character.id,
            )
        )
        if conflict:
            raise HTTPException(status_code=409, detail="A character with that name already exists")
        character.name = name
    if payload.notes is not None:
        character.notes = payload.notes.strip()
    if payload.voice_provider is not None:
        character.voice_provider = payload.voice_provider.strip()
    if payload.voice_id is not None:
        character.voice_id = payload.voice_id.strip() if payload.voice_id else None
    if payload.voice_settings is not None:
        character.voice_settings = payload.voice_settings
    db.commit()
    db.refresh(character)
    return character


@app.get("/api/voices", response_model=list[VoiceProviderInfo])
def get_voices() -> list[VoiceProviderInfo]:
    from .voice_service import list_voice_providers
    return list_voice_providers()


@app.get("/api/voices/vibevoice/{voice_id}/sample")
def get_vibevoice_sample(voice_id: str):
    from fastapi.responses import FileResponse
    from .voice_service import get_vibevoice_sample_path
    path = get_vibevoice_sample_path(voice_id)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Voice sample not found")
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/voices/audition", response_model=VoiceAuditionResponse)
def audition_voice(payload: VoiceAuditionRequest) -> VoiceAuditionResponse:
    from .voice_service import synthesize_audition
    try:
        return synthesize_audition(
            provider=payload.provider,
            voice_id=payload.voice_id,
            text=payload.text,
            settings_dict=payload.settings,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/voices/custom", response_model=VoicePresetRead)
async def upload_custom_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    gender: str = Form("male"),
    language: str = Form("en-NG"),
    description: str = Form(""),
) -> VoicePresetRead:
    from .voice_service import save_custom_voice
    if not name.strip():
        raise HTTPException(status_code=422, detail="Voice name is required")
    file_bytes = await file.read()
    if len(file_bytes) < 500:
        raise HTTPException(status_code=400, detail="Audio file is too short or empty")
    try:
        return save_custom_voice(
            file_bytes=file_bytes,
            name=name.strip(),
            gender=gender.strip().lower() if gender in ("male", "female", "neutral") else "neutral",
            language=language.strip() or "en-NG",
            description=description.strip(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to process custom voice: {exc}")


@app.delete("/api/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: str, db: Session = Depends(get_db)) -> None:
    character = db.scalar(
        select(Character)
        .where(Character.id == character_id)
        .options(selectinload(Character.references))
    )
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    filenames = [ref.filename for ref in character.references]
    db.delete(character)
    db.commit()
    for filename in filenames:
        delete_media_file(filename)


@app.post("/api/characters/{character_id}/references", response_model=CharacterRead)
async def upload_character_reference(
    character_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Character:
    character = db.scalar(
        select(Character)
        .where(Character.id == character_id)
        .options(selectinload(Character.references))
    )
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    if file.content_type not in extensions:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if not is_usable_reference_image(content):
        raise HTTPException(status_code=400, detail="Image must contain visible pixels")

    from uuid import uuid4

    filename = f"cast-ref-{uuid4()}{extensions[file.content_type]}"
    (settings.media_dir / filename).write_bytes(content)
    db.add(
        CharacterReference(
            character_id=character.id,
            filename=filename,
            mime_type=file.content_type or "image/png",
        )
    )
    db.commit()
    db.refresh(character)
    return character


@app.post(
    "/api/characters/{character_id}/references/generate",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_character_reference(
    character_id: str,
    look: str | None = None,
    db: Session = Depends(get_db),
) -> Job:
    character = db.scalar(
        select(Character)
        .where(Character.id == character_id)
        .options(selectinload(Character.references))
    )
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    if not (character.notes or "").strip() and not character.name.strip():
        raise HTTPException(status_code=409, detail="Add appearance notes before generating a face")
    if not any(item.get("available") for item in image_provider_statuses()):
        raise HTTPException(
            status_code=409,
            detail="No image provider is available. Start the ChatGPT/Gemini bridges or upload a face still.",
        )
    active = db.scalars(
        select(Job).where(
            Job.project_id == character.project_id,
            Job.kind == "cast_reference",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for job in active:
        attempts = job.attempts or []
        if any(item.get("character_id") == character.id for item in attempts if isinstance(item, dict)):
            return JobRead.model_validate(job)

    job = Job(
        project_id=character.project_id,
        kind="cast_reference",
        message=f"Queued face reference for {character.name}",
        attempts=[{"character_id": character.id, "look": look or ""}],
    )
    db.add(job)
    db.commit()
    task = enqueue_cast_reference(character.id, job.id, look)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.delete("/api/character-references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character_reference(reference_id: str, db: Session = Depends(get_db)) -> None:
    reference = db.get(CharacterReference, reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Reference image not found")
    filename = reference.filename
    db.delete(reference)
    db.commit()
    delete_media_file(filename)


@app.post("/api/shots/{shot_id}/image/upload", response_model=ShotRead)
async def upload_shot_image(
    shot_id: str,
    file: UploadFile = File(...),
    aspect_ratio: str | None = None,
    db: Session = Depends(get_db),
) -> Shot:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    if file.content_type not in extensions:
        raise HTTPException(
            status_code=415, detail="Upload a JPG, PNG, or WebP image"
        )
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    from uuid import uuid4

    filename = f"upload-{uuid4()}{extensions[file.content_type]}"
    (settings.media_dir / filename).write_bytes(content)
    project = shot.scene.script_version.project
    target_aspect = aspect_ratio or project.aspect_ratio
    if target_aspect not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == target_aspect), None)
    use_legacy = target_aspect == project.aspect_ratio
    previous_filename = (shot.image.filename if shot.image else None) if use_legacy else (variant.filename if variant else None)
    final_cut_filename = invalidate_final_cut(project, db)
    if use_legacy and shot.image:
        shot.image.filename = filename
        shot.image.mime_type = file.content_type
        shot.image.source = "uploaded"
        shot.image.provider = "manual-upload"
        shot.image.prompt = shot.image_prompt
    elif use_legacy:
        db.add(
            ShotImage(
                shot_id=shot.id,
                filename=filename,
                mime_type=file.content_type,
                source="uploaded",
                provider="manual-upload",
                prompt=shot.image_prompt,
            )
        )
    elif variant:
        variant.filename = filename
        variant.mime_type = file.content_type
        variant.source = "uploaded"
        variant.provider = "manual-upload"
        variant.prompt = shot.image_prompt
    else:
        db.add(ShotMediaVariant(shot_id=shot.id, media_type="image", aspect_ratio=target_aspect, filename=filename, mime_type=file.content_type, source="uploaded", provider="manual-upload", prompt=shot.image_prompt))
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "image_ready"
    db.commit()
    db.refresh(shot)
    delete_media_file(previous_filename)
    delete_media_file(final_cut_filename)
    return shot


@app.delete("/api/shots/{shot_id}/image", status_code=status.HTTP_204_NO_CONTENT)
def delete_shot_image(shot_id: str, aspect_ratio: str | None = None, db: Session = Depends(get_db)) -> None:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    target_aspect = aspect_ratio or project.aspect_ratio
    variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == target_aspect), None)
    media = shot.image if target_aspect == project.aspect_ratio else variant
    if not media:
        raise HTTPException(status_code=404, detail="Shot image not found")
    filename = media.filename
    final_cut_filename = invalidate_final_cut(project, db)
    db.delete(media)
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "clip_ready" if shot.clip else "planned"
    db.commit()
    delete_media_file(filename)
    delete_media_file(final_cut_filename)


@app.post(
    "/api/shots/{shot_id}/clip/generate",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_shot_clip(shot_id: str, aspect_ratio: str | None = None, db: Session = Depends(get_db)) -> Job:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(
            status_code=409, detail="Approve the script before generating clips"
        )
    target_aspect = aspect_ratio or project.aspect_ratio
    image_variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == target_aspect), None)
    continuity_frame = next(
        (
            frame
            for frame in shot.continuity_frames
            if frame.aspect_ratio == target_aspect
        ),
        None,
    )
    image_ref = next(
        (
            ref
            for ref in shot.image_references
            if ref.aspect_ratio == target_aspect
        ),
        None,
    )
    if not continuity_frame and not shot.image and not image_variant and not image_ref:
        raise HTTPException(
            status_code=409, detail="Generate or upload a shot image first"
        )
    if not shot.video_prompt.strip():
        raise HTTPException(status_code=409, detail="Add a video prompt first")
    _, _, configured, available, _, detail = video_provider_status()
    if not configured or not available:
        raise HTTPException(status_code=503, detail=detail)
    job = Job(
        project_id=project.id,
        kind="shot_clip",
        message="Queued",
        attempts=[{"shot_id": shot.id, "aspect_ratio": target_aspect}],
    )
    db.add(job)
    db.commit()
    task = enqueue_shot_clip(shot.id, job.id, target_aspect)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.post(
    "/api/shots/{shot_id}/continuity-frame",
    response_model=ShotRead,
)
async def save_shot_continuity_frame(
    shot_id: str,
    file: UploadFile = File(...),
    source_shot_id: str = Form(...),
    source_time_ms: int = Form(..., ge=0, le=1_800_000),
    aspect_ratio: str = Form(...),
    db: Session = Depends(get_db),
) -> Shot:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before editing shot continuity")
    if aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG, or WebP frame")

    ordered_shots = [
        candidate
        for scene in sorted(shot.scene.script_version.scenes, key=lambda item: item.order_index)
        for candidate in sorted(scene.shots, key=lambda item: item.order_index)
    ]
    shot_index = next(
        (index for index, candidate in enumerate(ordered_shots) if candidate.id == shot.id),
        -1,
    )
    if shot_index <= 0 or ordered_shots[shot_index - 1].id != source_shot_id:
        raise HTTPException(
            status_code=409,
            detail="Continuity must come from the immediately previous shot",
        )
    source_shot = ordered_shots[shot_index - 1]
    source_clip_variant = next(
        (
            item
            for item in source_shot.media_variants
            if item.media_type == "clip" and item.aspect_ratio == aspect_ratio
        ),
        None,
    )
    source_clip = source_clip_variant or (
        source_shot.clip if aspect_ratio == project.aspect_ratio else None
    )
    if not source_clip:
        raise HTTPException(
            status_code=409,
            detail=f"The previous shot has no {aspect_ratio} video",
        )

    from uuid import uuid4

    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Frame must be 20 MB or smaller")
    if not content or not is_usable_reference_image(content):
        raise HTTPException(status_code=400, detail="Frame image is empty or invalid")

    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[file.content_type]
    filename = f"continuity-{uuid4()}{extension}"
    destination = settings.media_dir / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)

    frame = next(
        (
            item
            for item in shot.continuity_frames
            if item.aspect_ratio == aspect_ratio
        ),
        None,
    )
    previous_filename = frame.filename if frame else None
    if frame:
        frame.filename = filename
        frame.mime_type = file.content_type
        frame.source_shot_id = source_shot.id
        frame.source_shot_title = source_shot.title
        frame.source_time_ms = source_time_ms
        frame.created_at = now()
    else:
        frame = ShotContinuityFrame(
            shot_id=shot.id,
            aspect_ratio=aspect_ratio,
            filename=filename,
            mime_type=file.content_type,
            source_shot_id=source_shot.id,
            source_shot_title=source_shot.title,
            source_time_ms=source_time_ms,
        )
        db.add(frame)
    try:
        db.commit()
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    db.refresh(shot)
    delete_media_file(previous_filename)
    return shot


@app.delete(
    "/api/shots/{shot_id}/continuity-frame",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_shot_continuity_frame(
    shot_id: str, aspect_ratio: str, db: Session = Depends(get_db)
) -> None:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    frame = next(
        (
            item
            for item in shot.continuity_frames
            if item.aspect_ratio == aspect_ratio
        ),
        None,
    )
    if not frame:
        raise HTTPException(status_code=404, detail="Continuity frame not found")
    filename = frame.filename
    db.delete(frame)
    db.commit()
    delete_media_file(filename)



def _image_reference_target(db, shot_id, aspect_ratio):
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    if shot.scene.script_version.project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before editing image references")
    if aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    return shot


@app.get("/api/shots/{shot_id}/image-reference/sources")
def image_reference_sources(shot_id: str, db: Session = Depends(get_db)):
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    return [dict(id=item.id, title=item.title, media=media_sources(item)) for item in earlier_shots(shot)]


@app.post("/api/shots/{shot_id}/image-reference", response_model=ShotRead)
async def save_image_reference(
    shot_id: str,
    source_shot_id: str = Form(...),
    source_media_id: str = Form(...),
    source_media_kind: str = Form(...),
    aspect_ratio: str = Form(...),
    mode: str = Form("visual_continuity"),
    source_time_ms: int | None = Form(None, ge=0, le=1_800_000),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    shot = _image_reference_target(db, shot_id, aspect_ratio)
    if mode != "visual_continuity":
        raise HTTPException(status_code=422, detail="Unsupported image reference mode")
    source = next((item for item in earlier_shots(shot) if item.id == source_shot_id), None)
    if not source:
        raise HTTPException(status_code=409, detail="Choose an earlier shot in this script version")
    media = next((item for item in media_sources(source)
                  if item['source_media_id'] == source_media_id and item['source_media_kind'] == source_media_kind), None)
    if not media:
        raise HTTPException(status_code=404, detail="Source media is no longer available")
    if media['source_media_type'] == 'clip':
        if file is None or source_time_ms is None:
            raise HTTPException(status_code=422, detail="Choose and capture a video frame")
        content = await file.read(20 * 1024 * 1024 + 1)
        mime_type = file.content_type
    else:
        if file is not None or source_time_ms is not None:
            raise HTTPException(status_code=422, detail="Still references do not accept a frame or timestamp")
        content = (settings.media_dir / media['filename']).read_bytes()
        mime_type = media['mime_type']
    if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=415, detail="Choose a PNG, JPEG, or WebP reference")
    try:
        validate_reference_bytes(content, mime_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from uuid import uuid4
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
    filename = f"image-reference-{uuid4()}{extension}"
    path = settings.media_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    ref = next((item for item in shot.image_references if item.aspect_ratio == aspect_ratio), None)
    previous = ref.filename if ref else None
    if ref is None:
        ref = ShotImageReference(shot=shot, aspect_ratio=aspect_ratio)
        db.add(ref)
    for key in ('source_shot_id', 'source_shot_title', 'source_media_id', 'source_media_kind',
                'source_media_type', 'source_aspect_ratio'):
        setattr(ref, key, media[key])
    ref.filename, ref.mime_type = filename, mime_type
    ref.source_time_ms, ref.mode, ref.created_at = source_time_ms, mode, now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    db.refresh(shot)
    release_reference_asset(db, previous)
    return shot


@app.delete("/api/shots/{shot_id}/image-reference", status_code=204)
def remove_image_reference(shot_id: str, aspect_ratio: str, db: Session = Depends(get_db)):
    shot = _image_reference_target(db, shot_id, aspect_ratio)
    ref = next((item for item in shot.image_references if item.aspect_ratio == aspect_ratio), None)
    if ref:
        filename = ref.filename
        db.delete(ref)
        db.commit()
        db.expire(shot, ['image_references'])
        release_reference_asset(db, filename)


@app.post("/api/shots/{shot_id}/clip/upload", response_model=ShotRead)
async def upload_shot_clip(
    shot_id: str,
    file: UploadFile = File(...),
    aspect_ratio: str | None = None,
    db: Session = Depends(get_db),
) -> Shot:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    extensions = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    if file.content_type not in extensions:
        raise HTTPException(status_code=415, detail="Upload an MP4, WebM, or MOV video")

    from uuid import uuid4

    filename = f"upload-{uuid4()}{extensions[file.content_type]}"
    destination = settings.media_dir / filename
    total = 0
    max_bytes = 250 * 1024 * 1024
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Video must be 250 MB or smaller"
                    )
                target.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="Uploaded video is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    project = shot.scene.script_version.project
    target_aspect = aspect_ratio or project.aspect_ratio
    if target_aspect not in {"16:9", "9:16"}:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    variant = next((item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == target_aspect), None)
    use_legacy = target_aspect == project.aspect_ratio
    previous_filename = (shot.clip.filename if shot.clip else None) if use_legacy else (variant.filename if variant else None)
    final_cut_filename = invalidate_final_cut(project, db)
    if use_legacy and shot.clip:
        shot.clip.filename = filename
        shot.clip.mime_type = file.content_type
        shot.clip.source = "uploaded"
        shot.clip.provider = "manual-upload"
        shot.clip.prompt = shot.video_prompt
    elif use_legacy:
        db.add(
            ShotClip(
                shot_id=shot.id,
                filename=filename,
                mime_type=file.content_type,
                source="uploaded",
                provider="manual-upload",
                prompt=shot.video_prompt,
            )
        )
    elif variant:
        variant.filename = filename
        variant.mime_type = file.content_type
        variant.source = "uploaded"
        variant.provider = "manual-upload"
        variant.prompt = shot.video_prompt
    else:
        db.add(ShotMediaVariant(shot_id=shot.id, media_type="clip", aspect_ratio=target_aspect, filename=filename, mime_type=file.content_type, source="uploaded", provider="manual-upload", prompt=shot.video_prompt))
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "clip_ready"
    db.commit()
    db.refresh(shot)
    delete_media_file(previous_filename)
    delete_media_file(final_cut_filename)
    return shot


@app.delete("/api/shots/{shot_id}/clip", status_code=status.HTTP_204_NO_CONTENT)
def delete_shot_clip(shot_id: str, aspect_ratio: str | None = None, db: Session = Depends(get_db)) -> None:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    target_aspect = aspect_ratio or project.aspect_ratio
    variant = next((item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == target_aspect), None)
    media = shot.clip if target_aspect == project.aspect_ratio else variant
    if not media:
        raise HTTPException(status_code=404, detail="Shot clip not found")
    filename = media.filename
    final_cut_filename = invalidate_final_cut(project, db)
    db.delete(media)
    if project.timeline:
        project.timeline.revision += 1
    shot.status = "image_ready" if shot.image else "planned"
    db.commit()
    delete_media_file(filename)
    delete_media_file(final_cut_filename)


@app.post(
    "/api/projects/{project_id}/final-cut/render",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_final_cut(project_id: str, variant: str = "landscape", db: Session = Depends(get_db)) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before rendering")
    if variant not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="Variant must be landscape or shorts")
    active_job = db.scalar(
        select(Job).where(
            Job.project_id == project.id,
            Job.kind == f"final_cut:{variant}",
            Job.status.in_(["queued", "running"]),
        )
    )
    if active_job:
        return active_job

    timeline = ensure_timeline(project, db)
    document_data = (
        timeline.shorts_document or timeline.document
        if variant == "shorts"
        else timeline.document
    )
    try:
        validate_timeline(TimelineDocument.model_validate(document_data), project, db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = Job(project_id=project.id, kind=f"final_cut:{variant}")
    db.add(job)
    db.commit()
    task = enqueue_final_cut(project.id, job.id, variant)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/projects/{project_id}/draft/readiness")
def draft_readiness(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_readiness(project, db)


@app.post(
    "/api/projects/{project_id}/draft",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_draft(
    project_id: str,
    payload: CreateDraftRequest = Body(default_factory=CreateDraftRequest),
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before creating a draft")
    active = db.scalar(
        select(Job).where(
            Job.project_id == project.id,
            Job.kind == "create_draft",
            Job.status.in_(["queued", "running"]),
        )
    )
    if active:
        return active
    job = Job(
        project_id=project.id,
        kind="create_draft",
        attempts=[{"variant": payload.variant, "retry_failed_only": payload.retry_failed_only,
                   "image_references": {shot.id: reference_snapshot(shot, "9:16" if payload.variant == "shorts" else "16:9")
                       for version in project.versions for scene in version.scenes for shot in scene.shots}}],
    )
    db.add(job)
    db.commit()
    task = enqueue_create_draft(project.id, job.id, payload.variant, payload.retry_failed_only)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.patch("/api/scenes/{scene_id}", response_model=SceneRead)
def update_scene(scene_id: str, payload: SceneUpdate, db: Session = Depends(get_db)) -> Scene:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    scene.set_notes = payload.set_notes
    scene.lighting_notes = payload.lighting_notes
    scene.wardrobe_notes = payload.wardrobe_notes
    scene.props_notes = payload.props_notes
    db.commit()
    db.refresh(scene)
    return scene



@app.post(
    "/api/shots/{shot_id}/lipsync",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_shot_lipsync(
    shot_id: str,
    aspect_ratio: str | None = None,
    db: Session = Depends(get_db),
) -> Job:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    if not shot.dialogue:
        raise HTTPException(
            status_code=400, detail="Shot does not contain any dialogue lines to lip-sync"
        )
    project = shot.scene.script_version.project
    target_aspect = aspect_ratio or project.aspect_ratio

    has_visual = bool(
        shot.clip
        or shot.image
        or any(m.filename for m in shot.media_variants if m.aspect_ratio == target_aspect)
    )
    if not has_visual:
        raise HTTPException(
            status_code=409,
            detail="Generate or upload a shot frame or clip before running lip-sync",
        )

    job = Job(
        project_id=project.id,
        kind="shot_lipsync",
        status="queued",
        message="Queued for dialogue lip-sync...",
        attempts=[{"shot_id": shot.id, "aspect_ratio": target_aspect}],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    task = enqueue_shot_lipsync(shot.id, job.id, target_aspect)
    job.task_id = task.id if task else None
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/system/lipsync/status")
def system_lipsync_status() -> dict:
    from .lipsync import get_lipsync_status
    return get_lipsync_status().model_dump()


@app.post("/api/shots/{shot_id}/audio/generate", response_model=ShotRead)
def generate_shot_audio(shot_id: str, db: Session = Depends(get_db)) -> Shot:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.image),
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.scene).selectinload(Scene.script_version).selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    if not shot.dialogue:
        raise HTTPException(status_code=400, detail="Shot has no dialogue")
    configured, available, detail = vibevoice_status()
    if not configured or not available:
        raise HTTPException(status_code=503, detail=detail)
    project = shot.scene.script_version.project
    try:
        from .voice_service import synthesize_shot_dialogue
        filename, mime_type, provider = synthesize_shot_dialogue(shot, db)
        record_take(
            db, shot, media_type="audio", aspect_ratio=project.aspect_ratio, filename=filename,
            mime_type=mime_type, source="generated", provider=provider, prompt="dialogue",
        )
        db.commit()
        db.refresh(shot)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Dialogue generation failed: {exc}") from exc
    return shot


@app.post("/api/shots/{shot_id}/takes/select", response_model=ShotRead)
def select_shot_take(
    shot_id: str,
    payload: TakeSelectRequest,
    db: Session = Depends(get_db),
) -> Shot:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.image),
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.scene).selectinload(Scene.script_version).selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    try:
        select_take(db, shot, payload.take_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Mirror selected take into legacy current media slots and variants for edit compatibility.
    take = next(item for item in shot.takes if item.id == payload.take_id)
    project = shot.scene.script_version.project
    use_legacy = take.aspect_ratio == project.aspect_ratio

    if take.media_type == "image":
        if use_legacy:
            if shot.image:
                shot.image.filename = take.filename
                shot.image.mime_type = take.mime_type
                shot.image.provider = take.provider
                shot.image.prompt = take.prompt
                shot.image.source = take.source
            else:
                db.add(
                    ShotImage(
                        shot_id=shot.id,
                        filename=take.filename,
                        mime_type=take.mime_type,
                        source=take.source,
                        provider=take.provider,
                        prompt=take.prompt,
                    )
                )
        variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == take.aspect_ratio), None)
        if variant:
            variant.filename = take.filename
            variant.mime_type = take.mime_type
            variant.provider = take.provider
            variant.prompt = take.prompt
            variant.source = take.source
            variant.created_at = now()
        else:
            db.add(
                ShotMediaVariant(
                    shot_id=shot.id,
                    media_type="image",
                    aspect_ratio=take.aspect_ratio,
                    filename=take.filename,
                    mime_type=take.mime_type,
                    source=take.source,
                    provider=take.provider,
                    prompt=take.prompt,
                )
            )
        conv_url = getattr(take, "conversation_url", None) or (take.settings or {}).get("conversation_url")
        if conv_url:
            shot.conversation_url = conv_url

    elif take.media_type == "clip":
        if use_legacy:
            if shot.clip:
                shot.clip.filename = take.filename
                shot.clip.mime_type = take.mime_type
                shot.clip.provider = take.provider
                shot.clip.prompt = take.prompt
                shot.clip.source = take.source
            else:
                db.add(
                    ShotClip(
                        shot_id=shot.id,
                        filename=take.filename,
                        mime_type=take.mime_type,
                        source=take.source,
                        provider=take.provider,
                        prompt=take.prompt,
                    )
                )
        variant = next((item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == take.aspect_ratio), None)
        if variant:
            variant.filename = take.filename
            variant.mime_type = take.mime_type
            variant.provider = take.provider
            variant.prompt = take.prompt
            variant.source = take.source
            variant.created_at = now()
        else:
            db.add(
                ShotMediaVariant(
                    shot_id=shot.id,
                    media_type="clip",
                    aspect_ratio=take.aspect_ratio,
                    filename=take.filename,
                    mime_type=take.mime_type,
                    source=take.source,
                    provider=take.provider,
                    prompt=take.prompt,
                )
            )
        conv_url = getattr(take, "conversation_url", None) or (take.settings or {}).get("conversation_url")
        if conv_url:
            shot.conversation_url = conv_url
        from .final_cut import invalidate_final_cut
        invalidate_final_cut(project, db)

    db.commit()
    db.expire_all()
    fresh_shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.image),
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.scene).selectinload(Scene.script_version).selectinload(ScriptVersion.project),
        )
    )
    return fresh_shot or shot


@app.get("/api/publish/providers")
def publish_providers() -> list[dict]:
    return publish_provider_statuses()


@app.get("/api/projects/{project_id}/publish/plan", response_model=PublishPlanRead)
def project_publish_plan(project_id: str, variant: str = "landscape", db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return build_publish_plan(project, variant)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/publishes", response_model=list[PublishRecordRead])
def list_publishes(project_id: str, db: Session = Depends(get_db)) -> list[PublishRecord]:
    if not db.get(Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return db.scalars(
        select(PublishRecord)
        .where(PublishRecord.project_id == project_id)
        .order_by(PublishRecord.created_at.desc())
    ).all()


@app.post("/api/projects/{project_id}/publish", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def queue_publish(project_id: str, payload: PublishCreate, db: Session = Depends(get_db)) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    data = payload.model_dump(mode="json")
    try:
        validate_publish_request(data)
        plan = build_publish_plan(project, payload.variant)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    available_ids = {item["id"] for group in ("final", "scenes", "shots") for item in plan[group] if item.get("ready")}
    requested_ids = set(payload.selected_asset_ids or [])
    if requested_ids and not requested_ids <= available_ids:
        raise HTTPException(status_code=409, detail="One or more selected publish assets are not ready")
    job = Job(project_id=project.id, kind="publish", message="Queued publish", attempts=[data])
    db.add(job)
    db.flush()
    record = PublishRecord(
        project_id=project.id,
        job_id=job.id,
        scope=payload.scope,
        variant=payload.variant,
        platforms=payload.platforms,
        title=payload.title.strip(),
        description=payload.description.strip(),
        tags=payload.tags,
        privacy=payload.privacy,
        selected_asset_ids=payload.selected_asset_ids,
        scheduled_at=payload.scheduled_at,
        made_for_kids=payload.made_for_kids,
        options={**payload.platform_options, "schedule_interval_hours": payload.schedule_interval_hours},
    )
    db.add(record)
    db.flush()
    job.attempts = [{**data, "publish_record_id": record.id}]
    db.commit()
    task = enqueue_publish_project(project.id, job.id, record.id)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.delete(
    "/api/projects/{project_id}/final-cut",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_final_cut(project_id: str, variant: str = "landscape", db: Session = Depends(get_db)) -> None:
    final_cut = db.scalar(select(ProjectVariantExport).where(ProjectVariantExport.project_id == project_id, ProjectVariantExport.variant == variant))
    if not final_cut:
        raise HTTPException(status_code=404, detail="Final cut not found")
    filename = final_cut.filename
    db.delete(final_cut)
    db.commit()
    delete_media_file(filename)


@app.get("/api/projects/{project_id}/timeline", response_model=ProjectTimelineRead)
def get_timeline(project_id: str, edit: str = "landscape", db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if edit not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="edit must be landscape or shorts")
    timeline = ensure_timeline(project, db)
    if edit == "shorts":
        if not timeline.shorts_document:
            timeline.shorts_document = deepcopy(timeline.document)
            db.commit()
        document = timeline.shorts_document
    else:
        document = timeline.document
    db.commit()
    return {
        "revision": timeline.revision,
        "document": document,
        "edit": edit,
        "assets": project.timeline_assets,
    }


@app.put("/api/projects/{project_id}/timeline", response_model=ProjectTimelineRead)
def update_timeline(project_id: str, payload: TimelineUpdate, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db)
    if timeline.revision != payload.revision:
        raise HTTPException(status_code=409, detail="The timeline changed in another session. Reload it before saving.")
    try:
        validate_timeline(payload.document, project, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dumped = payload.document.model_dump(mode="json")
    if payload.edit == "shorts":
        timeline.shorts_document = dumped
    else:
        timeline.document = dumped
    # Planned shot durations stay separate; edit timing lives only on the timeline document.
    timeline.revision += 1
    db.commit()
    db.refresh(timeline)
    return {
        "revision": timeline.revision,
        "document": dumped,
        "edit": payload.edit,
        "assets": project.timeline_assets,
    }


@app.post("/api/projects/{project_id}/timeline/reset", response_model=ProjectTimelineRead)
def reset_timeline(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db)
    doc = initial_document(project, db)
    timeline.document = doc
    if timeline.shorts_document is not None:
        from copy import deepcopy
        timeline.shorts_document = deepcopy(doc)
    timeline.revision += 1
    db.commit()
    return {"revision": timeline.revision, "document": timeline.document, "assets": project.timeline_assets}


@app.post("/api/projects/{project_id}/timeline/assets", response_model=ProjectTimelineRead)
async def upload_timeline_asset(project_id: str, file: UploadFile = File(...), label: str = Form("Uploaded image"), db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if file.content_type not in extensions:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP image")
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413 if content else 400, detail="Image must be between 1 byte and 10 MB")
    from uuid import uuid4
    filename = f"timeline-{uuid4()}{extensions[file.content_type]}"
    (settings.media_dir / filename).write_bytes(content)
    asset = TimelineAsset(project_id=project.id, filename=filename, mime_type=file.content_type, label=label[:160] or "Uploaded image")
    db.add(asset)
    timeline = ensure_timeline(project, db)
    timeline.revision += 1
    db.commit()
    return {"revision": timeline.revision, "document": timeline.document, "assets": project.timeline_assets}


@app.delete("/api/projects/{project_id}/timeline/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeline_asset(project_id: str, asset_id: str, db: Session = Depends(get_db)) -> None:
    asset = db.get(TimelineAsset, asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Timeline asset not found")
    project = asset.project
    timeline = ensure_timeline(project, db)
    document = TimelineDocument.model_validate(timeline.document)
    used = any(item.source and item.source.kind == "asset" and item.source.ref_id == asset.id for item in document.primary) or any(item.source.kind == "asset" and item.source.ref_id == asset.id for item in document.image_overlays)
    if used:
        raise HTTPException(status_code=409, detail="Remove this image from the timeline before deleting it")
    filename = asset.filename
    db.delete(asset)
    timeline.revision += 1
    db.commit()
    delete_media_file(filename)


@app.post("/api/suggestions/{suggestion_id}/decision", response_model=SuggestionRead)
def decide_suggestion(
    suggestion_id: str,
    payload: SuggestionDecision,
    db: Session = Depends(get_db),
) -> Suggestion:
    suggestion = db.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(status_code=409, detail="Suggestion already decided")
    suggestion.status = payload.decision
    if payload.decision == "accepted":
        source_version = suggestion.script_version
        updated_data = deepcopy(source_version.structured_data)
        target = suggestion.target
        scene_index = target.get("scene_index")
        field = target.get("field")
        if isinstance(scene_index, int) and scene_index < len(updated_data.get("scenes", [])):
            scene_data = updated_data["scenes"][scene_index]
            shot_index = target.get("shot_index")
            if (
                isinstance(shot_index, int)
                and shot_index < len(scene_data.get("shots", []))
                and field
            ):
                allowed_shot_fields = {
                    "title",
                    "start_ms",
                    "end_ms",
                    "action",
                    "dialogue",
                    "sound",
                    "duration_ms",
                    "framing",
                    "image_prompt",
                    "video_prompt",
                    "title_card_text",
                }
                if field not in allowed_shot_fields:
                    raise HTTPException(
                        status_code=422,
                        detail=f"The review targeted unsupported shot field '{field}'.",
                    )
                shot_data = scene_data["shots"][shot_index]
                shot_data[field] = _suggestion_value(
                    shot_data.get(field),
                    suggestion.before_value,
                    suggestion.after_value,
                )
            elif field == "timing" and isinstance(suggestion.after_value, dict):
                scene_data.update(suggestion.after_value)
            elif field:
                allowed_scene_fields = {
                    "title",
                    "location",
                    "time_of_day",
                    "start_ms",
                    "end_ms",
                    "action",
                    "dialogue",
                }
                if field not in allowed_scene_fields:
                    raise HTTPException(
                        status_code=422,
                        detail=f"The review targeted unsupported scene field '{field}'.",
                    )
                scene_data[field] = _suggestion_value(
                    scene_data.get(field),
                    suggestion.before_value,
                    suggestion.after_value,
                )

        if source_version.status == "continuation_review":
            source_version.structured_data = updated_data
            flag_modified(source_version, "structured_data")
            if isinstance(scene_index, int):
                model_scene = next(
                    (scene for scene in source_version.scenes if scene.order_index == scene_index),
                    None,
                )
                if model_scene:
                    shot_index = target.get("shot_index")
                    if isinstance(shot_index, int) and field:
                        model_shot = next(
                            (shot for shot in model_scene.shots if shot.order_index == shot_index),
                            None,
                        )
                        if model_shot and hasattr(model_shot, field):
                            setattr(model_shot, field, updated_data["scenes"][scene_index]["shots"][shot_index][field])
                    elif field == "timing" and isinstance(suggestion.after_value, dict):
                        for timing_field in ("start_ms", "end_ms"):
                            if timing_field in suggestion.after_value:
                                setattr(model_scene, timing_field, suggestion.after_value[timing_field])
                    elif field and hasattr(model_scene, field):
                        setattr(model_scene, field, updated_data["scenes"][scene_index][field])
            db.commit()
            db.refresh(suggestion)
            return suggestion

        new_version = ScriptVersion(
            project_id=source_version.project_id,
            version_number=source_version.version_number + 1,
            source_text=source_version.source_text,
            structured_data=updated_data,
            status="review",
        )
        db.add(new_version)
        db.flush()
        for index, original_scene in enumerate(updated_data.get("scenes", [])):
            scene_data = dict(original_scene)
            shots = scene_data.pop("shots", [])
            scene = Scene(
                script_version_id=new_version.id,
                order_index=index,
                **scene_data,
            )
            db.add(scene)
            db.flush()
            shot_cursor = scene_data.get("start_ms")
            for shot_index, shot_data in enumerate(shots):
                duration_ms = shot_data.get("duration_ms", 5000)
                start_ms = shot_data.get("start_ms", shot_cursor)
                end_ms = shot_data.get(
                    "end_ms",
                    start_ms + duration_ms if start_ms is not None else None,
                )
                shot_data.setdefault(
                    "title", f"{scene_data.get('title', 'Scene')} - Shot {shot_index + 1}"
                )
                shot_data.setdefault("start_ms", start_ms)
                shot_data.setdefault("end_ms", end_ms)
                shot_data.setdefault("action", scene_data.get("action", ""))
                shot_data.setdefault("dialogue", [])
                shot_data.setdefault("sound", "")
                db.add(
                    Shot(
                        scene_id=scene.id,
                        order_index=shot_data.get("order_index", shot_index),
                        title=shot_data["title"],
                        start_ms=shot_data["start_ms"],
                        end_ms=shot_data["end_ms"],
                        action=shot_data["action"],
                        dialogue=shot_data["dialogue"],
                        sound=shot_data["sound"],
                        duration_ms=duration_ms,
                        framing=shot_data.get("framing", "Medium"),
                        image_prompt=shot_data.get("image_prompt", ""),
                        video_prompt=shot_data.get("video_prompt", ""),
                        title_card_text=shot_data.get("title_card_text", ""),
                    )
                )
                shot_cursor = end_ms
        flag_modified(new_version, "structured_data")
        for pending in source_version.suggestions:
            if pending.id == suggestion.id or pending.status != "pending":
                continue
            db.add(
                Suggestion(
                    script_version_id=new_version.id,
                    category=pending.category,
                    severity=pending.severity,
                    title=pending.title,
                    reason=pending.reason,
                    target=pending.target,
                    before_value=pending.before_value,
                    after_value=pending.after_value,
                )
            )
    db.commit()
    db.refresh(suggestion)
    return suggestion


def _suggestion_value(current: object, before: object, after: object) -> object:
    """Apply a quoted text edit without replacing the surrounding prompt."""
    if isinstance(current, str) and isinstance(before, str) and isinstance(after, str):
        if before and before != current and before in current:
            return current.replace(before, after, 1)
        return after

    if isinstance(current, bool) and not isinstance(after, bool):
        raise HTTPException(status_code=422, detail="The proposed value has the wrong type.")
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        if not isinstance(after, (int, float)) or isinstance(after, bool):
            raise HTTPException(status_code=422, detail="The proposed value must be numeric.")
    if isinstance(current, list) and not isinstance(after, list):
        raise HTTPException(status_code=422, detail="The proposed value must be a list.")
    if isinstance(current, dict) and not isinstance(after, dict):
        raise HTTPException(status_code=422, detail="The proposed value must be an object.")
    return after


@app.post("/api/projects/{project_id}/approve", response_model=ProjectSummary)
def approve_project(
    project_id: str,
    payload: ProjectApproval,
    db: Session = Depends(get_db),
) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    version = db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.version_number.desc())
    )
    if not version:
        raise HTTPException(status_code=409, detail="Project has no script")
    pending = db.scalar(
        select(Suggestion).where(
            Suggestion.script_version_id == version.id,
            Suggestion.status == "pending",
            Suggestion.severity == "blocking",
        )
    )
    if pending:
        raise HTTPException(status_code=409, detail="Resolve blocking suggestions first")
    invalidated_filename = None
    if payload.approved and append_missing_shots(project, db):
        invalidated_filename = invalidate_final_cut(project, db)
    if payload.approved:
        sync_project_cast(project, db, source_text=version.source_text, tag_shots=True)
    version.status = "approved" if payload.approved else "review"
    project.status = "shot_plan" if payload.approved else "review"
    db.commit()
    db.refresh(project)
    delete_media_file(invalidated_filename)
    return project


@app.post("/api/projects/{project_id}/cast/sync", response_model=list[CharacterRead])
def sync_cast_from_script(project_id: str, db: Session = Depends(get_db)) -> list[Character]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    version = db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.version_number.desc())
    )
    sync_project_cast(
        project,
        db,
        source_text=version.source_text if version else "",
        tag_shots=True,
    )
    db.commit()
    return list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .options(selectinload(Character.references))
            .order_by(Character.name)
        )
    )

# ─────────────────────────────────────────────────────────────────────────────
# Social OAuth Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/auth/social/status")
def social_auth_status(db: Session = Depends(get_db)) -> list[dict]:
    """Return connection status for all social platforms."""
    return all_token_statuses(db)


@app.get("/auth/youtube/start")
def youtube_auth_start(db: Session = Depends(get_db)):
    """Return the Google OAuth authorization URL for YouTube."""
    from fastapi.responses import JSONResponse
    try:
        url = youtube_auth_url(db)
        return {"url": url}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/auth/youtube/callback")
def youtube_auth_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Handle Google OAuth callback, exchange code for token, redirect to UI."""
    from fastapi.responses import HTMLResponse
    try:
        tok = handle_youtube_callback(code, state, db)
        name = tok.account_name or "your channel"
        safe_name = html_lib.escape(name)
        message_json = json.dumps({"type": "oauth_success", "platform": "youtube", "account": name}).replace("</", "<\\/")
        target_origin_json = json.dumps(settings.frontend_origin)
        html = f"""<!DOCTYPE html>
<html>
<head><title>Connected!</title>
<style>
  body {{ background: #0f1117; color: #e2e8f0; font-family: system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .card {{ background: #1a1f2e; border: 1px solid rgba(255,255,255,.12); border-radius: 16px;
           padding: 2rem 3rem; text-align: center; max-width: 400px; }}
  .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
  h2 {{ color: #4ade80; margin: 0 0 .5rem; }}
  p {{ color: #94a3b8; margin: 0 0 1.5rem; }}
  small {{ color: #64748b; font-size: .8rem; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h2>YouTube Connected!</h2>
  <p>Linked to <strong>{safe_name}</strong></p>
  <small>You can close this window and return to Cutline.</small>
</div>
<script>
  // Notify the opener window and close
  if (window.opener) {{
    window.opener.postMessage({message_json}, {target_origin_json});
    setTimeout(() => window.close(), 1500);
  }} else {{
    setTimeout(() => window.close(), 3000);
  }}
</script>
</body>
</html>"""
        return HTMLResponse(content=html)
    except Exception as exc:
        error_text = str(exc)
        safe_error = html_lib.escape(error_text)
        message_json = json.dumps({"type": "oauth_error", "platform": "youtube", "error": error_text}).replace("</", "<\\/")
        target_origin_json = json.dumps(settings.frontend_origin)
        html_err = f"""<!DOCTYPE html>
<html>
<head><title>Connection Failed</title>
<style>
  body {{ background: #0f1117; color: #e2e8f0; font-family: system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .card {{ background: #1a1f2e; border: 1px solid rgba(239,68,68,.3); border-radius: 16px;
           padding: 2rem 3rem; text-align: center; max-width: 400px; }}
  h2 {{ color: #ef4444; margin: 0 0 .5rem; }}
  p {{ color: #94a3b8; margin: 0; }}
</style>
</head>
<body>
<div class="card">
  <div style="font-size:3rem;margin-bottom:1rem">❌</div>
  <h2>Connection Failed</h2>
  <p>{safe_error}</p>
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({message_json}, {target_origin_json});
    setTimeout(() => window.close(), 3000);
  }}
</script>
</body>
</html>"""
        from fastapi.responses import HTMLResponse as HR
        return HR(content=html_err, status_code=400)


@app.get("/auth/facebook/start")
def facebook_auth_start(db: Session = Depends(get_db)):
    """Return the Facebook OAuth dialog URL."""
    try:
        url = facebook_auth_url(db)
        return {"url": url}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/auth/facebook/callback")
def facebook_auth_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Handle Facebook OAuth callback."""
    from fastapi.responses import HTMLResponse
    try:
        tok = handle_facebook_callback(code, state, db)
        name = tok.account_name or "your page"
        safe_name = html_lib.escape(name)
        message_json = json.dumps({"type": "oauth_success", "platform": "facebook", "account": name}).replace("</", "<\\/")
        target_origin_json = json.dumps(settings.frontend_origin)
        html = f"""<!DOCTYPE html>
<html>
<head><title>Connected!</title>
<style>
  body {{ background: #0f1117; color: #e2e8f0; font-family: system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .card {{ background: #1a1f2e; border: 1px solid rgba(255,255,255,.12); border-radius: 16px;
           padding: 2rem 3rem; text-align: center; max-width: 400px; }}
  h2 {{ color: #4ade80; margin: 0 0 .5rem; }}
  p {{ color: #94a3b8; margin: 0 0 1.5rem; }}
  small {{ color: #64748b; font-size: .8rem; }}
</style>
</head>
<body>
<div class="card">
  <div style="font-size:3rem;margin-bottom:1rem">✅</div>
  <h2>Facebook Connected!</h2>
  <p>Linked to <strong>{safe_name}</strong></p>
  <small>You can close this window and return to Cutline.</small>
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({message_json}, {target_origin_json});
    setTimeout(() => window.close(), 1500);
  }} else {{
    setTimeout(() => window.close(), 3000);
  }}
</script>
</body>
</html>"""
        return HTMLResponse(content=html)
    except Exception as exc:
        error_text = str(exc)
        safe_error = html_lib.escape(error_text)
        message_json = json.dumps({"type": "oauth_error", "platform": "facebook", "error": error_text}).replace("</", "<\\/")
        target_origin_json = json.dumps(settings.frontend_origin)
        html_err = f"""<!DOCTYPE html>
<html><head><title>Connection Failed</title></head>
<body style="background:#0f1117;color:#e2e8f0;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="background:#1a1f2e;border:1px solid rgba(239,68,68,.3);border-radius:16px;padding:2rem 3rem;text-align:center;max-width:400px">
  <div style="font-size:3rem;margin-bottom:1rem">❌</div>
  <h2 style="color:#ef4444;margin:0 0 .5rem">Connection Failed</h2>
  <p style="color:#94a3b8;margin:0">{safe_error}</p>
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({message_json}, {target_origin_json});
    setTimeout(() => window.close(), 3000);
  }}
</script>
</body></html>"""
        from fastapi.responses import HTMLResponse as HR
        return HR(content=html_err, status_code=400)


@app.delete("/auth/{platform}/disconnect", status_code=204)
def social_disconnect(platform: str, db: Session = Depends(get_db)):
    """Revoke stored OAuth token for a platform."""
    if platform not in ("youtube", "facebook"):
        raise HTTPException(status_code=400, detail="Unknown platform")
    disconnect_platform(platform, db)
