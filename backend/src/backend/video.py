import base64
import json
import mimetypes
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .media import normalize_conversation_url
from .runway_video import generate_runway_clip, runway_status
from .settings import settings
from .timing import flow_clip_duration_seconds


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


FLOWKIT_MODEL_PRESETS: list[dict[str, str]] = [
    {
        "id": "veo_lite",
        "label": "Veo 3.1 Lite · Standard",
        "short_label": "Veo Lite · Standard (Low credits)",
        "model_family": "veo",
        "flow_model_key": "veo_3_1_i2v_lite",
        "credit_note": "Works on Free/Pro accounts · lowest credit cost",
    },
    {
        "id": "omni_flash",
        "label": "Gemini Omni 1.1 Flash",
        "short_label": "Omni 1.1 Flash (4–10s)",
        "model_family": "omni_flash",
        "flow_model_key": "abra_i2v_*",
        "credit_note": "Works on Free/Pro accounts · ~10–20 credits/clip",
    },
    {
        "id": "veo_lite_relaxed",
        "label": "Veo 3.1 Lite · Relaxed (0 credits · Ultra only)",
        "short_label": "Veo Lite · 0 Credits (Ultra sub only)",
        "model_family": "veo",
        "flow_model_key": "veo_3_1_i2v_lite_low_priority",
        "credit_note": "Requires paid Google AI Ultra subscription",
    },
    {
        "id": "veo_fast",
        "label": "Veo 3.1 Fast · Ultra",
        "short_label": "Veo Fast · Ultra",
        "model_family": "veo",
        "flow_model_key": "veo_3_1_i2v_s_fast_ultra",
        "credit_note": "Fastest Veo queue · uses credits",
    },
]


def _flowkit_host() -> str:
    base_url = settings.flowkit_base_url.rstrip("/")
    if base_url.endswith("/api/flow"):
        return base_url[: -len("/api/flow")]
    return base_url


def get_active_flowkit_preset() -> dict[str, str]:
    preset_by_id = {item["id"]: item for item in FLOWKIT_MODEL_PRESETS}
    state_file = settings.data_dir / "flowkit_model.json"
    if state_file.is_file():
        try:
            saved = json.loads(state_file.read_text())
            preset_id = str(saved.get("preset") or "").strip()
            if preset_id in preset_by_id:
                return dict(preset_by_id[preset_id])
        except Exception:
            pass
    family = (settings.flowkit_model_family or "veo").strip().lower()
    if family == "omni_flash":
        return dict(preset_by_id["omni_flash"])
    return dict(preset_by_id["veo_lite"])


def _sync_flowkit_veo_model_key(flow_model_key: str) -> None:
    host = _flowkit_host()
    if not host or "flowkit.test" in host:
        return
    tier_block = {
        "frame_2_video": {
            "VIDEO_ASPECT_RATIO_LANDSCAPE": flow_model_key,
            "VIDEO_ASPECT_RATIO_PORTRAIT": flow_model_key,
        },
        "start_end_frame_2_video": {
            "VIDEO_ASPECT_RATIO_LANDSCAPE": flow_model_key,
            "VIDEO_ASPECT_RATIO_PORTRAIT": flow_model_key,
        },
    }
    try:
        httpx.patch(
            f"{host}/api/models",
            json={
                "video_models": {
                    "PAYGATE_TIER_TWO": tier_block,
                    "PAYGATE_TIER_ONE": tier_block,
                }
            },
            timeout=3.0,
        )
    except Exception:
        pass


