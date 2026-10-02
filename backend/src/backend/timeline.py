import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .final_cut import probe_duration_ms
from .models import Project, ProjectTimeline, Scene, ScriptVersion, Shot, TimelineAsset
from .schemas import TimelineDocument, TimelinePrimaryItem, TimelineSource, TimelineTextOverlay
from .settings import settings


def _id() -> str:
    return str(uuid.uuid4())


def latest_shots(project_id: str, db: Session) -> list[Shot]:
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
        )
        .order_by(ScriptVersion.version_number.desc())
    )
    return [shot for scene in version.scenes for shot in scene.shots] if version else []


def initial_document(project: Project, db: Session) -> dict:
    primary: list[dict] = []
    text_overlays: list[dict] = []
    elapsed = 0
    for index, shot in enumerate(latest_shots(project.id, db)):
        kind = "video" if shot.clip else "image"
        source_kind = "shot_clip" if shot.clip else "shot_image"
        duration_ms = shot.duration_ms
        if shot.clip:
            clip_path = settings.media_dir / shot.clip.filename
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
        image_overlays=[],
        text_overlays=text_overlays,
    ).model_dump(mode="json")


def ensure_timeline(project: Project, db: Session) -> ProjectTimeline:
    if project.timeline:
        return project.timeline
    timeline = ProjectTimeline(
        project_id=project.id,
        revision=1,
        document=initial_document(project, db),
    )
    db.add(timeline)
    db.flush()
    return timeline


def promote_available_clips(project: Project, db: Session) -> int:
    """Promote default storyboard blocks when their shot now has video media.

    Existing timeline timing, trims, and per-variant framing are kept intact.
    Only the source type changes from the shot's still image to its video.
    """
    timeline = project.timeline or db.scalar(
        select(ProjectTimeline).where(ProjectTimeline.project_id == project.id)
    )
    if not timeline:
        return 0
    shots = latest_shots(project.id, db)
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
            if shot.clip:
                clip_path = settings.media_dir / shot.clip.filename
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


def append_missing_shots(project: Project, db: Session) -> int:
    """Append newly approved shots without changing existing timeline edits."""
    if not project.timeline:
        return 0
    shots = latest_shots(project.id, db)
    total_added = 0
    for doc_attr in ("document", "shorts_document"):
        raw_doc = getattr(project.timeline, doc_attr)
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
            setattr(project.timeline, doc_attr, document.model_dump(mode="json"))
            total_added = max(total_added, doc_added)
    if total_added:
        project.timeline.revision += 1
    return total_added


def timeline_duration(document: TimelineDocument | dict) -> int:
    parsed = document if isinstance(document, TimelineDocument) else TimelineDocument.model_validate(document)
    return sum(item.duration_ms for item in parsed.primary)


def validate_timeline(document: TimelineDocument, project: Project, db: Session) -> None:
    total = timeline_duration(document)
    if not document.primary:
        raise ValueError("The timeline needs at least one primary item")
    if total > 30 * 60 * 1000:
        raise ValueError("The timeline cannot exceed 30 minutes")

    shots = {shot.id for shot in latest_shots(project.id, db)}
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
            if item.duration_ms != item.source_out_ms - item.source_in_ms:
                if abs(item.duration_ms - (item.source_out_ms - item.source_in_ms)) <= 2:
                    item.duration_ms = item.source_out_ms - item.source_in_ms
                else:
                    raise ValueError(f"{item.label} duration must match its trim range")

    for overlay in [*document.image_overlays, *document.text_overlays]:
        if overlay.start_ms + overlay.duration_ms > total:
            raise ValueError("An overlay extends beyond the timeline")
    for overlay in document.image_overlays:
        if overlay.source.kind == "asset" and overlay.source.ref_id not in assets:
            raise ValueError("An image overlay uses a missing uploaded asset")
        if overlay.source.kind != "asset" and overlay.source.ref_id not in shots:
            raise ValueError("An image overlay uses a missing shot")
