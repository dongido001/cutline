"""Resumable create-draft workflow: images → clips → dialogue → preview."""

from __future__ import annotations

from .image_references import load_visual_reference, queued_reference
from .media import prepare_visual_reference_prompt

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .cast import load_cast_references
from .database import SessionLocal
from .final_cut import invalidate_final_cut
from .jobs import JobCancelled, preserve_cancelled, raise_if_cancelled
from .models import (
    Job,
    Project,
    ProjectVariantExport,
    Scene,
    ScriptVersion,
    Shot,
    ShotClip,
    ShotImage,
    ShotMediaVariant,
    now,
)
from .queue import video_huey
from .schemas import TimelineDocument
from .settings import settings
from .takes import record_take
from .timeline import ensure_timeline, promote_available_clips
from .timeline_render import assemble_timeline
from .timing import estimate_dialogue_duration_ms
from .video import generate_shot_clip, video_provider_status, clip_source_image
from .vibevoice_tts import synthesize_dialogue, vibevoice_status


def _latest_version(db: Session, project_id: str) -> ScriptVersion | None:
    return db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .options(
            selectinload(ScriptVersion.scenes).selectinload(Scene.shots).selectinload(Shot.image),
            selectinload(ScriptVersion.scenes).selectinload(Scene.shots).selectinload(Shot.clip),
            selectinload(ScriptVersion.scenes).selectinload(Scene.shots).selectinload(Shot.media_variants),
            selectinload(ScriptVersion.scenes).selectinload(Scene.shots).selectinload(Shot.takes),
            selectinload(ScriptVersion.scenes).selectinload(Scene.shots).selectinload(Shot.scene),
        )
        .order_by(ScriptVersion.version_number.desc())
    )


def _latest_shots(db: Session, project_id: str) -> list[Shot]:
    version = _latest_version(db, project_id)
    if not version:
        return []
    return [shot for scene in version.scenes for shot in scene.shots]


def _scene_index_map(shots: list[Shot]) -> dict[str, tuple[int, int, str]]:
    """shot_id → (scene_number_1based, scene_total, scene_title)."""
    ordered: list[Scene] = []
    seen: set[str] = set()
    for shot in shots:
        scene = shot.scene
        if scene.id in seen:
            continue
        seen.add(scene.id)
        ordered.append(scene)
    total = len(ordered)
    by_scene = {
        scene.id: (index + 1, total, scene.title or f"Scene {index + 1}")
        for index, scene in enumerate(ordered)
    }
    return {shot.id: by_scene[shot.scene_id] for shot in shots if shot.scene_id in by_scene}


def _format_scene_message(shot: Shot, scene_map: dict[str, tuple[int, int, str]], action: str) -> str:
    info = scene_map.get(shot.id)
    shot_label = shot.title or "shot"
    if not info:
        return f"{action} · {shot_label}"
    number, total, title = info
    return f"Scene {number}/{total} · {action} · {title}"


def _clip_duration_for_shot(shot: Shot) -> int:
    """Prefer authored duration; raise to dialogue speech-rate estimate when longer."""
    authored = int(shot.duration_ms or 5000)
    spoken = estimate_dialogue_duration_ms(shot.dialogue)
    if spoken is None:
        return authored
    return max(authored, spoken)


def _shot_image(shot: Shot, aspect: str) -> ShotImage | ShotMediaVariant | None:
    if aspect == shot.scene.script_version.project.aspect_ratio and shot.image:
        return shot.image
    return next(
        (
            item
            for item in shot.media_variants
            if item.media_type == "image" and item.aspect_ratio == aspect
        ),
        None,
    )


def _shot_clip(shot: Shot, aspect: str) -> ShotClip | ShotMediaVariant | None:
    if aspect == shot.scene.script_version.project.aspect_ratio and shot.clip:
        return shot.clip
    return next(
        (
            item
            for item in shot.media_variants
            if item.media_type == "clip" and item.aspect_ratio == aspect
        ),
        shot.clip,
    )


