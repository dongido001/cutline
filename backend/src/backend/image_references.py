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


def _still_snapshot(
    source_shot,
    item,
    kind: str,
    aspect: str,
    time_ms: int | None = None,
    *,
    source_aspect_ratio: str | None = None,
    mode: str = "visual_continuity",
    snapshot_id: str | None = None,
) -> dict | None:
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
        "id": snapshot_id or f"auto-{source_shot.id}-{aspect}",
        "aspect_ratio": aspect,
        "filename": item.filename,
        "url": f"/media/{item.filename}",
        "mime_type": mime_type,
        "source_shot_id": source_shot.id,
        "source_shot_title": source_shot.title or "",
        "source_media_id": item.id,
        "source_media_kind": kind,
        "source_media_type": "image",
        "source_aspect_ratio": source_aspect_ratio or aspect,
        "source_time_ms": time_ms,
        "mode": mode,
        "created_at": created_at,
    }


def _extract_clip_first_frame(clip) -> object | None:
    if not clip or not getattr(clip, "filename", None):
        return None
    clip_path = settings.media_dir / clip.filename
    if not clip_path.is_file():
        return None
    frame_filename = f"clip-frame-{Path(clip.filename).stem}.png"
    frame_path = settings.media_dir / frame_filename
    if not frame_path.is_file():
        import subprocess
        try:
            cmd = [
                "ffmpeg", "-y", "-ss", "0", "-i", str(clip_path),
                "-vframes", "1", "-q:v", "2", str(frame_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            return None
    if frame_path.is_file():
        class _ClipFrameWrapper:
            def __init__(self, filename, orig_id):
                self.filename = filename
                self.mime_type = "image/png"
                self.id = orig_id
        return _ClipFrameWrapper(frame_filename, getattr(clip, "id", "clip-frame"))
    return None


def _shot_still_for_aspect(shot, aspect: str) -> tuple[object | None, str]:
    variant = next(
        (
            item
            for item in (shot.media_variants or [])
            if item.media_type == "image" and item.aspect_ratio == aspect
        ),
        None,
    )
    if variant and (settings.media_dir / variant.filename).is_file():
        return variant, "variant"
    project = shot.scene.script_version.project if getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None) else None
    if project and shot.image and aspect == project.aspect_ratio and (settings.media_dir / shot.image.filename).is_file():
        return shot.image, "image"
    matching_takes = [
        take
        for take in (getattr(shot, "takes", None) or [])
        if take.media_type == "image"
        and take.aspect_ratio == aspect
        and getattr(take, "filename", None)
        and (settings.media_dir / take.filename).is_file()
    ]
    if matching_takes:
        selected = next((take for take in matching_takes if getattr(take, "selected", False)), None)
        return selected or matching_takes[-1], "take"

    # Fallback to video clip opening frame if no image take exists
    clip_variant = next(
        (
            item
            for item in (shot.media_variants or [])
            if item.media_type == "clip" and item.aspect_ratio == aspect
        ),
        None,
    )
    if clip_variant and (settings.media_dir / clip_variant.filename).is_file():
        wrapped = _extract_clip_first_frame(clip_variant)
        if wrapped:
            return wrapped, "clip_variant"
    if project and shot.clip and aspect == project.aspect_ratio and (settings.media_dir / shot.clip.filename).is_file():
        wrapped = _extract_clip_first_frame(shot.clip)
        if wrapped:
            return wrapped, "clip"
    matching_clip_takes = [
        take
        for take in (getattr(shot, "takes", None) or [])
        if take.media_type == "clip"
        and take.aspect_ratio == aspect
        and getattr(take, "filename", None)
        and (settings.media_dir / take.filename).is_file()
    ]
    if matching_clip_takes:
        selected_clip = next((take for take in matching_clip_takes if getattr(take, "selected", False)), None) or matching_clip_takes[-1]
        wrapped = _extract_clip_first_frame(selected_clip)
        if wrapped:
            return wrapped, "clip_take"

    return None, ""


def default_visual_reference(shot, aspect: str) -> dict | None:
    """Resolve the best visual reference for generating `aspect` on `shot`.

    Priority:
    1. Same-shot continuity frame matching `aspect`.
    2. Same-shot approved image in the other aspect ratio (`mode='aspect_conversion'`).
    3. Same-shot grabbed image reference or continuity frame from the other aspect ratio.
    4. Previous-shot still in `aspect`, or fallback to previous-shot still in the other aspect ratio.
    """
    other_aspect = "9:16" if aspect == "16:9" else "16:9"

    ref = next(
        (ref for ref in (getattr(shot, "image_references", None) or []) if ref.aspect_ratio == aspect),
        None,
    )
    if ref and (settings.media_dir / ref.filename).is_file():
        return ShotImageReferenceRead.model_validate(ref).model_dump(mode="json")

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

    current_still, _ = _shot_still_for_aspect(shot, aspect)
    project = (
        shot.scene.script_version.project
        if getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None)
        else None
    )
    is_secondary_aspect = bool(project and aspect != project.aspect_ratio)
    if current_still is None or is_secondary_aspect:
        other_still, other_kind = _shot_still_for_aspect(shot, other_aspect)
        if other_still is not None:
            snapshot = _still_snapshot(
                shot,
                other_still,
                other_kind,
                aspect,
                source_aspect_ratio=other_aspect,
                mode="aspect_conversion",
                snapshot_id=f"convert-{shot.id}-{other_aspect}-to-{aspect}",
            )
            if snapshot:
                return snapshot

    other_ref = next(
        (ref for ref in (getattr(shot, "image_references", None) or []) if ref.aspect_ratio == other_aspect),
        None,
    )
    if other_ref and (settings.media_dir / other_ref.filename).is_file():
        dumped = ShotImageReferenceRead.model_validate(other_ref).model_dump(mode="json")
        dumped["aspect_ratio"] = aspect
        return dumped

    other_frame = next(
        (item for item in (shot.continuity_frames or []) if item.aspect_ratio == other_aspect),
        None,
    )
    if other_frame:
        snapshot = _still_snapshot(
            shot,
            other_frame,
            "continuity_frame",
            aspect,
            getattr(other_frame, "source_time_ms", None),
            source_aspect_ratio=other_aspect,
        )
        if snapshot:
            if getattr(other_frame, "source_shot_id", None):
                snapshot["source_shot_id"] = other_frame.source_shot_id
            if getattr(other_frame, "source_shot_title", None):
                snapshot["source_shot_title"] = other_frame.source_shot_title
            return snapshot

    source = previous_shot(shot)
    if not source:
        return None
    source_still, source_kind = _shot_still_for_aspect(source, aspect)
    if source_still is not None:
        return _still_snapshot(source, source_still, source_kind, aspect)
    source_other_still, source_other_kind = _shot_still_for_aspect(source, other_aspect)
    if source_other_still is not None:
        return _still_snapshot(
            source,
            source_other_still,
            source_other_kind,
            aspect,
            source_aspect_ratio=other_aspect,
        )
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
    if ref:
        return ShotImageReferenceRead.model_validate(ref).model_dump(mode='json')
    if reference_id is not None:
        cross_ref = next(
            (ref for ref in shot.image_references if ref.id == reference_id),
            None,
        )
        if cross_ref:
            dumped = ShotImageReferenceRead.model_validate(cross_ref).model_dump(mode='json')
            dumped['aspect_ratio'] = aspect
            return dumped
        frame = next(
            (item for item in (shot.continuity_frames or []) if item.id == reference_id),
            None,
        )
        if frame:
            snapshot = _still_snapshot(
                shot,
                frame,
                "continuity_frame",
                aspect,
                getattr(frame, "source_time_ms", None),
                source_aspect_ratio=getattr(frame, "aspect_ratio", aspect),
                snapshot_id=frame.id,
            )
            if snapshot:
                if getattr(frame, "source_shot_id", None):
                    snapshot["source_shot_id"] = frame.source_shot_id
                if getattr(frame, "source_shot_title", None):
                    snapshot["source_shot_title"] = frame.source_shot_title
                return snapshot
        default_ref = default_visual_reference(shot, aspect)
        if default_ref and default_ref.get("id") == reference_id:
            return default_ref
    return None


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
    if snapshot.get("mode") == "aspect_conversion":
        src_aspect = snapshot.get("source_aspect_ratio") or "9:16"
        dst_aspect = snapshot.get("aspect_ratio") or "16:9"
        return VisualReferenceImage(
            data=content,
            mime_type=snapshot["mime_type"],
            character_name=f"Approved {src_aspect} shot frame to reframe/expand into {dst_aspect}",
            reference_type="edit_target",
        )
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
