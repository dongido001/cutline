"""Runway ML image-to-video provider."""

from __future__ import annotations

import base64
import mimetypes
import time
import uuid
from pathlib import Path

import httpx

from .settings import settings
from .timing import runway_clip_duration_seconds

RUNWAY_API = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.runway_api_key}",
        "Content-Type": "application/json",
        "X-Runway-Version": RUNWAY_VERSION,
    }


def runway_status() -> tuple[bool, bool, str]:
    configured = bool(settings.runway_api_key.strip())
    if not configured:
        return False, False, "Set MOVIE_RUNWAY_API_KEY to enable Runway image-to-video"
    try:
        response = httpx.get(
            f"{RUNWAY_API}/organization",
            headers=_headers(),
            timeout=5,
        )
        available = response.is_success
        if response.status_code in {401, 403}:
            return True, False, "Runway API key rejected"
        if not available:
            return True, False, f"Runway unreachable ({response.status_code})"
        return True, True, f"Ready · {settings.runway_model}"
    except httpx.HTTPError:
        # Key present is enough to queue; network may be flaky during status checks.
        return True, True, f"Configured · {settings.runway_model}"


def _ratio(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return settings.runway_ratio_portrait
    return settings.runway_ratio_landscape


def _duration_seconds(duration_ms: int) -> int:
    cap = 5 if int(settings.runway_max_duration or 5) <= 5 else 10
    if duration_ms > cap * 1000:
        raise ValueError(
            f"Shot needs {duration_ms / 1000:g}s, but Runway is capped at {cap}s. "
            "Split the shot, increase MOVIE_RUNWAY_MAX_DURATION to 10, or use another provider."
        )
    return runway_clip_duration_seconds(duration_ms, max_seconds=cap)


def _data_uri(image_path: Path) -> str:
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def generate_runway_clip(
    image_path: Path,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
) -> tuple[str, str, str]:
    if not settings.runway_api_key.strip():
        raise RuntimeError("MOVIE_RUNWAY_API_KEY is not configured")
    if not image_path.is_file():
        raise RuntimeError(f"Source image missing: {image_path}")

    payload = {
        "model": settings.runway_model,
        "promptImage": _data_uri(image_path),
        "promptText": prompt.strip()[:1000],
        "ratio": _ratio(aspect_ratio),
        "duration": _duration_seconds(duration_ms),
    }
    with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client:
        created = client.post(
            f"{RUNWAY_API}/image_to_video",
            headers=_headers(),
            json=payload,
        )
        if created.is_error:
            detail = created.text
            try:
                body = created.json()
                detail = str(body.get("error") or body.get("message") or detail)
            except Exception:
                pass
            raise RuntimeError(f"Runway rejected the request ({created.status_code}): {detail}")
        task_id = created.json().get("id")
        if not task_id:
            raise RuntimeError("Runway did not return a task id")

        deadline = time.monotonic() + settings.runway_timeout_seconds
        output_url: str | None = None
        while time.monotonic() < deadline:
            status_response = client.get(
                f"{RUNWAY_API}/tasks/{task_id}",
                headers=_headers(),
            )
            status_response.raise_for_status()
            body = status_response.json()
            state = str(body.get("status") or "").upper()
            if state in {"SUCCEEDED", "COMPLETED"}:
                outputs = body.get("output") or []
                if isinstance(outputs, list) and outputs:
                    output_url = str(outputs[0])
                break
            if state in {"FAILED", "CANCELLED"}:
                detail = body.get("failure") or body.get("error") or state
                raise RuntimeError(f"Runway task failed: {detail}")
            time.sleep(3)
        if not output_url:
            raise TimeoutError("Runway did not produce a video before the timeout")

        video = client.get(output_url)
        video.raise_for_status()
        filename = f"clip-{uuid.uuid4()}.mp4"
        (settings.media_dir / filename).write_bytes(video.content)
        return filename, "video/mp4", "runway"
