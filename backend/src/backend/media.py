import logging
import base64
import subprocess
import sys

logger = logging.getLogger(__name__)
import hashlib
import html
import re
import textwrap
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import time

import httpx

from .settings import settings


def _auto_sync_gemini_cookies() -> bool:
    sync_script = Path(__file__).resolve().parents[3] / "bridges" / "sync_gemini_cookies.py"
    if not sync_script.is_file():
        return False
    try:
        res = subprocess.run([sys.executable, str(sync_script)], capture_output=True, text=True, timeout=30)
        return "Done. Changed: True" in res.stdout
    except Exception as exc:
        logger.warning("Auto-sync of Gemini cookies failed: %s", exc)
        return False


class ImagePolicyError(RuntimeError):
    """The provider refused the prompt under its image safety policy."""


@dataclass(frozen=True)
class FaceReferenceImage:
    character_name: str
    mime_type: str
    data: bytes
    reference_id: str = ""
    character_id: str = ""
    view_type: str = "main"


@dataclass(frozen=True)
class VisualReferenceImage:
    mime_type: str
    data: bytes
    character_name: str = "Visual continuity reference (not a cast portrait)"
    reference_type: str = "visual_continuity"


IMAGE_REFERENCE_PROVIDERS = {"chatgpt_web", "gemini_web"}


def normalize_conversation_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = str(url).strip()
    return re.sub(r"(https?://gemini\.google\.com/app/)c_([a-zA-Z0-9]+)", r"\1\2", cleaned)


def prepare_visual_reference_prompt(
    prompt: str,
    references: Sequence[FaceReferenceImage | VisualReferenceImage] | None = None,
    *,
    lock_blocking: bool = False,
    focal_name: str | None = None,
    target_names: str | None = None,
) -> str:
    is_aspect_conversion = any(
        isinstance(ref, VisualReferenceImage) and ref.reference_type in {"edit_target", "edit_target_fallback"}
        for ref in (references or [])
    )
    if is_aspect_conversion:
        if "Aspect ratio conversion:" in prompt:
            return prompt
        return (
            f"{prompt}\n\nAspect ratio conversion: Reframe and expand/outpaint the shot frame generated above (or attached reference frame) "
            "to fill the full target aspect ratio canvas edge-to-edge without black bars, letterbox, or pillarbox borders. "
            "Preserve the exact characters, faces, expressions, poses, wardrobe, props, background environment, and lighting."
        )
    if "Visual continuity reference:" in prompt:
        return prompt
    if lock_blocking:
        if focal_name:
            focus_clause = (
                f" This is a close-up of {focal_name} ONLY: from the attached reference image, turn the camera toward {focal_name} "
                f"and zoom in closer (tight waist-up close-up) to capture {focal_name} as the sole subject in their established "
                "screen position and side of frame from the reference without changing anything in the image sent; other characters stay off-frame in their established positions."
                f" IMPORTANT: the attached shot reference is for SETTING, LIGHTING, and {focal_name}'s SCREEN POSITION ONLY. "
                f"Do NOT reproduce the other people visible in it; the output must show {focal_name} alone. "
                f"Simulate a professional film camera zooming in on {focal_name} from the original camera position on the same movie set: "
                f"preserve the original environment directly behind {focal_name} exactly as it would appear at this tighter zoom level, "
                f"so the surface/door/wall directly behind {focal_name} occupies most of the background and any chairs, windows, or desk areas where "
                "adjacent people stood are cropped out of frame."
            )
        else:
            who = target_names or "the selected characters"
            focus_clause = (
                f" CAMERA TURN & ZOOM-IN: From the attached reference image, turn the camera toward {who} and zoom in closer "
                f"(tight waist-up framing) to capture ONLY {who} without changing anything in the image sent. "
                f"Keep {who} in their exact positions, clothing, faces, and lighting from the reference image, "
                "and remove all other people by zooming in so the outer left and right edges of the wider scene are outside the camera's field of view. "
                "Do NOT keep the wide full-body framing, do NOT show feet or floor paving, and do NOT include the empty white plastic chairs, tables, gates, staircases, or windows "
                f"where the other people were standing in the reference image — preserve ONLY the background directly behind {who} at this tighter camera zoom."
            )
        return (
            f"{prompt}\n\nVisual continuity reference: use the attached shot reference for exact "
            "spatial blocking, character positions, wardrobe, location, lighting, and visual continuity. "
            "SPATIAL BLOCKING CONSTRAINT: Maintain the continuous horizontal stage arrangement and character "
            "positions established in the reference frame. DO NOT swap, mirror, or rearrange characters."
            f"{focus_clause} "
            "When cast portraits are attached, use those for each named character's face."
        )
    return (
        f"{prompt}\n\nVisual continuity reference: use the attached shot reference for relevant "
        "identity, wardrobe, location, lighting, and visual continuity. "
        "The current shot's action, camera angle, framing, and composition remain authoritative. "
        "Do not duplicate the reference composition or treat it as an exact opening frame. "
        "When cast portraits are attached, use those for each named character's face."
    )


