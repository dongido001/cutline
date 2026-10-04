from .image_references import load_visual_reference, queued_reference
from .media import prepare_visual_reference_prompt
from copy import deepcopy
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from .cast import cast_portrait_prompt, is_usable_reference_image, load_cast_references, sync_project_cast
from .database import SessionLocal
from .final_cut import invalidate_final_cut

from .jobs import JobCancelled, preserve_cancelled, raise_if_cancelled
from .media import delete_media_file, generate_storyboard_image
from .models import Character, CharacterReference, Job, Project, ProjectVariantExport, PublishRecord, Scene, ScriptVersion, Shot, ShotClip, ShotImage, ShotMediaVariant, Suggestion, now
from .providers import get_script_provider, normalize_analysis_result
from .publishing import collect_publish_assets, publish_assets
from .queue import chatgpt_huey, gemini_huey, general_huey, image_huey, video_huey
from .screenplay_parser import parse_structured_screenplay
from .settings import settings
from .timeline import append_missing_shots, ensure_timeline, promote_available_clips
from .timeline_render import assemble_timeline
from .schemas import TimelineDocument
from .video import generate_shot_clip as render_shot_clip
from uuid import uuid4
from pathlib import Path
import shutil

SCENE_PERSIST_FIELDS = {
    "title",
    "location",
    "time_of_day",
    "start_ms",
    "end_ms",
    "action",
    "dialogue",
    "set_notes",
    "lighting_notes",
    "wardrobe_notes",
    "props_notes",
}


def _scene_kwargs(scene_data: dict) -> dict:
    return {key: scene_data[key] for key in SCENE_PERSIST_FIELDS if key in scene_data}


def _planning_duration_seconds(source_text: str, parsed: dict | None, configured_target: int) -> int:
    if configured_target > 0:
        return configured_target
    if parsed:
        shots = [shot for scene in parsed.get("scenes", []) for shot in scene.get("shots", [])]
        latest_end = max((int(shot.get("end_ms") or 0) for shot in shots), default=0)
        if latest_end > 0:
            return max(10, round(latest_end / 1000))
        summed = sum(int(shot.get("duration_ms") or 0) for shot in shots)
        if summed > 0:
            return max(10, round(summed / 1000))
    declared = re.search(r"(?im)^Duration:\s*(\d+)\s*(seconds?|secs?|minutes?|mins?)\b", source_text)
    if declared:
        seconds = int(declared.group(1)) * (60 if declared.group(2).lower().startswith("m") else 1)
        return max(10, min(1800, seconds))
    word_count = len(re.findall(r"\b\w+\b", source_text))
    return max(30, min(1800, round(word_count / 2.5)))


