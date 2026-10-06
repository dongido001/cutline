"""Publishing plans and provider dispatch for YouTube/Facebook releases."""
from __future__ import annotations

import uuid
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

from sqlalchemy.orm import Session

from .models import Project, Scene, Shot, SocialToken
from .schemas import TimelineDocument
from .settings import settings
from .timeline import append_missing_shots, ensure_timeline, promote_available_clips
from .timeline_render import assemble_timeline, resolve_shot_media

PublishPlatform = Literal["youtube", "facebook"]
PublishScope = Literal["final", "scenes", "shots"]

PLATFORM_LABELS = {"youtube": "YouTube", "facebook": "Facebook"}
PRIVACY = {
    "youtube": {"private", "unlisted", "public"},
    "facebook": {"self", "friends", "public"},
}


@dataclass(frozen=True)
class PublishAsset:
    id: str
    scope: str
    label: str
    filename: str
    path: Path
    duration_ms: int
    aspect_ratio: str
    variant: str
    source_ref: str | None = None

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


def provider_statuses(db: Session | None = None) -> list[dict[str, Any]]:
    if db is None:
        try:
            from .database import SessionLocal
            with SessionLocal() as session:
                return provider_statuses(session)
        except Exception:
            pass
    mode = settings.publish_mode.strip().lower()
    dry_run = mode in {"mock", "dry_run", "dry-run", "local"}

    def _yt_token() -> str:
        return _get_youtube_token(db)

    def _fb_token() -> str:
        if db:
            tok = db.query(SocialToken).filter_by(platform="facebook").first()
            if tok and not tok.is_expired:
                return tok.access_token
        return settings.facebook_page_access_token

    def _yt_account() -> str | None:
        if db:
            tok = db.query(SocialToken).filter_by(platform="youtube").first()
            if tok and tok.account_name:
                return tok.account_name
        return None

    def _fb_account() -> str | None:
        if db:
            tok = db.query(SocialToken).filter_by(platform="facebook").first()
            if tok and tok.account_name:
                return tok.account_name
        return None

    yt_tok = _yt_token()
    fb_tok = _fb_token()
    yt_account = _yt_account()
    fb_account = _fb_account()

    return [
        {
            "id": "youtube",
            "label": "YouTube",
            "configured": dry_run or bool(yt_tok),
            "available": dry_run or bool(yt_tok),
            "connected": bool(yt_tok) and not dry_run,
            "account_name": yt_account,
            "detail": (
                "Dry-run publishing enabled" if dry_run
                else (f"Connected as {yt_account}" if yt_account
                      else ("Token configured" if yt_tok
                            else "Click Connect to link your YouTube channel"))
            ),
            "privacy_options": ["private", "unlisted", "public"],
        },
        {
            "id": "facebook",
            "label": "Facebook",
            "configured": dry_run or bool(fb_tok),
            "available": dry_run or bool(fb_tok),
            "connected": bool(fb_tok) and not dry_run,
            "account_name": fb_account,
            "detail": (
                "Dry-run publishing enabled" if dry_run
                else (f"Connected as {fb_account}" if fb_account
                      else ("Token configured" if fb_tok
                            else "Click Connect to link your Facebook Page"))
            ),
            "privacy_options": ["self", "friends", "public"],
        },
    ]


def platform_status(platform: str, db: Session | None = None) -> dict[str, Any]:
    statuses = {item["id"]: item for item in provider_statuses(db)}
    if platform not in statuses:
        raise RuntimeError(f"Unsupported publish platform: {platform}")
    return statuses[platform]


def publish_variant_to_aspect(variant: str) -> str:
    return "9:16" if variant == "shorts" else "16:9"


def _all_shots(project: Project) -> list[Shot]:
    return [shot for version in project.versions for scene in version.scenes for shot in scene.shots]


