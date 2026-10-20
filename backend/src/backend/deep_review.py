"""Deep editorial script review service, job runners, and versioned apply/undo."""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import re
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from .database import SessionLocal
from .deep_review_context import (
    build_context,
    context_for_prompt,
    fingerprint,
    preflight,
    validate_patches,
    validate_report,
)
from .deep_review_provider import editorial_response
from .job_limits import ensure_project_job_capacity
from .jobs import JobCancelled, preserve_cancelled, raise_if_cancelled
from .models import (
    DeepReviewRun,
    Episode,
    Job,
    ProjectTimeline,
    Scene,
    ScriptVersion,
    Shot,
    ShotClip,
    ShotContinuityFrame,
    ShotImage,
    ShotImageReference,
    ShotMediaVariant,
    ShotTake,
    Suggestion,
)
from .providers import get_script_provider
from .screenplay_parser import FIELD_HEADING


def _replace_source_fields(version: ScriptVersion, patches: list[dict[str, Any]]) -> str:
    """Keep untouched source verbatim; reject ambiguous edits rather than drop prose."""
    source = version.source_text
    headings = list(re.finditer(r"^SHOT\s+\d+\s*(?:[—–-]|:)\s*.+$", source, re.I | re.M))
    shots = [shot for scene in sorted(version.scenes, key=lambda s: s.order_index)
             for shot in sorted(scene.shots, key=lambda s: s.order_index)]
    if len(headings) != len(shots):
        raise HTTPException(422, "Cannot safely map the source screenplay to its saved shots. Update the source before applying this revision.")
    by_shot = {}
    for patch in patches:
        by_shot.setdefault(patch["shot_id"], []).append(patch)

    def replace_once(text: str, before: str, after: str) -> str:
        pattern = r"\s+".join(re.escape(word) for word in before.split())
        matches = list(re.finditer(pattern, text)) if pattern else []
        if len(matches) != 1:
            raise HTTPException(422, "The proposed field cannot be located uniquely in the source screenplay. Update the source before applying it.")
        match = matches[0]
        return text[:match.start()] + after + text[match.end():]

    for index in reversed(range(len(shots))):
        edits = by_shot.get(shots[index].id, [])
        if not edits:
            continue
        start = headings[index].start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
        block = source[start:end]
        for patch in edits:
            field, before, after = patch["field"], patch["before"], patch["after"]
            if before == after:
                continue
            if field == "title":
                first, separator, rest = block.partition("\n")
                first = replace_once(first, before, after)
                block = first + separator + rest
                continue
            # Limit matching to its source field so action repeated in prompts stays intact.
            fields = []
            for line in re.finditer(r"^.*$", block, re.M):
                match = FIELD_HEADING.match(line.group().strip())
                if match:
                    name = match.group("field").lower().replace(" ", "_").removeprefix("final_")
                    fields.append((name, line.start(), line.end()))
            selected = next((i for i, entry in enumerate(fields) if entry[0] == field), None)
            if selected is not None:
                region_start = fields[selected][1]
                region_end = fields[selected + 1][1] if selected + 1 < len(fields) else len(block)
            else:
                region_start = block.find("\n") + 1
                region_end = next((entry[1] for entry in fields if entry[0] in {"sound", "image_prompt", "video_prompt"}), len(block))
            region = block[region_start:region_end]
            if field == "dialogue":
                if len(before) != len(after):
                    raise HTTPException(422, "Changing the number of dialogue turns requires a source edit before applying this revision.")
                for old, new in zip(before, after):
                    speaker = old.get("character") or old.get("speaker")
                    pattern = (r"(?im)^(?P<cue>[ \t]*" + re.escape(speaker)
                               + r"(?:[ \t]*\([^\n)]*\))?[ \t]*:?[ \t]*\n?[ \t]*)"
                               + r"[\"“']?" + r"\s+".join(re.escape(word) for word in old["text"].split())
                               + r"[\"”']?[ \t]*(?=\n|$)")
                    matches = list(re.finditer(pattern, region))
                    if len(matches) != 1:
                        raise HTTPException(422, "Cannot safely locate this dialogue turn in the source screenplay.")
                    match = matches[0]
                    cue = match.group("cue")
                    new_speaker = new.get("character") or new.get("speaker")
                    cue = re.sub(re.escape(speaker), lambda _: new_speaker, cue, count=1, flags=re.I)
                    replacement = cue + '"' + new["text"] + '"'
                    region = region[:match.start()] + replacement + region[match.end():]
            elif not before and selected is not None:
                first, separator, remainder = region.partition("\n")
                if first.split(":", 1)[1].strip():
                    raise HTTPException(422, "The source field differs from the empty saved value. Update the source before applying this revision.")
                region = first + '\n' + after + '\n' + remainder
            else:
                try:
                    region = replace_once(region, before, after)
                except HTTPException:
                    if field != "action":
                        raise
                    # Parsed action can span dialogue. Change only differing narrative lines,
                    # preserving intervening speech, pauses, and transitions in their positions.
                    old_lines = [line.strip() for line in before.splitlines() if line.strip()]
                    new_lines = [line.strip() for line in after.splitlines() if line.strip()]
                    if not old_lines:
                        raise HTTPException(422, "Cannot safely locate the empty action field in the source screenplay.")
                    for operation, a, b, c, d in reversed(SequenceMatcher(a=old_lines, b=new_lines, autojunk=False).get_opcodes()):
                        if operation == "equal":
                            continue
                        if a == b:
                            anchor = old_lines[a - 1] if a else old_lines[0]
                            inserted = '\n'.join(new_lines[c:d])
                            value = anchor + '\n' + inserted if a else inserted + '\n' + anchor
                            region = replace_once(region, anchor, value)
                        else:
                            for offset in reversed(range(a, b)):
                                value = '\n'.join(new_lines[c:d]) if offset == a else ''
                                region = replace_once(region, old_lines[offset], value)
            block = block[:region_start] + region + block[region_end:]
        source = source[:start] + block + source[end:]
    return source