def _analyze_script(project_id: str, version_id: str, job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        project = db.get(Project, project_id)
        version = db.get(ScriptVersion, version_id)
        if not job or not project or not version:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.1
            job.message = (
                "Generating with local Ollama"
                if project.text_provider == "ollama"
                else "Reading screenplay"
            )
            db.commit()

            provider = get_script_provider(project.text_provider)
            parsed = parse_structured_screenplay(version.source_text)
            planning_duration = _planning_duration_seconds(
                version.source_text, parsed, project.target_duration_seconds
            )
            raise_if_cancelled(db, job_id)
            if parsed:
                job.message = "Reviewing parsed shot plan"
                db.commit()
                review = provider.review(parsed, planning_duration)
                parsed["suggestions"].extend(
                    review.get("suggestions", []) if isinstance(review, dict) else []
                )
                result = normalize_analysis_result(
                    parsed, planning_duration
                )
                result["parser"] = parsed["parser"]
            else:
                result = normalize_analysis_result(
                    provider.analyze(
                        version.source_text, planning_duration
                    ),
                    planning_duration,
                )
            raise_if_cancelled(db, job_id)
            job.progress = 0.55
            job.message = "Building scenes and suggestions"
            db.commit()

            db.execute(delete(Scene).where(Scene.script_version_id == version.id))
            db.execute(delete(Suggestion).where(Suggestion.script_version_id == version.id))
            db.flush()

            for scene_index, original_scene_data in enumerate(result.get("scenes", [])):
                scene_data = dict(original_scene_data)
                shot_data = scene_data.pop("shots", [])
                scene = Scene(
                    script_version_id=version.id,
                    order_index=scene_index,
                    **_scene_kwargs(scene_data),
                )
                db.add(scene)
                db.flush()
                for shot_index, shot in enumerate(shot_data):
                    db.add(
                        Shot(
                            scene_id=scene.id,
                            order_index=shot.get("order_index", shot_index),
                            title=shot.get("title", ""),
                            start_ms=shot.get("start_ms"),
                            end_ms=shot.get("end_ms"),
                            action=shot.get("action", ""),
                            dialogue=shot.get("dialogue", []),
                            sound=shot.get("sound", ""),
                            duration_ms=shot.get("duration_ms", 5000),
                            framing=shot.get("framing", "Medium"),
                            image_prompt=shot.get("image_prompt", ""),
                            video_prompt=shot.get("video_prompt", ""),
                            title_card_text=shot.get("title_card_text", ""),
                        )
                    )

            for suggestion in result.get("suggestions", []):
                db.add(Suggestion(script_version_id=version.id, **suggestion))

            raise_if_cancelled(db, job_id)
            version.structured_data = result
            version.status = "review"
            if version.episode_id:
                from .models import Episode
                ep = db.get(Episode, version.episode_id)
                if ep:
                    ep.status = "review"
            project.status = "review"
            sync_project_cast(project, db, source_text=version.source_text, tag_shots=True)
            job.status = "completed"
            job.progress = 1
            job.message = "Review ready"
            db.commit()
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.message = "Script analysis failed"
                job.error = str(exc)
                db.commit()
            raise


@general_huey.task(retries=2, retry_delay=5)
def analyze_script_general(project_id: str, version_id: str, job_id: str) -> None:
    _analyze_script(project_id, version_id, job_id)


@chatgpt_huey.task(retries=2, retry_delay=5)
def analyze_script_chatgpt(project_id: str, version_id: str, job_id: str) -> None:
    _analyze_script(project_id, version_id, job_id)


@gemini_huey.task(retries=2, retry_delay=5)
def analyze_script_gemini(project_id: str, version_id: str, job_id: str) -> None:
    _analyze_script(project_id, version_id, job_id)


def enqueue_script_analysis(
    provider: str, project_id: str, version_id: str, job_id: str
):
    if provider == "chatgpt_web":
        return analyze_script_chatgpt(project_id, version_id, job_id)
    if provider == "gemini_web":
        return analyze_script_gemini(project_id, version_id, job_id)
    return analyze_script_general(project_id, version_id, job_id)


def _continuation_context(version: ScriptVersion) -> str:
    lines: list[str] = []
    for scene in version.scenes:
        characters = sorted(
            {
                str(line.get("character") or line.get("speaker") or "").strip()
                for line in scene.dialogue
                if isinstance(line, dict) and (line.get("character") or line.get("speaker"))
            }
        )
        lines.append(
            f"Scene {scene.order_index + 1}: {scene.title}; "
            f"location {scene.location}; characters {', '.join(characters) or 'none'}; "
            f"action {scene.action[:500]}"
        )
    return "\n".join(lines)[-6000:]


def _offset_continuation(result: dict, cursor_ms: int, scene_offset: int) -> dict:
    scenes = result.get("scenes", [])
    supplied_starts = [
        value
        for scene in scenes
        for value in [scene.get("start_ms")]
        if isinstance(value, int)
    ]
    first_start = min(supplied_starts, default=0)
    shift = 0 if first_start >= cursor_ms else cursor_ms - first_start
    for scene in scenes:
        if isinstance(scene.get("start_ms"), int):
            scene["start_ms"] += shift
        if isinstance(scene.get("end_ms"), int):
            scene["end_ms"] += shift
        for shot in scene.get("shots", []):
            if isinstance(shot.get("start_ms"), int):
                shot["start_ms"] += shift
            if isinstance(shot.get("end_ms"), int):
                shot["end_ms"] += shift
    for suggestion in result.get("suggestions", []):
        target = suggestion.get("target")
        if isinstance(target, dict) and isinstance(target.get("scene_index"), int):
            target["scene_index"] += scene_offset
    return result


def _analyze_continuation(
    project_id: str,
    job_id: str,
    source_text: str,
    target_duration_seconds: int,
) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        project = db.get(Project, project_id)
        version = db.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
            .order_by(ScriptVersion.version_number.desc())
        )
        if not job or not project or not version:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.1
            job.message = "Reading screenplay continuation"
            db.commit()

            provider = get_script_provider(project.text_provider)
            parsed = parse_structured_screenplay(source_text)
            raise_if_cancelled(db, job_id)
            if parsed:
                job.message = "Reviewing continuation shot plan"
                db.commit()
                review = provider.review(parsed, target_duration_seconds)
                parsed["suggestions"].extend(
                    review.get("suggestions", []) if isinstance(review, dict) else []
                )
                result = normalize_analysis_result(parsed, target_duration_seconds)
                result["parser"] = parsed["parser"]
            else:
                context = _continuation_context(version)
                provider_source = source_text
                if project.text_provider != "mock" and context:
                    provider_source = f"""Continue an existing screenplay. Return only NEW scenes and shots; do not repeat the existing story.

EXISTING STORY CONTEXT:
{context}

NEW SCREENPLAY CONTINUATION:
{source_text}"""
                result = normalize_analysis_result(
                    provider.analyze(provider_source, target_duration_seconds),
                    target_duration_seconds,
                )
            if not result.get("scenes"):
                raise RuntimeError("The continuation did not produce any scenes")

            existing_scenes = list(version.scenes)
            cursor_ms = max(
                [
                    value
                    for scene in existing_scenes
                    for value in [scene.end_ms, *(shot.end_ms for shot in scene.shots)]
                    if isinstance(value, int)
                ],
                default=sum(shot.duration_ms for scene in existing_scenes for shot in scene.shots),
            )
            result = _offset_continuation(result, cursor_ms, len(existing_scenes))
            job.progress = 0.6
            job.message = "Appending new scenes"
            db.commit()

            combined = deepcopy(version.structured_data or {})
            combined.setdefault("scenes", [])
            combined["scenes"].extend(result.get("scenes", []))
            combined["parser"] = result.get("parser", "continuation")
            version.structured_data = combined
            flag_modified(version, "structured_data")
            version.source_text = f"{version.source_text.rstrip()}\n\n{source_text.strip()}"
            version.status = "continuation_review"

            for relative_index, original_scene_data in enumerate(result.get("scenes", [])):
                scene_data = dict(original_scene_data)
                shot_data = scene_data.pop("shots", [])
                scene = Scene(
                    script_version_id=version.id,
                    order_index=len(existing_scenes) + relative_index,
                    **_scene_kwargs(scene_data),
                )
                db.add(scene)
                db.flush()
                for shot_index, shot in enumerate(shot_data):
                    db.add(
                        Shot(
                            scene_id=scene.id,
                            order_index=shot.get("order_index", shot_index),
                            title=shot.get("title", ""),
                            start_ms=shot.get("start_ms"),
                            end_ms=shot.get("end_ms"),
                            action=shot.get("action", ""),
                            dialogue=shot.get("dialogue", []),
                            sound=shot.get("sound", ""),
                            duration_ms=shot.get("duration_ms", 5000),
                            framing=shot.get("framing", "Medium"),
                            image_prompt=shot.get("image_prompt", ""),
                            video_prompt=shot.get("video_prompt", ""),
                            title_card_text=shot.get("title_card_text", ""),
                        )
                    )
            for suggestion in result.get("suggestions", []):
                db.add(Suggestion(script_version_id=version.id, **suggestion))

            if project.target_duration_seconds > 0:
                project.target_duration_seconds += target_duration_seconds
            raise_if_cancelled(db, job_id)
            project.status = "review"
            sync_project_cast(project, db, source_text=version.source_text, tag_shots=True)
            job.status = "completed"
            job.progress = 1
            job.message = "Continuation ready for review"
            db.commit()
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.message = "Continuation analysis failed"
                job.error = str(exc)
                db.commit()
            raise