def project_readiness(project: Project, db: Session) -> dict[str, Any]:
    shots = _latest_shots(db, project.id)
    aspect = project.aspect_ratio
    video_id, video_label, video_configured, video_available, _, video_detail = video_provider_status()
    vv_configured, vv_available, vv_detail = vibevoice_status()
    attention: list[dict[str, str]] = []
    missing_images: list[str] = []
    missing_clips: list[str] = []
    missing_audio: list[str] = []

    if project.status != "shot_plan":
        attention.append({"code": "script", "message": "Approve the script before creating a draft"})
    if not video_configured or not video_available:
        attention.append({"code": "video_provider", "message": video_detail})
    if not vv_configured or not vv_available:
        attention.append({"code": "vibevoice", "message": vv_detail})

    for shot in shots:
        if not _shot_image(shot, aspect):
            missing_images.append(shot.id)
            if not (shot.image_prompt or "").strip():
                attention.append(
                    {
                        "code": "image_prompt",
                        "message": f"{shot.title or shot.id}: add an image prompt",
                        "shot_id": shot.id,
                    }
                )
        if not _shot_clip(shot, aspect):
            missing_clips.append(shot.id)
        if shot.dialogue and not shot.dialogue_audio_filename:
            missing_audio.append(shot.id)

    scenes_progress: list[dict[str, Any]] = []
    version = _latest_version(db, project.id)
    if version:
        for index, scene in enumerate(version.scenes):
            scene_shots = list(scene.shots)
            images_ready = sum(1 for shot in scene_shots if _shot_image(shot, aspect))
            clips_ready = sum(1 for shot in scene_shots if _shot_clip(shot, aspect))
            audio_needed = sum(1 for shot in scene_shots if shot.dialogue)
            audio_ready = sum(
                1
                for shot in scene_shots
                if shot.dialogue and shot.dialogue_audio_filename
            )
            shot_count = len(scene_shots)
            if shot_count and clips_ready >= shot_count and audio_ready >= audio_needed:
                status = "ready"
            elif clips_ready or images_ready:
                status = "in_progress"
            else:
                status = "pending"
            scenes_progress.append(
                {
                    "id": scene.id,
                    "title": scene.title or f"Scene {index + 1}",
                    "order_index": index,
                    "shot_count": shot_count,
                    "images_ready": images_ready,
                    "clips_ready": clips_ready,
                    "audio_ready": audio_ready,
                    "audio_needed": audio_needed,
                    "status": status,
                }
            )

    return {
        "video_provider": {
            "id": video_id,
            "label": video_label,
            "configured": video_configured,
            "available": video_available,
            "detail": video_detail,
        },
        "vibevoice": {
            "configured": vv_configured,
            "available": vv_available,
            "detail": vv_detail,
        },
        "missing_images": missing_images,
        "missing_clips": missing_clips,
        "missing_audio": missing_audio,
        "attention": attention,
        "shot_count": len(shots),
        "scene_count": len(scenes_progress),
        "scenes": scenes_progress,
        "can_start": project.status == "shot_plan",
    }


def _persist_attempt(job: Job, step: str, payload: dict[str, Any]) -> None:
    attempts = [item for item in (job.attempts or []) if isinstance(item, dict)]
    others = [item for item in attempts if item.get("draft_step") != step]
    others.append({"draft_step": step, **payload})
    job.attempts = others


def _failed_steps(job: Job) -> set[str]:
    failed: set[str] = set()
    for item in job.attempts or []:
        if isinstance(item, dict) and item.get("status") == "failed" and item.get("draft_step"):
            failed.add(str(item["draft_step"]))
    return failed