def set_flowkit_model_preset(preset_id: str) -> dict[str, str]:
    normalized = (preset_id or "").strip().lower()
    if normalized == "veo":
        normalized = "veo_lite"
    preset_by_id = {item["id"]: item for item in FLOWKIT_MODEL_PRESETS}
    if normalized not in preset_by_id:
        raise ValueError(f"Unsupported FlowKit model preset: {preset_id}")
    preset = dict(preset_by_id[normalized])
    settings.flowkit_model_family = preset["model_family"]
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "flowkit_model.json").write_text(
            json.dumps(
                {
                    "preset": preset["id"],
                    "model_family": preset["model_family"],
                    "flow_model_key": preset["flow_model_key"],
                },
                indent=2,
            )
        )
    except Exception:
        pass
    from .settings import BACKEND_ROOT

    env_path = BACKEND_ROOT / ".env"
    if env_path.is_file():
        try:
            lines = env_path.read_text().splitlines()
            updated_lines: list[str] = []
            found = False
            for line in lines:
                if line.strip().startswith("MOVIE_FLOWKIT_MODEL_FAMILY="):
                    updated_lines.append(f"MOVIE_FLOWKIT_MODEL_FAMILY={preset['model_family']}")
                    found = True
                else:
                    updated_lines.append(line)
            if not found:
                updated_lines.append(f"MOVIE_FLOWKIT_MODEL_FAMILY={preset['model_family']}")
            env_path.write_text("\n".join(updated_lines) + "\n")
        except Exception:
            pass
    if preset["model_family"] == "veo":
        _sync_flowkit_veo_model_key(preset["flow_model_key"])
    return preset


def flowkit_status() -> tuple[bool, bool, str]:
    base_url = settings.flowkit_base_url.rstrip("/")
    configured = bool(base_url)
    if not configured:
        return False, False, "FlowKit URL is not configured"
    preset = get_active_flowkit_preset()
    try:
        response = httpx.get(f"{base_url}/status", timeout=2)
        response.raise_for_status()
    except Exception as exc:
        return True, False, f"FlowKit unavailable: {exc}"
    return True, True, f"{preset['label']} · {preset['credit_note']}"