@general_huey.task(retries=2, retry_delay=5)
def analyze_continuation_general(project_id: str, job_id: str, source_text: str, target_duration_seconds: int) -> None:
    _analyze_continuation(project_id, job_id, source_text, target_duration_seconds)


@chatgpt_huey.task(retries=2, retry_delay=5)
def analyze_continuation_chatgpt(project_id: str, job_id: str, source_text: str, target_duration_seconds: int) -> None:
    _analyze_continuation(project_id, job_id, source_text, target_duration_seconds)


@gemini_huey.task(retries=2, retry_delay=5)
def analyze_continuation_gemini(project_id: str, job_id: str, source_text: str, target_duration_seconds: int) -> None:
    _analyze_continuation(project_id, job_id, source_text, target_duration_seconds)


def enqueue_script_continuation(provider: str, project_id: str, job_id: str, source_text: str, target_duration_seconds: int):
    if provider == "chatgpt_web":
        return analyze_continuation_chatgpt(project_id, job_id, source_text, target_duration_seconds)
    if provider == "gemini_web":
        return analyze_continuation_gemini(project_id, job_id, source_text, target_duration_seconds)
    return analyze_continuation_general(project_id, job_id, source_text, target_duration_seconds)


def _generate_shot_image(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    character_ids: list[str] | None = None,
) -> None:
    with SessionLocal() as db:
        generated_filename = None
        job = db.get(Job, job_id)
        shot = db.get(Shot, shot_id)
        if not job or not shot:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.15
            job.message = "Rendering storyboard frame"
            db.commit()

            project = shot.scene.script_version.project
            target_aspect = aspect_ratio or project.aspect_ratio
            from .continuity import get_or_create_shot_snapshot
            from .takes import record_take

            queued_snapshot = next((
                item.get("continuity_snapshot")
                for item in (job.attempts or [])
                if isinstance(item, dict) and item.get("continuity_snapshot")
            ), None)
            snapshot = queued_snapshot or get_or_create_shot_snapshot(
                db, project, shot, target_aspect,
                character_ids=character_ids,
                prompt_override=prompt_override,
            )
            shot.continuity_snapshot = snapshot
            shot.character_ids = list(snapshot.get("character_ids") or [])
            prompt = snapshot["effective_image_prompt"]
            image_reference = queued_reference(job, shot)
            visual_reference = load_visual_reference(image_reference)
            references = load_cast_references(
                db, project.id, shot.character_ids,
                reference_ids=[
                    reference_id
                    for character in snapshot.get("characters", [])
                    for reference_id in character.get("reference_ids", [])
                ],
            )
            if image_reference and visual_reference is None:
                raise RuntimeError("Selected image reference could not be loaded")
            if visual_reference is not None:
                references.append(visual_reference)
            def report_progress(update: dict) -> None:
                raise_if_cancelled(db, job_id)
                provider = str(update.get("provider") or "")
                phase = str(update.get("phase") or "generating")
                labels = {"chatgpt_web": "ChatGPT Web", "gemini_web": "Gemini Web"}
                messages = {
                    "submitting": f"Submitting to {labels.get(provider, provider)}",
                    "generating": f"Generating with {labels.get(provider, provider)}",
                    "downloading": "Downloading generated image",
                    "saving": "Saving storyboard image",
                    "failed": f"{labels.get(provider, provider)} attempt failed",
                }
                job.provider = provider or job.provider
                job.provider_phase = phase
                job.message = str(update.get("message") or messages.get(phase, "Rendering storyboard frame"))
                if update.get("conversation_url"):
                    job.conversation_url = str(update["conversation_url"])
                    shot.conversation_url = str(update["conversation_url"])
                attempts = deepcopy(job.attempts or [])
                meta = [
                    item
                    for item in attempts
                    if isinstance(item, dict)
                    and (item.get("shot_id") or item.get("character_id") or item.get("character_ids"))
                    and not item.get("provider")
                ]
                provider_attempts = [
                    item for item in attempts if isinstance(item, dict) and item.get("provider")
                ]
                attempt = next((item for item in provider_attempts if item.get("provider") == provider), None)
                values = {
                    key: update[key]
                    for key in (
                        "provider", "phase", "error", "failure_category", "conversation_url",
                        "reference_count", "reference_types",
                    )
                    if update.get(key) is not None
                }
                if attempt:
                    attempt.update(values)
                elif provider:
                    provider_attempts.append(values)
                job.attempts = meta + provider_attempts
                db.commit()

            filename, mime_type, provider = generate_storyboard_image(
                prompt,
                (snapshot.get("scene") or {}).get("title") or shot.scene.title,
                snapshot.get("framing") or shot.framing,
                target_aspect,
                progress=report_progress,
                references=references,
            )
            if image_reference:
                prompt = prepare_visual_reference_prompt(prompt)
            generated_filename = filename
            variant = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == target_aspect), None)
            use_legacy = target_aspect == project.aspect_ratio
            previous_filename = (shot.image.filename if shot.image else None) if use_legacy else (variant.filename if variant else None)
            final_cut_filename = invalidate_final_cut(project, db)
            if use_legacy and shot.image:
                shot.image.filename = filename
                shot.image.mime_type = mime_type
                shot.image.source = "generated"
                shot.image.provider = provider
                shot.image.prompt = prompt
                shot.image.created_at = now()
            elif use_legacy:
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
            elif variant:
                variant.filename = filename
                variant.mime_type = mime_type
                variant.source = "generated"
                variant.provider = provider
                variant.prompt = prompt
                variant.created_at = now()
            else:
                db.add(ShotMediaVariant(shot_id=shot.id, media_type="image", aspect_ratio=target_aspect, filename=filename, mime_type=mime_type, source="generated", provider=provider, prompt=prompt))
            record_take(
                db,
                shot,
                media_type="image",
                aspect_ratio=target_aspect,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider=provider,
                prompt=prompt,
                settings={
                    "continuity_snapshot": snapshot,
                    **({"image_reference": image_reference} if image_reference else {}),
                    **({"conversation_url": job.conversation_url} if job.conversation_url else {}),
                },
            )
            episode_id = shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None
            promote_available_clips(project, db, episode_id=episode_id)
            raise_if_cancelled(db, job_id)
            shot.status = "image_ready"
            job.status = "completed"
            job.provider_phase = "completed"
            job.progress = 1
            job.message = "Storyboard ready"
            db.commit()
            generated_filename = None
            if previous_filename != filename:
                delete_media_file(previous_filename, db=db)
            delete_media_file(final_cut_filename)
        except JobCancelled:
            delete_media_file(generated_filename)
            return
        except Exception as exc:
            db.rollback()
            delete_media_file(generated_filename)
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.provider_phase = "failed"
                job.message = (
                    "Clarify character ages and retry"
                    if "safety system could not verify" in str(exc)
                    else "Storyboard generation failed"
                )
                job.error = str(exc)
                db.commit()
            raise


