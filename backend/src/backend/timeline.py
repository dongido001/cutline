from copy import deepcopy
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .final_cut import probe_duration_ms
from .models import Compilation, Episode, Project, ProjectTimeline, Scene, ScriptVersion, Shot, ShotNote, TimelineAsset
from .schemas import TimelineDocument, TimelinePrimaryItem, TimelineSource, TimelineTextOverlay
from .settings import settings


def _id() -> str:
    return str(uuid.uuid4())


def latest_script_version(
    db: Session,
    project_id: str,
    episode_id: str | None = None,
    extra_options: tuple = (),
):
    options = (
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
        .selectinload(Shot.notes)
        .selectinload(ShotNote.attachments),
        *extra_options,
    )

    def _load(constraint):
        stmt = select(ScriptVersion).where(ScriptVersion.project_id == project_id)
        if constraint is not None:
            stmt = stmt.where(constraint)
        return db.scalar(stmt.options(*options).order_by(ScriptVersion.version_number.desc()))

    if episode_id:
        version = _load(ScriptVersion.episode_id == episode_id)
        if version:
            return version
        return _load(ScriptVersion.episode_id.is_(None))
    return _load(None)


def latest_shots(project_id: str, db: Session, episode_id: str | None = None) -> list[Shot]:
    version = latest_script_version(db, project_id, episode_id=episode_id)
    return [shot for scene in version.scenes for shot in scene.shots] if version else []


def initial_document(project: Project, db: Session, episode_id: str | None = None) -> dict:
    primary: list[dict] = []
    text_overlays: list[dict] = []
    elapsed = 0
    for index, shot in enumerate(latest_shots(project.id, db, episode_id=episode_id)):
        has_clip = bool(shot.clip or any(m.media_type == "clip" for m in shot.media_variants))
        kind = "video" if has_clip else "image"
        source_kind = "shot_clip" if has_clip else "shot_image"
        duration_ms = shot.duration_ms
        clip_obj = shot.clip or next((m for m in shot.media_variants if m.media_type == "clip"), None)
        if clip_obj and clip_obj.filename:
            clip_path = settings.media_dir / clip_obj.filename
            if clip_path.is_file():
                try:
                    duration_ms = probe_duration_ms(clip_path)
                except Exception:
                    pass
        primary.append(
            {
                "id": _id(),
                "kind": kind,
                "label": shot.title or f"Shot {index + 1}",
                "source": {"kind": source_kind, "ref_id": shot.id},
                "duration_ms": duration_ms,
                "source_in_ms": 0,
                "source_out_ms": duration_ms if kind == "video" else None,
                "text": "",
                "variant": {},
            }
        )
        if shot.title_card_text.strip():
            duration = min(2000, duration_ms)
            text_overlays.append(
                {
                    "id": _id(),
                    "text": shot.title_card_text.strip(),
                    "start_ms": elapsed + duration_ms - duration,
                    "duration_ms": duration,
                    "presentation": "full-frame",
                    "size": "large",
                    "color": "white",
                    "background": "solid",
                    "variant": {},
                }
            )
        elapsed += duration_ms
    return TimelineDocument(
        version=1,
        primary=primary,
        video_overlays=[],
        image_overlays=[],
        text_overlays=text_overlays,
    ).model_dump(mode="json")


def ensure_timeline(project: Project, db: Session, episode_id: str | None = None) -> ProjectTimeline:
    if episode_id:
        timeline = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.project_id == project.id,
                ProjectTimeline.episode_id == episode_id,
            )
        )
        if timeline:
            return timeline
        ep = db.get(Episode, episode_id)
        if ep and ep.project_id == project.id and ep.episode_number == 1:
            legacy = db.scalar(
                select(ProjectTimeline).where(
                    ProjectTimeline.project_id == project.id,
                    ProjectTimeline.episode_id.is_(None),
                )
            )
            if legacy:
                legacy.episode_id = episode_id
                db.flush()
                return legacy
        timeline = ProjectTimeline(
            project_id=project.id,
            episode_id=episode_id,
            revision=1,
            document=initial_document(project, db, episode_id=episode_id),
        )
        db.add(timeline)
        db.flush()
        return timeline

    timeline = db.scalar(
        select(ProjectTimeline).where(
            ProjectTimeline.project_id == project.id,
            ProjectTimeline.episode_id.is_(None),
        )
    )
    if timeline:
        return timeline
    ep1_timeline = db.scalar(
        select(ProjectTimeline)
        .join(Episode, ProjectTimeline.episode_id == Episode.id)
        .where(
            ProjectTimeline.project_id == project.id,
            Episode.episode_number == 1,
        )
    )
    if ep1_timeline:
        return ep1_timeline
    timeline = ProjectTimeline(
        project_id=project.id,
        revision=1,
        document=initial_document(project, db),
    )
    db.add(timeline)
    db.flush()
    return timeline