_ASPECT_INSTRUCTION = re.compile(
    r"(?i)\b(?:vertical|portrait|horizontal|landscape|widescreen)?\s*"
    r"(?:9\s*:\s*16|16\s*:\s*9|\d{3,5}\s*[x×]\s*\d{3,5})\s*"
    r"(?:vertical|portrait|horizontal|landscape|widescreen)?\s*"
    r"(?:composition\s+for\s+shorts|for\s+shorts|composition|framing|format|aspect(?:\s+ratio)?)?\b[,.;]?"
)
_ADULT_TERMS = re.compile(r"(?i)\b(?:adult|woman|women|man|men|mother|father|mama|wife|husband)\b")
_MINOR_TERMS = re.compile(r"(?i)\b(?:baby|babies|child|children|kid|kids|teen|teenager|boy|girl|minor|schoolchild)\b")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_IMAGE_URL = re.compile(r"(https?://[^\s)]+\.(?:png|jpe?g|webp)(?:\?[^\s)]*)?)", re.I)
_ALLOWED_IMAGE_HOST_SUFFIXES = (
    "googleusercontent.com",
    "ggpht.com",
    "google.com",
    "gstatic.com",
)


def _gemini_headers() -> dict[str, str]:
    key = (settings.gemini_web_api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _assert_safe_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Image URL is not allowed")
    host = (parsed.hostname or "").lower()
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES):
        raise RuntimeError("Image URL host is not allowed")