@image_huey.task()
def generate_shot_image_worker(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    character_ids: list[str] | None = None,
) -> None:
    _generate_shot_image(shot_id, job_id, aspect_ratio, prompt_override, character_ids)


def enqueue_shot_image(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    character_ids: list[str] | None = None,
):
    return generate_shot_image_worker(shot_id, job_id, aspect_ratio, prompt_override, character_ids)


def _generate_cast_reference(character_id: str, job_id: str, look: str | None = None) -> None:
    with SessionLocal() as db:
        generated_filename = None
        job = db.get(Job, job_id)
        character = db.scalar(
            select(Character)
            .where(Character.id == character_id)
            .options(selectinload(Character.references))
        )
        if not job or not character:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.15
            job.message = f"Generating face reference for {character.name}"
            db.commit()

            prompt = cast_portrait_prompt(character, look=look)

            def report_progress(update: dict) -> None:
                raise_if_cancelled(db, job_id)
                provider = str(update.get("provider") or "")
                phase = str(update.get("phase") or "generating")
                labels = {"chatgpt_web": "ChatGPT Web", "gemini_web": "Gemini Web"}
                messages = {
                    "submitting": f"Submitting {character.name} to {labels.get(provider, provider)}",
                    "generating": f"Generating {character.name} face with {labels.get(provider, provider)}",
                    "downloading": "Downloading face reference",
                    "saving": "Saving face reference",
                    "failed": f"{labels.get(provider, provider)} attempt failed",
                }
                job.provider = provider or job.provider
                job.provider_phase = phase
                job.message = str(update.get("message") or messages.get(phase, job.message))
                if update.get("conversation_url"):
                    job.conversation_url = str(update["conversation_url"])
                attempts = deepcopy(job.attempts or [])
                meta = [item for item in attempts if isinstance(item, dict) and item.get("character_id")]
                provider_attempts = [
                    item for item in attempts if isinstance(item, dict) and item.get("provider")
                ]
                attempt = next((item for item in provider_attempts if item.get("provider") == provider), None)
                values = {
                    key: update[key]
                    for key in ("provider", "phase", "error", "failure_category", "conversation_url")
                    if update.get(key)
                }
                if attempt:
                    attempt.update(values)
                elif provider:
                    provider_attempts.append(values)
                job.attempts = meta + provider_attempts
                job.progress = min(0.9, max(job.progress, 0.2))
                db.commit()

            filename, mime_type, provider = generate_storyboard_image(
                prompt,
                f"Cast · {character.name}",
                "Close-up",
                "9:16",
                progress=report_progress,
            )
            generated_filename = filename
            # Normalize to cast-ref-* naming used by uploads.
            extension = Path(filename).suffix or ".png"
            cast_filename = f"cast-ref-{uuid4()}{extension}"
            source_path = settings.media_dir / filename
            target_path = settings.media_dir / cast_filename
            shutil.move(str(source_path), str(target_path))
            generated_filename = cast_filename
            if not is_usable_reference_image(target_path.read_bytes()):
                raise RuntimeError("Generated face reference is blank or contains no visible pixels")

            raise_if_cancelled(db, job_id)
            db.add(
                CharacterReference(
                    character_id=character.id,
                    filename=cast_filename,
                    mime_type=mime_type,
                )
            )
            job.status = "completed"
            job.provider = provider
            job.provider_phase = "completed"
            job.progress = 1
            job.message = f"Face reference ready for {character.name}"
            db.commit()
            generated_filename = None
        except JobCancelled:
            delete_media_file(generated_filename)
            return
        except Exception as exc:
            db.rollback()
            delete_media_file(generated_filename)
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.provider_phase = "failed"
                job.message = (
                    "Clarify character ages and retry"
                    if "safety system could not verify" in str(exc)
                    else "Face reference generation failed"
                )
                job.error = str(exc)
                db.commit()
            raise