def _version_options() -> tuple:
    return (
        selectinload(ScriptVersion.project),
        selectinload(ScriptVersion.episode),
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
        .selectinload(Shot.continuity_frames),
        selectinload(ScriptVersion.scenes)
        .selectinload(Scene.shots)
        .selectinload(Shot.image_references),
        selectinload(ScriptVersion.suggestions),
    )


def _load_version(db: Session, version_id: str) -> ScriptVersion:
    version = db.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.id == version_id)
        .options(*_version_options())
    )
    if not version:
        raise HTTPException(status_code=404, detail="Script version not found")
    return version


def _latest_episode_version(db: Session, version: ScriptVersion) -> ScriptVersion:
    stmt = select(ScriptVersion).where(ScriptVersion.project_id == version.project_id)
    if version.episode_id:
        stmt = stmt.where(ScriptVersion.episode_id == version.episode_id)
    else:
        stmt = stmt.where(ScriptVersion.episode_id.is_(None))
    latest = db.scalar(
        stmt.options(*_version_options()).order_by(
            ScriptVersion.version_number.desc(),
            ScriptVersion.created_at.desc(),
        )
    )
    return latest or version


def _episode_runs(db: Session, version: ScriptVersion) -> list[DeepReviewRun]:
    stmt = (
        select(DeepReviewRun)
        .join(ScriptVersion, DeepReviewRun.script_version_id == ScriptVersion.id)
        .where(ScriptVersion.project_id == version.project_id)
    )
    if version.episode_id:
        stmt = stmt.where(ScriptVersion.episode_id == version.episode_id)
    else:
        stmt = stmt.where(ScriptVersion.episode_id.is_(None))
    return list(
        db.scalars(
            stmt.order_by(DeepReviewRun.created_at.desc(), DeepReviewRun.id.desc())
        ).all()
    )


def _sync_run_with_jobs(db: Session, run: DeepReviewRun) -> bool:
    changed = False
    if run.status in {"queued", "running"}:
        job = db.get(Job, run.job_id)
        if job and job.status in {"cancelled", "failed"} and run.status != job.status:
            run.status = job.status
            if job.status == "failed" and not run.error:
                run.error = job.error or "Review failed"
            changed = True
    return changed


