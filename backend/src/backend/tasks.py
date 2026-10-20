from .image_references import default_visual_reference, freeze_visual_reference, load_visual_reference, queued_reference, should_use_default_reference
from .media import prepare_visual_reference_prompt
from copy import deepcopy
from dataclasses import replace
import re

from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from .cast import (
    cast_portrait_prompt,
    element_mention_slug,
    extract_mention_element_ids,
    is_usable_reference_image,
    load_cast_references,
    sync_project_cast,
    shot_element_ids,
)
from .continuity import current_shot_snapshot, get_or_create_shot_snapshot, video_continuity_prompt
from .database import SessionLocal
from .final_cut import _run as _run_ffmpeg, invalidate_final_cut

from .job_limits import JobCapacityExceeded, raise_if_over_capacity
from .jobs import JobCancelled, preserve_cancelled, raise_if_cancelled
from .media import FaceReferenceImage, VisualReferenceImage, delete_media_file, generate_storyboard_image, normalize_conversation_url
from .models import Character, CharacterReference, Compilation, Job, Project, ProjectVariantExport, PublishRecord, Scene, ScriptVersion, Shot, ShotClip, ShotImage, ShotMediaVariant, StemSeparation, Suggestion, TimelineAsset, now
from .providers import get_script_provider, normalize_analysis_result
from .publishing import collect_publish_assets, publish_assets
from .queue import chatgpt_huey, gemini_huey, general_huey, image_huey, video_huey
from .screenplay_parser import canonical_character_name, parse_structured_screenplay
from .settings import settings
from .timeline import append_missing_shots, ensure_compilation_timeline, ensure_timeline, promote_available_clips
from .timeline_render import assemble_timeline
from .schemas import TimelineDocument, TimelineSource
from .video import generate_shot_clip as render_shot_clip, clip_source_image, get_active_video_provider
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
        edit_meta = next(
            (
                item
                for item in (job.attempts or [])
                if isinstance(item, dict) and (item.get("edit_take_id") or item.get("edit_reference"))
            ),
            None,
        )
        edit_attachment_files: list[str] = [
            str(f.get("filename") if isinstance(f, dict) else f)
            for f in ((edit_meta or {}).get("edit_attachment_files") or [])
            if f
        ]

        def cleanup_edit_attachments() -> None:
            for fname in edit_attachment_files:
                if fname.startswith("edit-ref-") and Path(fname).name == fname:
                    (settings.media_dir / fname).unlink(missing_ok=True)

        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.15
            job.message = (
                "Applying follow-up image edit"
                if edit_meta
                else "Rendering storyboard frame"
            )
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
                prompt_override=None if edit_meta else prompt_override,
            )
            shot.continuity_snapshot = snapshot
            shot.character_ids = list(snapshot.get("character_ids") or [])

            if edit_meta:
                prompt = str(
                    edit_meta.get("edit_prompt")
                    or prompt_override
                    or snapshot.get("effective_image_prompt")
                    or ""
                ).strip()
                image_reference = None
                references = []
                edit_conversation_url = (
                    str(edit_meta.get("edit_conversation_url") or "").strip() or None
                )
                is_latest_in_conv = bool(edit_meta.get("edit_is_latest_in_conversation"))
                base_filename = str(edit_meta.get("edit_base_filename") or "").strip()
                base_mime = (
                    str(edit_meta.get("edit_base_mime_type") or "image/png").strip()
                    or "image/png"
                )
                if base_filename and Path(base_filename).name == base_filename:
                    base_path = settings.media_dir / base_filename
                    if base_path.is_file():
                        ref_type = (
                            "edit_target_fallback"
                            if (edit_conversation_url and is_latest_in_conv)
                            else "edit_target"
                        )
                        references.append(
                            VisualReferenceImage(
                                mime_type=base_mime,
                                data=base_path.read_bytes(),
                                character_name=f"{shot.title} (Take to edit)",
                                reference_type=ref_type,
                            )
                        )
                for idx, att_item in enumerate(
                    edit_meta.get("edit_attachment_files") or [], start=1
                ):
                    att_filename = str(
                        att_item.get("filename") if isinstance(att_item, dict) else att_item
                    ).strip()
                    att_mime = (
                        str(
                            att_item.get("mime_type")
                            if isinstance(att_item, dict)
                            else "image/png"
                        ).strip()
                        or "image/png"
                    )
                    if att_filename and Path(att_filename).name == att_filename:
                        att_path = settings.media_dir / att_filename
                        if att_path.is_file():
                            references.append(
                                VisualReferenceImage(
                                    mime_type=att_mime,
                                    data=att_path.read_bytes(),
                                    character_name=f"Attachment {idx}",
                                    reference_type="edit_attachment",
                                )
                            )
            else:
                prompt = snapshot["effective_image_prompt"]
                image_reference = queued_reference(job, shot)
                if not image_reference and should_use_default_reference(job, shot):
                    image_reference = freeze_visual_reference(default_visual_reference(shot, target_aspect))
                visual_reference = load_visual_reference(image_reference)
                if image_reference and visual_reference is None:
                    raise RuntimeError("Selected image reference could not be loaded")
                is_aspect_conversion = bool(
                    image_reference and image_reference.get("mode") == "aspect_conversion"
                )
                char_ids = list(dict.fromkeys(shot.character_ids or []))
                references = (
                    []
                    if (is_aspect_conversion and visual_reference is not None)
                    else load_cast_references(
                        db, project.id, char_ids,
                        reference_ids=[
                            reference_id
                            for character in snapshot.get("characters", [])
                            for reference_id in character.get("reference_ids", [])
                        ],
                        max_per_character=1 if len(char_ids) > 1 else None,
                    )
                )
                fallback_conv_url = shot.conversation_url or next(
                    (
                        (take.settings or {}).get("conversation_url")
                        for take in reversed(shot.takes or [])
                        if take.media_type == "image" and (take.settings or {}).get("conversation_url")
                    ),
                    None,
                )
                if visual_reference is not None:
                    if is_aspect_conversion and fallback_conv_url:
                        visual_reference = replace(visual_reference, reference_type="edit_target_fallback")
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
                    clean_conv_url = normalize_conversation_url(update["conversation_url"])
                    job.conversation_url = clean_conv_url
                    shot.conversation_url = clean_conv_url
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
                    key: (normalize_conversation_url(update[key]) if key == "conversation_url" else update[key])
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

            attempt_meta = next(
                (
                    item
                    for item in (job.attempts or [])
                    if isinstance(item, dict) and item.get("shot_id") == shot.id
                ),
                {},
            )
            chatgpt_dual = bool(attempt_meta.get("chatgpt_dual")) and not edit_meta

            if chatgpt_dual:
                from .media import generate_storyboard_chatgpt_dual
                invalidate_final_cut(project, db)
                (fn_16_9, mime_16_9), (fn_9_16, mime_9_16), provider = generate_storyboard_chatgpt_dual(
                    prompt,
                    (snapshot.get("scene") or {}).get("title") or shot.scene.title,
                    snapshot.get("framing") or shot.framing,
                    progress=report_progress,
                    references=references,
                    conversation_url=shot.conversation_url or fallback_conv_url,
                )

                # 16:9 variant & take
                variant_16 = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == "16:9"), None)
                if project.aspect_ratio == "16:9":
                    if shot.image:
                        shot.image.filename = fn_16_9
                        shot.image.mime_type = mime_16_9
                        shot.image.source = "generated"
                        shot.image.provider = provider
                        shot.image.prompt = prompt
                        shot.image.created_at = now()
                    else:
                        shot.image = ShotImage(
                            shot_id=shot.id,
                            filename=fn_16_9,
                            mime_type=mime_16_9,
                            source="generated",
                            provider=provider,
                            prompt=prompt,
                        )
                if variant_16:
                    variant_16.filename = fn_16_9
                    variant_16.mime_type = mime_16_9
                    variant_16.source = "generated"
                    variant_16.provider = provider
                    variant_16.prompt = prompt
                    variant_16.created_at = now()
                else:
                    shot.media_variants.append(
                        ShotMediaVariant(
                            shot_id=shot.id,
                            media_type="image",
                            aspect_ratio="16:9",
                            filename=fn_16_9,
                            mime_type=mime_16_9,
                            source="generated",
                            provider=provider,
                            prompt=prompt,
                        )
                    )
                record_take(
                    db,
                    shot,
                    media_type="image",
                    aspect_ratio="16:9",
                    filename=fn_16_9,
                    mime_type=mime_16_9,
                    source="generated",
                    provider=provider,
                    prompt=prompt,
                    settings={
                        "continuity_snapshot": snapshot,
                        **({"conversation_url": job.conversation_url} if job.conversation_url else {}),
                    },
                )

                # 9:16 variant & take
                variant_9 = next((item for item in shot.media_variants if item.media_type == "image" and item.aspect_ratio == "9:16"), None)
                if project.aspect_ratio == "9:16":
                    if shot.image:
                        shot.image.filename = fn_9_16
                        shot.image.mime_type = mime_9_16
                        shot.image.source = "generated"
                        shot.image.provider = provider
                        shot.image.prompt = prompt
                        shot.image.created_at = now()
                    else:
                        shot.image = ShotImage(
                            shot_id=shot.id,
                            filename=fn_9_16,
                            mime_type=mime_9_16,
                            source="generated",
                            provider=provider,
                            prompt=prompt,
                        )
                if variant_9:
                    variant_9.filename = fn_9_16
                    variant_9.mime_type = mime_9_16
                    variant_9.source = "generated"
                    variant_9.provider = provider
                    variant_9.prompt = prompt
                    variant_9.created_at = now()
                else:
                    shot.media_variants.append(
                        ShotMediaVariant(
                            shot_id=shot.id,
                            media_type="image",
                            aspect_ratio="9:16",
                            filename=fn_9_16,
                            mime_type=mime_9_16,
                            source="generated",
                            provider=provider,
                            prompt=prompt,
                        )
                    )
                record_take(
                    db,
                    shot,
                    media_type="image",
                    aspect_ratio="9:16",
                    filename=fn_9_16,
                    mime_type=mime_9_16,
                    source="generated",
                    provider=provider,
                    prompt=prompt,
                    settings={
                        "continuity_snapshot": snapshot,
                        **({"conversation_url": job.conversation_url} if job.conversation_url else {}),
                    },
                )

                episode_id = shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None
                promote_available_clips(project, db, episode_id=episode_id)
                raise_if_cancelled(db, job_id)
                shot.status = "image_ready"
                job.status = "completed"
                job.progress = 1.0
                job.message = "Storyboard frames ready (16:9 + 9:16)"
                db.commit()
                return

            if edit_meta:
                from .media import expand_color_retouch_prompt
                effective_edit_prompt = expand_color_retouch_prompt(prompt)
                filename, mime_type, provider = generate_storyboard_image(
                    effective_edit_prompt,
                    (snapshot.get("scene") or {}).get("title") or shot.scene.title,
                    snapshot.get("framing") or shot.framing,
                    target_aspect,
                    progress=report_progress,
                    references=references,
                    conversation_url=edit_conversation_url,
                    is_followup_edit=True,
                )
            else:
                filename, mime_type, provider = generate_storyboard_image(
                    prompt,
                    (snapshot.get("scene") or {}).get("title") or shot.scene.title,
                    snapshot.get("framing") or shot.framing,
                    target_aspect,
                    progress=report_progress,
                    references=references,
                    conversation_url=fallback_conv_url if is_aspect_conversion else None,
                )
            if image_reference:
                prompt = prepare_visual_reference_prompt(prompt, references=references)
            generated_filename = filename
            attempt_meta = next(
                (
                    item
                    for item in (job.attempts or [])
                    if isinstance(item, dict) and item.get("shot_id") == shot.id
                ),
                {},
            )
            both_aspects = bool(attempt_meta.get("both_aspects")) and not edit_meta
            generate_video = bool(attempt_meta.get("generate_video"))
            include_cast_refs = bool(attempt_meta.get("include_cast_references"))

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
                shot.image = ShotImage(
                    shot_id=shot.id,
                    filename=filename,
                    mime_type=mime_type,
                    source="generated",
                    provider=provider,
                    prompt=prompt,
                )
            if variant:
                variant.filename = filename
                variant.mime_type = mime_type
                variant.source = "generated"
                variant.provider = provider
                variant.prompt = prompt
                variant.created_at = now()
            elif not use_legacy:
                shot.media_variants.append(
                    ShotMediaVariant(
                        shot_id=shot.id,
                        media_type="image",
                        aspect_ratio=target_aspect,
                        filename=filename,
                        mime_type=mime_type,
                        source="generated",
                        provider=provider,
                        prompt=prompt,
                    )
                )
            take_settings: dict = {
                "continuity_snapshot": snapshot,
                **({"image_reference": image_reference} if image_reference else {}),
                **({"conversation_url": job.conversation_url} if job.conversation_url else {}),
            }
            if edit_meta:
                if edit_meta.get("edit_take_id"):
                    take_settings["parent_take_id"] = edit_meta.get("edit_take_id")
                take_settings["edit_prompt"] = str(edit_meta.get("edit_prompt") or "")
                if edit_attachment_files:
                    take_settings["edit_attachment_count"] = len(edit_attachment_files)
                if edit_meta.get("edit_reference"):
                    ext = Path(filename).suffix or ".png"
                    src_file = settings.media_dir / filename
                    if src_file.is_file():
                        ref_obj = next(
                            (item for item in shot.image_references if item.aspect_ratio == target_aspect),
                            None,
                        )
                        cont_obj = next(
                            (item for item in shot.continuity_frames if item.aspect_ratio == target_aspect),
                            None,
                        )
                        if ref_obj:
                            new_ref_fn = f"image-reference-repainted-{uuid4()}{ext}"
                            shutil.copy2(str(src_file), str(settings.media_dir / new_ref_fn))
                            ref_obj.filename = new_ref_fn
                            ref_obj.mime_type = mime_type
                            ref_obj.created_at = now()
                        elif cont_obj:
                            new_cont_fn = f"continuity-repainted-{uuid4()}{ext}"
                            shutil.copy2(str(src_file), str(settings.media_dir / new_cont_fn))
                            cont_obj.filename = new_cont_fn
                            cont_obj.mime_type = mime_type
                            cont_obj.created_at = now()
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
                settings=take_settings,
            )
            episode_id = shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None
            promote_available_clips(project, db, episode_id=episode_id)
            raise_if_cancelled(db, job_id)
            shot.status = "image_ready"

            if both_aspects:
                other_aspect = "9:16" if target_aspect == "16:9" else "16:9"
                job.progress = 0.5
                job.message = f"Generating {other_aspect} storyboard (2/2)"
                db.commit()
                generated_filename = None
                if previous_filename != filename:
                    delete_media_file(previous_filename, db=db)
                previous_filename = None
                if generate_video:
                    _enqueue_chained_shot_clip(
                        db, project, shot, target_aspect, include_cast_references=include_cast_refs
                    )

                other_snapshot = get_or_create_shot_snapshot(
                    db, project, shot, other_aspect, character_ids=shot.character_ids
                )
                other_prompt = other_snapshot["effective_image_prompt"]
                other_ref = default_visual_reference(shot, other_aspect)
                if other_ref:
                    other_ref = freeze_visual_reference(other_ref)
                other_visual = load_visual_reference(other_ref)
                other_conv_url = job.conversation_url or shot.conversation_url or fallback_conv_url
                if other_visual is not None and other_conv_url:
                    other_visual = replace(other_visual, reference_type="edit_target_fallback")
                other_refs = [other_visual] if other_visual is not None else []
                if not other_refs and not other_conv_url:
                    other_refs = load_cast_references(
                        db, project.id, shot.character_ids,
                        reference_ids=[
                            reference_id
                            for character in other_snapshot.get("characters", [])
                            for reference_id in character.get("reference_ids", [])
                        ],
                    )
                other_filename, other_mime_type, other_provider = generate_storyboard_image(
                    other_prompt,
                    (other_snapshot.get("scene") or {}).get("title") or shot.scene.title,
                    other_snapshot.get("framing") or shot.framing,
                    other_aspect,
                    progress=report_progress,
                    references=other_refs,
                    conversation_url=other_conv_url,
                )
                generated_filename = other_filename
                if other_ref:
                    other_prompt = prepare_visual_reference_prompt(other_prompt, references=other_refs)
                other_variant = next(
                    (
                        item
                        for item in shot.media_variants
                        if item.media_type == "image" and item.aspect_ratio == other_aspect
                    ),
                    None,
                )
                other_use_legacy = other_aspect == project.aspect_ratio
                other_previous = (
                    (shot.image.filename if shot.image else None)
                    if other_use_legacy
                    else (other_variant.filename if other_variant else None)
                )
                if other_use_legacy and shot.image:
                    shot.image.filename = other_filename
                    shot.image.mime_type = other_mime_type
                    shot.image.source = "generated"
                    shot.image.provider = other_provider
                    shot.image.prompt = other_prompt
                    shot.image.created_at = now()
                elif other_use_legacy:
                    shot.image = ShotImage(
                        shot_id=shot.id,
                        filename=other_filename,
                        mime_type=other_mime_type,
                        source="generated",
                        provider=other_provider,
                        prompt=other_prompt,
                    )
                if other_variant:
                    other_variant.filename = other_filename
                    other_variant.mime_type = other_mime_type
                    other_variant.source = "generated"
                    other_variant.provider = other_provider
                    other_variant.prompt = other_prompt
                    other_variant.created_at = now()
                elif not other_use_legacy:
                    shot.media_variants.append(
                        ShotMediaVariant(
                            shot_id=shot.id,
                            media_type="image",
                            aspect_ratio=other_aspect,
                            filename=other_filename,
                            mime_type=other_mime_type,
                            source="generated",
                            provider=other_provider,
                            prompt=other_prompt,
                        )
                    )
                record_take(
                    db,
                    shot,
                    media_type="image",
                    aspect_ratio=other_aspect,
                    filename=other_filename,
                    mime_type=other_mime_type,
                    source="generated",
                    provider=other_provider,
                    prompt=other_prompt,
                    settings={
                        "continuity_snapshot": other_snapshot,
                        **({"image_reference": other_ref} if other_ref else {}),
                        **({"conversation_url": job.conversation_url} if job.conversation_url else {}),
                    },
                )
                raise_if_cancelled(db, job_id)
                if other_previous and other_previous != other_filename:
                    delete_media_file(other_previous, db=db)

            job.status = "completed"
            job.provider_phase = "completed"
            job.progress = 1
            job.message = "16:9 + 9:16 storyboards ready" if both_aspects else "Storyboard ready"
            db.commit()
            generated_filename = None
            cleanup_edit_attachments()
            if previous_filename and previous_filename != filename:
                delete_media_file(previous_filename, db=db)
            delete_media_file(final_cut_filename)
            if generate_video:
                if both_aspects:
                    other_aspect = "9:16" if target_aspect == "16:9" else "16:9"
                    _enqueue_chained_shot_clip(
                        db, project, shot, other_aspect, include_cast_references=include_cast_refs
                    )
                else:
                    _enqueue_chained_shot_clip(
                        db, project, shot, target_aspect, include_cast_references=include_cast_refs
                    )
        except JobCancelled:
            cleanup_edit_attachments()
            delete_media_file(generated_filename)
            return
        except Exception as exc:
            db.rollback()
            cleanup_edit_attachments()
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
            char_kind = (getattr(character, "kind", None) or "character").strip().lower()
            look_key = (look or "").strip().lower()
            if look_key in {"3d_view", "turnaround", "3d"}:
                target_view_type = "3d_view"
                target_aspect = "16:9"
                target_framing = "16:9 Character 3D Turnaround Sheet"
                label_noun = "3D turnaround view"
            elif char_kind == "scene" or look_key == "scene":
                target_view_type = "scene"
                target_aspect = "16:9"
                target_framing = "Wide establishing set view"
                label_noun = "scene reference"
            elif char_kind == "prop" or look_key == "prop":
                target_view_type = "prop"
                target_aspect = "16:9"
                target_framing = "Isolated studio prop view"
                label_noun = "prop reference"
            else:
                target_view_type = "main"
                target_aspect = "9:16"
                target_framing = "Close-up"
                label_noun = "face reference"

            job.status = "running"
            job.progress = 0.15
            job.message = f"Generating {label_noun} for {character.name}"
            db.commit()

            seed_references: list[FaceReferenceImage] = []
            if character.references:
                if target_view_type in {"prop", "scene"}:
                    # Prefer same-kind refs when regenerating prop/scene sheets.
                    seed_candidates = [
                        r
                        for r in character.references
                        if getattr(r, "view_type", "main") == target_view_type
                    ] or list(character.references)
                elif target_view_type in {"main", "3d_view"}:
                    seed_candidates = [
                        r for r in character.references if getattr(r, "view_type", "main") != "3d_view"
                    ] or list(character.references)
                else:
                    seed_candidates = []
                for ref in reversed(seed_candidates):
                    ref_path = settings.media_dir / ref.filename
                    try:
                        ref_bytes = ref_path.read_bytes()
                    except OSError:
                        continue
                    if is_usable_reference_image(ref_bytes):
                        seed_references.append(
                            FaceReferenceImage(
                                character_name=character.name,
                                mime_type=ref.mime_type,
                                data=ref_bytes,
                                reference_id=ref.id,
                                character_id=character.id,
                                view_type=getattr(ref, "view_type", "main") or "main",
                            )
                        )
                        break

            prompt = cast_portrait_prompt(
                character,
                look=look,
                has_attached_reference=bool(seed_references),
            )

            def report_progress(update: dict) -> None:
                raise_if_cancelled(db, job_id)
                provider = str(update.get("provider") or "")
                phase = str(update.get("phase") or "generating")
                labels = {"chatgpt_web": "ChatGPT Web", "gemini_web": "Gemini Web"}
                messages = {
                    "submitting": f"Submitting {character.name} to {labels.get(provider, provider)}",
                    "generating": f"Generating {character.name} {label_noun} with {labels.get(provider, provider)}",
                    "downloading": f"Downloading {label_noun}",
                    "saving": f"Saving {label_noun}",
                    "failed": f"{labels.get(provider, provider)} attempt failed",
                }
                job.provider = provider or job.provider
                job.provider_phase = phase
                job.message = str(update.get("message") or messages.get(phase, job.message))
                if update.get("conversation_url"):
                    job.conversation_url = normalize_conversation_url(update["conversation_url"])
                attempts = deepcopy(job.attempts or [])
                meta = [item for item in attempts if isinstance(item, dict) and item.get("character_id")]
                provider_attempts = [
                    item for item in attempts if isinstance(item, dict) and item.get("provider")
                ]
                attempt = next((item for item in provider_attempts if item.get("provider") == provider), None)
                values = {
                    key: (normalize_conversation_url(update[key]) if key == "conversation_url" else update[key])
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

            import inspect
            extra_gen_kwargs: dict[str, object] = {"progress": report_progress}
            if seed_references:
                try:
                    if "references" in inspect.signature(generate_storyboard_image).parameters:
                        extra_gen_kwargs["references"] = seed_references
                except (TypeError, ValueError):
                    pass

            filename, mime_type, provider = generate_storyboard_image(
                prompt,
                f"Cast · {character.name}",
                target_framing,
                target_aspect,
                **extra_gen_kwargs,
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
                    view_type=target_view_type,
                    filename=cast_filename,
                    mime_type=mime_type,
                )
            )
            job.status = "completed"
            job.provider = provider
            job.provider_phase = "completed"
            job.progress = 1
            job.message = f"{label_noun.capitalize()} ready for {character.name}"
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