def _generate_missing_images(db: Session, project: Project, job: Job, shots: list[Shot]) -> list[str]:
    from .media import delete_media_file, generate_storyboard_image

    attention: list[str] = []
    aspect = project.aspect_ratio
    scene_map = _scene_index_map(shots)
    pending = [shot for shot in shots if not _shot_image(shot, aspect)]
    total = max(len(pending), 1)
    completed = 0
    for shot in shots:
        raise_if_cancelled(db, job.id)
        if _shot_image(shot, aspect):
            continue
        prompt = (shot.image_prompt or "").strip()
        if not prompt:
            attention.append(f"{shot.title or shot.id}: missing image prompt")
            completed += 1
            continue
        from .continuity import get_or_create_shot_snapshot
        try:
            snapshot = get_or_create_shot_snapshot(db, project, shot, aspect)
        except RuntimeError as exc:
            attention.append(f"{shot.title or shot.id}: image failed ({exc})")
            completed += 1
            continue
        prompt = snapshot["effective_image_prompt"]
        shot.character_ids = list(snapshot.get("character_ids") or [])
        job.message = _format_scene_message(shot, scene_map, "Generating image")
        job.progress = 0.05 + (completed / total) * 0.30
        db.commit()
        try:
            image_reference = queued_reference(job, shot)
            visual_reference = load_visual_reference(image_reference)
            references = load_cast_references(
                db, project.id, shot.character_ids or [],
                reference_ids=[
                    reference_id
                    for character in snapshot.get("characters", [])
                    for reference_id in character.get("reference_ids", [])
                ],
            )
            if visual_reference:
                references.append(visual_reference)
            def report_image_progress(update: dict) -> None:
                if update.get("provider"):
                    job.provider = str(update["provider"])
                if update.get("phase"):
                    job.provider_phase = str(update["phase"])
                if update.get("conversation_url"):
                    job.conversation_url = str(update["conversation_url"])
                    shot.conversation_url = str(update["conversation_url"])
                db.commit()

            filename, mime_type, provider = generate_storyboard_image(
                prompt,
                shot.scene.title,
                snapshot.get("framing") or shot.framing,
                aspect,
                progress=report_image_progress,
                references=references,
            )
            if image_reference:
                prompt = prepare_visual_reference_prompt(prompt)
            previous = shot.image.filename if shot.image else None
            invalidate_final_cut(project, db)
            if shot.image:
                shot.image.filename = filename
                shot.image.mime_type = mime_type
                shot.image.source = "generated"
                shot.image.provider = provider
                shot.image.prompt = prompt
                shot.image.created_at = now()
            else:
                db.add(
                    ShotImage(
                        shot_id=shot.id,
                        filename=filename,
                        mime_type=mime_type,
                        source="generated",
                        provider=provider,
                        prompt=prompt,
                    )
                )
            record_take(
                db,
                shot,
                media_type="image",
                aspect_ratio=aspect,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider=provider,
                prompt=prompt,
                settings={"continuity_snapshot": snapshot, **({"image_reference": image_reference} if image_reference else {})},
            )
            shot.status = "image_ready"
            db.commit()
            if previous and previous != filename:
                delete_media_file(previous)
        except Exception as exc:  # noqa: BLE001
            attention.append(f"{shot.title or shot.id}: image failed ({exc})")
            db.rollback()
            job = db.get(Job, job.id) or job
        completed += 1
        job.progress = 0.05 + (completed / total) * 0.30
        db.commit()
    return attention