@image_huey.task()
def generate_cast_reference_worker(character_id: str, job_id: str, look: str | None = None) -> None:
    _generate_cast_reference(character_id, job_id, look)


def enqueue_cast_reference(character_id: str, job_id: str, look: str | None = None):
    return generate_cast_reference_worker(character_id, job_id, look)


def _generate_shot_clip(shot_id: str, job_id: str, aspect_ratio: str | None = None, prompt_override: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        shot = db.get(Shot, shot_id)
        if not job or not shot:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.1
            provider_name = (settings.video_provider or "runway").strip().lower()
            provider_labels = {
                "runway": "Runway",
                "flowkit": "Google Flow",
                "comfyui": "ComfyUI",
            }
            job.message = f"Sending shot to {provider_labels.get(provider_name, provider_name.title())}"
            db.commit()

            project = shot.scene.script_version.project
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
            source_image = (
                continuity_frame
                or image_ref
                or (shot.image if target_aspect == project.aspect_ratio and shot.image else image_variant or shot.image)
            )
            if not source_image:
                raise RuntimeError("Generate or upload a shot image first")
            base_prompt = (prompt_override or shot.video_prompt).strip()
            if target_aspect == "9:16" and not prompt_override:
                base_prompt += "\n\nVertical 9:16 composition for Shorts. Keep the main subject inside the central safe area."
            from .continuity import current_shot_snapshot, video_continuity_prompt
            from .takes import record_take

            matching_image_takes = [
                take for take in shot.takes
                if take.media_type == "image" and take.aspect_ratio == target_aspect
                and take.filename == source_image.filename
            ]
            image_take = next(
                (take for take in matching_image_takes if take.id == shot.selected_image_take_id),
                max(matching_image_takes, key=lambda take: take.created_at, default=None),
            )
            snapshot = (
                (image_take.settings or {}).get("continuity_snapshot")
                if image_take else None
            )
            if not snapshot and not continuity_frame and getattr(source_image, "source", None) == "generated":
                candidate = shot.continuity_snapshot
                if candidate and candidate.get("aspect_ratio") == target_aspect:
                    snapshot = candidate
            if not snapshot:
                snapshot = current_shot_snapshot(shot, target_aspect)
            effective_prompt = compose_video_prompt(
                video_continuity_prompt(snapshot, base_prompt, target_aspect),
                shot.sound,
            )
            if continuity_frame or image_ref:
                effective_prompt = "\n\n".join(
                    part
                    for part in (
                        effective_prompt,
                        "Continue directly from the selected frame of the previous shot. Preserve its background, set layout, lighting, props, and overall visual composition as the exact opening state.",
                    )
                    if part
                )
            filename, mime_type, provider = render_shot_clip(
                settings.media_dir / source_image.filename,
                effective_prompt,
                shot.duration_ms,
                target_aspect,
                scene_id=str(shot.scene.id),
            )
            variant = next((item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == target_aspect), None)
            use_legacy = target_aspect == project.aspect_ratio
            previous_filename = (shot.clip.filename if shot.clip else None) if use_legacy else (variant.filename if variant else None)
            final_cut_filename = invalidate_final_cut(project, db)
            if use_legacy and shot.clip:
                shot.clip.filename = filename
                shot.clip.mime_type = mime_type
                shot.clip.source = "generated"
                shot.clip.provider = provider
                shot.clip.prompt = effective_prompt
            elif use_legacy:
                db.add(
                    ShotClip(
                        shot_id=shot.id,
                        filename=filename,
                        mime_type=mime_type,
                        source="generated",
                        provider=provider,
                        prompt=effective_prompt,
                    )
                )
            elif variant:
                variant.filename = filename
                variant.mime_type = mime_type
                variant.source = "generated"
                variant.provider = provider
                variant.prompt = effective_prompt
                variant.created_at = now()
            else:
                db.add(ShotMediaVariant(shot_id=shot.id, media_type="clip", aspect_ratio=target_aspect, filename=filename, mime_type=mime_type, source="generated", provider=provider, prompt=effective_prompt))
            record_take(
                db,
                shot,
                media_type="clip",
                aspect_ratio=target_aspect,
                filename=filename,
                mime_type=mime_type,
                source="generated",
                provider=provider,
                prompt=effective_prompt,
                settings={
                    "continuity_frame": {
                        "source_shot_id": continuity_frame.source_shot_id,
                        "source_shot_title": continuity_frame.source_shot_title,
                        "source_time_ms": continuity_frame.source_time_ms,
                    }
                } if continuity_frame else None,
            )
            if project.timeline and not promote_available_clips(project, db):
                project.timeline.revision += 1
            raise_if_cancelled(db, job_id)
            shot.status = "clip_ready"
            job.status = "completed"
            job.progress = 1
            job.message = "Shot clip ready"
            db.commit()
            if previous_filename != filename:
                delete_media_file(previous_filename, db=db)
            delete_media_file(final_cut_filename)
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.message = "Video generation failed"
                job.error = str(exc)
                db.commit()
            raise


def compose_video_prompt(prompt: str, sound: str) -> str:
    prompt = prompt.strip()
    sound = sound.strip()
    if not sound or sound in prompt:
        return prompt
    return f"{prompt}\n\nAudio direction: {sound}"


@video_huey.task()
def generate_shot_clip(shot_id: str, job_id: str, aspect_ratio: str | None = None, prompt_override: str | None = None) -> None:
    _generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override)