def _concat_episode_timelines(
    project: Project,
    db: Session,
    episode_ids: list[str],
    *,
    prefer_shorts: bool = False,
) -> dict:
    """Concatenate episode timelines into one compilation document.

    When prefer_shorts=True, each episode contributes its shorts_document
    (falling back to landscape document when shorts is missing).
    """
    primary: list[dict] = []
    video_overlays: list[dict] = []
    image_overlays: list[dict] = []
    text_overlays: list[dict] = []
    audio_overlays: list[dict] = []
    current_offset_ms = 0

    for ep_id in episode_ids or []:
        ep_timeline = ensure_timeline(project, db, episode_id=ep_id)
        if prefer_shorts:
            doc = ep_timeline.shorts_document or ep_timeline.document
        else:
            doc = ep_timeline.document
        if not doc:
            doc = initial_document(project, db, episode_id=ep_id)
        ep_duration = 0
        for item in doc.get("primary", []):
            item_copy = deepcopy(item)
            item_copy["id"] = _id()
            primary.append(item_copy)
            ep_duration += int(item_copy.get("duration_ms", 0) or 0)

        for vid in doc.get("video_overlays", []):
            vid_copy = deepcopy(vid)
            vid_copy["id"] = _id()
            vid_copy["start_ms"] = int(vid_copy.get("start_ms", 0) or 0) + current_offset_ms
            video_overlays.append(vid_copy)

        for img in doc.get("image_overlays", []):
            img_copy = deepcopy(img)
            img_copy["id"] = _id()
            img_copy["start_ms"] = int(img_copy.get("start_ms", 0) or 0) + current_offset_ms
            image_overlays.append(img_copy)

        for txt in doc.get("text_overlays", []):
            txt_copy = deepcopy(txt)
            txt_copy["id"] = _id()
            txt_copy["start_ms"] = int(txt_copy.get("start_ms", 0) or 0) + current_offset_ms
            text_overlays.append(txt_copy)

        for aud in doc.get("audio_overlays", []):
            aud_copy = deepcopy(aud)
            aud_copy["id"] = _id()
            aud_copy["start_ms"] = int(aud_copy.get("start_ms", 0) or 0) + current_offset_ms
            audio_overlays.append(aud_copy)

        current_offset_ms += ep_duration

    return TimelineDocument(
        version=1,
        primary=primary,
        video_overlays=video_overlays,
        image_overlays=image_overlays,
        text_overlays=text_overlays,
        audio_overlays=audio_overlays,
    ).model_dump(mode="json")