def _resolve_video_cast_references(
    db,
    project: Project,
    shot: Shot,
    continuity_frame,
    image_ref,
) -> tuple[list[Path], list[str]]:
    all_chars = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .options(selectinload(Character.references))
        ).all()
    )
    mention_ids = extract_mention_element_ids(
        all_chars, shot.video_prompt, shot.image_prompt, shot.action
    )
    tagged_ids = list(
        dict.fromkeys(
            [str(cid) for cid in (shot.character_ids or []) if cid] + mention_ids
        )
    )
    if not tagged_ids:
        return [], []

    source_shot_id = (
        getattr(continuity_frame, "source_shot_id", None)
        or getattr(image_ref, "source_shot_id", None)
    )
    candidate_ids = tagged_ids
    if source_shot_id:
        source_shot = db.get(Shot, str(source_shot_id))
        if source_shot:
            source_char_ids = {str(cid) for cid in (source_shot.character_ids or []) if cid}
            missing_ids = [cid for cid in tagged_ids if cid not in source_char_ids]
            if missing_ids:
                candidate_ids = missing_ids

    by_id = {char.id: char for char in all_chars}
    ref_paths: list[Path] = []
    char_names: list[str] = []
    for cid in candidate_ids:
        char = by_id.get(cid)
        if not char or not char.references:
            continue
        ordered_refs = sorted(
            char.references,
            key=lambda r: 0 if getattr(r, "view_type", "main") == "3d_view" else 1,
        )
        for ref in ordered_refs:
            path = settings.media_dir / ref.filename
            if path.is_file():
                ref_paths.append(path)
                char_names.append(char.name)
                break
        if len(ref_paths) >= 2:
            break
    return ref_paths, char_names