@video_huey.task()
def generate_shot_clip_comfy(shot_id: str, job_id: str, aspect_ratio: str | None = None, prompt_override: str | None = None) -> None:
    """Backward-compatible queue alias for jobs enqueued before the FlowKit routing rename."""
    _generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override)


def enqueue_shot_clip(shot_id: str, job_id: str, aspect_ratio: str | None = None, prompt_override: str | None = None):
    return generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override)

def _generate_shot_lipsync(shot_id: str, job_id: str, aspect_ratio: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        shot = db.get(Shot, shot_id)
        if not job or not shot:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.1
            job.message = "Preparing audio and visual references for lip-sync..."
            db.commit()

            from .lipsync import execute_shot_lipsync

            def progress_cb(pct: float, msg: str):
                raise_if_cancelled(db, job_id)
                job.progress = pct
                job.message = msg
                db.commit()

            execute_shot_lipsync(
                shot_id=shot_id,
                db=db,
                aspect_ratio=aspect_ratio,
                progress_callback=progress_cb,
            )

            raise_if_cancelled(db, job_id)
            job.status = "completed"
            job.progress = 1.0
            job.message = "Dialogue lip-sync completed successfully"
            db.commit()
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            job.status = "failed"
            job.error = str(exc)
            job.message = f"Lip-sync failed: {exc}"
            db.commit()


@video_huey.task()
def generate_shot_lipsync_worker(shot_id: str, job_id: str, aspect_ratio: str | None = None) -> None:
    _generate_shot_lipsync(shot_id, job_id, aspect_ratio)


def enqueue_shot_lipsync(shot_id: str, job_id: str, aspect_ratio: str | None = None):
    return generate_shot_lipsync_worker(shot_id, job_id, aspect_ratio)



def _render_project_final_cut(project_id: str, job_id: str, variant: str = "landscape") -> None:
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
            job.message = "Preparing the edit timeline"
            db.commit()

            episode_id = job.episode_id
            timeline = ensure_timeline(project, db, episode_id=episode_id)
            promote_available_clips(project, db, episode_id=episode_id)
            append_missing_shots(project, db, episode_id=episode_id)
            if variant == "shorts":
                if not timeline.shorts_document:
                    timeline.shorts_document = deepcopy(timeline.document)
                    db.commit()
                document = TimelineDocument.model_validate(timeline.shorts_document)
            else:
                document = TimelineDocument.model_validate(timeline.document)

            def update_progress(completed: int, total: int) -> None:
                raise_if_cancelled(db, job_id)
                job.progress = 0.1 + (completed / total) * 0.8
                job.message = f"Rendering shot {completed} of {total}"
                db.commit()

            result = assemble_timeline(project, document, variant, db, update_progress)
            query = select(ProjectVariantExport).where(ProjectVariantExport.project_id == project.id, ProjectVariantExport.variant == variant)
            if episode_id:
                query = query.where(ProjectVariantExport.episode_id == episode_id)
            export = db.scalar(query)
            previous_filename = export.filename if export else None
            if export:
                export.filename = result.filename
                export.duration_ms = result.duration_ms
                export.width = result.width
                export.height = result.height
                export.clip_count = result.clip_count
                export.fallback_count = result.fallback_count
                export.timeline_revision = timeline.revision
                export.episode_id = episode_id
                export.created_at = now()
            else:
                db.add(
                    ProjectVariantExport(
                        project_id=project.id,
                        episode_id=episode_id,
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
            raise_if_cancelled(db, job_id)
            job.status = "completed"
            job.progress = 1
            job.message = "Final cut ready"
            db.commit()
            if previous_filename != result.filename:
                delete_media_file(previous_filename, db=db)
        except JobCancelled:
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if preserve_cancelled(job):
                return
            if job:
                job.status = "failed"
                job.message = "Final cut render failed"
                job.error = str(exc)
                db.commit()
            raise


def _publish_project(project_id: str, job_id: str, publish_record_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        project = db.get(Project, project_id)
        record = db.get(PublishRecord, publish_record_id)
        if not job or not project or not record:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.05
            job.message = "Preparing publish assets"
            record.status = "running"
            db.commit()
            payload = (job.attempts or [{}])[0]
            assets = collect_publish_assets(project, payload, db)
            job.progress = 0.15
            job.message = f"Prepared {len(assets)} video{'s' if len(assets) != 1 else ''}"
            db.commit()

            def report(step: int, total: int, message: str) -> None:
                raise_if_cancelled(db, job_id)
                job.progress = 0.15 + (step / max(1, total)) * 0.75
                job.message = message
                db.commit()

            results = publish_assets(assets, payload, report, db)
            record.results = results
            failed = [item for item in results if item.get("status") == "failed"]
            if failed:
                record.status = "failed" if len(failed) == len(results) else "partial"
                job.status = "failed" if len(failed) == len(results) else "completed"
                job.error = "; ".join(str(item.get("message")) for item in failed[:3])
                job.message = "Publish completed with issues" if record.status == "partial" else "Publish failed"
            else:
                record.status = "completed"
                job.status = "completed"
                job.error = None
                job.message = "Publish complete"
            job.progress = 1
            job.provider_phase = record.status
            db.commit()
        except JobCancelled:
            if record:
                record.status = "cancelled"
                db.commit()
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            record = db.get(PublishRecord, publish_record_id)
            if preserve_cancelled(job):
                if record:
                    record.status = "cancelled"
                    db.commit()
                return
            if job:
                job.status = "failed"
                job.message = "Publish failed"
                job.error = str(exc)
                job.provider_phase = "failed"
            if record:
                record.status = "failed"
                record.results = [{"status": "failed", "message": str(exc)}]
            db.commit()


@video_huey.task()
def publish_project_worker(project_id: str, job_id: str, publish_record_id: str) -> None:
    _publish_project(project_id, job_id, publish_record_id)


def enqueue_publish_project(project_id: str, job_id: str, publish_record_id: str):
    return publish_project_worker(project_id, job_id, publish_record_id)


@video_huey.task()
def render_project_final_cut(project_id: str, job_id: str, variant: str = "landscape") -> None:
    _render_project_final_cut(project_id, job_id, variant)


def enqueue_final_cut(project_id: str, job_id: str, variant: str = "landscape"):
    return render_project_final_cut(project_id, job_id, variant)


# Preserve task names stored by the original single queue and forward them once.
@general_huey.task()
def analyze_script(project_id: str, version_id: str, job_id: str) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project:
            enqueue_script_analysis(
                project.text_provider, project_id, version_id, job_id
            )


@general_huey.task()
def generate_shot_image(shot_id: str, job_id: str) -> None:
    enqueue_shot_image(shot_id, job_id)
