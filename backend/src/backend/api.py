from datetime import datetime, timezone
import html as html_lib
import json
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from .cast import is_usable_reference_image, sync_project_cast
from .continuity import get_or_create_shot_snapshot
from .database import Base, engine, get_db
from .final_cut import invalidate_final_cut
from .media import (
    clear_reference_color_state,
    delete_media_file,
    enhance_image_vibrancy,
    get_reference_color_state,
    image_provider_statuses,
    save_reference_color_state,
)
from .luts import list_lut_presets
from .migrations import migrate_schema
from .video import (
    FLOWKIT_MODEL_PRESETS,
    clip_source_image,
    get_active_flowkit_preset,
    set_flowkit_model_preset,
    video_provider_status,
)
from .draft import enqueue_create_draft, project_readiness
from .takes import record_take, select_take
from .vibevoice_tts import synthesize_dialogue, vibevoice_status
from .models import Character, CharacterReference, Compilation, DeepReviewRun, Episode, Job, Project, ProjectFinalCut, ProjectTimeline, ProjectVariantExport, Scene, ScriptVersion, Shot, ShotClip, ShotContinuityFrame, ShotImage, ShotImageReference, ShotMediaVariant, ShotTake, Suggestion, TimelineAsset, PublishRecord, now
from .schemas import (
    CompilationCreate,
    CompilationRead,
    CompilationUpdate,
    EpisodeCreate,
    EpisodeSummary,
    EpisodeUpdate,
    ProjectUpdate,
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    VoiceAuditionRequest,
    VoiceAuditionResponse,
    VoiceProviderInfo,
    VoicePresetRead,
    CreateDraftRequest,
    DeepReviewFindingDecision,
    DeepReviewUndoRequest,
    FlowModelUpdateRequest,
    JobRead,
    JobQueueItemRead,
    ImageProviderStatus,
    ProjectTimelineRead,
    ProjectVariantExportRead,
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
    ScriptVersionRead,
    ScriptVersionRestoreRequest,
    SceneUpdate,
    ShotClipGenerateRequest,
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
from .image_references import default_visual_reference, earlier_shots, freeze_visual_reference, media_sources, reference_snapshot, release_reference_asset, snapshot_filenames, validate_reference_bytes
from .jobs import mark_job_cancelled, revoke_job_task
from .job_limits import ensure_project_job_capacity
from .job_status import compute_global_queue_position, compute_queue_position
from .deep_review import (
    decide_deep_review_finding,
    get_deep_review,
    start_deep_review,
    start_deep_review_draft,
    undo_deep_review_apply,
)
from .tasks import (
    enqueue_cast_reference,
    enqueue_deep_review,
    enqueue_deep_review_draft,
    enqueue_final_cut,
    enqueue_script_analysis,
    enqueue_script_continuation,
    enqueue_shot_clip,
    enqueue_shot_lipsync,
    enqueue_shot_image,
    enqueue_publish_project,
)
from .queue_admin import job_matches_queue
from .system_guard import require_local_system
from .timeline import append_missing_shots, ensure_compilation_timeline, ensure_timeline, initial_document, latest_shots, promote_available_clips, rebuild_compilation_timeline, timeline_duration, validate_timeline
from .ws import ws_manager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)
    await ws_manager.start()
    try:
        yield
    finally:
        await ws_manager.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        dict.fromkeys(
            [
                settings.frontend_origin.rstrip("/"),
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ]
        )
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "worker_queues": "chatgpt, gemini, image, general, video",
        "database": "sqlite" if settings.uses_sqlite else "external",
        "queue_backend": "redis" if settings.uses_redis_queue else "sqlite",
    }


async def _handle_websocket(websocket: WebSocket, initial_project_id: str | None = None) -> None:
    await ws_manager.connect(websocket, initial_project_id)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                pid = msg.get("project_id")
                await ws_manager.set_project(websocket, str(pid) if pid else None)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_manager.disconnect(websocket)


@app.websocket("/api/ws")
async def websocket_global(websocket: WebSocket, project_id: str | None = None) -> None:
    await _handle_websocket(websocket, project_id)


@app.websocket("/api/ws/projects/{project_id}")
async def websocket_project(websocket: WebSocket, project_id: str) -> None:
    await _handle_websocket(websocket, project_id)


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
    active_preset: str | None = None
    presets: list[dict[str, str]] | None = None
    if provider_id == "flowkit":
        preset = get_active_flowkit_preset()
        active_preset = preset["id"]
        model = preset["short_label"]
        presets = [dict(item) for item in FLOWKIT_MODEL_PRESETS]
    return ProviderStatus(
        id=provider_id,
        label=label,
        configured=configured,
        available=available,
        model=model,
        detail=detail,
        active_preset=active_preset,
        presets=presets,
    )