def _resolve_r2v_element_references(
    db,
    project: Project,
    shot: Shot,
    continuity_frame,
    image_ref,
    *,
    element_ids: list[str] | None = None,
    include_scene_reference: bool = True,
) -> tuple[list[Path], list[dict[str, str]]]:
    """Collect up to 7 multi-modal Reference-to-Video (R2V) images: Characters (3D view preferred), Scenes, Props, and Continuity."""
    all_chars = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .options(selectinload(Character.references))
        ).all()
    )
    by_id = {char.id: char for char in all_chars}
    mention_ids = extract_mention_element_ids(
        all_chars, shot.video_prompt, shot.image_prompt, shot.action
    )
    base_ids = (
        [str(cid) for cid in element_ids if cid]
        if element_ids is not None
        else [str(cid) for cid in (shot.character_ids or []) if cid]
    )
    selected_ids = list(dict.fromkeys([*base_ids, *mention_ids]))

    if include_scene_reference and shot.scene:
        selected_ids = shot_element_ids(all_chars, shot, selected_ids)

    ref_paths: list[Path] = []
    ref_descriptors: list[dict[str, str]] = []
    seen_paths: set[Path] = set()

    for cid in selected_ids:
        char = by_id.get(cid)
        if not char or not char.references:
            continue
        ordered_refs = sorted(
            char.references,
            key=lambda r: 0 if getattr(r, "view_type", "main") == "3d_view" else 1,
        )
        for ref in ordered_refs:
            path = settings.media_dir / ref.filename
            if path.is_file() and path not in seen_paths:
                seen_paths.add(path)
                ref_paths.append(path)
                slug = element_mention_slug(char.name)
                ref_descriptors.append(
                    {
                        "id": char.id,
                        "name": char.name,
                        "tag": f"@{slug}" if slug else f"@{canonical_character_name(char.name) or 'ELEMENT'}",
                        "kind": (getattr(char, "kind", "character") or "character"),
                        "view_type": (getattr(ref, "view_type", "main") or "main"),
                    }
                )
                break
        if len(ref_paths) >= 7:
            break

    if include_scene_reference and len(ref_paths) < 7:
        for frame_ref in (continuity_frame, image_ref):
            if frame_ref is None:
                continue
            f_path = settings.media_dir / frame_ref.filename
            if f_path.is_file() and f_path not in seen_paths:
                seen_paths.add(f_path)
                ref_paths.append(f_path)
                ref_descriptors.append(
                    {
                        "id": getattr(frame_ref, "id", "continuity"),
                        "name": getattr(frame_ref, "source_shot_title", None) or "Previous Shot Continuity",
                        "tag": "@SCENE_CONTINUITY",
                        "kind": "scene",
                        "view_type": "continuity",
                    }
                )
                break

    return ref_paths[:7], ref_descriptors[:7]