def _build_shot_compilation_timeline(
    project: Project,
    db: Session,
    episode_id: str | None,
    shot_ids: list[str],
    prefer_shorts: bool = False,
) -> dict:
    """Build a compilation timeline document from specific shot IDs."""
    primary: list[dict] = []
    source_items_by_ref: dict[str, dict] = {}

    if episode_id:
        ep_timeline = ensure_timeline(project, db, episode_id=episode_id)
        doc = (ep_timeline.shorts_document if prefer_shorts else ep_timeline.document) or ep_timeline.document
        if not doc:
            doc = initial_document(project, db, episode_id=episode_id)
        for item in doc.get("primary", []):
            ref_id = item.get("source", {}).get("ref_id") if item.get("source") else None
            if ref_id and ref_id not in source_items_by_ref:
                source_items_by_ref[ref_id] = item

    shots_stmt = (
        select(Shot)
        .where(Shot.id.in_(shot_ids))
        .options(
            selectinload(Shot.media_variants),
            selectinload(Shot.takes),
            selectinload(Shot.image_references),
        )
    )
    shots_map = {s.id: s for s in db.scalars(shots_stmt).all()}

    for sid in shot_ids:
        shot = shots_map.get(sid)
        if not shot:
            continue
        if sid in source_items_by_ref:
            item_copy = deepcopy(source_items_by_ref[sid])
            item_copy["id"] = _id()
            primary.append(item_copy)
            continue

        has_clip = any(v.media_type == "clip" for v in (shot.media_variants or [])) or bool(shot.clip)
        source_kind = "shot_clip" if has_clip else "shot_image"
        dur = shot.duration_ms if shot.duration_ms and shot.duration_ms > 0 else 3000
        primary.append(
            {
                "id": _id(),
                "kind": "video" if has_clip else "image",
                "label": shot.title or f"Shot {shot.order_index + 1}",
                "source": {"kind": source_kind, "ref_id": shot.id},
                "duration_ms": dur,
                "source_in_ms": 0,
                "source_out_ms": dur,
                "text": "",
                "variant": {
                    "landscape": {
                        "fit": "cover",
                        "focal_x": 0.5,
                        "focal_y": 0.5,
                        "zoom": 1,
                    },
                    "shorts": {
                        "fit": "cover",
                        "focal_x": 0.5,
                        "focal_y": 0.5,
                        "zoom": 1,
                    },
                },
            }
        )

    return TimelineDocument(
        version=1,
        primary=primary,
        video_overlays=[],
        image_overlays=[],
        text_overlays=[],
        audio_overlays=[],
    ).model_dump(mode="json")


def ensure_compilation_timeline(db: Session, compilation: Compilation) -> ProjectTimeline:
    timeline = db.scalar(
        select(ProjectTimeline).where(
            ProjectTimeline.project_id == compilation.project_id,
            ProjectTimeline.compilation_id == compilation.id,
        )
    )
    if timeline:
        return timeline

    project = db.get(Project, compilation.project_id)
    if not project:
        raise ValueError("Project not found for compilation")

    if compilation.shot_ids:
        landscape_doc = _build_shot_compilation_timeline(
            project, db, compilation.episode_id, list(compilation.shot_ids or []), prefer_shorts=False
        )
        shorts_doc = _build_shot_compilation_timeline(
            project, db, compilation.episode_id, list(compilation.shot_ids or []), prefer_shorts=True
        )
    else:
        landscape_doc = _concat_episode_timelines(
            project, db, list(compilation.episode_ids or []), prefer_shorts=False
        )
        shorts_doc = _concat_episode_timelines(
            project, db, list(compilation.episode_ids or []), prefer_shorts=True
        )

    timeline = ProjectTimeline(
        project_id=compilation.project_id,
        compilation_id=compilation.id,
        revision=1,
        document=landscape_doc,
        shorts_document=shorts_doc,
    )
    db.add(timeline)
    db.flush()
    return timeline


def rebuild_compilation_timeline(db: Session, compilation: Compilation) -> ProjectTimeline:
    """Replace a compilation timeline with a fresh concat of its current episodes."""
    existing = db.scalar(
        select(ProjectTimeline).where(
            ProjectTimeline.project_id == compilation.project_id,
            ProjectTimeline.compilation_id == compilation.id,
        )
    )
    if existing:
        db.delete(existing)
        db.flush()
    return ensure_compilation_timeline(db, compilation)