def _shot_clip_asset(shot: Shot, variant: str, project_aspect: str) -> PublishAsset | None:
    aspect = publish_variant_to_aspect(variant)
    media = resolve_shot_media(shot, "shot_clip", aspect, "auto")
    if not media or not media.path.is_file() or not media.is_clip:
        return None
    return PublishAsset(
        id=f"shot:{shot.id}",
        scope="shot",
        label=shot.title or f"Shot {shot.order_index + 1}",
        filename=media.path.name,
        path=media.path,
        duration_ms=int(shot.duration_ms or 0),
        aspect_ratio=media.aspect_ratio or project_aspect,
        variant=variant,
        source_ref=shot.id,
    )


def final_asset(project: Project, variant: str) -> PublishAsset | None:
    export = next((item for item in project.variant_exports if item.variant == variant), None)
    if not export:
        return None
    path = settings.media_dir / export.filename
    if not path.is_file():
        return None
    return PublishAsset(
        id=f"final:{variant}",
        scope="final",
        label=f"{project.title} · {'Shorts' if variant == 'shorts' else 'Landscape'} final cut",
        filename=export.filename,
        path=path,
        duration_ms=export.duration_ms,
        aspect_ratio=publish_variant_to_aspect(variant),
        variant=variant,
        source_ref=export.id,
    )


def shot_assets(project: Project, variant: str) -> list[PublishAsset]:
    return [asset for shot in _all_shots(project) if (asset := _shot_clip_asset(shot, variant, project.aspect_ratio))]


def scene_asset_plan(project: Project, variant: str) -> list[dict[str, Any]]:
    aspect = publish_variant_to_aspect(variant)
    result = []
    for version in project.versions:
        for scene in version.scenes:
            shots = list(scene.shots)
            ready = [shot for shot in shots if _shot_clip_asset(shot, variant, project.aspect_ratio)]
            duration = sum(int(shot.duration_ms or 0) for shot in shots)
            result.append({
                "id": f"scene:{scene.id}",
                "scope": "scene",
                "label": scene.title or f"Scene {scene.order_index + 1}",
                "source_ref": scene.id,
                "variant": variant,
                "aspect_ratio": aspect,
                "duration_ms": duration,
                "ready": bool(shots) and len(ready) == len(shots),
                "missing_count": max(0, len(shots) - len(ready)),
                "url": None,
            })
    return result


def _asset_dict(item: PublishAsset) -> dict[str, Any]:
    return {
        "id": item.id,
        "scope": item.scope,
        "label": item.label,
        "source_ref": item.source_ref,
        "variant": item.variant,
        "aspect_ratio": item.aspect_ratio,
        "duration_ms": item.duration_ms,
        "ready": True,
        "missing_count": 0,
        "url": item.url,
    }


def build_publish_plan(project: Project, variant: str, db: Session | None = None) -> dict[str, Any]:
    if variant not in {"landscape", "shorts"}:
        raise RuntimeError("variant must be landscape or shorts")
    final = final_asset(project, variant)
    shots = shot_assets(project, variant)
    return {
        "variant": variant,
        "aspect_ratio": publish_variant_to_aspect(variant),
        "providers": provider_statuses(db),
        "final": [_asset_dict(final)] if final else [],
        "scenes": scene_asset_plan(project, variant),
        "shots": [_asset_dict(item) for item in shots],
    }


def validate_publish_request(payload: dict[str, Any]) -> None:
    platforms = payload.get("platforms") or []
    if not platforms:
        raise RuntimeError("Choose at least one publishing destination")
    unknown = [item for item in platforms if item not in PLATFORM_LABELS]
    if unknown:
        raise RuntimeError(f"Unsupported publish destination: {', '.join(unknown)}")
    if payload.get("scope") not in {"final", "scenes", "shots"}:
        raise RuntimeError("Choose final cut, scenes, or shots")
    if payload.get("variant") not in {"landscape", "shorts"}:
        raise RuntimeError("Choose a valid publish format")
    privacy = payload.get("privacy") or {}
    for platform in platforms:
        value = privacy.get(platform)
        if value not in PRIVACY[platform]:
            raise RuntimeError(f"Choose a valid {PLATFORM_LABELS[platform]} visibility")
    schedule = payload.get("scheduled_at")
    if schedule:
        when = datetime.fromisoformat(str(schedule).replace("Z", "+00:00"))
        if when <= datetime.now(timezone.utc):
            raise RuntimeError("Scheduled publish time must be in the future")