def _enqueue_chained_shot_clip(
    db,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    include_cast_references: bool = False,
) -> Job | None:
    if not (shot.video_prompt or "").strip():
        return None
    if not clip_source_image(shot, aspect_ratio):
        return None
    active_jobs = db.scalars(
        select(Job).where(
            Job.project_id == project.id,
            Job.kind == "shot_clip",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for existing in active_jobs:
        if any(
            isinstance(attempt, dict)
            and attempt.get("shot_id") == shot.id
            and attempt.get("aspect_ratio", project.aspect_ratio) == aspect_ratio
            for attempt in (existing.attempts or [])
        ):
            return existing
    attempt_meta: dict[str, object] = {"shot_id": shot.id, "aspect_ratio": aspect_ratio}
    if include_cast_references:
        attempt_meta["include_cast_references"] = True
    try:
        raise_if_over_capacity(db, project.id, "shot_clip")
    except JobCapacityExceeded:
        return None
    clip_job = Job(
        project_id=project.id,
        episode_id=(
            shot.scene.script_version.episode_id
            if shot.scene and shot.scene.script_version
            else None
        ),
        kind="shot_clip",
        message=f"Queued {aspect_ratio} shot clip",
        attempts=[attempt_meta],
    )
    db.add(clip_job)
    shot.status = "generating_clip"
    db.commit()
    task = (
        enqueue_shot_clip(shot.id, clip_job.id, aspect_ratio, include_cast_references=True)
        if include_cast_references
        else enqueue_shot_clip(shot.id, clip_job.id, aspect_ratio)
    )
    if task is not None and hasattr(task, "id"):
        clip_job.task_id = task.id
        db.commit()
    return clip_job


def _generate_shot_clip(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    include_cast_references: bool = False,
    _is_second_aspect: bool = False,
) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        shot = db.get(Shot, shot_id)
        if not job or not shot:
            return
        if job.status == "cancelled":
            return
        filename = None
        try:
            raise_if_cancelled(db, job_id)
            attempt_dicts = [item for item in (job.attempts or []) if isinstance(item, dict)]
            if not include_cast_references and attempt_dicts:
                include_cast_references = any(
                    bool(item.get("include_cast_references")) for item in attempt_dicts
                )
            video_mode = next(
                (
                    str(item.get("video_mode")).strip().lower()
                    for item in attempt_dicts
                    if item.get("video_mode") in {"i2v", "r2v"}
                ),
                "i2v",
            )
            include_scene_reference = next(
                (
                    bool(item.get("include_scene_reference"))
                    for item in attempt_dicts
                    if "include_scene_reference" in item
                ),
                True,
            )
            explicit_element_ids = next(
                (
                    [str(eid) for eid in item.get("element_ids") if eid]
                    for item in attempt_dicts
                    if isinstance(item.get("element_ids"), list)
                ),
                None,
            )
            both_aspects = (
                any(bool(item.get("both_aspects")) for item in attempt_dicts)
                and not _is_second_aspect
            )
            job.status = "running"
            if not _is_second_aspect:
                job.progress = 0.1
            provider_name = get_active_video_provider()
            provider_labels = {
                "runway": "Runway",
                "flowkit": "Google Flow",
                "gemini_web": "Gemini Web (Veo)",
                "comfyui": "ComfyUI",
            }
            project = shot.scene.script_version.project
            target_aspect = aspect_ratio or project.aspect_ratio
            other_aspect = "9:16" if target_aspect == "16:9" else "16:9"
            mode_badge = " (R2V)" if video_mode == "r2v" else ""
            if _is_second_aspect:
                job.message = f"Sending {target_aspect} shot{mode_badge} to {provider_labels.get(provider_name, provider_name.title())} (2/2)"
            elif both_aspects:
                job.message = f"Sending {target_aspect} shot{mode_badge} to {provider_labels.get(provider_name, provider_name.title())} (1/2)"
            else:
                job.message = f"Sending shot{mode_badge} to {provider_labels.get(provider_name, provider_name.title())}"
            db.commit()
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
            cross_aspect_continuity = (
                next(
                    (
                        frame
                        for frame in shot.continuity_frames
                        if frame.aspect_ratio == other_aspect
                    ),
                    None,
                )
                or next(
                    (
                        ref
                        for ref in shot.image_references
                        if ref.aspect_ratio == other_aspect
                    ),
                    None,
                )
            )
            source_image = clip_source_image(shot, target_aspect)
            r2v_ref_paths: list[Path] = []
            r2v_descriptors: list[dict[str, str]] = []
            if video_mode == "r2v":
                r2v_ref_paths, r2v_descriptors = _resolve_r2v_element_references(
                    db,
                    project,
                    shot,
                    continuity_frame or cross_aspect_continuity,
                    image_ref,
                    element_ids=explicit_element_ids,
                    include_scene_reference=include_scene_reference,
                )
                if not r2v_ref_paths and source_image:
                    src_p = settings.media_dir / source_image.filename
                    if src_p.is_file():
                        r2v_ref_paths = [src_p]
                        r2v_descriptors = [
                            {
                                "id": shot.id,
                                "name": shot.title,
                                "tag": "@SHOT_FRAME",
                                "kind": "scene",
                                "view_type": "frame",
                            }
                        ]
                if not r2v_ref_paths:
                    raise RuntimeError(
                        "Attach at least one Character, Scene, or Prop reference image (or @mention an element with a reference) to generate in Reference-to-Video (R2V) mode"
                    )
            elif not source_image:
                raise RuntimeError(f"Generate or upload a {target_aspect} shot image first")

            base_prompt = (prompt_override or shot.video_prompt).strip()
            if target_aspect == "9:16" and not prompt_override:
                base_prompt += "\n\nVertical 9:16 composition for Shorts. Keep the main subject inside the central safe area."
            from .continuity import current_shot_snapshot, video_continuity_prompt
            from .takes import record_take

            matching_image_takes = (
                [
                    take
                    for take in shot.takes
                    if take.media_type == "image"
                    and take.aspect_ratio == target_aspect
                    and take.filename == source_image.filename
                ]
                if source_image
                else []
            )
            image_take = next(
                (take for take in matching_image_takes if take.id == shot.selected_image_take_id),
                max(matching_image_takes, key=lambda take: take.created_at, default=None),
            )
            snapshot = (
                (image_take.settings or {}).get("continuity_snapshot")
                if image_take else None
            )
            if not snapshot and not continuity_frame and source_image and getattr(source_image, "source", None) == "generated":
                candidate = shot.continuity_snapshot
                if candidate and candidate.get("aspect_ratio") == target_aspect:
                    snapshot = candidate
            if not snapshot:
                snapshot = current_shot_snapshot(shot, target_aspect)
            effective_prompt = compose_video_prompt(
                video_continuity_prompt(snapshot, base_prompt, target_aspect, shot=shot),
                shot.sound,
            )
            if video_mode == "r2v":
                binding_lines: list[str] = ["Reference-to-Video (R2V) visual elements attached in order:"]
                for idx, desc in enumerate(r2v_descriptors, start=1):
                    kind_label = (
                        "Character 3D turnaround sheet (portrait + front/side/back full-body)"
                        if desc.get("view_type") == "3d_view"
                        else (
                            "Scene environment reference"
                            if desc.get("kind") == "scene"
                            else (
                                "Prop object reference"
                                if desc.get("kind") == "prop"
                                else "Character identity reference"
                            )
                        )
                    )
                    binding_lines.append(
                        f"- Reference {idx} ({desc['tag']} — {desc['name']}, {kind_label}): preserve exact visual identity, proportions, materials, and wardrobe."
                    )
                binding_lines.append(
                    "Maintain character orientation and eyeline consistency across shots. Do not flip the left-right axis between shots. Prioritize natural body movement over rigid spatial keywords."
                )
                effective_prompt = "\n\n".join(
                    part for part in (effective_prompt, "\n".join(binding_lines)) if part
                )
            else:
                if continuity_frame or image_ref or cross_aspect_continuity:
                    effective_prompt = "\n\n".join(
                        part
                        for part in (
                            effective_prompt,
                            "Continue directly from the selected frame of the previous shot. Preserve its background, set layout, lighting, props, and overall visual composition as the exact opening state.",
                        )
                        if part
                    )
                cast_ref_paths: list[Path] = []
                cast_ref_names: list[str] = []
                if include_cast_references:
                    cast_ref_paths, cast_ref_names = _resolve_video_cast_references(
                        db, project, shot, continuity_frame or cross_aspect_continuity, image_ref
                    )
                    if cast_ref_names:
                        effective_prompt = "\n\n".join(
                            part
                            for part in (
                                effective_prompt,
                                f"Reference images: Use the first image as the opening scene frame, and use the additional character reference portrait(s) for the exact facial identity, hair, and wardrobe of: {', '.join(cast_ref_names)}.",
                            )
                            if part
                        )
            take_settings: dict[str, object] = {}
            if continuity_frame:
                take_settings["continuity_frame"] = {
                    "source_shot_id": continuity_frame.source_shot_id,
                    "source_shot_title": continuity_frame.source_shot_title,
                    "source_time_ms": continuity_frame.source_time_ms,
                }
            if video_mode == "r2v":
                take_settings["video_mode"] = "r2v"
                take_settings["r2v_elements"] = [desc["name"] for desc in r2v_descriptors]
            elif include_cast_references:
                take_settings["include_cast_references"] = True
                if cast_ref_names:
                    take_settings["cast_reference_characters"] = cast_ref_names
            import inspect

            resolved_flow_projects: list[str] = []
            resolved_conversation_urls: list[str] = []
            render_kwargs: dict[str, object] = {"scene_id": str(shot.scene.id)}
            active_ref_paths = r2v_ref_paths if video_mode == "r2v" else cast_ref_paths
            try:
                sig = inspect.signature(render_shot_clip)
                if "flow_project_id" in sig.parameters:
                    render_kwargs["flow_project_id"] = project.flow_project_id
                if "on_flow_project_id" in sig.parameters:
                    render_kwargs["on_flow_project_id"] = resolved_flow_projects.append
                if "on_conversation_url" in sig.parameters:
                    render_kwargs["on_conversation_url"] = resolved_conversation_urls.append
                if active_ref_paths and "reference_image_paths" in sig.parameters:
                    render_kwargs["reference_image_paths"] = active_ref_paths
                if video_mode == "r2v" and "video_mode" in sig.parameters:
                    render_kwargs["video_mode"] = "r2v"
            except (TypeError, ValueError):
                pass
            opening_image_path = (
                r2v_ref_paths[0]
                if video_mode == "r2v"
                else (settings.media_dir / source_image.filename)
            )
            filename, mime_type, provider = render_shot_clip(
                opening_image_path,
                effective_prompt,
                shot.duration_ms,
                target_aspect,
                **render_kwargs,
            )
            if resolved_conversation_urls and resolved_conversation_urls[-1]:
                clean_url = normalize_conversation_url(resolved_conversation_urls[-1])
                take_settings["conversation_url"] = clean_url
                for attempt in (job.attempts or []):
                    if isinstance(attempt, dict):
                        attempt["conversation_url"] = clean_url
            # Release the render-time read snapshot, then serialize result writes.
            # ponytail: serialize SQLite writes; use server-database row locks for higher throughput.
            project_id = project.id
            db.rollback()
            project_updates: dict[str, object] = {"updated_at": Project.updated_at}
            if resolved_flow_projects and resolved_flow_projects[-1]:
                project_updates["flow_project_id"] = resolved_flow_projects[-1]
            db.execute(update(Project).where(Project.id == project_id).values(**project_updates))
            db.expire_all()
            raise_if_cancelled(db, job_id)
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
                shot.clip.created_at = now()
            elif use_legacy:
                shot.clip = ShotClip(
                    shot_id=shot.id,
                    filename=filename,
                    mime_type=mime_type,
                    source="generated",
                    provider=provider,
                    prompt=effective_prompt,
                )
            if variant:
                variant.filename = filename
                variant.mime_type = mime_type
                variant.source = "generated"
                variant.provider = provider
                variant.prompt = effective_prompt
                variant.created_at = now()
            elif not use_legacy:
                shot.media_variants.append(ShotMediaVariant(shot_id=shot.id, media_type="clip", aspect_ratio=target_aspect, filename=filename, mime_type=mime_type, source="generated", provider=provider, prompt=effective_prompt))

            other_aspect = "9:16" if target_aspect == "16:9" else "16:9"
            other_variant = next(
                (item for item in shot.media_variants if item.media_type == "clip" and item.aspect_ratio == other_aspect),
                None,
            )
            other_use_legacy = other_aspect == project.aspect_ratio
            other_has_legacy = bool(other_use_legacy and shot.clip and shot.clip.filename and getattr(shot.clip, "source", "") != "adapted")
            other_has_native = bool(other_variant and other_variant.source != "adapted") or other_has_legacy

            if not other_has_native:
                if other_variant:
                    other_variant.filename = filename
                    other_variant.mime_type = mime_type
                    other_variant.source = "adapted"
                    other_variant.provider = provider
                    other_variant.prompt = effective_prompt
                    other_variant.created_at = now()
                elif other_use_legacy and not shot.clip:
                    shot.clip = ShotClip(
                        shot_id=shot.id,
                        filename=filename,
                        mime_type=mime_type,
                        source="adapted",
                        provider=provider,
                        prompt=effective_prompt,
                    )
                else:
                    shot.media_variants.append(
                        ShotMediaVariant(
                            shot_id=shot.id,
                            media_type="clip",
                            aspect_ratio=other_aspect,
                            filename=filename,
                            mime_type=mime_type,
                            source="adapted",
                            provider=provider,
                            prompt=effective_prompt,
                        )
                    )
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
                settings=take_settings or None,
            )
            episode_id = shot.scene.script_version.episode_id if shot.scene and shot.scene.script_version else None
            promote_available_clips(project, db, episode_id=episode_id)
            raise_if_cancelled(db, job_id)
            shot.status = "clip_ready"
            has_other_source = bool(
                both_aspects
                and (video_mode == "r2v" or clip_source_image(shot, other_aspect))
            )
            if has_other_source:
                job.progress = 0.5
                job.message = f"Generating {other_aspect} shot clip (2/2)"
            else:
                job.status = "completed"
                job.progress = 1
                job.message = "16:9 + 9:16 shot clips ready" if _is_second_aspect else "Shot clip ready"
            db.commit()
            if previous_filename != filename:
                delete_media_file(previous_filename, db=db)
            delete_media_file(final_cut_filename)
            if has_other_source:
                _generate_shot_clip(
                    shot_id,
                    job_id,
                    other_aspect,
                    prompt_override,
                    include_cast_references,
                    _is_second_aspect=True,
                )
        except JobCancelled:
            db.rollback()
            delete_media_file(filename)
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


_SOUND_QUOTE_RE = re.compile(
    r"(?:\s*(?:says|speaks|plays)\s*,?\s*|\s*:\s*)([\"\u201c]([^\"\u201d]{6,})[\"\u201d])",
    re.IGNORECASE,
)


def compose_video_prompt(prompt: str, sound: str) -> str:
    prompt = prompt.strip()
    sound = sound.strip()
    if not sound or sound in prompt:
        return prompt

    def _dedup_quote(match: re.Match[str]) -> str:
        inner = match.group(2).strip()
        if inner and inner in prompt:
            return " (off-screen earpiece audio only; no lip movement on camera)"
        return match.group(0)

    cleaned_sound = _SOUND_QUOTE_RE.sub(_dedup_quote, sound).strip()
    if re.search(r"\bone\b.*\btwo\b.*\bthree\b", prompt, re.IGNORECASE | re.DOTALL):
        cleaned_sound = re.sub(
            r"^(?:approximately\s+)?(?:two-second|brief)\s+(?:quiet\s+)?(?:viewer\s+)?(?:response\s+)?pause,?\s*(?:then\s*)?",
            "",
            cleaned_sound,
            flags=re.IGNORECASE,
        )
        cleaned_sound = re.sub(
            r"\b(?:two|three|four|five|six|seven|eight|nine|ten)\s+(?=(?:clear|gentle|light|soft|rising)\b)",
            "",
            cleaned_sound,
            flags=re.IGNORECASE,
        )
        cleaned_sound = cleaned_sound.strip()
    return f"{prompt}\n\nAudio direction: {cleaned_sound}" if cleaned_sound else prompt



@video_huey.task()
def generate_shot_clip(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    include_cast_references: bool = False,
) -> None:
    _generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override, include_cast_references)