def _generate_missing_clips(db: Session, project: Project, job: Job, shots: list[Shot]) -> list[str]:
    attention: list[str] = []
    aspect = project.aspect_ratio
    scene_map = _scene_index_map(shots)
    _, _, configured, available, _, detail = video_provider_status()
    if not configured or not available:
        return [detail]
    pending = [shot for shot in shots if not _shot_clip(shot, aspect)]
    total = max(len(pending), 1)
    completed = 0
    for shot in shots:
        raise_if_cancelled(db, job.id)
        if _shot_clip(shot, aspect):
            continue
        continuity_frame = next(
            (
                frame
                for frame in shot.continuity_frames
                if frame.aspect_ratio == aspect
            ),
            None,
        )
        source = clip_source_image(shot, aspect)
        if not source:
            attention.append(f"{shot.title or shot.id}: needs an image before video")
            completed += 1
            continue
        from .continuity import current_shot_snapshot, video_continuity_prompt
        matching_image_takes = [
            take for take in shot.takes
            if take.media_type == "image" and take.aspect_ratio == aspect
            and take.filename == source.filename
        ] if not continuity_frame else []
        image_take = next(
            (take for take in matching_image_takes if take.id == shot.selected_image_take_id),
            max(matching_image_takes, key=lambda take: take.created_at, default=None),
        )
        snapshot = (image_take.settings or {}).get("continuity_snapshot") if image_take else None
        if not snapshot:
            candidate = shot.continuity_snapshot
            snapshot = candidate if candidate and candidate.get("aspect_ratio") == aspect else None
        snapshot = snapshot or current_shot_snapshot(shot, aspect)
        base_prompt = (shot.video_prompt or shot.image_prompt or "").strip()
        prompt = video_continuity_prompt(snapshot, base_prompt, aspect)
        if continuity_frame:
            prompt = "\n\n".join(
                part
                for part in (
                    prompt,
                    "Continue directly from the selected frame of the previous shot. Preserve its background, set layout, lighting, props, and overall visual composition as the exact opening state.",
                )
                if part
            )
        duration_ms = _clip_duration_for_shot(shot)
        if duration_ms != shot.duration_ms:
            shot.duration_ms = duration_ms
        job.message = _format_scene_message(shot, scene_map, "Generating clip")
        job.progress = 0.35 + (completed / total) * 0.35
        db.commit()
        try:
            filename, mime_type, provider = generate_shot_clip(
                settings.media_dir / source.filename,
                prompt,
                duration_ms,
                aspect,
                scene_id=str(shot.scene.id),
            )
            try:
                raise_if_cancelled(db, job.id)
            except JobCancelled:
                db.rollback()
                delete_media_file(filename)
                raise
            invalidate_final_cut(project, db)
            if shot.clip:
                shot.clip.filename = filename
                shot.clip.mime_type = mime_type
                shot.clip.source = "generated"
                shot.clip.provider = provider
                shot.clip.prompt = prompt
                shot.clip.created_at = now()
            else:
                db.add(
                    ShotClip(
                        shot_id=shot.id,
                        filename=filename,
                        mime_type=mime_type,
                        source="generated",
                        provider=provider,
                        prompt=prompt,
                    )
                )
            record_take(
                db,
                shot,
                media_type="clip",
                aspect_ratio=aspect,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider=provider,
                prompt=prompt,
                settings={
                    "continuity_frame": {
                        "source_shot_id": continuity_frame.source_shot_id,
                        "source_shot_title": continuity_frame.source_shot_title,
                        "source_time_ms": continuity_frame.source_time_ms,
                    }
                } if continuity_frame else None,
            )
            shot.status = "clip_ready"
            if project.timeline and not promote_available_clips(project, db):
                project.timeline.revision += 1
            db.commit()
        except JobCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            attention.append(f"{shot.title or shot.id}: clip failed ({exc})")
            db.rollback()
            job = db.get(Job, job.id) or job
        completed += 1
        job.progress = 0.35 + (completed / total) * 0.35
        db.commit()
    return attention