def generate_flowkit_clip(
    image_path: Path | None,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    scene_id: str | None = None,
    flow_project_id: str | None = None,
    on_flow_project_id: Any = None,
    reference_image_paths: list[Path] | None = None,
    video_mode: str = "i2v",
) -> tuple[str, str, str]:
    if not scene_id:
        raise RuntimeError("FlowKit video generation requires a scene_id")

    is_r2v = (video_mode or "i2v").strip().lower() == "r2v" or image_path is None
    valid_refs = [p for p in (reference_image_paths or []) if p and p.is_file()]
    if image_path is not None and image_path.is_file():
        primary_path = image_path
        extra_paths = [p for p in valid_refs if p != primary_path][: (6 if is_r2v else 2)]
    elif valid_refs:
        primary_path = valid_refs[0]
        extra_paths = valid_refs[1:7]
    else:
        raise RuntimeError("At least one reference image or opening frame is required for video generation")

    base_url = settings.flowkit_base_url.rstrip("/")
    flow_aspect_ratio = (
        "VIDEO_ASPECT_RATIO_PORTRAIT"
        if aspect_ratio == "9:16"
        else "VIDEO_ASPECT_RATIO_LANDSCAPE"
    )
    active_preset = get_active_flowkit_preset()
    active_model_family = (
        "omni_flash"
        if is_r2v
        else (active_preset.get("model_family") or settings.flowkit_model_family or "veo").strip().lower()
    )
    if active_model_family == "veo":
        _sync_flowkit_veo_model_key(active_preset.get("flow_model_key") or "veo_3_1_i2v_lite")
    requested_duration_s = max(1, (duration_ms + 999) // 1000)
    duration_s = flow_clip_duration_seconds(duration_ms, model_family=active_model_family)
    mime_type = mimetypes.guess_type(primary_path.name)[0] or "image/png"
    image_base64 = base64.b64encode(primary_path.read_bytes()).decode("ascii")
    initial_project_id = (flow_project_id or "").strip() or settings.flowkit_project_id

    with httpx.Client(timeout=settings.flowkit_timeout_seconds) as client:
        upload = client.post(
            f"{base_url}/upload-image",
            json={
                "image_base64": image_base64,
                "mime_type": mime_type,
                "file_name": primary_path.name,
                "project_id": initial_project_id,
            },
        )
        if (
            getattr(upload, "status_code", 200) >= 400
            and flow_project_id
            and not settings.flowkit_project_id
        ):
            initial_project_id = ""
            upload = client.post(
                f"{base_url}/upload-image",
                json={
                    "image_base64": image_base64,
                    "mime_type": mime_type,
                    "file_name": primary_path.name,
                    "project_id": "",
                },
            )
        upload.raise_for_status()
        uploaded = upload.json()
        media_id = uploaded.get("media_id")
        project_id = uploaded.get("project_id") or initial_project_id
        if not media_id:
            raise RuntimeError(f"FlowKit image upload returned no media_id: {uploaded}")

        extra_media_ids: list[str] = []
        for ref_path in extra_paths:
            ref_mime = mimetypes.guess_type(ref_path.name)[0] or "image/png"
            ref_b64 = base64.b64encode(ref_path.read_bytes()).decode("ascii")
            ref_payload = {
                "image_base64": ref_b64,
                "mime_type": ref_mime,
                "file_name": ref_path.name,
                "project_id": project_id,
            }
            ref_upload = client.post(
                f"{base_url}/upload-image",
                json=ref_payload,
            )
            if getattr(ref_upload, "status_code", 200) in (502, 503):
                time.sleep(2)
                ref_upload = client.post(
                    f"{base_url}/upload-image",
                    json=ref_payload,
                )
            ref_upload.raise_for_status()
            ref_uploaded = ref_upload.json()
            ref_media_id = ref_uploaded.get("media_id")
            if ref_media_id:
                extra_media_ids.append(str(ref_media_id))

        attempt_extra_sets: list[list[str]] = [extra_media_ids]
        if len(extra_media_ids) > 2:
            attempt_extra_sets.append(extra_media_ids[:2])
        if len(extra_media_ids) > 1:
            attempt_extra_sets.append(extra_media_ids[:1])
        if extra_media_ids:
            attempt_extra_sets.append([])
        # Some Flow accounts accept an Omni Flash request, then discard the
        # operation as soon as it is polled (failed: [5]). Keep one final
        # opening-frame-only attempt available so that case can fall back to
        # standard Veo instead of asking the user to regenerate repeatedly.
        if active_model_family == "omni_flash" and not is_r2v:
            attempt_extra_sets.append([])

        deadline = time.monotonic() + settings.flowkit_timeout_seconds
        for attempt_idx, current_extra_ids in enumerate(attempt_extra_sets):
            has_fallback = attempt_idx + 1 < len(attempt_extra_sets)
            if is_r2v or current_extra_ids:
                r2v_family = "omni_flash" if is_r2v else active_model_family
                r2v_dur = flow_clip_duration_seconds(duration_ms, model_family=r2v_family)
                payload = {
                    "reference_media_ids": [media_id, *current_extra_ids],
                    "prompt": prompt,
                    "project_id": project_id,
                    "scene_id": scene_id,
                    "duration_s": r2v_dur,
                    "resolution": "720p",
                    "aspect_ratio": flow_aspect_ratio,
                    "model_family": r2v_family,
                }
                submit = client.post(f"{base_url}/generate-video-refs", json=payload)
            else:
                payload = {
                    "start_image_media_id": media_id,
                    "prompt": prompt,
                    "project_id": project_id,
                    "scene_id": scene_id,
                    "duration_s": duration_s,
                    "resolution": "720p",
                    "aspect_ratio": flow_aspect_ratio,
                    "model_family": active_model_family,
                }
                submit = client.post(f"{base_url}/generate-video", json=payload)
            try:
                submit.raise_for_status()
            except httpx.HTTPStatusError as exc:
                err_text = submit.text
                if "PUBLIC_ERROR_MODEL_ACCESS_DENIED" in err_text:
                    # 0-credit Relaxed mode (veo_3_1_i2v_lite_low_priority) requires Google AI Ultra.
                    # Try standard Veo Lite first, then 4s Omni Flash in case the account has a few credits left.
                    recovered = False
                    for fallback_family, fallback_key, fallback_dur in (
                        ("veo", "veo_3_1_i2v_lite", requested_duration_s),
                        ("omni_flash", "", 4),
                    ):
                        if fallback_key:
                            _sync_flowkit_veo_model_key(fallback_key)
                        retry_payload = {
                            "start_image_media_id": media_id,
                            "prompt": prompt,
                            "project_id": project_id,
                            "scene_id": scene_id,
                            "duration_s": fallback_dur,
                            "resolution": "720p",
                            "aspect_ratio": flow_aspect_ratio,
                            "model_family": fallback_family,
                        }
                        retry_res = client.post(f"{base_url}/generate-video", json=retry_payload)
                        if retry_res.is_success:
                            submit = retry_res
                            recovered = True
                            break
                        err_text = retry_res.text
                    if not recovered:
                        if "PUBLIC_ERROR_USER_QUOTA_REACHED" in err_text:
                            raise RuntimeError(
                                "Out of Google Flow credits on this account, and 0-credit Relaxed mode requires a paid Google AI Ultra subscription. Switch to another Google account at flow.google.com and click 'Sync Flow'."
                            ) from exc
                        raise RuntimeError(
                            "This Google account does not have access to 0-credit Relaxed mode (requires Google AI Ultra subscription). Switch to 'Omni 1.1 Flash' or switch to a Google account with available credits and click 'Sync Flow'."
                        ) from exc
                elif "PUBLIC_ERROR_USER_QUOTA_REACHED" in err_text:
                    if active_model_family != "veo":
                        active_model_family = "veo"
                        _sync_flowkit_veo_model_key("veo_3_1_i2v_lite_low_priority")
                        fallback_payload = {
                            "start_image_media_id": media_id,
                            "prompt": prompt,
                            "project_id": project_id,
                            "scene_id": scene_id,
                            "duration_s": requested_duration_s,
                            "resolution": "720p",
                            "aspect_ratio": flow_aspect_ratio,
                            "model_family": "veo",
                        }
                        submit = client.post(f"{base_url}/generate-video", json=fallback_payload)
                        try:
                            submit.raise_for_status()
                        except httpx.HTTPStatusError as fallback_exc:
                            raise RuntimeError(
                                "Out of Google Flow credits on this account (and 0-credit Relaxed mode requires a Google AI Ultra subscription). Switch to another Google account at flow.google.com and click 'Sync Flow'."
                            ) from fallback_exc
                    else:
                        raise RuntimeError(
                            "Out of Google Flow credits on this account. Switch to another Google account at flow.google.com and click 'Sync Flow', or wait for your credits to reset."
                        ) from exc
                elif "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in err_text or "unusual activity" in err_text.lower():
                    raise RuntimeError(
                        "Google Flow blocked the video request (PUBLIC_ERROR_UNUSUAL_ACTIVITY — stale reCAPTCHA token or anti-abuse cooldown on the active Flow browser tab). Refresh your flow.google.com tab in Chrome, wait ~30–60s, or click 'Sync Flow' and retry."
                    ) from exc
                elif has_fallback:
                    continue
                else:
                    raise RuntimeError(
                        f"FlowKit video generation failed ({submit.status_code}): {err_text}"
                    ) from exc
            submitted = submit.json()

            polling = submitted.get("flowkitPolling") or {}
            operations = submitted.get("operations") or polling.get("operations") or []
            workflows = submitted.get("workflows") or polling.get("workflows")
            project_id = (
                submitted.get("project_id")
                or polling.get("project_id")
                or project_id
                or initial_project_id
            )
            if project_id and callable(on_flow_project_id):
                on_flow_project_id(str(project_id))
            if not operations and not workflows:
                if has_fallback:
                    continue
                raise RuntimeError(f"FlowKit returned no operation/workflow: {submitted}")

            discarded_for_fallback = False
            while time.monotonic() < deadline:
                status_payload = {
                    "operations": operations,
                    "project_id": project_id,
                    "include_encoded_video": True,
                }
                if workflows:
                    status_payload["workflows"] = workflows
                try:
                    status = client.post(f"{base_url}/check-status", json=status_payload)
                    if getattr(status, "status_code", 200) in (502, 503):
                        time.sleep(2)
                        continue
                    status.raise_for_status()
                    body = status.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as poll_err:
                    if isinstance(poll_err, httpx.HTTPStatusError) and poll_err.response.status_code not in (502, 503):
                        raise
                    time.sleep(2)
                    continue

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
                    status_val = str(result.get("status") or "").upper()
                    err_val = result.get("error") or result.get("complaint")
                    if "FAIL" in status_val:
                        err_lower = str(err_val or "").lower()
                        is_discarded = (
                            "discarded operation" in err_lower
                            or "media not found" in err_lower
                            or "failed: [5]" in err_lower
                        )
                        if (
                            is_discarded
                            and active_model_family == "omni_flash"
                            and not is_r2v
                            and not current_extra_ids
                            and has_fallback
                        ):
                            active_model_family = "veo"
                            _sync_flowkit_veo_model_key("veo_3_1_i2v_lite")
                            duration_s = flow_clip_duration_seconds(
                                duration_ms,
                                model_family=active_model_family,
                            )
                            discarded_for_fallback = True
                            break
                        if has_fallback and is_discarded:
                            discarded_for_fallback = True
                            break
                        raise RuntimeError(f"Google Flow video generation failed: {err_val or status_val}")
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

                if discarded_for_fallback:
                    break
                if body.get("done") is True and not found_video:
                    if has_fallback:
                        break
                    raise RuntimeError(f"FlowKit generation completed without video: {body}")
                time.sleep(2)

    raise TimeoutError("FlowKit video generation timed out")


VIDEO_PROVIDERS: dict[str, str] = {
    "flowkit": "Google Flow (FlowKit)",
    "gemini_web": "Gemini Web (Veo)",
    "comfyui": "ComfyUI",
    "runway": "Runway",
}


def get_active_video_provider() -> str:
    state_file = settings.data_dir / "video_provider.json"
    if state_file.is_file():
        try:
            saved = json.loads(state_file.read_text())
            provider = str(saved.get("provider") or "").strip().lower()
            if provider in VIDEO_PROVIDERS:
                return provider
        except Exception:
            pass
    return (settings.video_provider or "runway").strip().lower()


def set_video_provider(provider: str) -> str:
    provider = provider.strip().lower()
    if provider not in VIDEO_PROVIDERS:
        raise ValueError(f"Unknown video provider: {provider}")
    state_file = settings.data_dir / "video_provider.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"provider": provider}, indent=2))
    settings.video_provider = provider
    from .settings import BACKEND_ROOT

    env_path = BACKEND_ROOT / ".env"
    if env_path.is_file():
        try:
            lines = env_path.read_text().splitlines()
            updated_lines: list[str] = []
            found = False
            for line in lines:
                if line.strip().startswith("MOVIE_VIDEO_PROVIDER="):
                    updated_lines.append(f"MOVIE_VIDEO_PROVIDER={provider}")
                    found = True
                else:
                    updated_lines.append(line)
            if not found:
                updated_lines.append(f"MOVIE_VIDEO_PROVIDER={provider}")
            env_path.write_text("\n".join(updated_lines) + "\n")
        except Exception:
            pass
    return provider


def gemini_web_video_status() -> tuple[bool, bool, str]:
    base_url = settings.gemini_web_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    videos_url = f"{base_url}/videos" if not base_url.endswith("/videos") else base_url
    try:
        response = httpx.get(videos_url, headers={"Authorization": "Bearer not-needed"}, timeout=2)
        response.raise_for_status()
    except Exception as exc:
        return True, False, f"Gemini Web video unavailable: {exc}"
    return True, True, "Veo 3.0 · 10s HD video with audio"


def video_provider_statuses() -> list[dict[str, Any]]:
    active = get_active_video_provider()
    items: list[dict[str, Any]] = []
    # 1. FlowKit
    fk_conf, fk_avail, fk_detail = flowkit_status()
    preset = get_active_flowkit_preset()
    items.append({
        "id": "flowkit",
        "label": "Google Flow (FlowKit)",
        "configured": fk_conf,
        "available": fk_avail,
        "detail": fk_detail,
        "selected": active == "flowkit",
        "model": preset["short_label"],
        "active_preset": preset["id"],
        "presets": [dict(p) for p in FLOWKIT_MODEL_PRESETS],
    })
    # 2. Gemini Web (Veo)
    gw_conf, gw_avail, gw_detail = gemini_web_video_status()
    items.append({
        "id": "gemini_web",
        "label": "Gemini Web (Veo)",
        "configured": gw_conf,
        "available": gw_avail,
        "detail": gw_detail,
        "selected": active == "gemini_web",
        "model": "Veo 3.0 (10s HD)",
    })
    # 3. ComfyUI
    cf_conf, cf_avail, cf_detail = comfyui_status()
    items.append({
        "id": "comfyui",
        "label": "ComfyUI",
        "configured": cf_conf,
        "available": cf_avail,
        "detail": cf_detail,
        "selected": active == "comfyui",
        "model": "workflow",
    })
    return items


def generate_gemini_web_clip(
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    on_conversation_url: Any = None,
) -> tuple[str, str, str]:
    base_url = settings.gemini_web_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    videos_url = f"{base_url}/videos" if not base_url.endswith("/videos") else base_url
    ar = "9:16" if aspect_ratio == "9:16" else "16:9"
    payload = {
        "prompt": prompt,
        "aspect_ratio": ar,
    }
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = client.post(
            videos_url,
            json=payload,
            headers={"Authorization": "Bearer not-needed"},
        )
        resp.raise_for_status()
        job = resp.json()
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"Gemini Web bridge did not return a video job ID: {job}")

        deadline = time.monotonic() + 600
        content_url = None
        cid = None
        while time.monotonic() < deadline:
            time.sleep(4)
            poll_resp = client.get(
                f"{videos_url}/{job_id}",
                headers={"Authorization": "Bearer not-needed"},
            )
            poll_resp.raise_for_status()
            status_data = poll_resp.json()
            if status_data.get("conversation_id") and not cid:
                cid = status_data["conversation_id"]
                if on_conversation_url:
                    try:
                        clean_cid = re.sub(r"^c_", "", str(cid).strip())
                        on_conversation_url(f"https://gemini.google.com/app/{clean_cid}")
                    except Exception:
                        pass
            status = status_data.get("status")
            if status == "completed":
                content_url = status_data.get("content_url")
                cid = status_data.get("conversation_id") or cid
                break
            elif status == "failed":
                err = status_data.get("error") or "Video generation failed"
                raise RuntimeError(f"Gemini Web video generation failed: {err}")

        if not content_url:
            raise TimeoutError("Gemini Web video generation timed out after 10 minutes")

        if content_url.startswith("http"):
            download_url = content_url
        else:
            host = base_url.split("/openai")[0] if "/openai" in base_url else base_url
            download_url = f"{host.rstrip('/')}/{content_url.lstrip('/')}"

        dl_resp = client.get(
            download_url,
            headers={"Authorization": "Bearer not-needed"},
        )
        dl_resp.raise_for_status()
        filename = f"clip-{uuid.uuid4()}.mp4"
        (settings.media_dir / filename).write_bytes(dl_resp.content)
        if cid and on_conversation_url:
            try:
                clean_cid = re.sub(r"^c_", "", str(cid).strip())
                on_conversation_url(f"https://gemini.google.com/app/{clean_cid}")
            except Exception:
                pass
        return filename, "video/mp4", "gemini_web"