@video_huey.task()
def generate_shot_clip_comfy(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    include_cast_references: bool = False,
) -> None:
    """Backward-compatible queue alias for jobs enqueued before the FlowKit routing rename."""
    _generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override, include_cast_references)


def enqueue_shot_clip(
    shot_id: str,
    job_id: str,
    aspect_ratio: str | None = None,
    prompt_override: str | None = None,
    include_cast_references: bool = False,
):
    if include_cast_references:
        return generate_shot_clip(shot_id, job_id, aspect_ratio, prompt_override, True)
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
            compilation_id = getattr(job, "compilation_id", None)
            if compilation_id:
                compilation = db.get(Compilation, compilation_id)
                if not compilation:
                    raise ValueError(f"Compilation {compilation_id} not found")
                timeline = ensure_compilation_timeline(db, compilation)
                # Keep still→video promotions in sync without re-appending curated membership.
                promote_available_clips(project, db, compilation_id=compilation_id)
            else:
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

            def update_progress(completed: int, total: int, message: str | None = None) -> None:
                raise_if_cancelled(db, job_id)
                denom = max(total, 1)
                job.progress = min(0.95, 0.1 + (completed / denom) * 0.8)
                job.message = message or f"Rendering shot {completed} of {total}"
                db.commit()

            result = assemble_timeline(project, document, variant, db, update_progress)
            query = select(ProjectVariantExport).where(ProjectVariantExport.project_id == project.id, ProjectVariantExport.variant == variant)
            if compilation_id:
                query = query.where(ProjectVariantExport.compilation_id == compilation_id)
            elif episode_id:
                query = query.where(ProjectVariantExport.episode_id == episode_id)
            else:
                query = query.where(ProjectVariantExport.episode_id.is_(None), ProjectVariantExport.compilation_id.is_(None))
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
                export.episode_id = episode_id if not compilation_id else None
                export.compilation_id = compilation_id
                export.created_at = now()
            else:
                db.add(
                    ProjectVariantExport(
                        project_id=project.id,
                        episode_id=episode_id if not compilation_id else None,
                        compilation_id=compilation_id,
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


@general_huey.task()
def deep_review_general(run_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_job

    run_deep_review_job(run_id, job_id)


@chatgpt_huey.task()
def deep_review_chatgpt(run_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_job

    run_deep_review_job(run_id, job_id)


@gemini_huey.task()
def deep_review_gemini(run_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_job

    run_deep_review_job(run_id, job_id)


def enqueue_deep_review(provider: str, run_id: str, job_id: str):
    if provider == "chatgpt_web":
        return deep_review_chatgpt(run_id, job_id)
    if provider == "gemini_web":
        return deep_review_gemini(run_id, job_id)
    return deep_review_general(run_id, job_id)


@general_huey.task()
def deep_review_draft_general(run_id: str, finding_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_draft_job

    run_deep_review_draft_job(run_id, finding_id, job_id)


@chatgpt_huey.task()
def deep_review_draft_chatgpt(run_id: str, finding_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_draft_job

    run_deep_review_draft_job(run_id, finding_id, job_id)


@gemini_huey.task()
def deep_review_draft_gemini(run_id: str, finding_id: str, job_id: str) -> None:
    from .deep_review import run_deep_review_draft_job

    run_deep_review_draft_job(run_id, finding_id, job_id)


def enqueue_deep_review_draft(
    provider: str, run_id: str, finding_id: str, job_id: str
):
    if provider == "chatgpt_web":
        return deep_review_draft_chatgpt(run_id, finding_id, job_id)
    if provider == "gemini_web":
        return deep_review_draft_gemini(run_id, finding_id, job_id)
    return deep_review_draft_general(run_id, finding_id, job_id)


@general_huey.task()
def release_copy_general(kit_id: str, job_id: str) -> None:
    from .release_kit import run_copy_job

    run_copy_job(kit_id, job_id)


@chatgpt_huey.task()
def release_copy_chatgpt(kit_id: str, job_id: str) -> None:
    from .release_kit import run_copy_job

    run_copy_job(kit_id, job_id)


@gemini_huey.task()
def release_copy_gemini(kit_id: str, job_id: str) -> None:
    from .release_kit import run_copy_job

    run_copy_job(kit_id, job_id)


def enqueue_release_copy(provider: str, kit_id: str, job_id: str):
    if provider == "chatgpt_web":
        return release_copy_chatgpt(kit_id, job_id)
    if provider == "gemini_web":
        return release_copy_gemini(kit_id, job_id)
    return release_copy_general(kit_id, job_id)


@image_huey.task()
def release_cover_worker(kit_id: str, job_id: str, cover_id: str) -> None:
    from .release_kit import run_cover_job

    run_cover_job(kit_id, job_id, cover_id)


def _stem_to_asset(db, project_id: str, wav: Path, label: str) -> TimelineAsset:
    filename = f"stem-{uuid4()}.m4a"
    _run_ffmpeg(["ffmpeg", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "256k", str(settings.media_dir / filename)])
    asset = TimelineAsset(project_id=project_id, filename=filename, mime_type="audio/mp4", label=label[:160])
    db.add(asset)
    db.flush()
    return asset


def _separate_stems(separation_id: str) -> None:
    import tempfile

    from .stems import SEPARATOR_LABELS, separate
    from .timeline_render import resolve_audio_source

    with SessionLocal() as db:
        separation = db.get(StemSeparation, separation_id)
        if not separation:
            return
        job = db.get(Job, separation.job_id) if separation.job_id else None
        project = db.get(Project, separation.project_id)
        if not project or (job and job.status == "cancelled"):
            return
        provider_label = SEPARATOR_LABELS.get(separation.provider, separation.provider)

        def report(fraction: float, message: str) -> None:
            if not job:
                return
            raise_if_cancelled(db, job.id)
            job.progress = max(job.progress or 0, min(0.95, fraction))
            job.message = message[:240]
            db.commit()

        try:
            separation.status = "running"
            if job:
                job.status = "running"
                job.provider_phase = "running"
            report(0.05, f"Preparing audio for {provider_label}")
            source = TimelineSource(kind=separation.source_kind, ref_id=separation.source_ref_id)
            media = resolve_audio_source(source, project, db, "16:9")
            with tempfile.TemporaryDirectory(prefix="cutline-stems-") as tmp:
                work = Path(tmp)
                cut = work / "source.wav"
                _run_ffmpeg([
                    "ffmpeg", "-y",
                    "-ss", f"{max(0, separation.source_in_ms) / 1000:.3f}",
                    "-t", f"{max(1, separation.duration_ms) / 1000:.3f}",
                    "-i", str(media),
                    "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(cut),
                ])
                music_wav, voice_wav = separate(separation.provider, cut, work, report)
                report(0.92, "Saving music and voice tracks")
                music = _stem_to_asset(db, project.id, music_wav, f"{separation.label} music ({provider_label})")
                voice = _stem_to_asset(db, project.id, voice_wav, f"{separation.label} voice ({provider_label})")
            separation.music_asset_id = music.id
            separation.voice_asset_id = voice.id
            separation.status = "completed"
            if job:
                raise_if_cancelled(db, job.id)
                job.status = "completed"
                job.progress = 1
                job.message = f"Music separated with {provider_label}"
                job.provider_phase = "completed"
            db.commit()
        except JobCancelled:
            db.rollback()
            separation = db.get(StemSeparation, separation_id)
            if separation:
                separation.status = "cancelled"
                db.commit()
        except Exception as exc:
            db.rollback()
            separation = db.get(StemSeparation, separation_id)
            job = db.get(Job, separation.job_id) if separation and separation.job_id else None
            if separation:
                separation.status = "failed"
                separation.error = str(exc)
            if job and not preserve_cancelled(job):
                job.status = "failed"
                job.message = f"{provider_label} separation failed"
                job.error = str(exc)
                job.provider_phase = "failed"
            db.commit()


@general_huey.task()
def separate_stems_worker(separation_id: str) -> None:
    _separate_stems(separation_id)


def enqueue_stem_separation(separation_id: str):
    return separate_stems_worker(separation_id)


from .draft import create_draft_worker  # noqa: E402, F401