def prepare_image_prompt(prompt: str, aspect_ratio: str) -> str:
    """Remove contradictory output geometry and add supported age clarity."""
    cleaned = _ASPECT_INSTRUCTION.sub(" ", prompt)
    cleaned = re.sub(r"\s+([,.;])", r"\1", cleaned)
    cleaned = re.sub(r"[,;]\s*[,;]", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;")
    geometry = (
        "Output composition: vertical portrait 9:16 (768x1344)."
        if aspect_ratio == "9:16"
        else "Output composition: horizontal landscape 16:9 (1344x768)."
    )
    age_clarity = ""
    if _ADULT_TERMS.search(cleaned) and not _MINOR_TERMS.search(cleaned):
        age_clarity = " All people described as women or men are adults age 18 or older."
    return f"{cleaned}\n\n{geometry}{age_clarity}"


def prepare_face_reference_prompt(prompt: str, references: Sequence[FaceReferenceImage | VisualReferenceImage]) -> str:
    face_refs = [ref for ref in references if isinstance(ref, FaceReferenceImage)]
    if not face_refs:
        return prompt

    chars_with_3d: list[str] = []
    prop_names: list[str] = []
    scene_names: list[str] = []
    other_chars: list[str] = []
    for ref in face_refs:
        v_type = getattr(ref, "view_type", "main")
        name = ref.character_name
        if v_type == "3d_view":
            if name not in chars_with_3d:
                chars_with_3d.append(name)
        elif v_type == "prop":
            if name not in prop_names:
                prop_names.append(name)
        elif v_type == "scene":
            if name not in scene_names:
                scene_names.append(name)
        else:
            if name not in other_chars:
                other_chars.append(name)

    other_chars = [name for name in other_chars if name not in chars_with_3d]

    parts: list[str] = []
    if chars_with_3d:
        named_3d = ", ".join(chars_with_3d)
        parts.append(
            f"Character 3D turnaround reference sheet(s) (multi-angle turnaround showing face, hairstyle, body build, and canonical wardrobe) are attached for: {named_3d}. "
            "Preserve each character's exact face, hairstyle, clothing/wardrobe, body build, and proportions as depicted in their 3D turnaround sheet. "
            "Do not alter their wardrobe, colors, or facial structure. "
            "Use the turnaround sheet for identity and wardrobe only, never for pose: take body pose, arm and "
            "hand placement, and expression from this shot's description, and do not copy the sheet's neutral "
            "standing pose or hands-in-pockets stance unless the shot asks for it."
        )
    if other_chars:
        named_other = ", ".join(other_chars)
        parts.append(
            f"Identity references are attached for: {named_other}. "
            "Preserve each named character's exact face and likeness from their reference image(s). "
            "Do not invent a different face. Take pose and hand placement from this shot's description, not from the reference image."
        )
    if prop_names:
        named_props = ", ".join(prop_names)
        parts.append(
            f"Prop object references are attached for: {named_props}. "
            "Preserve each prop's exact shape, proportions, materials, colors, and surface details from its reference image. "
            "Do not invent a different object."
        )
    if scene_names:
        named_scenes = ", ".join(scene_names)
        parts.append(
            f"Scene/set environment references are attached for: {named_scenes}. "
            "Preserve architecture, layout, key furniture and materials from the reference image(s). Apply the shot's lighting and time of day. "
            "Do not invent a different location."
        )

    instruction = " ".join(parts)
    return f"{instruction}\n\n{prompt}"


def render_storyboard_placeholder(
    prompt: str,
    scene_title: str,
    framing: str,
    aspect_ratio: str,
) -> str:
    """Render a local storyboard slate until an image model is configured."""
    portrait = aspect_ratio == "9:16"
    width, height = (720, 1280) if portrait else (1280, 720)
    digest = hashlib.sha256(f"{prompt}:{uuid.uuid4()}".encode()).hexdigest()
    base = f"#{digest[:6]}"
    accent = f"#{digest[6:12]}"
    line_length = 34 if portrait else 62
    lines = textwrap.wrap(prompt, width=line_length)[:8]
    line_height = 38 if portrait else 34
    text_y = height // 2 - ((len(lines) - 1) * line_height) // 2
    prompt_lines = "".join(
        f'<text x="{width // 2}" y="{text_y + index * line_height}" '
        f'text-anchor="middle" fill="#f4f6f4" font-size="{24 if portrait else 22}" '
        f'font-family="Arial, sans-serif">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="{base}"/>
<rect width="100%" height="100%" fill="#111814" opacity="0.58"/>
<circle cx="{int(width * .82)}" cy="{int(height * .22)}" r="{int(min(width, height) * .23)}" fill="{accent}" opacity="0.34"/>
<path d="M0 {int(height * .78)} L{width} {int(height * .55)} L{width} {height} L0 {height}Z" fill="#0c100e" opacity="0.55"/>
<text x="48" y="58" fill="#cbd4ce" font-size="18" font-family="Arial, sans-serif" font-weight="700">CUTLINE STORYBOARD</text>
<text x="48" y="88" fill="#96a29a" font-size="15" font-family="Arial, sans-serif">{html.escape(scene_title)} · {html.escape(framing)}</text>
{prompt_lines}
<text x="{width - 48}" y="{height - 38}" text-anchor="end" fill="#b8c2bc" font-size="14" font-family="Arial, sans-serif">LOCAL PROTOTYPE RENDER</text>
</svg>"""
    filename = f"generated-{uuid.uuid4()}.svg"
    (settings.media_dir / filename).write_text(svg, encoding="utf-8")
    return filename


def image_provider_statuses() -> list[dict]:
    statuses = []
    for priority, provider in enumerate(settings.image_provider_order, start=1):
        if provider == "chatgpt_web":
            base_url = settings.chatgpt_web_base_url
            model = settings.chatgpt_web_model
            headers = {"Authorization": f"Bearer {settings.chatgpt_web_api_key}"}
            label = "ChatGPT Web"
            configured = bool(settings.chatgpt_web_api_key)
            capability_url = f"{base_url.rstrip('/')}/provider/capabilities"
        else:
            base_url = settings.gemini_web_image_base_url
            model = settings.gemini_web_image_model
            headers = _gemini_headers()
            label = "Gemini Web"
            configured = True
            capability_url = f"{base_url.rstrip('/')}/provider/capabilities"
        connected = False
        image_generation = False
        verified = False
        detail = "Not configured" if not configured else "Offline"
        if configured:
            try:
                models = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=2)
                if not models.is_success and provider == "gemini_web":
                    if _auto_sync_gemini_cookies():
                        time.sleep(2)
                        models = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=2)
                connected = models.is_success
                image_generation = connected
                verified = connected if not capability_url else False
                if connected and capability_url:
                    capabilities = httpx.get(capability_url, headers=headers, timeout=2)
                    image_capability = capabilities.json().get("image_generation", {}) if capabilities.is_success else {}
                    image_generation = bool(image_capability)
                    verified = bool(image_capability.get("verified")) if isinstance(image_capability, dict) else False
                detail = "Verified for image generation" if verified else "Connected; image generation not yet verified" if connected and image_generation else "Connected without image capability" if connected else "Offline"
            except (httpx.HTTPError, ValueError):
                pass
        statuses.append({
            "id": provider,
            "label": label,
            "configured": configured,
            "available": connected and image_generation,
            "model": model,
            "detail": detail,
            "priority": priority,
            "image_generation": image_generation,
            "supports_image_references": provider in IMAGE_REFERENCE_PROVIDERS,
            "connected": connected,
            "verified": verified,
        })
    return statuses


def _optimize_reference_bytes(data: bytes, mime_type: str, max_dimension: int = 1024) -> tuple[bytes, str]:
    if not data or len(data) <= 500 * 1024:
        return data, mime_type
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            out = BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Exception:
        return data, mime_type


def _reference_payload(references: Sequence[FaceReferenceImage | VisualReferenceImage]) -> list[dict[str, str]]:
    payload = []
    for ref in references:
        data, mime = _optimize_reference_bytes(ref.data, ref.mime_type)
        item = {
            "character_name": ref.character_name,
            "mime_type": mime,
            "b64_json": base64.b64encode(data).decode("ascii"),
        }
        if isinstance(ref, VisualReferenceImage):
            item["reference_type"] = ref.reference_type
        else:
            v_type = getattr(ref, "view_type", "main") or "main"
            if v_type == "3d_view":
                item["reference_type"] = "character_3d"
            elif v_type == "prop":
                item["reference_type"] = "prop_object"
            elif v_type == "scene":
                item["reference_type"] = "scene_environment"
            else:
                item["reference_type"] = "face_identity"
        payload.append(item)
    return payload


def _decode_provider_image(payload: dict, provider: str) -> tuple[bytes, dict]:
    try:
        encoded = payload["data"][0]["b64_json"]
        metadata = payload.get("provider_metadata") or {}
        if "conversation_url" in metadata:
            metadata["conversation_url"] = normalize_conversation_url(metadata["conversation_url"])
        return base64.b64decode(encoded, validate=True), metadata
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{provider} returned an invalid image response") from exc


def _extract_image_url(content: str) -> str | None:
    markdown = _MARKDOWN_IMAGE.search(content or "")
    if markdown:
        return markdown.group(1)
    bare = _BARE_IMAGE_URL.search(content or "")
    return bare.group(1) if bare else None


def _download_image_bytes(url: str, headers: dict | None = None) -> bytes:
    _assert_safe_image_url(url)
    response = httpx.get(url, headers=headers or {}, timeout=httpx.Timeout(60, connect=5), follow_redirects=True)
    response.raise_for_status()
    return response.content


def _request_provider_image(
    provider: str,
    prompt: str,
    aspect_ratio: str,
    progress: Callable[[dict], None] | None = None,
    references: Sequence[FaceReferenceImage | VisualReferenceImage] | None = None,
    conversation_url: str | None = None,
) -> tuple[bytes, str, dict]:
    references = list(references or [])
    size = "1344x768" if aspect_ratio == "16:9" else "768x1344"
    if provider == "chatgpt_web":
        base_url = settings.chatgpt_web_base_url
        model = settings.chatgpt_web_model
        headers = {"Authorization": f"Bearer {settings.chatgpt_web_api_key}"}
    elif provider == "gemini_web":
        base_url = settings.gemini_web_image_base_url
        model = settings.gemini_web_image_model
        headers = _gemini_headers()
    else:
        raise RuntimeError(f"Unsupported image provider: {provider}")
    request_id = str(uuid.uuid4())
    request_headers = {**headers, "X-Request-ID": request_id}
    payload: dict = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    if references and provider in IMAGE_REFERENCE_PROVIDERS:
        payload["reference_images"] = _reference_payload(references)
    if conversation_url:
        c_url = str(conversation_url).strip()
        if provider == "gemini_web" and "gemini.google.com" in c_url:
            payload["conversation_url"] = c_url
        elif provider == "chatgpt_web" and ("chatgpt.com" in c_url or "openai.com" in c_url):
            payload["conversation_url"] = c_url

    # The browser bridges (ChatGPT and Gemini) can take up to 300 seconds to finish
    # attaching files, rendering, and extracting an image. Poll status during generation.
    provider_timeout = max(settings.image_provider_timeout_seconds, 330)
    response = None
    for attempt in range(3):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                httpx.post,
                f"{base_url.rstrip('/')}/images/generations",
                headers=request_headers,
                json=payload,
                timeout=httpx.Timeout(provider_timeout, connect=5),
            )
            last_phase = None
            while not future.done():
                try:
                    status_response = httpx.get(
                        f"{base_url.rstrip('/')}/images/generations/status/{request_id}",
                        headers=headers,
                        timeout=2,
                    )
                    operation = status_response.json() if status_response.is_success else {}
                    if "conversation_url" in operation:
                        operation["conversation_url"] = normalize_conversation_url(operation["conversation_url"])
                    if operation.get("phase") != last_phase and progress:
                        progress({"provider": provider, **operation})
                    last_phase = operation.get("phase", last_phase)
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.75)
            try:
                response = future.result()
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                err_text = ""
                try:
                    err_text = exc.response.text.lower()
                except Exception:
                    pass
                if attempt < 2 and (
                    (exc.response.status_code == 500 and "client not initialized" in err_text)
                    or (provider == "gemini_web" and exc.response.status_code in (401, 403, 500) and attempt == 0 and _auto_sync_gemini_cookies())
                ):
                    logger.warning("%s error %s; retrying attempt %d/3...", provider, exc, attempt + 2)
                    time.sleep(2)
                    continue
                raise

    if response is None:
        raise RuntimeError(f"{provider} failed to return a response")
    image, metadata = _decode_provider_image(response.json(), provider)
    return image, model, metadata


def _image_format(image: bytes) -> tuple[str, str]:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise RuntimeError("Image provider returned an unsupported image format")


def generate_storyboard_image(
    prompt: str,
    scene_title: str,
    framing: str,
    aspect_ratio: str,
    progress: Callable[[dict], None] | None = None,
    references: Sequence[FaceReferenceImage | VisualReferenceImage] | None = None,
    conversation_url: str | None = None,
    is_followup_edit: bool = False,
    lock_blocking: bool = False,
    focal_name: str | None = None,
    target_names: str | None = None,
    is_locked_camera_zoom: bool = False,
) -> tuple[str, str, str]:
    references = list(references or [])
    has_visual_reference = any(isinstance(ref, VisualReferenceImage) for ref in references)
    if not is_locked_camera_zoom:
        base_prompt = prepare_image_prompt(prompt, aspect_ratio)
        prompt = prepare_face_reference_prompt(base_prompt, references)
        if has_visual_reference and not is_followup_edit:
            prompt = prepare_visual_reference_prompt(
                prompt,
                references=references,
                lock_blocking=lock_blocking,
                focal_name=focal_name,
                target_names=target_names,
            )
    providers = settings.image_provider_order
    if not providers:
        if has_visual_reference and not is_followup_edit:
            raise RuntimeError("No configured provider supports image references")
        return (
            render_storyboard_placeholder(prompt, scene_title, framing, aspect_ratio),
            "image/svg+xml",
            "local-prototype",
        )
    failures = []
    statuses = {item["id"]: item for item in image_provider_statuses()}
    for index, provider in enumerate(providers):
        status = statuses.get(provider, {})
        if has_visual_reference and provider not in IMAGE_REFERENCE_PROVIDERS:
            failures.append(f"{provider}: image references are unsupported")
            continue
        if not status.get("available"):
            failure = f"{status.get('label', provider)}: offline or missing image capability"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
            continue
        label = status.get("label", provider)
        if progress:
            progress({"provider": provider, "phase": "submitting", "message": f"Generating with {label}" if index == 0 else f"Trying fallback: {label}"})
        try:
            extra_kwargs = {"conversation_url": conversation_url} if conversation_url else {}
            image, _model, metadata = _request_provider_image(
                provider,
                prompt,
                aspect_ratio,
                progress,
                references=references,
                **extra_kwargs,
            )
            if progress:
                progress({"provider": provider, "phase": "saving", **metadata})
            extension, mime_type = _image_format(image)
            filename = f"generated-{uuid.uuid4()}{extension}"
            (settings.media_dir / filename).write_bytes(image)
            return filename, mime_type, provider
        except httpx.TimeoutException:
            failure = f"{label}: timed out after {max(settings.image_provider_timeout_seconds, 330)}s"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                detail = str(body.get("detail") or (body.get("error") if isinstance(body.get("error"), str) else (body.get("error") or {}).get("message")) or "")
            except (ValueError, AttributeError):
                pass
            if exc.response.status_code == 422 and "content_policy_refusal" in detail:
                failure = (
                    f"{label}: the provider's safety system could not verify the depicted characters' ages. "
                    "Add explicit adult ages to the image prompt, review it, and retry."
                )
                failures.append(failure)
                if progress:
                    progress({"provider": provider, "phase": "failed", "error": failure, "failure_category": "content_policy_refusal"})
                raise ImagePolicyError(failure) from exc
            failure = f"{label}: HTTP {exc.response.status_code} ({detail})" if detail else f"{label}: HTTP {exc.response.status_code}"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            failure = f"{label}: {exc}"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
    raise RuntimeError("Image generation failed. " + "; ".join(failures) + ". Refresh bridge sessions or upload an image manually.")


def generate_storyboard_chatgpt_dual(
    prompt: str,
    scene_title: str,
    framing: str,
    progress: Callable[[dict], None] | None = None,
    references: Sequence[FaceReferenceImage | VisualReferenceImage] | None = None,
    conversation_url: str | None = None,
) -> tuple[tuple[str, str], tuple[str, str], str]:
    references = list(references or [])
    cleaned = _ASPECT_INSTRUCTION.sub(" ", prompt)
    cleaned = re.sub(r"\s+([,.;])", r"\1", cleaned)
    cleaned = re.sub(r"[,;]\s*[,;]", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;")
    age_clarity = ""
    if _ADULT_TERMS.search(cleaned) and not _MINOR_TERMS.search(cleaned):
        age_clarity = " All people described as women or men are adults age 18 or older."
    dual_prompt = (
        "Create two images, first 16:9 and 9:16:\n"
        "- 16:9 widescreen landscape composition.\n"
        "- 9:16 vertical smartphone composition.\n"
        "Keep the character's face, clothing, expression, and environment strictly identical in both panels.\n"
        f"Scene: {cleaned}{age_clarity}"
    )
    if references:
        dual_prompt = prepare_face_reference_prompt(dual_prompt, references)

    provider = "chatgpt_web"
    statuses = {item["id"]: item for item in image_provider_statuses()}
    status = statuses.get(provider, {})
    if not status.get("available"):
        raise RuntimeError("ChatGPT Web provider is offline or missing image capability")

    base_url = settings.chatgpt_web_base_url
    model = settings.chatgpt_web_model
    headers = {"Authorization": f"Bearer {settings.chatgpt_web_api_key}"}
    request_id = str(uuid.uuid4())
    request_headers = {**headers, "X-Request-ID": request_id}
    payload: dict = {
        "model": model,
        "prompt": dual_prompt,
        "size": "1024x1024",
        "n": 2,
        "response_format": "b64_json",
    }
    if references:
        payload["reference_images"] = _reference_payload(references)
    if conversation_url:
        payload["conversation_url"] = conversation_url

    if progress:
        progress({
            "provider": provider,
            "phase": "submitting",
            "message": "Submitting dual 16:9 + 9:16 prompt to ChatGPT Web",
        })

    provider_timeout = max(settings.image_provider_timeout_seconds, 360)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            httpx.post,
            f"{base_url.rstrip('/')}/images/generations",
            headers=request_headers,
            json=payload,
            timeout=httpx.Timeout(provider_timeout, connect=3),
        )
        last_phase = None
        while not future.done():
            try:
                status_response = httpx.get(
                    f"{base_url.rstrip('/')}/images/generations/status/{request_id}",
                    headers=headers,
                    timeout=2,
                )
                operation = status_response.json() if status_response.is_success else {}
                if "conversation_url" in operation:
                    operation["conversation_url"] = normalize_conversation_url(operation["conversation_url"])
                if operation.get("phase") != last_phase and progress:
                    progress({"provider": provider, **operation})
                last_phase = operation.get("phase", last_phase)
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.75)
        response = future.result()

    response.raise_for_status()
    resp_json = response.json()
    metadata = resp_json.get("provider_metadata") or {}
    if "conversation_url" in metadata:
        metadata["conversation_url"] = normalize_conversation_url(metadata["conversation_url"])
    data_items = resp_json.get("data") or []
    if not data_items:
        raise RuntimeError("ChatGPT Web did not return any image data")

    images_bytes: list[bytes] = []
    for item in data_items:
        if item.get("b64_json"):
            images_bytes.append(base64.b64decode(item["b64_json"], validate=True))

    if not images_bytes:
        raise RuntimeError("ChatGPT Web returned invalid image payloads")

    if len(images_bytes) == 1:
        bytes1 = images_bytes[0]
        bytes2 = images_bytes[0]
    else:
        bytes1 = images_bytes[0]
        bytes2 = images_bytes[1]

    from io import BytesIO
    from PIL import Image

    def _aspect(b: bytes) -> float:
        try:
            with Image.open(BytesIO(b)) as im:
                return float(im.width) / max(1.0, float(im.height))
        except Exception:
            return 1.0

    ratio1 = _aspect(bytes1)
    ratio2 = _aspect(bytes2)
    if ratio1 >= ratio2:
        landscape_bytes, portrait_bytes = bytes1, bytes2
    else:
        landscape_bytes, portrait_bytes = bytes2, bytes1

    ext1, mime1 = _image_format(landscape_bytes)
    ext2, mime2 = _image_format(portrait_bytes)
    fn_16_9 = f"generated-{uuid.uuid4()}{ext1}"
    fn_9_16 = f"generated-{uuid.uuid4()}{ext2}"
    (settings.media_dir / fn_16_9).write_bytes(landscape_bytes)
    (settings.media_dir / fn_9_16).write_bytes(portrait_bytes)

    if progress:
        progress({"provider": provider, "phase": "saving", **metadata})

    return (fn_16_9, mime1), (fn_9_16, mime2), provider


_COLOR_RETOUCH_PHRASE = re.compile(
    r"^\s*(?:please\s+)?(?:retouch|repaint|recolor|revive|restore|enhance|boost)"
    r"(?:\s+(?:the\s+)?(?:color|colors|colour|colours|vibrancy|saturation|image|frame|this(?:\s+image|\s+frame)?))*\s*[.!]?\s*$",
    re.IGNORECASE,
)

COLOR_RETOUCH_STRICT_PROMPT = (
    "Retouch and repaint the colors of this exact image to restore rich, vibrant, natural color saturation, "
    "deep contrast, and crisp lighting. Do NOT modify, add, remove, or reposition any character, face, "
    "expression, hairstyle, clothing, prop, or background detail — keep the entire composition and subjects 100% identical."
)


def expand_color_retouch_prompt(prompt: str) -> str:
    stripped = (prompt or "").strip()
    if _COLOR_RETOUCH_PHRASE.match(stripped):
        return COLOR_RETOUCH_STRICT_PROMPT
    return stripped


def enhance_image_vibrancy(data: bytes, strength: float = 1.0) -> tuple[bytes, str]:
    """Revive or adjust frame color with pixel-exact contrast, color vibrancy, and clarity."""
    from io import BytesIO
    from PIL import Image, ImageEnhance, ImageOps

    s = max(-1.0, min(3.0, float(strength)))
    with Image.open(BytesIO(data)) as im:
        has_alpha = im.mode in ("RGBA", "LA")
        alpha = im.getchannel("A") if has_alpha else None
        rgb = im.convert("RGB")

        if abs(s) >= 1e-4:
            if s > 0:
                leveled = ImageOps.autocontrast(rgb, cutoff=0.5)
                rgb = Image.blend(rgb, leveled, min(1.0, 0.55 * s))
            rgb = ImageEnhance.Contrast(rgb).enhance(max(0.3, 1.0 + 0.14 * s))
            rgb = ImageEnhance.Color(rgb).enhance(max(0.1, 1.0 + 0.32 * s))
            rgb = ImageEnhance.Sharpness(rgb).enhance(max(0.5, 1.0 + 0.20 * s))

        if alpha is not None:
            rgb = rgb.convert("RGBA")
            rgb.putalpha(alpha)

        out = BytesIO()
        rgb.save(out, format="PNG")
        return out.getvalue(), "image/png"


def _ref_color_state_path(target_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(target_id or ""))
    return settings.media_dir / f".ref-color-state-{safe_id}.json"


def get_reference_color_state(target_id: str, current_filename: str) -> dict:
    import json

    default_state = {
        "original_filename": current_filename,
        "history": [{"filename": current_filename, "strength": 0.0}],
        "index": 0,
    }
    if not target_id or not current_filename:
        return default_state
    state_path = _ref_color_state_path(target_id)
    if not state_path.is_file():
        return default_state
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        history = raw.get("history")
        index = int(raw.get("index", 0))
        orig = str(raw.get("original_filename") or "")
        if (
            not isinstance(history, list)
            or not history
            or not orig
            or not (0 <= index < len(history))
        ):
            return default_state
        # If current_filename was externally replaced (e.g. new frame selected or AI repaint), reset state
        known_filenames = {str(item.get("filename") or "") for item in history if isinstance(item, dict)}
        if current_filename not in known_filenames:
            return default_state
        if str(history[index].get("filename") or "") != current_filename:
            for idx, item in enumerate(history):
                if isinstance(item, dict) and item.get("filename") == current_filename:
                    index = idx
                    break
        return {
            "original_filename": orig,
            "history": history,
            "index": index,
        }
    except Exception:
        return default_state


def save_reference_color_state(target_id: str, state: dict) -> None:
    import json

    if not target_id:
        return
    state_path = _ref_color_state_path(target_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def clear_reference_color_state(target_id: str, keep_filename: str | None = None) -> None:
    if not target_id:
        return
    state_path = _ref_color_state_path(target_id)
    if state_path.is_file():
        try:
            import json

            raw = json.loads(state_path.read_text(encoding="utf-8"))
            for item in raw.get("history") or []:
                fn = item.get("filename") if isinstance(item, dict) else None
                if fn and fn != keep_filename and (
                    fn.startswith("image-reference-retouched-") or fn.startswith("continuity-retouched-")
                ):
                    delete_media_file(fn)
        except Exception:
            pass
        state_path.unlink(missing_ok=True)


def delete_media_file(filename: str | None, db: object = None) -> None:
    if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
        return
    if db is not None:
        try:
            from sqlalchemy import select
            from .models import ShotMediaVariant, ShotTake
            # Never delete files that are referenced in take history or other variants
            if db.scalar(select(ShotTake.id).where(ShotTake.filename == filename).limit(1)) is not None:
                return
            if db.scalar(select(ShotMediaVariant.id).where(ShotMediaVariant.filename == filename).limit(1)) is not None:
                return
        except Exception:
            pass
    media_root = settings.media_dir.resolve()
    path = (settings.media_dir / filename).resolve()
    if not path.is_relative_to(media_root):
        return
    if path.is_file():
        path.unlink()