def video_provider_status() -> tuple[str, str, bool, bool, str, str]:
    """Return id, label, configured, available, model, detail."""
    provider = get_active_video_provider()
    if provider == "gemini_web":
        configured, available, detail = gemini_web_video_status()
        return "gemini_web", "Gemini Web (Veo)", configured, available, "veo-3.0", detail
    if provider == "comfyui":
        configured, available, detail = comfyui_status()
        return "comfyui", "ComfyUI", configured, available, "workflow", detail
    if provider == "flowkit":
        configured, available, detail = flowkit_status()
        return "flowkit", "Google Flow (FlowKit)", configured, available, settings.flowkit_model_family, detail
    configured, available, detail = runway_status()
    return "runway", "Runway", configured, available, settings.runway_model, detail


def clip_source_image(shot, aspect_ratio: str):
    """Choose an opening frame only from the requested output format."""
    if aspect_ratio not in {"16:9", "9:16"}:
        raise ValueError("Aspect ratio must be 16:9 or 9:16")
    for items in (shot.continuity_frames, shot.image_references):
        match = next((item for item in items if item.aspect_ratio == aspect_ratio), None)
        if match:
            return match
    if aspect_ratio == shot.scene.script_version.project.aspect_ratio and shot.image:
        return shot.image
    return next((item for item in shot.media_variants
                 if item.media_type == "image" and item.aspect_ratio == aspect_ratio), None)