@app.post("/api/video-provider/flow-model", response_model=ProviderStatus)
def set_flow_video_model(payload: FlowModelUpdateRequest) -> ProviderStatus:
    try:
        set_flowkit_model_preset(payload.preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return video_provider()



@app.post("/api/video-provider/reset-flow-session")
def reset_flow_session(
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    base_url = settings.flowkit_base_url.rstrip("/")
    try:
        res = httpx.post(f"{base_url}/reset-session", timeout=15.0)
        if not res.is_success:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        if project_id:
            project = db.get(Project, project_id)
            if project:
                project.flow_project_id = None
                db.commit()
        else:
            for project in db.scalars(select(Project).where(Project.flow_project_id.is_not(None))):
                project.flow_project_id = None
            db.commit()
        return res.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"FlowKit communication error: {exc}")


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


@app.post("/api/demo-project", response_model=ProjectSummary, status_code=201)
def create_demo_project(db: Session = Depends(get_db)) -> Project:
    from .demo import load_demo_project
    return load_demo_project(db)


@app.post("/api/projects", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Job:
    project = Project(
        title=payload.title,
        aspect_ratio=payload.aspect_ratio,
        target_duration_seconds=payload.target_duration_seconds,
        text_provider=payload.text_provider,
        episode_label=payload.episode_label,
        status="analyzing",
    )
    db.add(project)
    db.flush()
    episode = Episode(
        project_id=project.id,
        episode_number=1,
        title=payload.title,
        status="analyzing",
    )
    db.add(episode)
    db.flush()
    version = ScriptVersion(
        project_id=project.id,
        episode_id=episode.id,
        source_text=payload.script,
    )
    db.add(version)
    db.flush()
    ensure_project_job_capacity(db, project.id, "script_analysis")
    job = Job(
        project_id=project.id,
        episode_id=episode.id,
        kind="script_analysis",
    )
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
    ensure_project_job_capacity(db, project.id, "script_continuation")
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


@app.patch("/api/projects/{project_id}", response_model=ProjectSummary)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.title is not None:
        project.title = payload.title
    if payload.episode_label is not None:
        project.episode_label = payload.episode_label
    if payload.aspect_ratio is not None:
        project.aspect_ratio = payload.aspect_ratio
    if payload.target_duration_seconds is not None:
        project.target_duration_seconds = payload.target_duration_seconds
    if payload.text_provider is not None:
        project.text_provider = payload.text_provider
    if "flow_project_id" in payload.model_fields_set:
        project.flow_project_id = (payload.flow_project_id or "").strip() or None
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}/episodes", response_model=list[EpisodeSummary])
def list_episodes(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    episodes = db.scalars(
        select(Episode)
        .where(Episode.project_id == project_id)
        .order_by(Episode.episode_number)
    ).all()
    results = []
    for ep in episodes:
        shots = latest_shots(project_id, db, episode_id=ep.id)
        results.append(
            {
                "id": ep.id,
                "project_id": ep.project_id,
                "episode_number": ep.episode_number,
                "title": ep.title,
                "synopsis": ep.synopsis,
                "status": ep.status,
                "shot_count": len(shots),
                "duration_ms": sum(s.duration_ms for s in shots),
                "created_at": ep.created_at,
                "updated_at": ep.updated_at,
            }
        )
    return results


@app.post("/api/projects/{project_id}/episodes", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def create_episode(
    project_id: str,
    payload: EpisodeCreate,
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    existing_nums = db.scalars(
        select(Episode.episode_number).where(Episode.project_id == project_id)
    ).all()
    next_num = max(existing_nums, default=0) + 1

    episode = Episode(
        project_id=project.id,
        episode_number=next_num,
        title=payload.title,
        status="analyzing",
    )
    db.add(episode)
    db.flush()

    version = ScriptVersion(
        project_id=project.id,
        episode_id=episode.id,
        source_text=payload.script,
    )
    db.add(version)
    db.flush()

    ensure_project_job_capacity(db, project.id, "script_analysis")
    job = Job(
        project_id=project.id,
        episode_id=episode.id,
        kind="script_analysis",
    )
    db.add(job)
    db.commit()

    task = enqueue_script_analysis(
        project.text_provider, project.id, version.id, job.id
    )
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.patch("/api/projects/{project_id}/episodes/{episode_id}", response_model=EpisodeSummary)
def update_episode(
    project_id: str,
    episode_id: str,
    payload: EpisodeUpdate,
    db: Session = Depends(get_db),
) -> dict:
    episode = db.scalar(
        select(Episode).where(
            Episode.id == episode_id, Episode.project_id == project_id
        )
    )
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    if payload.title is not None:
        episode.title = payload.title
    if payload.synopsis is not None:
        episode.synopsis = payload.synopsis
    if payload.status is not None:
        episode.status = payload.status
    db.commit()
    db.refresh(episode)
    shots = latest_shots(project_id, db, episode_id=episode.id)
    return {
        "id": episode.id,
        "project_id": episode.project_id,
        "episode_number": episode.episode_number,
        "title": episode.title,
        "synopsis": episode.synopsis,
        "status": episode.status,
        "shot_count": len(shots),
        "duration_ms": sum(s.duration_ms for s in shots),
        "created_at": episode.created_at,
        "updated_at": episode.updated_at,
    }


@app.delete("/api/projects/{project_id}/episodes/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_episode(
    project_id: str,
    episode_id: str,
    db: Session = Depends(get_db),
) -> None:
    count = db.scalar(
        select(func.count(Episode.id)).where(Episode.project_id == project_id)
    )
    if count is not None and count <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only episode of a project")
    episode = db.scalar(
        select(Episode).where(
            Episode.id == episode_id, Episode.project_id == project_id
        )
    )
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    version_ids = [v.id for v in episode.script_versions]
    if version_ids:
        db.execute(
            delete(DeepReviewRun).where(DeepReviewRun.script_version_id.in_(version_ids))
        )
    db.execute(
        delete(Job).where(Job.project_id == project_id, Job.episode_id == episode_id)
    )
    compilations = db.scalars(
        select(Compilation).where(Compilation.project_id == project_id)
    ).all()
    affected_compilations: list[Compilation] = []
    for comp in compilations:
        if episode_id in (comp.episode_ids or []):
            comp.episode_ids = [eid for eid in (comp.episode_ids or []) if eid != episode_id]
            flag_modified(comp, "episode_ids")
            affected_compilations.append(comp)

    db.delete(episode)
    db.flush()
    for comp in affected_compilations:
        # Drop deleted-episode clips from compilation timelines (membership snapshot rebuild).
        rebuild_compilation_timeline(db, comp)
    db.commit()


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: str,
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> ProjectDetail:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    episodes = db.scalars(
        select(Episode)
        .where(Episode.project_id == project_id)
        .order_by(Episode.episode_number)
    ).all()

    if not episodes:
        ep1 = Episode(
            project_id=project_id,
            episode_number=1,
            title=project.title,
            status=project.status,
        )
        db.add(ep1)
        db.flush()
        episodes = [ep1]

    active_episode = None
    if episode_id:
        active_episode = next(
            (e for e in episodes if e.id == episode_id or str(e.episode_number) == str(episode_id)),
            None,
        )
    if not active_episode:
        active_episode = episodes[0]

    version_query = select(ScriptVersion).where(
        ScriptVersion.project_id == project_id
    )
    if active_episode:
        version_query = version_query.where(ScriptVersion.episode_id == active_episode.id)

    version = db.scalar(
        version_query.options(
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
    if not version and active_episode:
        version = db.scalar(
            select(ScriptVersion)
            .where(
                ScriptVersion.project_id == project_id,
                ScriptVersion.episode_id.is_(None),
            )
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

    active_timeline = db.scalar(
        select(ProjectTimeline).where(
            ProjectTimeline.project_id == project_id,
            ProjectTimeline.episode_id == active_episode.id,
        )
    ) or (project.timeline if active_episode.episode_number == 1 else None)

    active_final_cut = db.scalar(
        select(ProjectFinalCut).where(
            ProjectFinalCut.project_id == project_id,
            ProjectFinalCut.episode_id == active_episode.id,
        )
    ) or (project.final_cut if active_episode.episode_number == 1 else None)

    active_variant_exports = db.scalars(
        select(ProjectVariantExport).where(
            ProjectVariantExport.project_id == project_id,
            ProjectVariantExport.episode_id == active_episode.id,
            ProjectVariantExport.compilation_id.is_(None),
        )
    ).all()
    if not active_variant_exports and active_episode.episode_number == 1:
        # Legacy project-level exports only — never surface compilation boxes as episode exports.
        active_variant_exports = [
            item for item in project.variant_exports if not getattr(item, "compilation_id", None)
        ]

    episodes_summary = []
    for ep in episodes:
        ep_shots = latest_shots(project_id, db, episode_id=ep.id)
        episodes_summary.append(
            {
                "id": ep.id,
                "project_id": ep.project_id,
                "episode_number": ep.episode_number,
                "title": ep.title,
                "synopsis": ep.synopsis,
                "status": ep.status,
                "shot_count": len(ep_shots),
                "duration_ms": sum(s.duration_ms for s in ep_shots),
                "created_at": ep.created_at,
                "updated_at": ep.updated_at,
            }
        )

    data = ProjectSummary.model_validate(project).model_dump()
    data["episodes"] = episodes_summary
    data["current_episode_id"] = active_episode.id
    data["current_version"] = version
    data["final_cut"] = active_final_cut
    timeline_revision = active_timeline.revision if active_timeline else 0
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
        for export in active_variant_exports
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

    compilations = db.scalars(
        select(Compilation)
        .where(Compilation.project_id == project_id)
        .order_by(Compilation.created_at)
    ).all()
    compilations_data = []
    for comp in compilations:
        comp_timeline = db.scalar(
            select(ProjectTimeline).where(ProjectTimeline.compilation_id == comp.id)
        )
        duration_ms = 0
        clip_count = 0
        if comp_timeline and comp_timeline.document:
            duration_ms = timeline_duration(comp_timeline.document)
            clip_count = len(comp_timeline.document.get("primary", []))
        compilations_data.append(
            {
                "id": comp.id,
                "project_id": comp.project_id,
                "title": comp.title,
                "description": comp.description,
                "episode_ids": comp.episode_ids or [],
                "settings": comp.settings or {},
                "created_at": comp.created_at,
                "updated_at": comp.updated_at,
                "duration_ms": duration_ms,
                "clip_count": clip_count,
            }
        )
    data["compilations"] = compilations_data

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
    queue_position = compute_queue_position(db, job)
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
    db.execute(
        update(DeepReviewRun)
        .where(DeepReviewRun.job_id == job.id, DeepReviewRun.status.in_(["queued", "running"]))
        .values(status="cancelled")
    )
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

    def _to_utc(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    for job in jobs:
        data = JobRead.model_validate(job).model_dump(mode="json")
        data["project_title"] = projects.get(job.project_id, "Untitled project")
        created_dt = _to_utc(job.created_at)
        updated_dt = _to_utc(job.updated_at)
        created_ts = created_dt.timestamp() if created_dt else now_ts
        updated_ts = updated_dt.timestamp() if updated_dt else now_ts
        data["elapsed_seconds"] = max(
            0.0,
            round(
                (updated_ts if job.status in {"completed", "failed", "cancelled"} else now_ts)
                - created_ts,
                1,
            ),
        )
        data["queue_position"] = compute_global_queue_position(db, job)
        results.append(data)
    return results


@app.get("/api/system/queues", dependencies=[Depends(require_local_system)])
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
        res = httpx.get(f"{settings.flowkit_base_url.rstrip('/')}/status", timeout=1.5)
        if not res.is_success:
            res = httpx.get("http://127.0.0.1:8100/health", timeout=1.5)
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

    ensure_project_job_capacity(db, job.project_id, job.kind)

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
        include_cast_refs = bool(job.attempts[0].get("include_cast_references")) if job.attempts else False
        if not shot_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing shot information")
        task = (
            enqueue_shot_clip(shot_id, job.id, aspect_ratio, include_cast_references=True)
            if include_cast_refs
            else enqueue_shot_clip(shot_id, job.id, aspect_ratio)
        )
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
    elif job.kind == "deep_review":
        run = db.scalar(select(DeepReviewRun).where(DeepReviewRun.job_id == job.id))
        if not run:
            raise HTTPException(status_code=400, detail="Cannot retry: missing deep review run")
        run.status = "queued"
        run.error = None
        provider_name = run.provider or job.provider or "mock"
        task = enqueue_deep_review(provider_name, run.id, job.id)
    elif job.kind == "deep_review_draft":
        run = db.scalar(select(DeepReviewRun).where(DeepReviewRun.draft_job_id == job.id))
        if not run or not run.draft_finding_id:
            raise HTTPException(status_code=400, detail="Cannot retry: missing deep review draft finding")
        provider_name = run.provider or job.provider or "mock"
        task = enqueue_deep_review_draft(provider_name, run.id, run.draft_finding_id, job.id)
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
        db.execute(
            update(DeepReviewRun)
            .where(DeepReviewRun.job_id == job.id, DeepReviewRun.status.in_(["queued", "running"]))
            .values(status="cancelled")
        )
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
    deleted = db.execute(
        delete(Job).where(
            Job.status.in_(statuses),
            Job.kind.notin_(["deep_review", "deep_review_draft"]),
        )
    ).rowcount
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


@app.post("/api/system/queues/{queue_name}/flush", dependencies=[Depends(require_local_system)])
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

    queued_jobs = list(db.scalars(
        select(Job).where(Job.status == "queued")
    ).all())
    cancelled_count = 0
    for job in queued_jobs:
        if job_matches_queue(job.kind, queue_name):
            job.status = "cancelled"
            job.message = f"Cancelled: queue '{queue_name}' was flushed"
            cancelled_count += 1
    db.commit()

    return {"status": "ok", "message": f"Queue {queue_name} flushed ({cancelled_count} queued jobs cancelled)"}


@app.get("/api/system/services", dependencies=[Depends(require_local_system)])
def get_system_services() -> list[dict]:
    from .services_manager import list_services
    return list_services()


@app.post("/api/system/services/{service_id}/start", dependencies=[Depends(require_local_system)])
def start_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    result = svc.start()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start service"))
    return result


@app.post("/api/system/services/{service_id}/stop", dependencies=[Depends(require_local_system)])
def stop_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc.stop()


@app.post("/api/system/services/{service_id}/restart", dependencies=[Depends(require_local_system)])
def restart_system_service(service_id: str) -> dict:
    from .services_manager import get_service
    svc = get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    result = svc.restart()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to restart service"))
    return result


@app.get("/api/system/services/{service_id}/logs", dependencies=[Depends(require_local_system)])
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
            orig_video_overlay_len = len(document.video_overlays)
            orig_image_overlay_len = len(document.image_overlays)
            document.primary = [
                item for item in document.primary
                if not (item.source and item.source.ref_id == shot_id)
            ]
            document.video_overlays = [
                overlay for overlay in document.video_overlays
                if not (overlay.source and overlay.source.ref_id == shot_id)
            ]
            document.image_overlays = [
                overlay for overlay in document.image_overlays
                if not (overlay.source and overlay.source.ref_id == shot_id)
            ]
            if (
                len(document.primary) != orig_primary_len
                or len(document.video_overlays) != orig_video_overlay_len
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
    db.execute(update(Project).where(Project.id == project.id).values(updated_at=Project.updated_at))
    active_image_jobs = db.scalars(select(Job).where(
        Job.project_id == project.id, Job.kind == "shot_image",
        Job.status.in_(["queued", "running"]),
    )).all()
    for existing in active_image_jobs:
        if any(
            attempt.get("shot_id") == shot.id
            and attempt.get("aspect_ratio", project.aspect_ratio) == target_aspect
            for attempt in (existing.attempts or [])
        ):
            db.commit()
            return existing
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
        elif payload.use_default_image_reference:
            image_reference = default_visual_reference(shot, target_aspect)
            image_reference_id = image_reference["id"] if image_reference else None
        else:
            image_reference = None
            image_reference_id = None
    if image_reference:
        image_reference = freeze_visual_reference(image_reference)
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
    attempt_meta: dict[str, Any] = {
        "shot_id": shot.id,
        "aspect_ratio": target_aspect,
        "character_ids": character_ids,
        "continuity_snapshot": snapshot,
        "image_reference_id": image_reference_id,
        "image_reference": image_reference,
        "use_default_image_reference": payload.use_default_image_reference,
    }
    if payload.both_aspects:
        attempt_meta["both_aspects"] = True
    if payload.chatgpt_dual:
        attempt_meta["chatgpt_dual"] = True
    if payload.generate_video:
        attempt_meta["generate_video"] = True
    if payload.include_cast_references:
        attempt_meta["include_cast_references"] = True
    ensure_project_job_capacity(db, project.id, "shot_image")
    job = Job(
        project_id=project.id,
        episode_id=shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None,
        kind="shot_image",
        message="Queued",
        attempts=[attempt_meta],
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
        queue_position = compute_queue_position(db, job)
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
    notes = payload.notes.strip()
    voice_description = payload.voice_description.strip()
    if not notes:
        version = db.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
            .order_by(ScriptVersion.version_number.desc(), ScriptVersion.created_at.desc())
        )
        if version and version.source_text:
            from .screenplay_parser import canonical_character_name, parse_character_consistency

            target_key = canonical_character_name(name)
            for entry in parse_character_consistency(version.source_text):
                entry_key = canonical_character_name(entry["name"])
                if entry_key == target_key or (
                    target_key and entry_key.split() and entry_key.split()[0] == target_key
                ):
                    notes = entry["notes"]
                    if not voice_description:
                        voice_description = entry.get("voice_description", "")
                    break
    character = Character(
        project_id=project_id,
        kind=(payload.kind or "character"),
        name=name,
        notes=notes,
        voice_description=voice_description,
        voice_provider=payload.voice_provider or "vibevoice",
        voice_id=payload.voice_id,
        voice_settings=payload.voice_settings or {},
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


@app.post("/api/projects/{project_id}/elements/sync", response_model=list[CharacterRead])
def sync_elements(
    project_id: str,
    db: Session = Depends(get_db),
) -> list[Character]:
    from .cast import sync_project_elements

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    sync_project_elements(project, db)
    db.commit()
    return list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .options(selectinload(Character.references))
            .order_by(Character.name)
        )
    )


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
    if payload.kind is not None:
        character.kind = payload.kind
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
    if payload.voice_description is not None:
        character.voice_description = payload.voice_description.strip()
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
    view_type: str | None = None,
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

    char_kind = (getattr(character, "kind", None) or "character").strip().lower()
    resolved_view = (view_type or "").strip().lower()
    if resolved_view not in {"main", "3d_view", "scene", "prop"}:
        resolved_view = char_kind if char_kind in {"scene", "prop"} else "main"

    from uuid import uuid4

    filename = f"cast-ref-{uuid4()}{extensions[file.content_type]}"
    (settings.media_dir / filename).write_bytes(content)
    db.add(
        CharacterReference(
            character_id=character.id,
            view_type=resolved_view,
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
    requested_look = (look or "").strip().lower()
    for job in active:
        attempts = job.attempts or []
        if any(
            item.get("character_id") == character.id
            and str(item.get("look") or "").strip().lower() == requested_look
            for item in attempts
            if isinstance(item, dict)
        ):
            return JobRead.model_validate(job)

    ensure_project_job_capacity(db, character.project_id, "cast_reference")
    job = Job(
        project_id=character.project_id,
        kind="cast_reference",
        message=(
            f"Queued 3D turnaround view for {character.name}"
            if requested_look in {"3d_view", "turnaround", "3d"}
            else f"Queued reference for {character.name}"
        ),
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
        shot.image.created_at = now()
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
    if variant:
        variant.filename = filename
        variant.mime_type = file.content_type
        variant.source = "uploaded"
        variant.provider = "manual-upload"
        variant.prompt = shot.image_prompt
        variant.created_at = now()
    elif not use_legacy:
        db.add(ShotMediaVariant(shot_id=shot.id, media_type="image", aspect_ratio=target_aspect, filename=filename, mime_type=file.content_type, source="uploaded", provider="manual-upload", prompt=shot.image_prompt))
    record_take(
        db,
        shot,
        media_type="image",
        aspect_ratio=target_aspect,
        filename=filename,
        mime_type=file.content_type,
        source="uploaded",
        provider="manual-upload",
        prompt=shot.image_prompt,
    )
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "image_ready"
    db.commit()
    db.refresh(shot)
    delete_media_file(previous_filename, db=db)
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
    media = (shot.image or variant) if target_aspect == project.aspect_ratio else variant
    if not media:
        raise HTTPException(status_code=404, detail="Shot image not found")
    filename = media.filename
    final_cut_filename = invalidate_final_cut(project, db)
    if target_aspect == project.aspect_ratio and shot.image:
        db.delete(shot.image)
    if variant:
        db.delete(variant)
    for t in shot.takes:
        if t.media_type == "image" and t.aspect_ratio == target_aspect:
            t.selected = False
    if target_aspect == project.aspect_ratio:
        shot.selected_image_take_id = None
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "clip_ready" if shot.clip else "planned"
    db.commit()
    delete_media_file(filename, db=db)
    delete_media_file(final_cut_filename)


@app.post(
    "/api/shots/{shot_id}/clip/generate",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_shot_clip(
    shot_id: str,
    aspect_ratio: str | None = None,
    payload: ShotClipGenerateRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> Job:
    if isinstance(payload, Session):
        db, payload = payload, None
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(
            status_code=409, detail="Approve the script before generating clips"
        )
    target_aspect = aspect_ratio or project.aspect_ratio
    if target_aspect not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    both_aspects = bool(payload and payload.both_aspects)
    video_mode = str(getattr(payload, "video_mode", None) or "i2v").strip().lower()
    include_scene_ref = bool(getattr(payload, "include_scene_reference", True)) if payload else True
    element_ids = list(payload.element_ids) if (payload and payload.element_ids is not None) else None
    other_aspect = "9:16" if target_aspect == "16:9" else "16:9"
    if video_mode == "r2v":
        from .tasks import _resolve_r2v_element_references

        cont_frame = next(
            (f for f in shot.continuity_frames if f.aspect_ratio == target_aspect),
            next(iter(shot.continuity_frames), None),
        )
        img_ref = next(
            (r for r in shot.image_references if r.aspect_ratio == target_aspect),
            next(iter(shot.image_references), None),
        )
        r2v_paths, _ = _resolve_r2v_element_references(
            db,
            project,
            shot,
            cont_frame,
            img_ref,
            element_ids=element_ids,
            include_scene_reference=include_scene_ref,
        )
        if not r2v_paths and not clip_source_image(shot, target_aspect):
            raise HTTPException(
                status_code=409,
                detail="Attach at least one Character, Scene, or Prop reference image (or @mention an element with a reference) to generate in Reference-to-Video (R2V) mode",
            )
    elif not clip_source_image(shot, target_aspect):
        if both_aspects and clip_source_image(shot, other_aspect):
            target_aspect, other_aspect = other_aspect, target_aspect
        else:
            raise HTTPException(status_code=409, detail=f"Generate or upload a {target_aspect} shot image first")
    if not shot.video_prompt.strip():
        raise HTTPException(status_code=409, detail="Add a video prompt first")
    _, _, configured, available, _, detail = video_provider_status()
    if not configured or not available:
        raise HTTPException(status_code=503, detail=detail)
    # Acquire SQLite's write lock before checking/inserting, including concurrent requests.
    db.execute(update(Project).where(Project.id == project.id).values(updated_at=Project.updated_at))
    active_jobs = db.scalars(select(Job).where(
        Job.project_id == project.id, Job.kind == "shot_clip",
        Job.status.in_(["queued", "running"]),
    )).all()
    for existing in active_jobs:
        if any(attempt.get("shot_id") == shot.id and
               attempt.get("aspect_ratio", project.aspect_ratio) == target_aspect
               for attempt in (existing.attempts or [])):
            db.commit()
            return existing
    include_cast_refs = bool(payload and payload.include_cast_references) or video_mode == "r2v"
    attempt_meta: dict[str, Any] = {"shot_id": shot.id, "aspect_ratio": target_aspect}
    if include_cast_refs:
        attempt_meta["include_cast_references"] = True
    if video_mode == "r2v":
        attempt_meta["video_mode"] = "r2v"
        attempt_meta["include_scene_reference"] = include_scene_ref
        if element_ids is not None:
            attempt_meta["element_ids"] = element_ids
    if both_aspects:
        attempt_meta["both_aspects"] = True
    ensure_project_job_capacity(db, project.id, "shot_clip")
    job = Job(
        project_id=project.id,
        episode_id=shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None,
        kind="shot_clip",
        message="Queued (R2V)" if video_mode == "r2v" else "Queued",
        attempts=[attempt_meta],
    )
    db.add(job)
    db.commit()
    task = (
        enqueue_shot_clip(shot.id, job.id, target_aspect, include_cast_references=True)
        if include_cast_refs
        else enqueue_shot_clip(shot.id, job.id, target_aspect)
    )
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


@app.get("/api/shots/{shot_id}", response_model=ShotRead)
def read_shot(shot_id: str, db: Session = Depends(get_db)) -> Shot:
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
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    return shot


@app.get("/api/shots/{shot_id}/image-reference/sources")
def image_reference_sources(shot_id: str, db: Session = Depends(get_db)):
    shot = db.get(Shot, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    results = []
    current_ep = shot.scene.script_version.episode
    for item in earlier_shots(shot):
        item_ep = item.scene.script_version.episode
        title = item.title
        if current_ep and item_ep and item_ep.id != current_ep.id:
            label = shot.scene.script_version.project.episode_label or "Episode"
            title = f"[{label} {item_ep.episode_number}] {item.title}"
        results.append(dict(id=item.id, title=title, media=media_sources(item)))
    return results


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
        raise HTTPException(status_code=409, detail="Choose an earlier shot in this project")
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
    clear_reference_color_state(ref.id, keep_filename=filename)
    release_reference_asset(db, previous)
    return shot


@app.delete("/api/shots/{shot_id}/image-reference", status_code=204)
def remove_image_reference(shot_id: str, aspect_ratio: str, db: Session = Depends(get_db)):
    shot = _image_reference_target(db, shot_id, aspect_ratio)
    ref = next((item for item in shot.image_references if item.aspect_ratio == aspect_ratio), None)
    if ref:
        ref_id = ref.id
        filename = ref.filename
        db.delete(ref)
        db.commit()
        db.expire(shot, ['image_references'])
        clear_reference_color_state(ref_id)
        release_reference_asset(db, filename)


@app.post("/api/shots/{shot_id}/image-reference/retouch-color", response_model=ShotRead)
def retouch_shot_reference_color(
    shot_id: str,
    aspect_ratio: str = "16:9",
    action: str = "boost",
    strength: float | None = None,
    db: Session = Depends(get_db),
) -> Shot:
    from uuid import uuid4

    shot = _image_reference_target(db, shot_id, aspect_ratio)
    ref = next((item for item in shot.image_references if item.aspect_ratio == aspect_ratio), None)
    cont = next((item for item in shot.continuity_frames if item.aspect_ratio == aspect_ratio), None)
    target = ref or cont
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"No {aspect_ratio} image or video frame reference found on this shot.",
        )

    state = get_reference_color_state(target.id, target.filename)
    history: list[dict] = list(state["history"])
    idx = int(state["index"])
    orig_filename = str(state["original_filename"])
    action_norm = (action or "boost").strip().lower()

    if action_norm in ("undo", "redo"):
        if action_norm == "undo":
            if idx <= 0:
                raise HTTPException(status_code=422, detail="Nothing to undo.")
            next_idx = idx - 1
        else:
            if idx >= len(history) - 1:
                raise HTTPException(status_code=422, detail="Nothing to redo.")
            next_idx = idx + 1
        entry = history[next_idx]
        candidate_fn = str(entry.get("filename") or "")
        if not candidate_fn or not (settings.media_dir / candidate_fn).exists():
            raise HTTPException(status_code=422, detail="History frame file is missing on disk.")
        target.filename = candidate_fn
        target.created_at = now()
        db.commit()
        state["index"] = next_idx
        save_reference_color_state(target.id, state)
        return read_shot(shot.id, db)

    current_strength = float(history[idx].get("strength", 0.0))
    if action_norm == "reset":
        target_strength = 0.0
    elif action_norm == "increase":
        target_strength = float(strength) if strength is not None else (current_strength + 0.25)
    elif action_norm == "decrease":
        target_strength = float(strength) if strength is not None else (current_strength - 0.25)
    elif action_norm == "set":
        target_strength = float(strength if strength is not None else 1.0)
    else:
        # "boost": apply the standard +100% preset (use + / - to step by 25%)
        target_strength = float(strength) if strength is not None else 1.0

    target_strength = round(max(-1.0, min(3.0, target_strength)), 4)

    orig_path = settings.media_dir / orig_filename
    if not orig_path.exists():
        orig_path = settings.media_dir / target.filename
        orig_filename = target.filename
        state["original_filename"] = orig_filename
    if not orig_path.exists():
        raise HTTPException(
            status_code=422,
            detail="Reference frame file is missing on disk.",
        )

    created_new_file = False
    if abs(target_strength) < 1e-4 and (settings.media_dir / orig_filename).exists():
        new_filename = orig_filename
        mime_type = target.mime_type or "image/png"
        destination = settings.media_dir / new_filename
    else:
        try:
            enhanced_bytes, mime_type = enhance_image_vibrancy(
                orig_path.read_bytes(), strength=target_strength
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Could not enhance reference frame color: {exc}",
            ) from exc

        prefix = "image-reference-retouched" if ref else "continuity-retouched"
        new_filename = f"{prefix}-{uuid4()}.png"
        destination = settings.media_dir / new_filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(enhanced_bytes)
        created_new_file = True

    discarded = history[idx + 1 :]
    history = history[: idx + 1]
    history.append({"filename": new_filename, "strength": target_strength})
    next_idx = len(history) - 1

    target.filename = new_filename
    target.mime_type = mime_type
    target.created_at = now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        if created_new_file:
            destination.unlink(missing_ok=True)
        raise

    kept_filenames = {orig_filename, *(str(item.get("filename") or "") for item in history)}
    for item in discarded:
        old_fn = str(item.get("filename") or "")
        if old_fn and old_fn not in kept_filenames and (
            old_fn.startswith("image-reference-retouched-") or old_fn.startswith("continuity-retouched-")
        ):
            delete_media_file(old_fn)

    state["original_filename"] = orig_filename
    state["history"] = history
    state["index"] = next_idx
    save_reference_color_state(target.id, state)

    return read_shot(shot.id, db)


@app.post("/api/shots/{shot_id}/clip/retouch-color", response_model=ShotRead)
def retouch_shot_clip_color(
    shot_id: str,
    aspect_ratio: str = "16:9",
    action: str = "boost",
    strength: float | None = None,
    db: Session = Depends(get_db),
) -> Shot:
    if aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Unsupported aspect ratio")

    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.scene)
            .selectinload(Scene.script_version)
            .selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")

    project = shot.scene.script_version.project
    targets: list[ShotClip | ShotMediaVariant] = []
    variant = next(
        (
            item
            for item in shot.media_variants
            if item.media_type == "clip" and item.aspect_ratio == aspect_ratio
        ),
        None,
    )
    if variant is not None:
        targets.append(variant)
    if shot.clip is not None and (aspect_ratio == project.aspect_ratio or variant is None):
        targets.append(shot.clip)

    if not targets:
        raise HTTPException(
            status_code=404,
            detail=f"No {aspect_ratio} video clip found on this shot.",
        )

    primary_target = targets[0]
    state = get_reference_color_state(primary_target.id, primary_target.filename)
    history: list[dict] = list(state["history"])
    idx = int(state["index"])
    orig_filename = str(state["original_filename"] or primary_target.filename)
    action_norm = (action or "boost").strip().lower()

    if action_norm in ("undo", "redo"):
        if action_norm == "undo":
            if idx <= 0:
                raise HTTPException(status_code=422, detail="Nothing to undo.")
            next_idx = idx - 1
        else:
            if idx >= len(history) - 1:
                raise HTTPException(status_code=422, detail="Nothing to redo.")
            next_idx = idx + 1
        state["index"] = next_idx
        for target in targets:
            target_state = {
                "original_filename": target.filename,
                "history": [
                    {"filename": target.filename, "strength": float(item.get("strength", 0.0))}
                    for item in history
                ],
                "index": next_idx,
            }
            save_reference_color_state(target.id, target_state)
        return read_shot(shot.id, db)

    current_strength = float(history[idx].get("strength", 0.0))
    if action_norm == "reset":
        target_strength = 0.0
    elif action_norm == "increase":
        target_strength = float(strength) if strength is not None else (current_strength + 0.25)
    elif action_norm == "decrease":
        target_strength = float(strength) if strength is not None else (current_strength - 0.25)
    elif action_norm == "set":
        target_strength = float(strength if strength is not None else 1.0)
    else:
        target_strength = float(strength) if strength is not None else 1.0

    target_strength = round(max(-1.0, min(3.0, target_strength)), 4)

    history = history[: idx + 1]
    history.append({"filename": primary_target.filename, "strength": target_strength})
    next_idx = len(history) - 1

    for target in targets:
        target_state = {
            "original_filename": orig_filename if target is primary_target else target.filename,
            "history": [
                {"filename": target.filename, "strength": float(item.get("strength", 0.0))}
                for item in history
            ],
            "index": next_idx,
        }
        save_reference_color_state(target.id, target_state)

    return read_shot(shot.id, db)



@app.post(
    "/api/shots/{shot_id}/image-reference/repaint",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def repaint_shot_reference(
    shot_id: str,
    aspect_ratio: str = Form("16:9"),
    prompt: str = Form("repaint this image"),
    db: Session = Depends(get_db),
) -> Job:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.image_references),
            selectinload(Shot.continuity_frames),
            selectinload(Shot.scene)
            .selectinload(Scene.script_version)
            .selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before repainting reference frames")
    if aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")

    ref = next((item for item in shot.image_references if item.aspect_ratio == aspect_ratio), None)
    cont = next((item for item in shot.continuity_frames if item.aspect_ratio == aspect_ratio), None)
    target = ref or cont
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"No {aspect_ratio} image or video frame reference found on this shot.",
        )

    base_path = settings.media_dir / target.filename
    if not base_path.exists():
        raise HTTPException(
            status_code=422,
            detail="Reference frame file is missing on disk.",
        )

    try:
        snapshot = get_or_create_shot_snapshot(db, project, shot, aspect_ratio)
    except RuntimeError:
        snapshot = shot.continuity_snapshot or {
            "character_ids": list(shot.character_ids or []),
            "effective_image_prompt": shot.image_prompt,
            "scene": {"title": shot.scene.title},
            "framing": shot.framing,
        }
    character_ids = list(snapshot.get("character_ids") or shot.character_ids or [])
    edit_prompt = (prompt or "").strip() or "repaint this image"

    ensure_project_job_capacity(db, project.id, "shot_image")
    job = Job(
        project_id=project.id,
        kind="shot_image",
        message="Queued AI color repaint",
        attempts=[
            {
                "shot_id": shot.id,
                "aspect_ratio": aspect_ratio,
                "character_ids": character_ids,
                "continuity_snapshot": snapshot,
                "edit_reference": True,
                "edit_prompt": edit_prompt,
                "edit_base_filename": target.filename,
                "edit_base_mime_type": target.mime_type or "image/png",
                "edit_is_latest_in_conversation": False,
            }
        ],
    )
    db.add(job)
    db.commit()
    task = enqueue_shot_image(
        shot.id, job.id, aspect_ratio, character_ids=character_ids
    )
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job



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
    max_bytes = 2500 * 1024 * 1024
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Video must be 2.5 GB or smaller"
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
        shot.clip.created_at = now()
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
    if variant:
        variant.filename = filename
        variant.mime_type = file.content_type
        variant.source = "uploaded"
        variant.provider = "manual-upload"
        variant.prompt = shot.video_prompt
        variant.created_at = now()
    elif not use_legacy:
        db.add(ShotMediaVariant(shot_id=shot.id, media_type="clip", aspect_ratio=target_aspect, filename=filename, mime_type=file.content_type, source="uploaded", provider="manual-upload", prompt=shot.video_prompt))
    for t in shot.takes:
        if t.media_type == "clip" and t.aspect_ratio == target_aspect:
            t.selected = False
    if use_legacy:
        shot.selected_clip_take_id = None
    if project.timeline and not promote_available_clips(project, db):
        project.timeline.revision += 1
    shot.status = "clip_ready"
    db.commit()
    db.refresh(shot)
    delete_media_file(previous_filename, db=db)
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
    media = (shot.clip or variant) if target_aspect == project.aspect_ratio else variant
    if not media:
        raise HTTPException(status_code=404, detail="Shot clip not found")
    filename = media.filename
    final_cut_filename = invalidate_final_cut(project, db)
    if target_aspect == project.aspect_ratio and shot.clip:
        db.delete(shot.clip)
    if variant:
        db.delete(variant)
    for t in shot.takes:
        if t.media_type == "clip" and t.aspect_ratio == target_aspect:
            t.selected = False
    if target_aspect == project.aspect_ratio:
        shot.selected_clip_take_id = None
    if project.timeline:
        project.timeline.revision += 1
    shot.status = "image_ready" if shot.image else "planned"
    db.commit()
    delete_media_file(filename, db=db)
    delete_media_file(final_cut_filename)


@app.post(
    "/api/projects/{project_id}/final-cut/render",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_final_cut(
    project_id: str,
    variant: str = "landscape",
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before rendering")
    if variant not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="Variant must be landscape or shorts")
    
    job_query = select(Job).where(
        Job.project_id == project.id,
        Job.kind == f"final_cut:{variant}",
        Job.status.in_(["queued", "running"]),
    )
    if episode_id:
        job_query = job_query.where(Job.episode_id == episode_id)
    active_job = db.scalar(job_query)
    if active_job:
        return active_job

    timeline = ensure_timeline(project, db, episode_id=episode_id)
    document_data = (
        timeline.shorts_document or timeline.document
        if variant == "shorts"
        else timeline.document
    )
    try:
        validate_timeline(TimelineDocument.model_validate(document_data), project, db, episode_id=episode_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    ensure_project_job_capacity(db, project.id, f"final_cut:{variant}")
    job = Job(project_id=project.id, episode_id=episode_id, kind=f"final_cut:{variant}")
    db.add(job)
    db.commit()
    task = enqueue_final_cut(project.id, job.id, variant)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/projects/{project_id}/draft/readiness")
def draft_readiness(
    project_id: str,
    episode_id: str | None = None,
    variant: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_readiness(project, db, episode_id=episode_id, variant=variant)


@app.post(
    "/api/projects/{project_id}/draft",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_draft(
    project_id: str,
    payload: CreateDraftRequest = Body(default_factory=CreateDraftRequest),
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != "shot_plan":
        raise HTTPException(status_code=409, detail="Approve the script before creating a draft")
    active_query = select(Job).where(
        Job.project_id == project.id,
        Job.kind == "create_draft",
        Job.status.in_(["queued", "running"]),
    )
    if episode_id:
        active_query = active_query.where(Job.episode_id == episode_id)
    active = db.scalar(active_query)
    if active:
        return active
    ensure_project_job_capacity(db, project.id, "create_draft")
    draft_shots = latest_shots(project.id, db, episode_id=episode_id)
    primary_aspect = (
        project.aspect_ratio
        if payload.variant == "both"
        else ("9:16" if payload.variant == "shorts" else "16:9")
    )
    job = Job(
        project_id=project.id,
        episode_id=episode_id,
        kind="create_draft",
        attempts=[{"variant": payload.variant, "retry_failed_only": payload.retry_failed_only,
                   "image_references": {shot.id: reference_snapshot(shot, primary_aspect)
                       for shot in draft_shots}}],
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

    ensure_project_job_capacity(db, project.id, "shot_lipsync")
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
    take_timestamp = take.created_at or now()

    if take.media_type == "image":
        if use_legacy:
            if shot.image:
                shot.image.filename = take.filename
                shot.image.mime_type = take.mime_type
                shot.image.provider = take.provider
                shot.image.prompt = take.prompt
                shot.image.source = take.source
                shot.image.created_at = take_timestamp
            else:
                db.add(
                    ShotImage(
                        shot_id=shot.id,
                        filename=take.filename,
                        mime_type=take.mime_type,
                        source=take.source,
                        provider=take.provider,
                        prompt=take.prompt,
                        created_at=take_timestamp,
                    )
                )
        variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == take.aspect_ratio), None)
        if variant:
            variant.filename = take.filename
            variant.mime_type = take.mime_type
            variant.provider = take.provider
            variant.prompt = take.prompt
            variant.source = take.source
            variant.created_at = take_timestamp
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
                    created_at=take_timestamp,
                )
            )
        conv_url = getattr(take, "conversation_url", None) or (take.settings or {}).get("conversation_url")
        if conv_url:
            shot.conversation_url = conv_url
        if project.timeline and not promote_available_clips(project, db):
            project.timeline.revision += 1
        invalidate_final_cut(project, db)

    elif take.media_type == "clip":
        if use_legacy:
            if shot.clip:
                shot.clip.filename = take.filename
                shot.clip.mime_type = take.mime_type
                shot.clip.provider = take.provider
                shot.clip.prompt = take.prompt
                shot.clip.source = take.source
                shot.clip.created_at = take_timestamp
            else:
                db.add(
                    ShotClip(
                        shot_id=shot.id,
                        filename=take.filename,
                        mime_type=take.mime_type,
                        source=take.source,
                        provider=take.provider,
                        prompt=take.prompt,
                        created_at=take_timestamp,
                    )
                )
        variant = next((item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == take.aspect_ratio), None)
        if variant:
            variant.filename = take.filename
            variant.mime_type = take.mime_type
            variant.provider = take.provider
            variant.prompt = take.prompt
            variant.source = take.source
            variant.created_at = take_timestamp
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
                    created_at=take_timestamp,
                )
            )
        conv_url = getattr(take, "conversation_url", None) or (take.settings or {}).get("conversation_url")
        if conv_url:
            shot.conversation_url = conv_url
        if project.timeline and not promote_available_clips(project, db):
            project.timeline.revision += 1
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


@app.post(
    "/api/shots/{shot_id}/takes/{take_id}/edit",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def edit_shot_image_take(
    shot_id: str,
    take_id: str,
    prompt: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> Job:
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.scene)
            .selectinload(Scene.script_version)
            .selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")
    take = next((item for item in shot.takes if item.id == take_id), None)
    if not take:
        raise HTTPException(status_code=404, detail="Take not found")
    if take.media_type != "image":
        raise HTTPException(
            status_code=422, detail="Only image takes support follow-up edits"
        )
    edit_prompt = prompt.strip()
    if not edit_prompt:
        raise HTTPException(
            status_code=422, detail="Enter follow-up edit instructions"
        )
    project = shot.scene.script_version.project
    if project.status != "shot_plan":
        raise HTTPException(
            status_code=409, detail="Approve the script before editing images"
        )
    base_path = settings.media_dir / take.filename if take.filename else None
    if not base_path or not base_path.is_file():
        raise HTTPException(
            status_code=409, detail="Target take image file is missing on disk"
        )

    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    validated_attachments: list[tuple[str, str, bytes]] = []
    for upload in files or []:
        if not upload or not (upload.filename or upload.content_type):
            continue
        if upload.content_type not in extensions:
            raise HTTPException(
                status_code=415, detail="Upload JPG, PNG, or WebP attachment images"
            )
        content = await upload.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail="Attachment image must be 10 MB or smaller"
            )
        if not content or not is_usable_reference_image(content):
            raise HTTPException(
                status_code=400, detail="Uploaded attachment image is empty or invalid"
            )
        validated_attachments.append(
            (extensions[upload.content_type], upload.content_type, content)
        )

    target_aspect = take.aspect_ratio or project.aspect_ratio
    db.execute(
        update(Project)
        .where(Project.id == project.id)
        .values(updated_at=Project.updated_at)
    )
    active_image_jobs = db.scalars(
        select(Job).where(
            Job.project_id == project.id,
            Job.kind == "shot_image",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for existing in active_image_jobs:
        if any(
            attempt.get("shot_id") == shot.id
            and attempt.get("aspect_ratio", project.aspect_ratio) == target_aspect
            for attempt in (existing.attempts or [])
            if isinstance(attempt, dict)
        ):
            db.commit()
            return existing

    take_settings = take.settings if isinstance(take.settings, dict) else {}
    edit_conversation_url = (
        str(
            getattr(take, "conversation_url", None)
            or take_settings.get("conversation_url")
            or ""
        ).strip()
        or None
    )
    image_takes_for_aspect = [
        t
        for t in shot.takes
        if t.media_type == "image" and t.aspect_ratio == target_aspect
    ]
    if not edit_conversation_url and shot.conversation_url:
        if (
            image_takes_for_aspect
            and image_takes_for_aspect[-1].id == take.id
            and take.source == "generated"
        ):
            edit_conversation_url = shot.conversation_url
    same_conv_takes = (
        [
            t
            for t in image_takes_for_aspect
            if (
                getattr(t, "conversation_url", None)
                or (t.settings if isinstance(t.settings, dict) else {}).get(
                    "conversation_url"
                )
            )
            == edit_conversation_url
        ]
        if edit_conversation_url
        else []
    )
    is_latest_in_conv = bool(
        edit_conversation_url
        and (not same_conv_takes or same_conv_takes[-1].id == take.id)
    )

    snapshot = (
        deepcopy(take_settings.get("continuity_snapshot"))
        if isinstance(take_settings.get("continuity_snapshot"), dict)
        else None
    )
    if not snapshot:
        try:
            snapshot = get_or_create_shot_snapshot(db, project, shot, target_aspect)
        except RuntimeError:
            snapshot = shot.continuity_snapshot or {
                "character_ids": list(shot.character_ids or []),
                "effective_image_prompt": shot.image_prompt,
                "scene": {"title": shot.scene.title},
                "framing": shot.framing,
            }
    character_ids = list(snapshot.get("character_ids") or shot.character_ids or [])

    from uuid import uuid4

    saved_attachments: list[dict[str, str]] = []
    for ext, mime_type, content in validated_attachments:
        att_filename = f"edit-ref-{uuid4()}{ext}"
        (settings.media_dir / att_filename).write_bytes(content)
        saved_attachments.append({"filename": att_filename, "mime_type": mime_type})

    ensure_project_job_capacity(db, project.id, "shot_image")
    job = Job(
        project_id=project.id,
        kind="shot_image",
        message="Queued follow-up edit",
        conversation_url=edit_conversation_url,
        attempts=[
            {
                "shot_id": shot.id,
                "aspect_ratio": target_aspect,
                "character_ids": character_ids,
                "continuity_snapshot": snapshot,
                "edit_take_id": take.id,
                "edit_prompt": edit_prompt,
                "edit_base_filename": take.filename,
                "edit_base_mime_type": take.mime_type or "image/png",
                "edit_conversation_url": edit_conversation_url,
                "edit_is_latest_in_conversation": is_latest_in_conv,
                "edit_attachment_files": saved_attachments,
            }
        ],
    )
    db.add(job)
    db.commit()
    task = enqueue_shot_image(
        shot.id, job.id, target_aspect, character_ids=character_ids
    )
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.post(
    "/api/shots/{shot_id}/takes/{take_id}/retouch-color",
    response_model=ShotRead,
)
def retouch_shot_image_take_color(
    shot_id: str,
    take_id: str,
    strength: float = 1.0,
    db: Session = Depends(get_db),
) -> Shot:
    from uuid import uuid4

    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id)
        .options(
            selectinload(Shot.takes),
            selectinload(Shot.image),
            selectinload(Shot.clip),
            selectinload(Shot.media_variants),
            selectinload(Shot.scene)
            .selectinload(Scene.script_version)
            .selectinload(ScriptVersion.project),
        )
    )
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")

    take = next((item for item in shot.takes if item.id == take_id), None)
    if not take:
        raise HTTPException(status_code=404, detail="Take not found")
    if take.media_type != "image":
        raise HTTPException(
            status_code=422, detail="Only image takes can be color-retouched."
        )

    base_path = settings.media_dir / take.filename
    if not base_path.exists():
        raise HTTPException(
            status_code=422, detail="Base take image file is missing on disk."
        )

    try:
        enhanced_bytes, mime_type = enhance_image_vibrancy(
            base_path.read_bytes(), strength=strength
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not enhance image color: {exc}"
        ) from exc

    filename = f"retouched-{uuid4()}.png"
    (settings.media_dir / filename).write_bytes(enhanced_bytes)

    project = shot.scene.script_version.project
    target_aspect = take.aspect_ratio or project.aspect_ratio
    use_legacy = target_aspect == project.aspect_ratio
    prompt_label = (take.prompt or shot.image_prompt or "Shot image") + " [Color Boost]"
    take_timestamp = now()

    if use_legacy:
        if shot.image:
            shot.image.filename = filename
            shot.image.mime_type = mime_type
            shot.image.source = "generated"
            shot.image.provider = "color-boost"
            shot.image.prompt = prompt_label
            shot.image.created_at = take_timestamp
        else:
            db.add(
                ShotImage(
                    shot_id=shot.id,
                    filename=filename,
                    mime_type=mime_type,
                    source="generated",
                    provider="color-boost",
                    prompt=prompt_label,
                    created_at=take_timestamp,
                )
            )

    variant = next(
        (
            item
            for item in shot.media_variants
            if item.media_type == "image" and item.aspect_ratio == target_aspect
        ),
        None,
    )
    if variant:
        variant.filename = filename
        variant.mime_type = mime_type
        variant.source = "generated"
        variant.provider = "color-boost"
        variant.prompt = prompt_label
        variant.created_at = take_timestamp
    else:
        db.add(
            ShotMediaVariant(
                shot_id=shot.id,
                media_type="image",
                aspect_ratio=target_aspect,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider="color-boost",
                prompt=prompt_label,
                created_at=take_timestamp,
            )
        )

    take_settings = dict(take.settings or {})
    take_settings["parent_take_id"] = take.id
    take_settings["edit_prompt"] = (
        "Instant Color Boost (pixel-exact vibrancy & contrast revival)"
    )
    conv_url = getattr(take, "conversation_url", None) or take_settings.get(
        "conversation_url"
    )
    if conv_url:
        take_settings["conversation_url"] = conv_url

    record_take(
        db,
        shot,
        media_type="image",
        aspect_ratio=target_aspect,
        filename=filename,
        mime_type=mime_type,
        source="generated",
        provider="color-boost",
        prompt=prompt_label,
        settings=take_settings,
    )
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
            selectinload(Shot.scene)
            .selectinload(Scene.script_version)
            .selectinload(ScriptVersion.project),
        )
    )
    return fresh_shot or shot


@app.get("/api/publish/providers")
def publish_providers() -> list[dict]:
    return publish_provider_statuses()


@app.get("/api/projects/{project_id}/publish/plan", response_model=PublishPlanRead)
def project_publish_plan(
    project_id: str,
    variant: str = "landscape",
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return build_publish_plan(project, variant, db, episode_id=episode_id)
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
def queue_publish(
    project_id: str,
    payload: PublishCreate,
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> Job:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    data = payload.model_dump(mode="json")
    if episode_id:
        data["episode_id"] = episode_id
    try:
        validate_publish_request(data)
        plan = build_publish_plan(project, payload.variant, db, episode_id=episode_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    available_ids = {item["id"] for group in ("final", "scenes", "shots") for item in plan[group] if item.get("ready")}
    requested_ids = set(payload.selected_asset_ids or [])
    if payload.scope != "final" and not requested_ids:
        raise HTTPException(status_code=422, detail="Select at least one asset to publish")
    if requested_ids and not requested_ids <= available_ids:
        raise HTTPException(status_code=409, detail="One or more selected publish assets are not ready")
    ensure_project_job_capacity(db, project.id, "publish")
    job = Job(
        project_id=project.id,
        episode_id=episode_id,
        kind="publish",
        message="Queued publish",
        attempts=[data],
    )
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
def delete_final_cut(
    project_id: str,
    variant: str = "landscape",
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> None:
    query = select(ProjectVariantExport).where(
        ProjectVariantExport.project_id == project_id,
        ProjectVariantExport.variant == variant,
        ProjectVariantExport.compilation_id.is_(None),
    )
    if episode_id:
        query = query.where(ProjectVariantExport.episode_id == episode_id)
    else:
        query = query.where(ProjectVariantExport.episode_id.is_(None))
    final_cut = db.scalar(query)
    if not final_cut:
        raise HTTPException(status_code=404, detail="Final cut not found")
    filename = final_cut.filename
    db.delete(final_cut)
    db.commit()
    delete_media_file(filename)


@app.get("/api/timeline/luts")
def get_timeline_luts() -> list[dict]:
    return list_lut_presets()


@app.get("/api/projects/{project_id}/timeline", response_model=ProjectTimelineRead)
def get_timeline(
    project_id: str,
    edit: str = "landscape",
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if edit not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="edit must be landscape or shorts")
    timeline = ensure_timeline(project, db, episode_id=episode_id)
    promote_available_clips(project, db, episode_id=episode_id)
    append_missing_shots(project, db, episode_id=episode_id)
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
def update_timeline(
    project_id: str,
    payload: TimelineUpdate,
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db, episode_id=episode_id)
    if timeline.revision != payload.revision:
        raise HTTPException(status_code=409, detail="The timeline changed in another session. Reload it before saving.")
    try:
        validate_timeline(payload.document, project, db, episode_id=episode_id)
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
def reset_timeline(
    project_id: str,
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db, episode_id=episode_id)
    doc = initial_document(project, db, episode_id=episode_id)
    timeline.document = doc
    if timeline.shorts_document is not None:
        from copy import deepcopy
        timeline.shorts_document = deepcopy(doc)
    timeline.revision += 1
    db.commit()
    return {"revision": timeline.revision, "document": timeline.document, "assets": project.timeline_assets}


@app.post("/api/projects/{project_id}/timeline/assets", response_model=ProjectTimelineRead)
async def upload_timeline_asset(
    project_id: str,
    file: UploadFile = File(...),
    label: str = Form("Uploaded asset"),
    kind: str | None = Form(None),
    episode_id: str | None = None,
    compilation_id: str | None = None,
    edit: str = "landscape",
    db: Session = Depends(get_db),
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if compilation_id and episode_id:
        raise HTTPException(status_code=422, detail="Provide episode_id or compilation_id, not both")
    image_extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    video_extensions = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    audio_extensions = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
        "audio/flac": ".flac",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }
    ext_to_video_mime = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }
    ext_to_audio_mime = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".webm": "audio/webm",
    }
    from pathlib import Path
    from uuid import uuid4

    raw_mime = (file.content_type or "").lower().strip()
    suffix = Path(file.filename or "").suffix.lower()
    requested_kind = (kind or "").lower().strip()
    if raw_mime in {"", "application/octet-stream"}:
        if requested_kind == "video" and suffix in ext_to_video_mime:
            raw_mime = ext_to_video_mime[suffix]
        elif requested_kind == "audio" and suffix in ext_to_audio_mime:
            raw_mime = ext_to_audio_mime[suffix]
        elif suffix in ext_to_video_mime:
            raw_mime = ext_to_video_mime[suffix]
        elif suffix in ext_to_audio_mime:
            raw_mime = ext_to_audio_mime[suffix]

    is_image = raw_mime in image_extensions and requested_kind != "audio" and requested_kind != "video"
    is_video = (
        requested_kind == "video" and (raw_mime in video_extensions or suffix in ext_to_video_mime)
    ) or (
        requested_kind != "audio" and raw_mime in video_extensions
    )
    is_audio = (not is_image and not is_video) and (raw_mime in audio_extensions)
    if not is_image and not is_video and not is_audio:
        raise HTTPException(
            status_code=415,
            detail="Upload a JPG, PNG, WebP image; MP4, WebM, MOV video; or MP3, WAV, M4A, AAC, OGG, FLAC audio file",
        )
    if is_image:
        max_bytes = 10 * 1024 * 1024
        limit_label = "10 MB"
        kind_label = "Image"
        ext = image_extensions[raw_mime]
        stored_mime = raw_mime
        default_label = "Uploaded image"
    elif is_video:
        max_bytes = 2500 * 1024 * 1024
        limit_label = "2.5 GB"
        kind_label = "Video"
        resolved_video_mime = raw_mime if raw_mime in video_extensions else ext_to_video_mime.get(suffix, "video/mp4")
        ext = video_extensions[resolved_video_mime]
        stored_mime = resolved_video_mime
        default_label = "Uploaded video"
    else:
        max_bytes = 50 * 1024 * 1024
        limit_label = "50 MB"
        kind_label = "Audio"
        ext = audio_extensions[raw_mime]
        stored_mime = (
            "audio/mp4" if raw_mime == "video/mp4" else ("audio/webm" if raw_mime == "video/webm" else raw_mime)
        )
        default_label = "Uploaded audio"

    filename = f"timeline-{uuid4()}{ext}"
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    dest_path = settings.media_dir / filename
    total_written = 0
    try:
        with dest_path.open("wb") as out_f:
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > max_bytes:
                    break
                out_f.write(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    if total_written == 0 or total_written > max_bytes:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413 if total_written > max_bytes else 400,
            detail=f"{kind_label} must be between 1 byte and {limit_label}",
        )

    clean_label = (label or "").strip()[:160]
    if not clean_label or clean_label == "Uploaded asset":
        clean_label = default_label
    asset = TimelineAsset(
        project_id=project.id,
        filename=filename,
        mime_type=stored_mime,
        label=clean_label,
    )
    db.add(asset)
    if compilation_id:
        comp = db.get(Compilation, compilation_id)
        if not comp or comp.project_id != project.id:
            raise HTTPException(status_code=404, detail="Compilation not found")
        timeline = ensure_compilation_timeline(db, comp)
    else:
        timeline = ensure_timeline(project, db, episode_id=episode_id)
    timeline.revision += 1
    db.commit()
    active_doc = (
        timeline.shorts_document or timeline.document
        if edit == "shorts"
        else timeline.document
    )
    return {"revision": timeline.revision, "document": active_doc, "edit": edit if edit in {"landscape", "shorts"} else "landscape", "assets": project.timeline_assets}


@app.delete("/api/projects/{project_id}/timeline/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeline_asset(
    project_id: str,
    asset_id: str,
    episode_id: str | None = None,
    compilation_id: str | None = None,
    db: Session = Depends(get_db),
) -> None:
    asset = db.get(TimelineAsset, asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Timeline asset not found")
    if compilation_id and episode_id:
        raise HTTPException(status_code=422, detail="Provide episode_id or compilation_id, not both")
    project = asset.project
    if compilation_id:
        comp = db.get(Compilation, compilation_id)
        if not comp or comp.project_id != project.id:
            raise HTTPException(status_code=404, detail="Compilation not found")
        timeline = ensure_compilation_timeline(db, comp)
    else:
        timeline = ensure_timeline(project, db, episode_id=episode_id)
    docs = [TimelineDocument.model_validate(timeline.document)]
    if timeline.shorts_document:
        docs.append(TimelineDocument.model_validate(timeline.shorts_document))
    used = any(
        any(item.source and item.source.kind == "asset" and item.source.ref_id == asset.id for item in doc.primary)
        or any(item.source.kind == "asset" and item.source.ref_id == asset.id for item in doc.video_overlays)
        or any(item.source.kind == "asset" and item.source.ref_id == asset.id for item in doc.image_overlays)
        or any(item.source.kind == "asset" and item.source.ref_id == asset.id for item in doc.audio_overlays)
        for doc in docs
    )
    if used:
        raise HTTPException(status_code=409, detail="Remove this asset from the timeline before deleting it")
    filename = asset.filename
    db.delete(asset)
    timeline.revision += 1
    db.commit()
    delete_media_file(filename)


@app.get("/api/projects/{project_id}/compilations", response_model=list[CompilationRead])
def list_compilations(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    compilations = db.scalars(
        select(Compilation)
        .where(Compilation.project_id == project_id)
        .order_by(Compilation.created_at)
    ).all()
    results = []
    for comp in compilations:
        comp_timeline = db.scalar(
            select(ProjectTimeline).where(ProjectTimeline.compilation_id == comp.id)
        )
        duration_ms = 0
        clip_count = 0
        if comp_timeline and comp_timeline.document:
            duration_ms = timeline_duration(comp_timeline.document)
            clip_count = len(comp_timeline.document.get("primary", []))
        results.append(
            {
                "id": comp.id,
                "project_id": comp.project_id,
                "title": comp.title,
                "description": comp.description,
                "episode_ids": comp.episode_ids or [],
                "settings": comp.settings or {},
                "created_at": comp.created_at,
                "updated_at": comp.updated_at,
                "duration_ms": duration_ms,
                "clip_count": clip_count,
            }
        )
    return results


@app.post("/api/projects/{project_id}/compilations", response_model=CompilationRead, status_code=status.HTTP_201_CREATED)
def create_compilation(project_id: str, payload: CompilationCreate, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    valid_ep_ids = {ep.id for ep in project.episodes}
    for ep_id in payload.episode_ids:
        if ep_id not in valid_ep_ids:
            raise HTTPException(status_code=400, detail=f"Episode {ep_id} does not belong to project")
    compilation = Compilation(
        project_id=project_id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        episode_ids=payload.episode_ids,
        settings=payload.settings or {},
    )
    db.add(compilation)
    db.flush()
    timeline = ensure_compilation_timeline(db, compilation)
    db.commit()
    db.refresh(compilation)

    duration_ms = timeline_duration(timeline.document) if timeline.document else 0
    clip_count = len(timeline.document.get("primary", [])) if timeline.document else 0

    return {
        "id": compilation.id,
        "project_id": compilation.project_id,
        "title": compilation.title,
        "description": compilation.description,
        "episode_ids": compilation.episode_ids,
        "settings": compilation.settings,
        "created_at": compilation.created_at,
        "updated_at": compilation.updated_at,
        "duration_ms": duration_ms,
        "clip_count": clip_count,
    }


@app.get("/api/compilations/{compilation_id}", response_model=CompilationRead)
def get_compilation(compilation_id: str, db: Session = Depends(get_db)) -> dict:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    comp_timeline = db.scalar(
        select(ProjectTimeline).where(ProjectTimeline.compilation_id == comp.id)
    )
    duration_ms = 0
    clip_count = 0
    if comp_timeline and comp_timeline.document:
        duration_ms = timeline_duration(comp_timeline.document)
        clip_count = len(comp_timeline.document.get("primary", []))
    return {
        "id": comp.id,
        "project_id": comp.project_id,
        "title": comp.title,
        "description": comp.description,
        "episode_ids": comp.episode_ids or [],
        "settings": comp.settings or {},
        "created_at": comp.created_at,
        "updated_at": comp.updated_at,
        "duration_ms": duration_ms,
        "clip_count": clip_count,
    }


@app.get("/api/compilations/{compilation_id}/shots", response_model=list[ShotRead])
def get_compilation_shots(compilation_id: str, db: Session = Depends(get_db)):
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    all_shots: list[Shot] = []
    for ep_id in (comp.episode_ids or []):
        all_shots.extend(latest_shots(comp.project_id, db, episode_id=ep_id))
    return all_shots


@app.patch("/api/compilations/{compilation_id}", response_model=CompilationRead)
def update_compilation(compilation_id: str, payload: CompilationUpdate, db: Session = Depends(get_db)) -> dict:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    project = comp.project
    if payload.title is not None:
        comp.title = payload.title.strip()
    if payload.description is not None:
        comp.description = payload.description.strip() if payload.description else None
    membership_changed = False
    if payload.episode_ids is not None:
        valid_ep_ids = {ep.id for ep in project.episodes}
        for ep_id in payload.episode_ids:
            if ep_id not in valid_ep_ids:
                raise HTTPException(status_code=400, detail=f"Episode {ep_id} does not belong to project")
        previous_ids = list(comp.episode_ids or [])
        next_ids = list(payload.episode_ids)
        membership_changed = previous_ids != next_ids
        comp.episode_ids = next_ids
        flag_modified(comp, "episode_ids")
    if payload.settings is not None:
        comp.settings = {**comp.settings, **payload.settings}
    comp.updated_at = now()
    if membership_changed:
        # Membership is a snapshot source: rebuild landscape + shorts from current episodes.
        rebuild_compilation_timeline(db, comp)
    db.commit()
    db.refresh(comp)

    comp_timeline = db.scalar(
        select(ProjectTimeline).where(ProjectTimeline.compilation_id == comp.id)
    )
    duration_ms = 0
    clip_count = 0
    if comp_timeline and comp_timeline.document:
        duration_ms = timeline_duration(comp_timeline.document)
        clip_count = len(comp_timeline.document.get("primary", []))

    return {
        "id": comp.id,
        "project_id": comp.project_id,
        "title": comp.title,
        "description": comp.description,
        "episode_ids": comp.episode_ids or [],
        "settings": comp.settings or {},
        "created_at": comp.created_at,
        "updated_at": comp.updated_at,
        "duration_ms": duration_ms,
        "clip_count": clip_count,
    }


@app.delete("/api/compilations/{compilation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_compilation(compilation_id: str, db: Session = Depends(get_db)) -> None:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    active_jobs = db.scalars(
        select(Job).where(
            Job.compilation_id == compilation_id,
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for job in active_jobs:
        mark_job_cancelled(job)
        revoke_job_task(job)
    exports = db.scalars(
        select(ProjectVariantExport).where(ProjectVariantExport.compilation_id == compilation_id)
    ).all()
    for exp in exports:
        delete_media_file(exp.filename, db=db)
    db.delete(comp)
    db.commit()


@app.get("/api/compilations/{compilation_id}/timeline", response_model=ProjectTimelineRead)
def get_compilation_timeline(
    compilation_id: str,
    edit: str = "landscape",
    db: Session = Depends(get_db),
) -> dict:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    if edit not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="edit must be landscape or shorts")
    timeline = ensure_compilation_timeline(db, comp)
    dirty = False
    if edit == "shorts":
        if not timeline.shorts_document:
            timeline.shorts_document = deepcopy(timeline.document)
            dirty = True
        document = timeline.shorts_document
    else:
        document = timeline.document
    # Persist newly created compilation timelines (ensure only flushes).
    db.commit()
    if dirty:
        db.refresh(timeline)
    return {
        "revision": timeline.revision,
        "document": document,
        "edit": edit,
        "assets": comp.project.timeline_assets,
    }


@app.put("/api/compilations/{compilation_id}/timeline", response_model=ProjectTimelineRead)
def update_compilation_timeline(
    compilation_id: str,
    payload: TimelineUpdate,
    db: Session = Depends(get_db),
) -> dict:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    timeline = ensure_compilation_timeline(db, comp)
    if payload.revision != timeline.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Timeline was edited elsewhere. Refresh to load revision {timeline.revision}",
        )
    doc = payload.document.model_dump(mode="json")
    try:
        validate_timeline(payload.document, comp.project, db, max_duration_ms=120 * 60 * 1000)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.edit == "shorts":
        timeline.shorts_document = doc
    else:
        timeline.document = doc
    timeline.revision += 1
    db.commit()
    active_doc = (
        timeline.shorts_document or timeline.document
        if payload.edit == "shorts"
        else timeline.document
    )
    return {
        "revision": timeline.revision,
        "document": active_doc,
        "edit": payload.edit,
        "assets": comp.project.timeline_assets,
    }


@app.post("/api/compilations/{compilation_id}/timeline/reset", response_model=ProjectTimelineRead)
def reset_compilation_timeline(
    compilation_id: str,
    edit: str = "landscape",
    db: Session = Depends(get_db),
) -> dict:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    if edit not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="edit must be landscape or shorts")
    new_timeline = rebuild_compilation_timeline(db, comp)
    if edit == "shorts" and not new_timeline.shorts_document:
        new_timeline.shorts_document = deepcopy(new_timeline.document)
    db.commit()
    document = (
        new_timeline.shorts_document or new_timeline.document
        if edit == "shorts"
        else new_timeline.document
    )
    return {
        "revision": new_timeline.revision,
        "document": document,
        "edit": edit,
        "assets": comp.project.timeline_assets,
    }


@app.post("/api/compilations/{compilation_id}/export", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def export_compilation(
    compilation_id: str,
    variant: str = "landscape",
    db: Session = Depends(get_db),
) -> Job:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    if variant not in {"landscape", "shorts"}:
        raise HTTPException(status_code=422, detail="Variant must be landscape or shorts")

    job_query = select(Job).where(
        Job.compilation_id == compilation_id,
        Job.kind == f"final_cut:{variant}",
        Job.status.in_(["queued", "running"]),
    )
    active_job = db.scalar(job_query)
    if active_job:
        return active_job

    timeline = ensure_compilation_timeline(db, comp)
    document_data = (
        timeline.shorts_document or timeline.document
        if variant == "shorts"
        else timeline.document
    )
    try:
        validate_timeline(
            TimelineDocument.model_validate(document_data),
            comp.project,
            db,
            max_duration_ms=120 * 60 * 1000,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    ensure_project_job_capacity(db, comp.project_id, f"final_cut:{variant}")
    job = Job(
        project_id=comp.project_id,
        compilation_id=comp.id,
        kind=f"final_cut:{variant}",
        message=f"Queued compilation final cut ({variant})",
    )
    db.add(job)
    db.commit()
    task = enqueue_final_cut(comp.project_id, job.id, variant)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/compilations/{compilation_id}/exports", response_model=list[ProjectVariantExportRead])
def list_compilation_exports(compilation_id: str, db: Session = Depends(get_db)) -> list[dict]:
    comp = db.get(Compilation, compilation_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Compilation not found")
    timeline = db.scalar(
        select(ProjectTimeline).where(ProjectTimeline.compilation_id == comp.id)
    )
    timeline_revision = timeline.revision if timeline else 0
    exports = db.scalars(
        select(ProjectVariantExport)
        .where(ProjectVariantExport.compilation_id == compilation_id)
        .order_by(ProjectVariantExport.created_at.desc())
    ).all()
    return [
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
        for export in exports
    ]


@app.delete("/api/compilations/{compilation_id}/exports", status_code=status.HTTP_204_NO_CONTENT)
def delete_compilation_export(
    compilation_id: str,
    variant: str = "landscape",
    db: Session = Depends(get_db),
) -> None:
    export = db.scalar(
        select(ProjectVariantExport).where(
            ProjectVariantExport.compilation_id == compilation_id,
            ProjectVariantExport.variant == variant,
        )
    )
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    filename = export.filename
    db.delete(export)
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
            episode_id=source_version.episode_id,
            version_number=source_version.version_number + 1,
            source_text=source_version.source_text,
            structured_data=updated_data,
            status=source_version.status if source_version.status == "approved" else "review",
        )
        db.add(new_version)
        db.flush()
        shot_id_map: dict[str, str] = {}
        media_id_map: dict[str, str] = {}
        pending_refs: list[tuple[ShotContinuityFrame | ShotImageReference, Shot]] = []
        for index, original_scene in enumerate(updated_data.get("scenes", [])):
            scene_data = dict(original_scene)
            shots = scene_data.pop("shots", [])
            orig_scene_model = next(
                (existing for existing in source_version.scenes if existing.order_index == index),
                None,
            )
            if orig_scene_model:
                for note_field in ("set_notes", "lighting_notes", "wardrobe_notes", "props_notes"):
                    scene_data.setdefault(note_field, getattr(orig_scene_model, note_field, ""))
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
                original = next(
                    (
                        item
                        for existing in source_version.scenes
                        if existing.order_index == index
                        for item in existing.shots
                        if item.order_index == shot_data.get("order_index", shot_index)
                    ),
                    None,
                )
                new_shot = Shot(
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
                    character_ids=list(shot_data.get("character_ids") or (original.character_ids if original else [])),
                    continuity_snapshot=(
                        shot_data.get("continuity_snapshot")
                        if isinstance(shot_data.get("continuity_snapshot"), dict)
                        else (original.continuity_snapshot if original else None)
                    ),
                    status=original.status if original else "planned",
                    dialogue_audio_filename=original.dialogue_audio_filename if original else None,
                    dialogue_audio_mime=original.dialogue_audio_mime if original else None,
                    conversation_url=original.conversation_url if original else None,
                )
                db.add(new_shot)
                db.flush()
                if original:
                    shot_id_map[original.id] = new_shot.id
                    if original.image:
                        new_img = ShotImage(
                            shot_id=new_shot.id,
                            filename=original.image.filename,
                            mime_type=original.image.mime_type,
                            source=original.image.source,
                            provider=original.image.provider,
                            prompt=original.image.prompt,
                            created_at=original.image.created_at,
                        )
                        db.add(new_img)
                        db.flush()
                        media_id_map[original.image.id] = new_img.id
                    if original.clip:
                        new_clip = ShotClip(
                            shot_id=new_shot.id,
                            filename=original.clip.filename,
                            mime_type=original.clip.mime_type,
                            source=original.clip.source,
                            provider=original.clip.provider,
                            prompt=original.clip.prompt,
                            created_at=original.clip.created_at,
                        )
                        db.add(new_clip)
                        db.flush()
                        media_id_map[original.clip.id] = new_clip.id
                    for mv in original.media_variants:
                        new_mv = ShotMediaVariant(
                            shot_id=new_shot.id,
                            media_type=mv.media_type,
                            aspect_ratio=mv.aspect_ratio,
                            filename=mv.filename,
                            mime_type=mv.mime_type,
                            source=mv.source,
                            provider=mv.provider,
                            prompt=mv.prompt,
                            created_at=mv.created_at,
                        )
                        db.add(new_mv)
                        db.flush()
                        media_id_map[mv.id] = new_mv.id
                    take_id_map: dict[str, str] = {}
                    for take in original.takes:
                        new_take = ShotTake(
                            shot_id=new_shot.id,
                            media_type=take.media_type,
                            aspect_ratio=take.aspect_ratio,
                            filename=take.filename,
                            mime_type=take.mime_type,
                            source=take.source,
                            provider=take.provider,
                            prompt=take.prompt,
                            settings=deepcopy(take.settings),
                            selected=take.selected,
                            created_at=take.created_at,
                        )
                        db.add(new_take)
                        db.flush()
                        take_id_map[take.id] = new_take.id
                        media_id_map[take.id] = new_take.id
                    new_shot.selected_image_take_id = take_id_map.get(original.selected_image_take_id or "")
                    new_shot.selected_clip_take_id = take_id_map.get(original.selected_clip_take_id or "")
                    new_shot.selected_audio_take_id = take_id_map.get(original.selected_audio_take_id or "")
                    for cf in original.continuity_frames:
                        pending_refs.append((cf, new_shot))
                    for ir in original.image_references:
                        pending_refs.append((ir, new_shot))
                shot_cursor = end_ms
        for ref_obj, target_shot in pending_refs:
            if isinstance(ref_obj, ShotContinuityFrame):
                db.add(
                    ShotContinuityFrame(
                        shot_id=target_shot.id,
                        aspect_ratio=ref_obj.aspect_ratio,
                        filename=ref_obj.filename,
                        mime_type=ref_obj.mime_type,
                        source_shot_id=shot_id_map.get(ref_obj.source_shot_id, ref_obj.source_shot_id),
                        source_shot_title=ref_obj.source_shot_title,
                        source_time_ms=ref_obj.source_time_ms,
                        created_at=ref_obj.created_at,
                    )
                )
            elif isinstance(ref_obj, ShotImageReference):
                db.add(
                    ShotImageReference(
                        shot_id=target_shot.id,
                        aspect_ratio=ref_obj.aspect_ratio,
                        filename=ref_obj.filename,
                        mime_type=ref_obj.mime_type,
                        source_shot_id=shot_id_map.get(ref_obj.source_shot_id, ref_obj.source_shot_id),
                        source_shot_title=ref_obj.source_shot_title,
                        source_media_id=media_id_map.get(ref_obj.source_media_id, ref_obj.source_media_id),
                        source_media_kind=ref_obj.source_media_kind,
                        source_media_type=ref_obj.source_media_type,
                        source_aspect_ratio=ref_obj.source_aspect_ratio,
                        source_time_ms=ref_obj.source_time_ms,
                        mode=ref_obj.mode,
                        created_at=ref_obj.created_at,
                    )
                )
        if shot_id_map:
            from .deep_review import _find_episode_timeline, _remap_timeline_doc
            timeline = _find_episode_timeline(db, source_version.project_id, source_version.episode_id)
            if timeline:
                timeline.document = _remap_timeline_doc(timeline.document, shot_id_map) or {}
                timeline.shorts_document = _remap_timeline_doc(timeline.shorts_document, shot_id_map)
                timeline.revision += 1
                flag_modified(timeline, "document")
                if timeline.shorts_document is not None:
                    flag_modified(timeline, "shorts_document")
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


@app.get("/api/script-versions/{version_id}/deep-review")
def read_deep_review(version_id: str, db: Session = Depends(get_db)) -> dict:
    return get_deep_review(db, version_id)


@app.get("/api/script-versions/{version_id}/history")
def script_version_history(version_id: str, db: Session = Depends(get_db)) -> dict:
    from .deep_review import _load_version, _latest_episode_version
    from .deep_review_context import build_context, fingerprint
    version = _load_version(db, version_id)
    current = _latest_episode_version(db, version)
    versions = db.scalars(select(ScriptVersion).where(
        ScriptVersion.project_id == version.project_id,
        ScriptVersion.episode_id == version.episode_id,
    ).order_by(ScriptVersion.version_number.desc())).all()
    return {"current_version_id": current.id,
            "current_fingerprint": fingerprint(build_context(db, current)),
            "versions": [{"id": item.id, "version_number": item.version_number,
                          "status": item.status, "created_at": item.created_at}
                         for item in versions]}


@app.get("/api/script-versions/{version_id}", response_model=ScriptVersionRead)
def read_script_version(version_id: str, db: Session = Depends(get_db)) -> ScriptVersion:
    from .deep_review import _load_version
    return _load_version(db, version_id)


@app.post("/api/script-versions/{version_id}/restore", response_model=ScriptVersionRead)
def restore_script_version(version_id: str, payload: ScriptVersionRestoreRequest,
                           db: Session = Depends(get_db)) -> ScriptVersion:
    from .deep_review import (_load_version, _latest_episode_version,
                             _clone_version_with_patches, _find_episode_timeline,
                             _remap_timeline_doc)
    from .deep_review_context import build_context, fingerprint
    source = _load_version(db, version_id)
    current = _latest_episode_version(db, source)
    if (current.id != payload.expected_version_id
            or fingerprint(build_context(db, current)) != payload.expected_fingerprint):
        raise HTTPException(409, "The current script changed. Reload version history before restoring.")
    if current.id == source.id:
        raise HTTPException(409, "This version is already current.")
    timeline = _find_episode_timeline(db, source.project_id, source.episode_id)
    # Save assembly with the version being left, so restoring it later recovers edits.
    if timeline:
        current.structured_data = {**(current.structured_data or {}), "history_timeline": {
            "document": deepcopy(timeline.document),
            "shorts_document": deepcopy(timeline.shorts_document)}}
        flag_modified(current, "structured_data")
    restored, _, _ = _clone_version_with_patches(db, source, [])
    if timeline:
        snapshot = (source.structured_data or {}).get("history_timeline") or {}
        old_shots = [sh for sc in sorted(source.scenes, key=lambda sc: sc.order_index)
                     for sh in sorted(sc.shots, key=lambda sh: sh.order_index)]
        new_shots = [sh for sc in sorted(restored.scenes, key=lambda sc: sc.order_index)
                     for sh in sorted(sc.shots, key=lambda sh: sh.order_index)]
        mapping = {old.id: new.id for old, new in zip(old_shots, new_shots)}
        timeline.document = _remap_timeline_doc(snapshot.get("document"), mapping) or {"version": 1, "primary": []}
        timeline.shorts_document = _remap_timeline_doc(snapshot.get("shorts_document"), mapping)
        flag_modified(timeline, "document")
        flag_modified(timeline, "shorts_document")
    restored.status = "review"
    restored.project.status = "review"
    if restored.episode:
        restored.episode.status = "review"
    # A rendered cut no longer represents this restored screenplay; retain its media file.
    cuts = select(ProjectFinalCut).where(ProjectFinalCut.project_id == source.project_id)
    cuts = cuts.where(ProjectFinalCut.episode_id == source.episode_id)
    for cut in db.scalars(cuts):
        db.delete(cut)
    db.commit()
    return _load_version(db, restored.id)


@app.post(
    "/api/script-versions/{version_id}/deep-review",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_deep_review(version_id: str, db: Session = Depends(get_db)) -> dict:
    return start_deep_review(db, version_id, enqueue_deep_review)


@app.post(
    "/api/deep-reviews/{run_id}/findings/{finding_id}/draft",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def draft_deep_review_finding(
    run_id: str,
    finding_id: str,
    db: Session = Depends(get_db),
) -> Job:
    return start_deep_review_draft(db, run_id, finding_id, enqueue_deep_review_draft)


@app.patch("/api/deep-reviews/{run_id}/findings/{finding_id}")
def update_deep_review_finding(
    run_id: str,
    finding_id: str,
    payload: DeepReviewFindingDecision,
    db: Session = Depends(get_db),
) -> dict:
    return decide_deep_review_finding(
        db,
        run_id,
        finding_id,
        payload.decision,
        expected_version_id=payload.expected_version_id,
        patches=payload.patches,
    )


@app.post("/api/deep-reviews/{run_id}/undo")
def undo_deep_review(
    run_id: str,
    payload: DeepReviewUndoRequest,
    db: Session = Depends(get_db),
) -> dict:
    return undo_deep_review_apply(db, run_id, payload.expected_version_id)


@app.post("/api/projects/{project_id}/approve", response_model=ProjectSummary)
def approve_project(
    project_id: str,
    payload: ProjectApproval,
    episode_id: str | None = None,
    db: Session = Depends(get_db),
) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    version_query = select(ScriptVersion).where(ScriptVersion.project_id == project_id)
    if episode_id:
        version_query = version_query.where(ScriptVersion.episode_id == episode_id)
    version = db.scalar(version_query.order_by(ScriptVersion.version_number.desc()))
    if not version and episode_id:
        version = db.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id, ScriptVersion.episode_id.is_(None))
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
    if payload.approved and append_missing_shots(project, db, episode_id=episode_id):
        invalidated_filename = invalidate_final_cut(project, db)
    if payload.approved:
        sync_project_cast(project, db, source_text=version.source_text, tag_shots=True)
    version.status = "approved" if payload.approved else "review"
    if episode_id:
        ep = db.get(Episode, episode_id)
        if ep:
            ep.status = "shot_plan" if payload.approved else "review"
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