def _selected_ids(payload: dict[str, Any]) -> set[str] | None:
    selected = payload.get("selected_asset_ids")
    if not selected:
        return None
    return {str(item) for item in selected}


def _timeline_for_scene(project: Project, scene_id: str, variant: str, db: Session) -> TimelineDocument:
    timeline = ensure_timeline(project, db)
    promote_available_clips(project, db)
    append_missing_shots(project, db)
    raw = timeline.shorts_document if variant == "shorts" and timeline.shorts_document else timeline.document
    document = TimelineDocument.model_validate(raw)
    shot_ids = {shot.id for version in project.versions for scene in version.scenes if scene.id == scene_id for shot in scene.shots}
    primary = [item for item in document.primary if item.source and item.source.ref_id in shot_ids]
    if not primary:
        raise RuntimeError("Scene has no publishable timeline media")
    doc = document.model_copy(deep=True)
    doc.primary = primary
    doc.image_overlays = []
    doc.text_overlays = []
    return doc


def _render_scene_asset(project: Project, scene_id: str, variant: str, db: Session) -> PublishAsset:
    scene = next((scene for version in project.versions for scene in version.scenes if scene.id == scene_id), None)
    if not scene:
        raise RuntimeError("Scene not found")
    result = assemble_timeline(project, _timeline_for_scene(project, scene_id, variant, db), variant, db)
    return PublishAsset(
        id=f"scene:{scene.id}",
        scope="scene",
        label=scene.title or f"Scene {scene.order_index + 1}",
        filename=result.filename,
        path=settings.media_dir / result.filename,
        duration_ms=result.duration_ms,
        aspect_ratio=publish_variant_to_aspect(variant),
        variant=variant,
        source_ref=scene.id,
    )


def collect_publish_assets(project: Project, payload: dict[str, Any], db: Session) -> list[PublishAsset]:
    scope = payload["scope"]
    variant = payload["variant"]
    selected = _selected_ids(payload)
    if scope == "final":
        asset = final_asset(project, variant)
        assets = [asset] if asset else []
    elif scope == "shots":
        assets = shot_assets(project, variant)
    else:
        if not selected:
            raise RuntimeError("Select at least one scene to publish")
        scene_ids = [item.removeprefix("scene:") for item in selected if item.startswith("scene:")]
        assets = [_render_scene_asset(project, scene_id, variant, db) for scene_id in scene_ids]
    if selected and scope != "scenes":
        assets = [item for item in assets if item.id in selected]
    if not assets:
        raise RuntimeError("No publishable videos are ready for this selection")
    return assets


def _segment_title(base: str, asset: PublishAsset, index: int, total: int) -> str:
    return base if total <= 1 else f"{base} — {asset.label} ({index}/{total})"


def _dry_run_upload(platform: str, asset: PublishAsset, metadata: dict[str, Any]) -> dict[str, Any]:
    slug = uuid.uuid5(uuid.NAMESPACE_URL, f"cutline:{platform}:{asset.filename}:{metadata.get('title')}")
    return {
        "platform": platform,
        "status": "completed",
        "external_id": str(slug),
        "external_url": f"https://{platform}.example.local/cutline/{slug}",
        "message": f"Dry-run published {asset.label} to {PLATFORM_LABELS[platform]}",
    }



