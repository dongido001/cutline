"""Image guidance sources, immutable snapshots, and shared media validation."""
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from .models import Job, Shot, ShotImageReference, ShotTake
from .schemas import ShotImageReferenceRead
from .settings import settings

_STILL_TYPES = {"image/png", "image/jpeg", "image/webp"}


def earlier_shots(shot):
    version = shot.scene.script_version
    project = version.project
    episode = version.episode
    earlier = []

    if project and episode and getattr(project, 'episodes', None):
        prev_episodes = [
            ep for ep in project.episodes
            if ep.episode_number < episode.episode_number
        ]
        prev_episodes.sort(key=lambda ep: ep.episode_number)
        for prev_ep in prev_episodes:
            ep_versions = sorted(prev_ep.script_versions, key=lambda v: v.version_number)
            if ep_versions:
                latest_v = ep_versions[-1]
                for sc in sorted(latest_v.scenes, key=lambda s: s.order_index):
                    for sh in sorted(sc.shots, key=lambda s: s.order_index):
                        earlier.append(sh)

    ordered_current = [
        item for scene in sorted(version.scenes, key=lambda x: x.order_index)
        for item in sorted(scene.shots, key=lambda x: x.order_index)
    ]
    try:
        shot_idx = next(i for i, item in enumerate(ordered_current) if item.id == shot.id)
        earlier.extend(ordered_current[:shot_idx])
    except StopIteration:
        pass

    return earlier


def previous_shot(shot) -> Shot | None:
    earlier = earlier_shots(shot)
    return earlier[-1] if earlier else None


def _still_snapshot(source_shot, item, kind: str, aspect: str, time_ms: int | None = None) -> dict | None:
    if not item or not getattr(item, "filename", None):
        return None
    mime_type = getattr(item, "mime_type", None) or "image/png"
    if mime_type not in _STILL_TYPES:
        return None
    if not (settings.media_dir / item.filename).is_file():
        return None
    created = getattr(item, "created_at", None) or datetime.now(timezone.utc)
    if isinstance(created, datetime):
        created_at = created.isoformat()
    else:
        created_at = str(created)
    return {
        "id": f"auto-{source_shot.id}-{aspect}",
        "aspect_ratio": aspect,
        "filename": item.filename,
        "url": f"/media/{item.filename}",
        "mime_type": mime_type,
        "source_shot_id": source_shot.id,
        "source_shot_title": source_shot.title or "",
        "source_media_id": item.id,
        "source_media_kind": kind,
        "source_media_type": "image",
        "source_aspect_ratio": aspect,
        "source_time_ms": time_ms,
        "mode": "visual_continuity",
        "created_at": created_at,
    }


def default_visual_reference(shot, aspect: str) -> dict | None:
    """Previous-shot still or a continuity frame already saved on this shot."""
    frame = next(
        (item for item in (shot.continuity_frames or []) if item.aspect_ratio == aspect),
        None,
    )
    if frame:
        snapshot = _still_snapshot(shot, frame, "continuity_frame", aspect, getattr(frame, "source_time_ms", None))
        if snapshot:
            if getattr(frame, "source_shot_id", None):
                snapshot["source_shot_id"] = frame.source_shot_id
            if getattr(frame, "source_shot_title", None):
                snapshot["source_shot_title"] = frame.source_shot_title
            return snapshot

    source = previous_shot(shot)
    if not source:
        return None
    variant = next(
        (
            item
            for item in (source.media_variants or [])
            if item.media_type == "image" and item.aspect_ratio == aspect
        ),
        None,
    )
    if variant:
        return _still_snapshot(source, variant, "variant", aspect)
    project = source.scene.script_version.project
    if source.image and aspect == project.aspect_ratio:
        return _still_snapshot(source, source.image, "image", aspect)
    return None