def promote_available_clips(
    project: Project,
    db: Session,
    episode_id: str | None = None,
    compilation_id: str | None = None,
) -> int:
    """Promote default storyboard blocks when their shot now has video media.

    Existing timeline timing, trims, and per-variant framing are kept intact.
    Only the source type changes from the shot's still image to its video.
    """
    if compilation_id:
        timeline = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.project_id == project.id,
                ProjectTimeline.compilation_id == compilation_id,
            )
        )
        compilation = db.get(Compilation, compilation_id)
        shots: list[Shot] = []
        seen: set[str] = set()
        if compilation and compilation.shot_ids:
            for shot in db.scalars(select(Shot).where(Shot.id.in_(compilation.shot_ids))).all():
                if shot.id not in seen:
                    seen.add(shot.id)
                    shots.append(shot)
        else:
            for ep_id in list(getattr(compilation, "episode_ids", None) or []):
                for shot in latest_shots(project.id, db, episode_id=ep_id):
                    if shot.id in seen:
                        continue
                    seen.add(shot.id)
                    shots.append(shot)
    elif episode_id:
        timeline = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.project_id == project.id,
                ProjectTimeline.episode_id == episode_id,
            )
        )
        shots = latest_shots(project.id, db, episode_id=episode_id)
    else:
        timeline = project.timeline or db.scalar(
            select(ProjectTimeline).where(ProjectTimeline.project_id == project.id)
        )
        shots = latest_shots(project.id, db, episode_id=episode_id)
    if not timeline:
        return 0
    clips_by_shot = {
        shot.id: shot
        for shot in shots
        if shot.clip or any(item.media_type == "clip" for item in shot.media_variants)
    }
    total_promoted = 0
    for doc_attr in ("document", "shorts_document"):
        raw_doc = getattr(timeline, doc_attr)
        if not raw_doc:
            continue
        document = TimelineDocument.model_validate(raw_doc)
        doc_promoted = 0
        for item in document.primary:
            if (
                item.kind != "image"
                or not item.source
                or item.source.kind != "shot_image"
                or item.source.ref_id not in clips_by_shot
            ):
                continue
            shot = clips_by_shot[item.source.ref_id]
            clip_dur = None
            clip_obj = shot.clip or next((m for m in shot.media_variants if m.media_type == "clip"), None)
            if clip_obj and clip_obj.filename:
                clip_path = settings.media_dir / clip_obj.filename
                if clip_path.is_file():
                    try:
                        clip_dur = probe_duration_ms(clip_path)
                    except Exception:
                        pass
            item.kind = "video"
            item.source = TimelineSource(kind="shot_clip", ref_id=item.source.ref_id)
            if clip_dur and (item.duration_ms > clip_dur or item.duration_ms == shot.duration_ms):
                item.duration_ms = clip_dur
            item.source_out_ms = item.source_in_ms + item.duration_ms
            doc_promoted += 1
        if doc_promoted:
            setattr(timeline, doc_attr, document.model_dump(mode="json"))
            total_promoted = max(total_promoted, doc_promoted)
    if total_promoted:
        timeline.revision += 1
    return total_promoted


def append_missing_shots(project: Project, db: Session, episode_id: str | None = None) -> int:
    """Append newly approved shots without changing existing timeline edits."""
    if episode_id:
        timeline = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.project_id == project.id,
                ProjectTimeline.episode_id == episode_id,
            )
        )
    else:
        timeline = project.timeline or db.scalar(
            select(ProjectTimeline).where(ProjectTimeline.project_id == project.id)
        )
    if not timeline:
        return 0
    shots = latest_shots(project.id, db, episode_id=episode_id)
    total_added = 0
    for doc_attr in ("document", "shorts_document"):
        raw_doc = getattr(timeline, doc_attr)
        if not raw_doc:
            continue
        document = TimelineDocument.model_validate(raw_doc)
        existing_shot_ids = {
            item.source.ref_id
            for item in document.primary
            if item.source and item.source.kind in {"shot_clip", "shot_image"}
        }
        elapsed = timeline_duration(document)
        doc_added = 0
        for shot in shots:
            if shot.id in existing_shot_ids:
                continue
            kind = "video" if shot.clip else "image"
            duration_ms = shot.duration_ms
            if shot.clip:
                clip_path = settings.media_dir / shot.clip.filename
                if clip_path.is_file():
                    try:
                        duration_ms = probe_duration_ms(clip_path)
                    except Exception:
                        pass
            document.primary.append(
                TimelinePrimaryItem(
                    id=_id(),
                    kind=kind,
                    label=shot.title or f"Shot {len(document.primary) + 1}",
                    source=TimelineSource(
                        kind="shot_clip" if shot.clip else "shot_image",
                        ref_id=shot.id,
                    ),
                    duration_ms=duration_ms,
                    source_in_ms=0,
                    source_out_ms=duration_ms if kind == "video" else None,
                    text="",
                    variant={},
                )
            )
            if shot.title_card_text.strip():
                duration = min(2000, duration_ms)
                document.text_overlays.append(
                    TimelineTextOverlay(
                        id=_id(),
                        text=shot.title_card_text.strip(),
                        start_ms=elapsed + duration_ms - duration,
                        duration_ms=duration,
                        presentation="full-frame",
                        size="large",
                        color="white",
                        background="solid",
                        variant={},
                    )
                )
            elapsed += duration_ms
            existing_shot_ids.add(shot.id)
            doc_added += 1
        if doc_added:
            setattr(timeline, doc_attr, document.model_dump(mode="json"))
            total_added = max(total_added, doc_added)
    if total_added:
        timeline.revision += 1
    return total_added