def _get_youtube_token(db: Session | None = None) -> str:
    from .database import SessionLocal
    def _fetch(s: Session) -> str:
        tok = s.query(SocialToken).filter_by(platform="youtube").first()
        if not tok:
            return ""
        if tok.is_expired and tok.refresh_token:
            import httpx
            try:
                resp = httpx.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "refresh_token": tok.refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"YouTube token refresh failed with HTTP {resp.status_code}")
                data = resp.json()
                tok.access_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                tok.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                s.commit()
            except Exception as exc:
                raise RuntimeError("YouTube authorization expired and could not be refreshed. Reconnect YouTube.") from exc
        if tok.is_expired:
            raise RuntimeError("YouTube authorization expired. Reconnect YouTube.")
        return tok.access_token

    if db:
        token = _fetch(db)
    else:
        with SessionLocal() as s:
            token = _fetch(s)
    return token or settings.youtube_access_token or ""


def _get_facebook_token_and_page(db: Session | None = None) -> tuple[str, str]:
    from .database import SessionLocal
    def _fetch(s: Session):
        tok = s.query(SocialToken).filter_by(platform="facebook").first()
        if tok and not tok.is_expired:
            return tok.access_token, (tok.extra.get("page_id") or tok.account_id or settings.facebook_page_id)
        return "", ""

    if db:
        fb_token, fb_page = _fetch(db)
    else:
        with SessionLocal() as s:
            fb_token, fb_page = _fetch(s)
    return (
        fb_token or settings.facebook_page_access_token or "",
        fb_page or settings.facebook_page_id or ""
    )


def _youtube_upload(asset: PublishAsset, metadata: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    access_token = _get_youtube_token(db)
    if not access_token:
        raise RuntimeError("YouTube is not connected. Click 'Connect YouTube' to link your account.")
    status_privacy = metadata.get("privacy") or "private"
    if metadata.get("scheduled_at"):
        status_privacy = "private"
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata.get("description") or "",
            "tags": metadata.get("tags") or [],
            **({"channelId": settings.youtube_channel_id} if settings.youtube_channel_id else {}),
        },
        "status": {
            "privacyStatus": status_privacy,
            "selfDeclaredMadeForKids": bool(metadata.get("made_for_kids", False)),
            **({"publishAt": metadata["scheduled_at"]} if metadata.get("scheduled_at") and status_privacy == "private" else {}),
        },
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(asset.path.stat().st_size),
    }
    with httpx.Client(timeout=httpx.Timeout(900, connect=15)) as client:
        init = client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=headers,
            json=body,
        )
        if init.is_error:
            try:
                err_data = init.json()
                msg = err_data.get("error", {}).get("message")
                reason = (err_data.get("error", {}).get("errors") or [{}])[0].get("reason", "")
                raise RuntimeError(f"YouTube Error: {msg} ({reason})" if reason else f"YouTube Error: {msg}")
            except Exception as parse_err:
                if isinstance(parse_err, RuntimeError):
                    raise
                init.raise_for_status()
        location = init.headers.get("Location")
        if not location:
            raise RuntimeError("YouTube did not return an upload URL")
        upload = client.put(location, headers={"Content-Type": "video/mp4"}, content=asset.path.read_bytes())
        upload.raise_for_status()
        payload = upload.json()
    video_id = payload.get("id")
    return {
        "platform": "youtube",
        "status": "completed",
        "external_id": video_id,
        "external_url": f"https://youtu.be/{video_id}" if video_id else None,
        "message": "Published to YouTube",
        "response": payload,
    }