def media_sources(shot):
    """Prefer immutable takes and deduplicate aliases for currently selected media."""
    aspect = shot.scene.script_version.project.aspect_ratio
    candidates = [(take, 'take', take.media_type, take.aspect_ratio) for take in reversed(shot.takes)]
    candidates += [(item, 'variant', item.media_type, item.aspect_ratio) for item in shot.media_variants]
    candidates += [(shot.image, 'image', 'image', aspect), (shot.clip, 'clip', 'clip', aspect)]
    seen = set()
    result = []
    for item, kind, media_type, source_aspect in candidates:
        if not item or media_type not in {'image', 'clip'} or item.filename in seen:
            continue
        if media_type == 'image' and item.mime_type not in {'image/png', 'image/jpeg', 'image/webp'}:
            continue
        if not (settings.media_dir / item.filename).is_file():
            continue
        seen.add(item.filename)
        result.append(dict(source_shot_id=shot.id, source_shot_title=shot.title,
                           source_media_id=item.id, source_media_kind=kind,
                           source_media_type=media_type, source_aspect_ratio=source_aspect,
                           url=item.url, filename=item.filename, mime_type=item.mime_type))
    return result


def reference_snapshot(shot, aspect, reference_id=None):
    ref = next(
        (
            ref for ref in shot.image_references
            if ref.aspect_ratio == aspect and (reference_id is None or ref.id == reference_id)
        ),
        None,
    )
    return ShotImageReferenceRead.model_validate(ref).model_dump(mode='json') if ref else None


def queued_reference(job, shot):
    for item in job.attempts or []:
        if not isinstance(item, dict):
            continue
        if item.get('shot_id') == shot.id and item.get('image_reference_id'):
            reference = deepcopy(item.get('image_reference'))
            if (reference or {}).get('id') != item['image_reference_id']:
                raise RuntimeError('Queued image reference is missing or does not match the selection')
            return reference
        if item.get('shot_id') == shot.id and 'image_reference' in item:
            return deepcopy(item.get('image_reference'))
        if 'image_references' in item:
            return deepcopy(item['image_references'].get(shot.id))
    return None


def freeze_visual_reference(snapshot):
    if not snapshot:
        return None
    frozen = deepcopy(snapshot)
    filename = frozen.get("filename")
    if not filename or Path(filename).name != filename:
        raise RuntimeError("Invalid image reference asset")
    if filename.startswith("image-reference-"):
        return frozen
    source = settings.media_dir / filename
    if not source.is_file():
        raise RuntimeError("Saved image reference is missing; choose another reference")
    dest_name = f"image-reference-{uuid4()}{source.suffix or '.png'}"
    dest = settings.media_dir / dest_name
    dest.write_bytes(source.read_bytes())
    frozen["filename"] = dest_name
    frozen["url"] = f"/media/{dest_name}"
    return frozen


def should_use_default_reference(job, shot) -> bool:
    for item in job.attempts or []:
        if isinstance(item, dict) and item.get("shot_id") == shot.id and "use_default_image_reference" in item:
            return bool(item["use_default_image_reference"])
    return False


def validate_reference_bytes(content, mime_type):
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError('Reference image must be between 1 byte and 20 MB')
    try:
        with Image.open(BytesIO(content)) as image:
            if Image.MIME.get(image.format) != mime_type:
                raise ValueError('Reference image does not match its media type')
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Reference image is invalid') from exc


def load_visual_reference(snapshot):
    if not snapshot:
        return None
    from .media import VisualReferenceImage
    filename = snapshot['filename']
    if Path(filename).name != filename:
        raise RuntimeError('Invalid image reference asset')
    path = settings.media_dir / filename
    if not path.is_file():
        raise RuntimeError('Saved image reference is missing; choose another reference')
    content = path.read_bytes()
    validate_reference_bytes(content, snapshot['mime_type'])
    return VisualReferenceImage(data=content, mime_type=snapshot['mime_type'])


def snapshot_filenames(value):
    """Only collect our owned assets from nested job/take snapshots."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'filename' and isinstance(item, str) and item.startswith('image-reference-'):
                yield item
            else:
                yield from snapshot_filenames(item)
    elif isinstance(value, list):
        for item in value:
            yield from snapshot_filenames(item)


def release_reference_asset(db, filename):
    if not filename:
        return
    if db.scalar(select(ShotImageReference.id).where(ShotImageReference.filename == filename)):
        return
    for value in db.scalars(select(ShotTake.settings)):
        if filename in set(snapshot_filenames(value)):
            return
    for value in db.scalars(select(Job.attempts)):
        if filename in set(snapshot_filenames(value)):
            return
    (settings.media_dir / filename).unlink(missing_ok=True)