def serialize_run(
    db: Session, run: DeepReviewRun, current_version: ScriptVersion
) -> dict[str, Any]:
    if _sync_run_with_jobs(db, run):
        db.commit()
    current_fp = fingerprint(build_context(db, current_version))
    stale = (
        run.script_version_id != current_version.id
        or run.input_fingerprint != current_fp
    )
    raw_ctx = run.input_context or {}
    episodes = [*(raw_ctx.get("previous") or []), raw_ctx.get("current") or {}]
    context = [
        {
            "episode_id": str(ep.get("episode_id") or ""),
            "episode_number": int(ep.get("episode_number") or 1),
            "title": str(ep.get("title") or ""),
            "version_id": str(ep.get("version_id") or ""),
            "draft": bool(ep.get("draft", False)),
        }
        for ep in episodes
        if ep and ep.get("version_id")
    ]
    draft_job = None
    if run.draft_job_id:
        djob = db.get(Job, run.draft_job_id)
        if djob:
            draft_job = {
                "status": djob.status,
                "error": djob.error,
                "finding_id": run.draft_finding_id,
            }
    return {
        "id": run.id,
        "script_version_id": run.script_version_id,
        "job_id": run.job_id,
        "provider": run.provider,
        "status": run.status,
        "stale": stale,
        "error": run.error,
        "context": context,
        "report": run.report,
        "draft_job": draft_job,
        "applied_version_id": run.applied_version_id,
        "undo_version_id": run.undo_version_id,
    }


def get_deep_review(db: Session, version_id: str) -> dict[str, Any]:
    version = _load_version(db, version_id)
    runs = _episode_runs(db, version)
    any_synced = False
    for run in runs:
        if _sync_run_with_jobs(db, run):
            any_synced = True
    if any_synced:
        db.commit()
    latest = runs[0] if runs else None
    previous_success = next(
        (r for r in runs if r.status == "completed" and r.report is not None),
        None,
    )
    return {
        "latest": serialize_run(db, latest, version) if latest else None,
        "previous_success": (
            serialize_run(db, previous_success, version)
            if previous_success
            else None
        ),
    }


def _resolve_deep_review_provider_name(preferred: str | None) -> str:
    """When a project uses the deterministic 'mock' parser for import, auto-select a connected AI text provider for Deep Review."""
    name = (preferred or "").strip() or "mock"
    if name != "mock":
        return name
    import httpx
    from .settings import settings

    def _bridge_up(base_url: str, api_key: str = "") -> bool:
        if not base_url:
            return False
        try:
            res = httpx.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=1.0,
            )
            return res.is_success
        except Exception:
            return False

    if _bridge_up(settings.gemini_web_base_url):
        return "gemini_web"
    if _bridge_up(settings.chatgpt_web_base_url, settings.chatgpt_web_api_key):
        return "chatgpt_web"
    if _bridge_up(settings.compatible_base_url, settings.compatible_api_key):
        return "compatible"
    try:
        res = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=1.0)
        if res.is_success:
            return "ollama"
    except Exception:
        pass
    return "mock"