def _facebook_upload(asset: PublishAsset, metadata: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    # Prefer DB-stored token (has page_id in extra), fall back to env settings
    fb_token = ""
    fb_page_id = settings.facebook_page_id
    if db:
        tok = db.query(SocialToken).filter_by(platform="facebook").first()
        if tok and not tok.is_expired:
            fb_token = tok.access_token
            fb_page_id = tok.extra.get("page_id") or tok.account_id or fb_page_id
    if not fb_token:
        fb_token = settings.facebook_page_access_token
    if not fb_token or not fb_page_id:
        raise RuntimeError("Facebook is not connected. Click 'Connect Facebook' to link your Page.")
    data: dict[str, Any] = {
        "access_token": fb_token,
        "title": metadata["title"],
        "description": metadata.get("description") or "",
        "published": "true",
    }
    if metadata.get("scheduled_at"):
        when = datetime.fromisoformat(str(metadata["scheduled_at"]).replace("Z", "+00:00"))
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(when.timestamp()))
    privacy = metadata.get("privacy")
    if privacy:
        data["privacy"] = {"value": {"self": "SELF", "friends": "ALL_FRIENDS", "public": "EVERYONE"}.get(str(privacy), "SELF")}
    with asset.path.open("rb") as handle:
        response = httpx.post(
            f"https://graph-video.facebook.com/v20.0/{fb_page_id}/videos",
            data=data,
            files={"source": (asset.filename, handle, "video/mp4")},
            timeout=httpx.Timeout(900, connect=15),
        )
    response.raise_for_status()
    payload = response.json()
    video_id = payload.get("id")
    return {
        "platform": "facebook",
        "status": "completed",
        "external_id": video_id,
        "external_url": f"https://www.facebook.com/{video_id}" if video_id else None,
        "message": "Published to Facebook",
        "response": payload,
    }


def upload_asset(platform: str, asset: PublishAsset, metadata: dict[str, Any], db: Session | None = None) -> dict[str, Any]:
    status = platform_status(platform, db)
    if not status["available"]:
        raise RuntimeError(status["detail"])
    if settings.publish_mode.strip().lower() in {"mock", "dry_run", "dry-run", "local"}:
        return _dry_run_upload(platform, asset, metadata)
    if platform == "youtube":
        return _youtube_upload(asset, metadata, db)
    if platform == "facebook":
        return _facebook_upload(asset, metadata, db)
    raise RuntimeError(f"Unsupported publish platform: {platform}")


def publish_assets(assets: Iterable[PublishAsset], payload: dict[str, Any], progress=None, db: Session | None = None) -> list[dict[str, Any]]:
    assets = list(assets)
    total_steps = max(1, len(assets) * len(payload["platforms"]))
    step = 0
    results: list[dict[str, Any]] = []

    base_scheduled_str = payload.get("scheduled_at")
    base_dt = None
    if base_scheduled_str:
        try:
            base_dt = datetime.fromisoformat(base_scheduled_str.replace("Z", "+00:00"))
        except Exception:
            base_dt = None
    interval_hours = float(payload.get("schedule_interval_hours") or 0)

    for index, asset in enumerate(assets, 1):
        asset_scheduled_at = None
        if base_dt:
            if interval_hours > 0 and len(assets) > 1:
                cur_dt = base_dt + timedelta(hours=interval_hours * (index - 1))
            else:
                cur_dt = base_dt
            asset_scheduled_at = cur_dt.isoformat().replace("+00:00", "Z")

        for platform in payload["platforms"]:
            step += 1
            metadata = {
                "title": _segment_title(payload["title"], asset, index, len(assets)),
                "description": payload.get("description") or "",
                "tags": payload.get("tags") or [],
                "privacy": (payload.get("privacy") or {}).get(platform),
                "scheduled_at": asset_scheduled_at,
                "made_for_kids": bool(payload.get("made_for_kids", False)),
                "platform_options": (payload.get("platform_options") or {}).get(platform, {}),
            }
            if progress:
                progress(step, total_steps, f"Publishing {asset.label} to {PLATFORM_LABELS[platform]}")
            try:
                outcome = upload_asset(platform, asset, metadata, db)
            except Exception as exc:
                outcome = {"platform": platform, "status": "failed", "message": str(exc)}
            outcome.update({
                "asset_id": asset.id,
                "asset_label": asset.label,
                "filename": asset.filename,
                "metadata": metadata,
            })
            results.append(outcome)
    return results