def generate_shot_clip(
    image_path: Path | None,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    scene_id: str | None = None,
    flow_project_id: str | None = None,
    on_flow_project_id: Any = None,
    reference_image_paths: list[Path] | None = None,
    video_mode: str = "i2v",
    on_conversation_url: Any = None,
) -> tuple[str, str, str]:
    dispatch_kwargs: dict[str, Any] = {
        "scene_id": scene_id,
        "flow_project_id": flow_project_id,
        "on_flow_project_id": on_flow_project_id,
        "reference_image_paths": reference_image_paths,
        "on_conversation_url": on_conversation_url,
    }
    if video_mode and video_mode != "i2v":
        dispatch_kwargs["video_mode"] = video_mode
    result = _dispatch_clip(
        image_path,
        prompt,
        duration_ms,
        aspect_ratio,
        **dispatch_kwargs,
    )
    filename, mime_type, _provider = result
    if mime_type == "video/mp4":
        from .clip_postprocess import postprocess_clip

        postprocess_clip(settings.media_dir / filename)
    return result


def _dispatch_clip(
    image_path: Path | None,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    scene_id: str | None = None,
    flow_project_id: str | None = None,
    on_flow_project_id: Any = None,
    reference_image_paths: list[Path] | None = None,
    video_mode: str = "i2v",
    on_conversation_url: Any = None,
) -> tuple[str, str, str]:
    provider = get_active_video_provider()
    if provider == "gemini_web":
        return generate_gemini_web_clip(
            prompt,
            duration_ms,
            aspect_ratio,
            on_conversation_url=on_conversation_url,
        )
    if provider == "flowkit":
        if (
            flow_project_id is None
            and on_flow_project_id is None
            and not reference_image_paths
            and video_mode == "i2v"
        ):
            return generate_flowkit_clip(
                image_path,
                prompt,
                duration_ms,
                aspect_ratio,
                scene_id=scene_id,
            )
        extra_kwargs: dict[str, Any] = {
            "scene_id": scene_id,
            "flow_project_id": flow_project_id,
            "on_flow_project_id": on_flow_project_id,
        }
        if reference_image_paths:
            extra_kwargs["reference_image_paths"] = reference_image_paths
        if video_mode and video_mode != "i2v":
            extra_kwargs["video_mode"] = video_mode
        return generate_flowkit_clip(
            image_path,
            prompt,
            duration_ms,
            aspect_ratio,
            **extra_kwargs,
        )
    fallback_image = image_path or (reference_image_paths[0] if reference_image_paths else None)
    if fallback_image is None:
        raise RuntimeError("An opening frame or reference image is required")
    if provider == "runway":
        return generate_runway_clip(fallback_image, prompt, duration_ms, aspect_ratio)
    return generate_comfyui_clip(fallback_image, prompt, duration_ms, aspect_ratio)




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