def start_deep_review(
    db: Session,
    version_id: str,
    enqueue_fn: Callable[[str, str, str], Any],
) -> dict[str, Any]:
    version = _load_version(db, version_id)
    ctx = build_context(db, version)
    fp = fingerprint(ctx)
    runs = _episode_runs(db, version)
    for existing in runs:
        _sync_run_with_jobs(db, existing)
        if existing.status in {"queued", "running"} and (
            existing.input_fingerprint == fp
            or existing.script_version_id == version.id
        ):
            db.commit()
            raise HTTPException(
                status_code=409,
                detail="A deep review is already running for these script inputs.",
            )
    provider_name = _resolve_deep_review_provider_name(version.project.text_provider)
    ensure_project_job_capacity(db, version.project_id, "deep_review")
    job = Job(
        project_id=version.project_id,
        episode_id=version.episode_id,
        kind="deep_review",
        status="queued",
        progress=0.0,
        message="Queued deep review",
        provider=provider_name,
    )
    db.add(job)
    db.flush()
    run = DeepReviewRun(
        script_version_id=version.id,
        job_id=job.id,
        provider=provider_name,
        input_fingerprint=fp,
        input_context=ctx,
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    task = enqueue_fn(provider_name, run.id, job.id)
    if task is not None and getattr(task, "id", None):
        job = db.get(Job, job.id)
        if job:
            job.task_id = task.id
            db.commit()
    db.expire_all()
    run = db.get(DeepReviewRun, run.id) or run
    version = _load_version(db, version_id)
    return serialize_run(db, run, version)


def run_deep_review_job(run_id: str, job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        run = db.get(DeepReviewRun, run_id)
        if not job or not run:
            return
        if job.status == "cancelled":
            run.status = "cancelled"
            db.commit()
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.15
            job.message = "Reviewing script and earlier episodes"
            run.status = "running"
            db.commit()

            resolved_provider = _resolve_deep_review_provider_name(run.provider)
            if resolved_provider != run.provider:
                run.provider = resolved_provider
                job.provider = resolved_provider
                db.commit()
            provider = get_script_provider(run.provider)
            prompt_context = context_for_prompt(run.input_context)
            preflight_findings = preflight(run.input_context)
            raw = editorial_response(provider, prompt_context)
            raise_if_cancelled(db, job_id)
            report = validate_report(raw, run.input_context, preflight_findings)
            raise_if_cancelled(db, job_id)

            run.report = report
            run.status = "completed"
            run.error = None
            job.status = "completed"
            job.progress = 1.0
            job.message = "Deep review ready"
            db.commit()
        except JobCancelled:
            db.rollback()
            run = db.get(DeepReviewRun, run_id)
            if run:
                run.status = "cancelled"
                db.commit()
            return
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            run = db.get(DeepReviewRun, run_id)
            if preserve_cancelled(job):
                if run:
                    run.status = "cancelled"
                    db.commit()
                return
            if run:
                run.status = "failed"
                run.error = str(exc)
            if job:
                job.status = "failed"
                job.message = "Deep review failed"
                job.error = str(exc)
            db.commit()


def start_deep_review_draft(
    db: Session,
    run_id: str,
    finding_id: str,
    enqueue_fn: Callable[[str, str, str, str], Any],
) -> Job:
    run = db.get(DeepReviewRun, run_id)
    if not run or not run.report:
        raise HTTPException(status_code=404, detail="Deep review run not found")
    version = _load_version(db, run.script_version_id)
    latest_version = _latest_episode_version(db, version)
    if (
        latest_version.id != run.script_version_id
        or fingerprint(build_context(db, latest_version)) != run.input_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail="This review is outdated. Review again before drafting a revision.",
        )
    findings = (run.report or {}).get("findings") or []
    finding = next((f for f in findings if f.get("id") == finding_id), None)
    if not finding:
        raise HTTPException(status_code=404, detail="Review finding not found")
    if run.draft_job_id:
        existing_job = db.get(Job, run.draft_job_id)
        if existing_job and existing_job.status in {"queued", "running"}:
            raise HTTPException(
                status_code=409,
                detail="A revision draft is already in progress.",
            )
    ensure_project_job_capacity(db, version.project_id, "deep_review_draft")
    job = Job(
        project_id=version.project_id,
        episode_id=version.episode_id,
        kind="deep_review_draft",
        status="queued",
        progress=0.0,
        message="Drafting localized revision",
        provider=run.provider,
    )
    db.add(job)
    db.flush()
    run.draft_job_id = job.id
    run.draft_finding_id = finding_id
    db.commit()
    db.refresh(job)

    task = enqueue_fn(run.provider, run.id, finding_id, job.id)
    if task is not None and getattr(task, "id", None):
        job = db.get(Job, job.id)
        if job:
            job.task_id = task.id
            db.commit()
    db.expire_all()
    return db.get(Job, job.id) or job


def run_deep_review_draft_job(run_id: str, finding_id: str, job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        run = db.get(DeepReviewRun, run_id)
        if not job or not run or not run.report:
            return
        if job.status == "cancelled":
            return
        try:
            raise_if_cancelled(db, job_id)
            job.status = "running"
            job.progress = 0.2
            job.message = "Drafting localized revision"
            db.commit()

            findings = (run.report or {}).get("findings") or []
            finding = next((f for f in findings if f.get("id") == finding_id), None)
            if not finding:
                raise RuntimeError("Review finding not found")

            provider = get_script_provider(run.provider)
            raw = editorial_response(
                provider, context_for_prompt(run.input_context), finding
            )
            raise_if_cancelled(db, job_id)
            if not isinstance(raw, dict):
                raise ValueError("Draft response must be a JSON object")
            patches = validate_patches(raw.get("patches", []), run.input_context)
            if not patches:
                raise ValueError(
                    "No safe localized revision could be drafted for this finding."
                )

            report = deepcopy(run.report)
            for item in report.get("findings", []):
                if item.get("id") == finding_id:
                    item["patches"] = patches
                    break
            run.report = report
            flag_modified(run, "report")
            job.status = "completed"
            job.progress = 1.0
            job.message = "Revision draft ready"
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
                job.message = "Revision draft failed"
                job.error = str(exc)
                db.commit()


def _find_episode_timeline(
    db: Session, project_id: str, episode_id: str | None
) -> ProjectTimeline | None:
    if episode_id:
        timeline = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.project_id == project_id,
                ProjectTimeline.episode_id == episode_id,
            )
        )
        if timeline:
            return timeline
        ep = db.get(Episode, episode_id)
        if ep and ep.episode_number == 1:
            return db.scalar(
                select(ProjectTimeline).where(
                    ProjectTimeline.project_id == project_id,
                    ProjectTimeline.episode_id.is_(None),
                )
            )
        return None
    return db.scalar(
        select(ProjectTimeline).where(
            ProjectTimeline.project_id == project_id,
            ProjectTimeline.episode_id.is_(None),
        )
    )


def _remap_timeline_doc(
    doc: dict[str, Any] | None, shot_id_map: dict[str, str]
) -> dict[str, Any] | None:
    if not doc or not isinstance(doc, dict):
        return doc
    remapped = deepcopy(doc)
    for item in remapped.get("primary") or []:
        source = item.get("source") if isinstance(item, dict) else None
        if isinstance(source, dict) and source.get("ref_id") in shot_id_map:
            source["ref_id"] = shot_id_map[source["ref_id"]]
    for item in remapped.get("video_overlays") or []:
        source = item.get("source") if isinstance(item, dict) else None
        if isinstance(source, dict) and source.get("ref_id") in shot_id_map:
            source["ref_id"] = shot_id_map[source["ref_id"]]
    for item in remapped.get("image_overlays") or []:
        source = item.get("source") if isinstance(item, dict) else None
        if isinstance(source, dict) and source.get("ref_id") in shot_id_map:
            source["ref_id"] = shot_id_map[source["ref_id"]]
    for item in remapped.get("audio_overlays") or []:
        source = item.get("source") if isinstance(item, dict) else None
        if isinstance(source, dict) and source.get("ref_id") in shot_id_map:
            source["ref_id"] = shot_id_map[source["ref_id"]]
    return remapped


def _clone_version_with_patches(
    db: Session,
    source_version: ScriptVersion,
    validated_patches: list[dict[str, Any]],
) -> tuple[ScriptVersion, dict[str, Any] | None, dict[str, Any] | None]:
    updated_source = _replace_source_fields(source_version, validated_patches) if validated_patches else source_version.source_text
    max_version = (
        db.scalar(
            select(func.max(ScriptVersion.version_number)).where(
                ScriptVersion.project_id == source_version.project_id
            )
        )
        or source_version.version_number
    )
    patch_map: dict[str, dict[str, Any]] = {}
    for patch in validated_patches:
        patch_map.setdefault(patch["shot_id"], {})[patch["field"]] = deepcopy(
            patch["after"]
        )

    new_structured = deepcopy(source_version.structured_data or {})
    new_version = ScriptVersion(
        project_id=source_version.project_id,
        episode_id=source_version.episode_id,
        version_number=max_version + 1,
        source_text=updated_source,
        structured_data=new_structured,
        status="review" if validated_patches else source_version.status,
    )
    db.add(new_version)
    db.flush()

    shot_id_map: dict[str, str] = {}
    media_id_map: dict[str, str] = {}
    pending_refs: list[tuple[ShotContinuityFrame | ShotImageReference, Shot]] = []

    for scene_idx, old_scene in enumerate(
        sorted(source_version.scenes, key=lambda s: s.order_index)
    ):
        new_scene = Scene(
            script_version_id=new_version.id,
            order_index=old_scene.order_index,
            title=old_scene.title,
            location=old_scene.location,
            time_of_day=old_scene.time_of_day,
            start_ms=old_scene.start_ms,
            end_ms=old_scene.end_ms,
            action=old_scene.action,
            dialogue=deepcopy(old_scene.dialogue),
            set_notes=old_scene.set_notes,
            lighting_notes=old_scene.lighting_notes,
            wardrobe_notes=old_scene.wardrobe_notes,
            props_notes=old_scene.props_notes,
        )
        db.add(new_scene)
        db.flush()

        for shot_idx, old_shot in enumerate(
            sorted(old_scene.shots, key=lambda sh: sh.order_index)
        ):
            overrides = patch_map.get(old_shot.id, {})
            dialogue_val = deepcopy(overrides.get("dialogue", old_shot.dialogue))
            if "dialogue" in overrides:
                normalized_dialogue = []
                for line in dialogue_val:
                    if isinstance(line, dict):
                        char = str(
                            line.get("character") or line.get("speaker") or "Character"
                        ).strip()
                        txt = str(line.get("text") or line.get("line") or "").strip()
                        normalized_dialogue.append(
                            {
                                "character": char,
                                "speaker": char,
                                "text": txt,
                                "line": txt,
                            }
                        )
                dialogue_val = normalized_dialogue

            new_shot = Shot(
                scene_id=new_scene.id,
                order_index=old_shot.order_index,
                title=overrides.get("title", old_shot.title),
                start_ms=old_shot.start_ms,
                end_ms=old_shot.end_ms,
                action=overrides.get("action", old_shot.action),
                dialogue=dialogue_val,
                sound=overrides.get("sound", old_shot.sound),
                duration_ms=old_shot.duration_ms,
                framing=overrides.get("framing", old_shot.framing),
                image_prompt=overrides.get("image_prompt", old_shot.image_prompt),
                video_prompt=overrides.get("video_prompt", old_shot.video_prompt),
                title_card_text=old_shot.title_card_text,
                character_ids=deepcopy(old_shot.character_ids),
                continuity_snapshot=deepcopy(old_shot.continuity_snapshot),
                status=old_shot.status,
                dialogue_audio_filename=old_shot.dialogue_audio_filename,
                dialogue_audio_mime=old_shot.dialogue_audio_mime,
                conversation_url=old_shot.conversation_url,
            )
            db.add(new_shot)
            db.flush()
            shot_id_map[old_shot.id] = new_shot.id

            if (
                overrides
                and isinstance(new_structured.get("scenes"), list)
                and scene_idx < len(new_structured["scenes"])
            ):
                struct_shots = new_structured["scenes"][scene_idx].get("shots")
                if isinstance(struct_shots, list) and shot_idx < len(struct_shots):
                    for k, v in overrides.items():
                        struct_shots[shot_idx][k] = deepcopy(v)

            if old_shot.image:
                new_img = ShotImage(
                    shot_id=new_shot.id,
                    filename=old_shot.image.filename,
                    mime_type=old_shot.image.mime_type,
                    source=old_shot.image.source,
                    provider=old_shot.image.provider,
                    prompt=old_shot.image.prompt,
                    created_at=old_shot.image.created_at,
                )
                db.add(new_img)
                db.flush()
                media_id_map[old_shot.image.id] = new_img.id

            if old_shot.clip:
                new_clip = ShotClip(
                    shot_id=new_shot.id,
                    filename=old_shot.clip.filename,
                    mime_type=old_shot.clip.mime_type,
                    source=old_shot.clip.source,
                    provider=old_shot.clip.provider,
                    prompt=old_shot.clip.prompt,
                    created_at=old_shot.clip.created_at,
                )
                db.add(new_clip)
                db.flush()
                media_id_map[old_shot.clip.id] = new_clip.id

            for mv in old_shot.media_variants:
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
            for take in old_shot.takes:
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

            new_shot.selected_image_take_id = take_id_map.get(
                old_shot.selected_image_take_id or ""
            )
            new_shot.selected_clip_take_id = take_id_map.get(
                old_shot.selected_clip_take_id or ""
            )
            new_shot.selected_audio_take_id = take_id_map.get(
                old_shot.selected_audio_take_id or ""
            )

            for cf in old_shot.continuity_frames:
                pending_refs.append((cf, new_shot))
            for ir in old_shot.image_references:
                pending_refs.append((ir, new_shot))

    for ref_obj, target_shot in pending_refs:
        if isinstance(ref_obj, ShotContinuityFrame):
            db.add(
                ShotContinuityFrame(
                    shot_id=target_shot.id,
                    aspect_ratio=ref_obj.aspect_ratio,
                    filename=ref_obj.filename,
                    mime_type=ref_obj.mime_type,
                    source_shot_id=shot_id_map.get(
                        ref_obj.source_shot_id, ref_obj.source_shot_id
                    ),
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
                    source_shot_id=shot_id_map.get(
                        ref_obj.source_shot_id, ref_obj.source_shot_id
                    ),
                    source_shot_title=ref_obj.source_shot_title,
                    source_media_id=media_id_map.get(
                        ref_obj.source_media_id, ref_obj.source_media_id
                    ),
                    source_media_kind=ref_obj.source_media_kind,
                    source_media_type=ref_obj.source_media_type,
                    source_aspect_ratio=ref_obj.source_aspect_ratio,
                    source_time_ms=ref_obj.source_time_ms,
                    mode=ref_obj.mode,
                    created_at=ref_obj.created_at,
                )
            )

    for sug in source_version.suggestions:
        db.add(
            Suggestion(
                script_version_id=new_version.id,
                category=sug.category,
                severity=sug.severity,
                title=sug.title,
                reason=sug.reason,
                target=deepcopy(sug.target),
                before_value=deepcopy(sug.before_value),
                after_value=deepcopy(sug.after_value),
                status=sug.status,
            )
        )

    if validated_patches:
        new_structured["review_production_changes"] = [shot_id_map[shot_id] for shot_id in patch_map]
        source_version.project.status = "review"
        if source_version.episode:
            source_version.episode.status = "review"
    new_version.structured_data = new_structured
    flag_modified(new_version, "structured_data")
    db.flush()

    timeline = _find_episode_timeline(
        db, source_version.project_id, source_version.episode_id
    )
    timeline_before = None
    timeline_state_after = None
    if timeline:
        timeline_before = {
            "id": timeline.id,
            "revision": timeline.revision,
            "document": deepcopy(timeline.document),
            "shorts_document": deepcopy(timeline.shorts_document),
        }
        timeline.document = _remap_timeline_doc(timeline.document, shot_id_map) or {}
        timeline.shorts_document = _remap_timeline_doc(
            timeline.shorts_document, shot_id_map
        )
        timeline.revision += 1
        flag_modified(timeline, "document")
        if timeline.shorts_document is not None:
            flag_modified(timeline, "shorts_document")
        db.flush()
        timeline_state_after = {
            "id": timeline.id,
            "revision": timeline.revision,
            "document": deepcopy(timeline.document),
            "shorts_document": deepcopy(timeline.shorts_document),
        }

    loaded_new = _load_version(db, new_version.id)
    timeline_after = {
        "version_fingerprint": fingerprint(build_context(db, loaded_new)),
        "timeline": timeline_state_after,
    }
    return loaded_new, timeline_before, timeline_after


def decide_deep_review_finding(
    db: Session,
    run_id: str,
    finding_id: str,
    decision: str,
    expected_version_id: str | None = None,
    patches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if decision not in {"rejected", "accepted", "addressed"}:
        raise HTTPException(status_code=422, detail="Unsupported finding decision")
    run = db.get(DeepReviewRun, run_id)
    if not run or not run.report:
        raise HTTPException(status_code=404, detail="Deep review run not found")
    source_version = _load_version(db, run.script_version_id)
    latest_version = _latest_episode_version(db, source_version)

    report = deepcopy(run.report)
    finding = next(
        (f for f in report.get("findings", []) if f.get("id") == finding_id), None
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Review finding not found")

    if finding.get("status") != "open":
        raise HTTPException(status_code=409, detail="This finding has already been decided.")
    if decision in {"rejected", "addressed"}:
        finding["status"] = decision
        finding["modified"] = False
        run.report = report
        flag_modified(run, "report")
        db.commit()
        db.refresh(run)
        return {
            "version_id": latest_version.id,
            "run": serialize_run(db, run, latest_version),
        }

    if expected_version_id and expected_version_id != run.script_version_id:
        raise HTTPException(
            status_code=409,
            detail="This review is outdated. Review again before applying a correction.",
        )
    current_ctx = build_context(db, latest_version)
    if (
        latest_version.id != run.script_version_id
        or fingerprint(current_ctx) != run.input_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail="This review is outdated. Review again before applying a correction.",
        )

    try:
        validated_patches = validate_patches(finding.get("patches", []) if patches is None else patches, current_ctx)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not validated_patches:
        raise HTTPException(status_code=400, detail="No valid changes to apply")

    original_patches = finding.get("patches") or []
    was_modified = validated_patches != original_patches

    new_version, timeline_before, timeline_after = _clone_version_with_patches(
        db, source_version, validated_patches
    )

    finding["status"] = "accepted"
    finding["modified"] = was_modified
    finding["patches"] = validated_patches
    run.report = report
    flag_modified(run, "report")
    run.undo_version_id = source_version.id
    run.applied_version_id = new_version.id
    run.timeline_before = timeline_before
    run.timeline_after = timeline_after
    db.commit()
    db.refresh(run)
    return {
        "version_id": new_version.id,
        "run": serialize_run(db, run, new_version),
    }


def undo_deep_review_apply(
    db: Session, run_id: str, expected_version_id: str
) -> dict[str, str]:
    run = db.get(DeepReviewRun, run_id)
    if not run or not run.report:
        raise HTTPException(status_code=404, detail="Deep review run not found")
    if (
        not run.applied_version_id
        or not run.undo_version_id
        or run.applied_version_id != expected_version_id
    ):
        raise HTTPException(
            status_code=409,
            detail="This review change can no longer be undone.",
        )
    applied_version = _load_version(db, run.applied_version_id)
    latest_version = _latest_episode_version(db, applied_version)
    if latest_version.id != applied_version.id:
        raise HTTPException(
            status_code=409,
            detail="Cannot undo because a newer script version exists.",
        )
    after_meta = run.timeline_after or {}
    expected_fp = after_meta.get("version_fingerprint")
    if expected_fp and fingerprint(build_context(db, applied_version)) != expected_fp:
        raise HTTPException(
            status_code=409,
            detail="Cannot undo because the script or context was modified after applying this review change.",
        )
    timeline = _find_episode_timeline(
        db, applied_version.project_id, applied_version.episode_id
    )
    saved_tl_after = after_meta.get("timeline")
    if bool(saved_tl_after) != bool(timeline):
        raise HTTPException(409, "Cannot undo because the timeline was created or removed after applying this review change.")
    if saved_tl_after and timeline:
        if (
            timeline.revision != saved_tl_after.get("revision")
            or timeline.document != saved_tl_after.get("document")
            or timeline.shorts_document != saved_tl_after.get("shorts_document")
        ):
            raise HTTPException(
                status_code=409,
                detail="Cannot undo because the timeline was modified after applying this review change.",
            )
    if run.timeline_before and timeline:
        timeline.document = deepcopy(run.timeline_before.get("document") or {})
        timeline.shorts_document = deepcopy(
            run.timeline_before.get("shorts_document")
        )
        flag_modified(timeline, "document")
        if timeline.shorts_document is not None:
            flag_modified(timeline, "shorts_document")

    original_version = _load_version(db, run.undo_version_id)
    restored_version, _, _ = _clone_version_with_patches(db, original_version, [])
    restored_version.project.status = "review" if restored_version.status != "approved" else "shot_plan"
    if restored_version.episode:
        restored_version.episode.status = restored_version.project.status
    report = deepcopy(run.report)
    for item in report.get("findings", []):
        if item.get("status") == "accepted":
            item["status"] = "open"
            item["modified"] = False
    run.report = report
    flag_modified(run, "report")
    run.applied_version_id = None
    run.undo_version_id = None
    run.timeline_before = None
    run.timeline_after = None

    db.commit()
    return {"version_id": restored_version.id}
