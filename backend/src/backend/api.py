from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from .database import Base, engine, get_db
from .final_cut import invalidate_final_cut
from .media import delete_media_file, image_provider_statuses
from .migrations import migrate_schema
from .models import Character, CharacterReference, Job, Project, ProjectFinalCut, ProjectVariantExport, Scene, ScriptVersion, Shot, ShotClip, ShotImage, ShotMediaVariant, Suggestion, TimelineAsset
from .schemas import (
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    JobRead,
    ImageProviderStatus,
    ProjectTimelineRead,
    ProjectApproval,
    ProjectContinuationCreate,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    ProviderStatus,
    ShotImageGenerateRequest,
    ShotRead,
    ShotUpdate,
    SuggestionDecision,
    SuggestionRead,
    TimelineDocument,
    TimelineUpdate,
)
from .settings import settings
from .tasks import enqueue_final_cut, enqueue_script_analysis, enqueue_script_continuation, enqueue_shot_clip, enqueue_shot_image
from .timeline import append_missing_shots, ensure_timeline, initial_document, promote_available_clips, validate_timeline
from .video import comfyui_status


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
    configured, available, detail = comfyui_status()
    return ProviderStatus(
        id="comfyui",
        label="ComfyUI",
        configured=configured,
        available=available,
        model="workflow",
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
    if job.kind == "shot_image" and job.status == "queued":
        pending = db.scalars(
            select(Job)
            .where(Job.kind == "shot_image", Job.status == "queued")
            .order_by(Job.created_at, Job.id)
        ).all()
        queue_position = next((index for index, item in enumerate(pending, start=1) if item.id == job.id), None)
    result = JobRead.model_validate(job)
    return result.model_copy(update={"queue_position": queue_position})


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
    shot.framing = payload.framing
    shot.duration_ms = payload.duration_ms
    shot.image_prompt = payload.image_prompt
    shot.video_prompt = payload.video_prompt
    shot.title_card_text = payload.title_card_text
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
    character_ids = list(dict.fromkeys(payload.character_ids or []))
    if character_ids:
        characters = db.scalars(
            select(Character)
            .where(
                Character.project_id == project.id,
                Character.id.in_(character_ids),
            )
            .options(selectinload(Character.references))
        ).all()
        if len(characters) != len(character_ids):
            raise HTTPException(status_code=404, detail="One or more cast characters were not found")
        missing_refs = [character.name for character in characters if not character.references]
        if missing_refs:
            raise HTTPException(
                status_code=409,
                detail=f"Add a reference image for: {', '.join(missing_refs)}",
            )
    job = Job(project_id=project.id, kind="shot_image")
    db.add(job)
    db.commit()
    target_aspect = aspect_ratio or project.aspect_ratio
    if target_aspect not in {"16:9", "9:16"}:
        raise HTTPException(status_code=422, detail="Aspect ratio must be 16:9 or 9:16")
    task = enqueue_shot_image(shot.id, job.id, target_aspect, character_ids=character_ids)
    job.task_id = task.id
    db.commit()
    db.refresh(job)
    return job


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
    character = Character(project_id=project_id, name=name, notes=payload.notes.strip())
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
    db.commit()
    db.refresh(character)
    return character


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
    if not shot.image and not image_variant:
        raise HTTPException(
            status_code=409, detail="Generate or upload a shot image first"
        )
    if not shot.video_prompt.strip():
        raise HTTPException(status_code=409, detail="Add a video prompt first")
    configured, available, detail = comfyui_status()
    if not configured or not available:
        raise HTTPException(status_code=503, detail=detail)
    job = Job(project_id=project.id, kind="shot_clip")
    db.add(job)
    db.commit()
    task = enqueue_shot_clip(shot.id, job.id, target_aspect)
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
    try:
        validate_timeline(TimelineDocument.model_validate(timeline.document), project, db)
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
def get_timeline(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db)
    db.commit()
    return {"revision": timeline.revision, "document": timeline.document, "assets": project.timeline_assets}


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
    timeline.document = payload.document.model_dump(mode="json")
    timeline.revision += 1
    db.commit()
    db.refresh(timeline)
    return {"revision": timeline.revision, "document": timeline.document, "assets": project.timeline_assets}


@app.post("/api/projects/{project_id}/timeline/reset", response_model=ProjectTimelineRead)
def reset_timeline(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    timeline = ensure_timeline(project, db)
    timeline.document = initial_document(project, db)
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
    version.status = "approved" if payload.approved else "review"
    project.status = "shot_plan" if payload.approved else "review"
    db.commit()
    db.refresh(project)
    delete_media_file(invalidated_filename)
    return project