def _generate_missing_audio(db: Session, project: Project, job: Job, shots: list[Shot]) -> list[str]:
    attention: list[str] = []
    scene_map = _scene_index_map(shots)
    configured, available, detail = vibevoice_status()
    if not configured or not available:
        return [detail]
    pending = [shot for shot in shots if shot.dialogue and not shot.dialogue_audio_filename]
    total = max(len(pending), 1)
    completed = 0
    for shot in shots:
        raise_if_cancelled(db, job.id)
        if not shot.dialogue or shot.dialogue_audio_filename:
            continue
        job.message = _format_scene_message(shot, scene_map, "Synthesizing dialogue")
        job.progress = 0.70 + (completed / total) * 0.15
        db.commit()
        try:
            from .voice_service import synthesize_shot_dialogue
            filename, mime_type, provider = synthesize_shot_dialogue(shot, db)
            record_take(
                db,
                shot,
                media_type="audio",
                aspect_ratio=project.aspect_ratio,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider=provider,
                prompt="dialogue",
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            attention.append(f"{shot.title or shot.id}: dialogue failed ({exc})")
            db.rollback()
            job = db.get(Job, job.id) or job
        completed += 1
        job.progress = 0.70 + (completed / total) * 0.15
        db.commit()
    return attention


def _assemble_preview(db: Session, project: Project, job: Job, variant: str) -> list[str]:
    attention: list[str] = []
    try:
        raise_if_cancelled(db, job.id)
        timeline = ensure_timeline(project, db)
        promote_available_clips(project, db)
        if variant == "shorts":
            document_data = timeline.shorts_document or deepcopy(timeline.document)
            if not timeline.shorts_document:
                timeline.shorts_document = document_data
                db.commit()
        else:
            document_data = timeline.document
        document = TimelineDocument.model_validate(document_data)
        job.message = f"Assembling {variant} preview"
        job.progress = 0.9
        db.commit()

        def update_progress(completed: int, total: int) -> None:
            raise_if_cancelled(db, job.id)
            job.progress = 0.9 + (completed / max(total, 1)) * 0.09
            job.message = f"Rendering preview shot {completed} of {total}"
            db.commit()

        result = assemble_timeline(project, document, variant, db, update_progress)
        export = db.scalar(
            select(ProjectVariantExport).where(
                ProjectVariantExport.project_id == project.id,
                ProjectVariantExport.variant == variant,
            )
        )
        previous = export.filename if export else None
        if export:
            export.filename = result.filename
            export.duration_ms = result.duration_ms
            export.width = result.width
            export.height = result.height
            export.clip_count = result.clip_count
            export.fallback_count = result.fallback_count
            export.timeline_revision = timeline.revision
            export.created_at = now()
        else:
            db.add(
                ProjectVariantExport(
                    project_id=project.id,
                    variant=variant,
                    filename=result.filename,
                    duration_ms=result.duration_ms,
                    width=result.width,
                    height=result.height,
                    clip_count=result.clip_count,
                    fallback_count=result.fallback_count,
                    timeline_revision=timeline.revision,
                )
            )
        db.commit()
        if previous and previous != result.filename:
            from .media import delete_media_file

            delete_media_file(previous)
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        attention.append(f"Preview assemble failed: {exc}")
        db.rollback()
    return attention


def run_create_draft(project_id: str, job_id: str, variant: str = "landscape", retry_failed_only: bool = False) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        project = db.get(Project, project_id)
        if not job or not project:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.05
            job.message = "Checking draft readiness"
            db.commit()

            failed = _failed_steps(job) if retry_failed_only else set()
            shots = _latest_shots(db, project_id)
            attention: list[str] = []

            steps = [
                ("images", lambda: _generate_missing_images(db, project, job, shots)),
                ("clips", lambda: _generate_missing_clips(db, project, job, shots)),
                ("audio", lambda: _generate_missing_audio(db, project, job, shots)),
                ("assemble", lambda: _assemble_preview(db, project, job, variant)),
            ]
            for name, runner in steps:
                if retry_failed_only and failed and name not in failed:
                    continue
                job.message = f"Draft step: {name}"
                db.commit()
                step_attention = runner()
                status = "failed" if step_attention else "completed"
                _persist_attempt(job, name, {"status": status, "attention": step_attention})
                attention.extend(step_attention)
                db.commit()

            raise_if_cancelled(db, job_id)
            job = db.get(Job, job_id)
            if not job:
                return
            job.progress = 1
            if attention:
                job.status = "completed"
                job.message = "Draft finished with items needing attention"
                job.error = "; ".join(attention[:8])
                job.attempts = (job.attempts or []) + [{"draft_step": "summary", "attention": attention}]
            else:
                job.status = "completed"
                job.message = "Draft preview ready"
                job.error = None
            db.commit()
        except JobCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.message = "Create draft failed"
                job.error = str(exc)
                db.commit()
            raise


@video_huey.task()
def create_draft_worker(
    project_id: str,
    job_id: str,
    variant: str = "landscape",
    retry_failed_only: bool = False,
) -> None:
    run_create_draft(project_id, job_id, variant, retry_failed_only)


def enqueue_create_draft(
    project_id: str,
    job_id: str,
    variant: str = "landscape",
    retry_failed_only: bool = False,
):
    return create_draft_worker(project_id, job_id, variant, retry_failed_only)
