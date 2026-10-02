"""Image guidance sources, immutable snapshots, and shared media validation."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from .models import Job, ShotImageReference, ShotTake
from .schemas import ShotImageReferenceRead
from .settings import settings


def earlier_shots(shot):
    ordered = [item for scene in sorted(shot.scene.script_version.scenes, key=lambda x: x.order_index)
               for item in sorted(scene.shots, key=lambda x: x.order_index)]
    return ordered[:next(i for i, item in enumerate(ordered) if item.id == shot.id)]


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
        if item.get('shot_id') == shot.id and 'image_reference_id' in item:
            reference = deepcopy(item.get('image_reference'))
            if (reference or {}).get('id') != item['image_reference_id']:
                raise RuntimeError('Queued image reference is missing or does not match the selection')
            return reference
        if item.get('shot_id') == shot.id and 'image_reference' in item:
            return deepcopy(item['image_reference'])
        if 'image_references' in item:
            return deepcopy(item['image_references'].get(shot.id))
    return None


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