def timeline_duration(document: TimelineDocument | dict) -> int:
    parsed = document if isinstance(document, TimelineDocument) else TimelineDocument.model_validate(document)
    return sum(item.duration_ms for item in parsed.primary)


def validate_timeline(
    document: TimelineDocument,
    project: Project,
    db: Session,
    episode_id: str | None = None,
    max_duration_ms: int = 180 * 60 * 1000,
) -> None:
    total = timeline_duration(document)
    if not document.primary:
        raise ValueError("The timeline needs at least one primary item")
    if total > max_duration_ms:
        minutes = max_duration_ms // (60 * 1000)
        raise ValueError(f"The timeline cannot exceed {minutes} minutes")

    ep_shots = {shot.id for shot in latest_shots(project.id, db, episode_id=episode_id)}
    project_shots = set(
        db.scalars(
            select(Shot.id).join(Scene).join(ScriptVersion).where(ScriptVersion.project_id == project.id)
        )
    )
    shots = ep_shots | project_shots
    assets = set(
        db.scalars(select(TimelineAsset.id).where(TimelineAsset.project_id == project.id))
    )
    for item in document.primary:
        if item.kind == "title":
            if not item.text.strip():
                raise ValueError("Title cards need text")
            continue
        if not item.source:
            raise ValueError(f"{item.label} needs a media source")
        if item.source.kind == "asset" and item.source.ref_id not in assets:
            raise ValueError(f"{item.label} uses a missing uploaded asset")
        if item.source.kind != "asset" and item.source.ref_id not in shots:
            raise ValueError(f"{item.label} uses a missing shot")
        if item.kind == "video" and item.source_out_ms is not None:
            if item.source_out_ms <= item.source_in_ms:
                raise ValueError(f"{item.label} has an invalid trim range")
            if item.loop and item.duration_ms > item.source_out_ms - item.source_in_ms:
                pass
            elif item.duration_ms != item.source_out_ms - item.source_in_ms:
                if abs(item.duration_ms - (item.source_out_ms - item.source_in_ms)) <= 2:
                    item.duration_ms = item.source_out_ms - item.source_in_ms
                else:
                    raise ValueError(f"{item.label} duration must match its trim range")

    for overlay in [*document.video_overlays, *document.image_overlays, *document.text_overlays, *document.audio_overlays]:
        if overlay.start_ms + overlay.duration_ms > total:
            raise ValueError("An overlay extends beyond the timeline")
    for overlay in document.video_overlays:
        if overlay.source.kind == "asset" and overlay.source.ref_id not in assets:
            raise ValueError("A video overlay uses a missing uploaded asset")
        if overlay.source.kind != "asset" and overlay.source.ref_id not in shots:
            raise ValueError("A video overlay uses a missing shot")
    for overlay in document.image_overlays:
        if overlay.source.kind == "asset" and overlay.source.ref_id not in assets:
            raise ValueError("An image overlay uses a missing uploaded asset")
        if overlay.source.kind != "asset" and overlay.source.ref_id not in shots:
            raise ValueError("An image overlay uses a missing shot")
    for overlay in document.audio_overlays:
        if overlay.source.kind == "asset" and overlay.source.ref_id not in assets:
            raise ValueError("An audio overlay uses a missing uploaded asset")
        if overlay.source.kind != "asset" and overlay.source.ref_id not in shots:
            raise ValueError("An audio overlay uses a missing shot")
