import base64
import json
import mimetypes
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .runway_video import generate_runway_clip, runway_status
from .settings import settings


TOKENS = {
    "{{PROMPT}}": "prompt",
    "{{INPUT_IMAGE}}": "input_image",
    "{{WIDTH}}": "width",
    "{{HEIGHT}}": "height",
    "{{FRAME_COUNT}}": "frame_count",
    "{{FPS}}": "fps",
    "{{SEED}}": "seed",
}


def frame_count(duration_ms: int, fps: int) -> int:
    requested = max(5, round(duration_ms / 1000 * fps))
    return max(5, requested - ((requested - 1) % 4))


def render_workflow(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render_workflow(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [render_workflow(item, replacements) for item in value]
    if not isinstance(value, str):
        return value
    if value in replacements:
        return replacements[value]
    result = value
    for token, replacement in replacements.items():
        result = result.replace(token, str(replacement))
    return result


def find_video_output(history: dict[str, Any]) -> dict[str, str] | None:
    for node in history.get("outputs", {}).values():
        if not isinstance(node, dict):
            continue
        for key in ("videos", "gifs", "images"):
            entries = node.get(key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                filename = str(entry.get("filename", ""))
                if Path(filename).suffix.lower() in {".mp4", ".webm", ".mov"}:
                    return {
                        "filename": filename,
                        "subfolder": str(entry.get("subfolder", "")),
                        "type": str(entry.get("type", "output")),
                    }
    return None


def comfyui_status() -> tuple[bool, bool, str]:
    configured = settings.comfyui_workflow_path.is_file()
    try:
        response = httpx.get(
            f"{settings.comfyui_base_url.rstrip('/')}/system_stats", timeout=1
        )
        available = response.is_success
    except httpx.HTTPError:
        available = False
    if not configured:
        detail = "Add an API-format ComfyUI image-to-video workflow"
    elif not available:
        detail = "Workflow configured; ComfyUI is not running"
    else:
        detail = "Ready for local image-to-video generation"
    return configured, available, detail


def flowkit_status() -> tuple[bool, bool, str]:
    base_url = settings.flowkit_base_url.rstrip("/")
    configured = bool(base_url)
    if not configured:
        return False, False, "FlowKit URL is not configured"
    try:
        response = httpx.get(f"{base_url}/status", timeout=2)
        response.raise_for_status()
    except Exception as exc:
        return True, False, f"FlowKit unavailable: {exc}"
    return True, True, "Ready for Google Flow video generation through FlowKit"


def generate_flowkit_clip(
    image_path: Path,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    scene_id: str | None = None,
) -> tuple[str, str, str]:
    if not scene_id:
        raise RuntimeError("FlowKit video generation requires a scene_id")

    base_url = settings.flowkit_base_url.rstrip("/")
    flow_aspect_ratio = (
        "VIDEO_ASPECT_RATIO_PORTRAIT"
        if aspect_ratio == "9:16"
        else "VIDEO_ASPECT_RATIO_LANDSCAPE"
    )
    requested_duration_s = max(1, round(duration_ms / 1000))
    if (settings.flowkit_model_family or "veo").strip().lower() == "omni_flash":
        duration_s = min((4, 6, 8, 10), key=lambda value: (abs(value - requested_duration_s), value))
    else:
        duration_s = requested_duration_s
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    with httpx.Client(timeout=settings.flowkit_timeout_seconds) as client:
        upload = client.post(
            f"{base_url}/upload-image",
            json={
                "image_base64": image_base64,
                "mime_type": mime_type,
                "file_name": image_path.name,
                "project_id": settings.flowkit_project_id,
            },
        )
        upload.raise_for_status()
        uploaded = upload.json()
        media_id = uploaded.get("media_id")
        project_id = uploaded.get("project_id") or settings.flowkit_project_id
        if not media_id:
            raise RuntimeError(f"FlowKit image upload returned no media_id: {uploaded}")

        payload = {
            "start_image_media_id": media_id,
            "prompt": prompt,
            "project_id": project_id,
            "scene_id": scene_id,
            "duration_s": duration_s,
            "resolution": "720p",
            "aspect_ratio": flow_aspect_ratio,
            "model_family": settings.flowkit_model_family,
        }
        submit = client.post(f"{base_url}/generate-video", json=payload)
        try:
            submit.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"FlowKit video generation failed ({submit.status_code}): {submit.text}"
            ) from exc
        submitted = submit.json()

        polling = submitted.get("flowkitPolling") or {}
        operations = submitted.get("operations") or polling.get("operations") or []
        workflows = submitted.get("workflows") or polling.get("workflows")
        project_id = (
            submitted.get("project_id")
            or polling.get("project_id")
            or project_id
            or settings.flowkit_project_id
        )
        if not operations and not workflows:
            raise RuntimeError(f"FlowKit returned no operation/workflow: {submitted}")

        deadline = time.monotonic() + settings.flowkit_timeout_seconds
        while time.monotonic() < deadline:
            status_payload = {
                "operations": operations,
                "project_id": project_id,
                "include_encoded_video": True,
            }
            if workflows:
                status_payload["workflows"] = workflows
            status = client.post(f"{base_url}/check-status", json=status_payload)
            status.raise_for_status()
            body = status.json()

            results = (
                body.get("results")
                or body.get("videos")
                or body.get("workflows")
                or body.get("operations")
                or []
            )
            if isinstance(results, dict):
                results = [results]
            found_video = False
            for result in results:
                if not isinstance(result, dict):
                    continue
                media_info = result.get("media") if isinstance(result.get("media"), dict) else {}
                operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
                metadata = operation.get("metadata") if isinstance(operation.get("metadata"), dict) else {}
                operation_video = metadata.get("video") if isinstance(metadata.get("video"), dict) else {}
                encoded = (
                    result.get("encoded_video")
                    or result.get("video_base64")
                    or result.get("encodedVideo")
                    or media_info.get("encoded_video")
                    or media_info.get("video_base64")
                    or media_info.get("encodedVideo")
                )
                if encoded:
                    content = base64.b64decode(encoded)
                    filename = f"clip-{uuid.uuid4()}.mp4"
                    (settings.media_dir / filename).write_bytes(content)
                    return filename, "video/mp4", "flowkit"
                url = (
                    result.get("video_url")
                    or result.get("url")
                    or result.get("media_url")
                    or media_info.get("video_url")
                    or media_info.get("url")
                    or media_info.get("media_url")
                    or operation_video.get("fifeUrl")
                    or operation_video.get("videoUrl")
                    or operation_video.get("url")
                )
                if url:
                    found_video = True
                    media = client.get(url)
                    media.raise_for_status()
                    filename = f"clip-{uuid.uuid4()}.mp4"
                    (settings.media_dir / filename).write_bytes(media.content)
                    return filename, media.headers.get("content-type", "video/mp4"), "flowkit"

            if body.get("done") is True and not found_video:
                raise RuntimeError(f"FlowKit generation completed without video: {body}")
            time.sleep(2)

    raise TimeoutError("FlowKit video generation timed out")


def video_provider_status() -> tuple[str, str, bool, bool, str, str]:
    """Return id, label, configured, available, model, detail."""
    provider = (settings.video_provider or "runway").strip().lower()
    if provider == "comfyui":
        configured, available, detail = comfyui_status()
        return "comfyui", "ComfyUI", configured, available, "workflow", detail
    if provider == "flowkit":
        configured, available, detail = flowkit_status()
        return "flowkit", "Google Flow (FlowKit)", configured, available, settings.flowkit_model_family, detail
    configured, available, detail = runway_status()
    return "runway", "Runway", configured, available, settings.runway_model, detail


def generate_shot_clip(
    image_path: Path,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    scene_id: str | None = None,
) -> tuple[str, str, str]:
    provider = (settings.video_provider or "runway").strip().lower()
    if provider == "flowkit":
        return generate_flowkit_clip(
            image_path,
            prompt,
            duration_ms,
            aspect_ratio,
            scene_id=scene_id,
        )
    if provider == "runway":
        return generate_runway_clip(image_path, prompt, duration_ms, aspect_ratio)
    return generate_comfyui_clip(image_path, prompt, duration_ms, aspect_ratio)


def generate_comfyui_clip(
    image_path: Path,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
) -> tuple[str, str, str]:
    if not settings.comfyui_workflow_path.is_file():
        raise RuntimeError(
            f"ComfyUI workflow not found: {settings.comfyui_workflow_path}"
        )

    base_url = settings.comfyui_base_url.rstrip("/")
    width, height = (480, 832) if aspect_ratio == "9:16" else (832, 480)
    fps = settings.video_fps
    uploaded_name = f"cutline-{uuid.uuid4()}{image_path.suffix.lower()}"

    with httpx.Client(timeout=httpx.Timeout(60, connect=3)) as client:
        with image_path.open("rb") as source:
            upload = client.post(
                f"{base_url}/upload/image",
                files={"image": (uploaded_name, source, mimetypes.guess_type(str(image_path))[0])},
                data={"type": "input", "overwrite": "true"},
            )
        upload.raise_for_status()
        uploaded = upload.json()
        input_name = "/".join(
            part for part in (uploaded.get("subfolder"), uploaded.get("name")) if part
        ) or uploaded_name

        template = json.loads(settings.comfyui_workflow_path.read_text())
        replacements = {
            token: {
                "prompt": prompt,
                "input_image": input_name,
                "width": width,
                "height": height,
                "frame_count": frame_count(duration_ms, fps),
                "fps": fps,
                "seed": random.randint(0, 2**31 - 1),
            }[name]
            for token, name in TOKENS.items()
        }
        workflow = render_workflow(template, replacements)
        queued = client.post(
            f"{base_url}/prompt",
            json={"prompt": workflow, "client_id": str(uuid.uuid4())},
        )
        queued.raise_for_status()
        prompt_id = queued.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt id")

        deadline = time.monotonic() + settings.comfyui_timeout_seconds
        descriptor = None
        while time.monotonic() < deadline:
            history_response = client.get(f"{base_url}/history/{prompt_id}")
            history_response.raise_for_status()
            record = history_response.json().get(prompt_id)
            if record:
                status = record.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError("ComfyUI workflow failed")
                descriptor = find_video_output(record)
                if descriptor:
                    break
            time.sleep(2)
        if not descriptor:
            raise TimeoutError("ComfyUI did not produce a video before the timeout")

        result = client.get(f"{base_url}/view", params=descriptor)
        result.raise_for_status()
        extension = Path(descriptor["filename"]).suffix.lower()
        mime_type = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
        }[extension]
        filename = f"clip-{uuid.uuid4()}{extension}"
        (settings.media_dir / filename).write_bytes(result.content)
        return filename, mime_type, "comfyui"

