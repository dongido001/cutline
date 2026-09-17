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


def video_provider_status() -> tuple[str, str, bool, bool, str, str]:
    """Return id, label, configured, available, model, detail."""
    provider = (settings.video_provider or "runway").strip().lower()
    if provider == "comfyui":
        configured, available, detail = comfyui_status()
        return "comfyui", "ComfyUI", configured, available, "workflow", detail
    configured, available, detail = runway_status()
    return "runway", "Runway", configured, available, settings.runway_model, detail


def generate_shot_clip(
    image_path: Path,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
) -> tuple[str, str, str]:
    provider = (settings.video_provider or "runway").strip().lower()
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

