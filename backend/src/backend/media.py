import logging
import base64

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


class ImagePolicyError(RuntimeError):
    """The provider refused the prompt under its image safety policy."""


@dataclass(frozen=True)
class FaceReferenceImage:
    character_name: str
    mime_type: str
    data: bytes
    reference_id: str = ""
    character_id: str = ""


@dataclass(frozen=True)
class VisualReferenceImage:
    mime_type: str
    data: bytes
    character_name: str = "Visual continuity reference (not a cast portrait)"
    reference_type: str = "visual_continuity"


IMAGE_REFERENCE_PROVIDERS = {"chatgpt_web", "gemini_web"}


def prepare_visual_reference_prompt(prompt: str) -> str:
    return (
        f"{prompt}\n\nVisual continuity reference: use the attached shot reference for relevant "
        "identity, wardrobe, location, lighting, and visual continuity. "
        "The current shot's action, camera angle, framing, and composition remain authoritative. "
        "Do not duplicate the reference composition or treat it as an exact opening frame. "
        "When cast portraits are attached, use those for each named character's face."
    )


_ASPECT_INSTRUCTION = re.compile(
    r"(?i)\b(?:vertical|portrait|horizontal|landscape)?\s*(?:9\s*:\s*16|16\s*:\s*9|\d{3,5}\s*[x×]\s*\d{3,5})\s*(?:format|aspect(?:\s+ratio)?)?\b[,.;]?"
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
    cleaned = _ASPECT_INSTRUCTION.sub("", prompt)
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
    references = [ref for ref in references if isinstance(ref, FaceReferenceImage)]
    if not references:
        return prompt
    names: list[str] = []
    for ref in references:
        if ref.character_name not in names:
            names.append(ref.character_name)
    named = ", ".join(names)
    return (
        f"Identity references are attached for: {named}. "
        "Preserve each named character's exact face and likeness from their reference image(s). "
        "Do not invent a different face.\n\n"
        f"{prompt}"
    )


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
            base_url = settings.gemini_web_base_url
            model = settings.gemini_web_image_model
            headers = _gemini_headers()
            label = "Gemini Web"
            configured = True
            capability_url = None
        connected = False
        image_generation = False
        verified = False
        detail = "Not configured" if not configured else "Offline"
        if configured:
            try:
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
            item["reference_type"] = "face_identity"
        payload.append(item)
    return payload


def _decode_provider_image(payload: dict, provider: str) -> tuple[bytes, dict]:
    try:
        encoded = payload["data"][0]["b64_json"]
        metadata = payload.get("provider_metadata") or {}
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


def _request_gemini_with_references(
    prompt: str,
    aspect_ratio: str,
    references: Sequence[FaceReferenceImage | VisualReferenceImage],
    progress: Callable[[dict], None] | None = None,
) -> tuple[bytes, str, dict]:
    base_url = settings.gemini_web_base_url
    model = settings.gemini_web_image_model
    size = "1344x768" if aspect_ratio == "16:9" else "768x1344"
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Generate exactly one image in {size} format. "
                "Return the generated image itself, not a text description. "
                f"{prompt}"
            ),
        }
    ]
    for ref in references:
        data, mime = _optimize_reference_bytes(ref.data, ref.mime_type)
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
                },
            }
        )
    if progress:
        progress({"provider": "gemini_web", "phase": "submitting"})
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=_gemini_headers(),
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "n": 1,
        },
        timeout=httpx.Timeout(settings.image_provider_timeout_seconds, connect=3),
    )
    response.raise_for_status()
    payload = response.json()
    # Prefer portable b64 when the bridge returns OpenAI image shape via tool/extensions.
    if isinstance(payload.get("data"), list) and payload["data"] and payload["data"][0].get("b64_json"):
        image, metadata = _decode_provider_image(payload, "gemini_web")
        return image, model, metadata
    try:
        message = payload["choices"][0]["message"]
        content_text = message.get("content") or ""
        if isinstance(content_text, list):
            parts = []
            for part in content_text:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    url = ((part.get("image_url") or {}).get("url") or "")
                    if url.startswith("data:") and "," in url:
                        encoded = url.split(",", 1)[1]
                        return base64.b64decode(encoded), model, {}
                    if url.startswith("http"):
                        if progress:
                            progress({"provider": "gemini_web", "phase": "downloading"})
                        return _download_image_bytes(url), model, {}
                elif isinstance(part, str):
                    parts.append(part)
            content_text = "\n".join(parts)
        if isinstance(content_text, str) and content_text.startswith("data:") and "," in content_text:
            encoded = content_text.split(",", 1)[1]
            return base64.b64decode(encoded), model, {}
        image_url = _extract_image_url(str(content_text))
        if not image_url:
            raise RuntimeError("gemini_web chat response did not include a generated image")
        if progress:
            progress({"provider": "gemini_web", "phase": "downloading"})
        return _download_image_bytes(image_url), model, {}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("gemini_web returned an invalid multimodal image response") from exc


def _request_provider_image(
    provider: str,
    prompt: str,
    aspect_ratio: str,
    progress: Callable[[dict], None] | None = None,
    references: Sequence[FaceReferenceImage | VisualReferenceImage] | None = None,
) -> tuple[bytes, str, dict]:
    references = list(references or [])
    size = "1344x768" if aspect_ratio == "16:9" else "768x1344"
    if provider == "chatgpt_web":
        base_url = settings.chatgpt_web_base_url
        model = settings.chatgpt_web_model
        headers = {"Authorization": f"Bearer {settings.chatgpt_web_api_key}"}
    elif provider == "gemini_web":
        if references:
            return _request_gemini_with_references(prompt, aspect_ratio, references, progress)
        base_url = settings.gemini_web_base_url
        model = settings.gemini_web_image_model
        headers = _gemini_headers()
    else:
        raise RuntimeError(f"Unsupported image provider: {provider}")
    request_id = str(uuid.uuid4())
    request_headers = {**headers, "X-Request-ID": request_id}
    payload: dict = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    if references and provider == "chatgpt_web":
        payload["reference_images"] = _reference_payload(references)
    if provider == "chatgpt_web":
        # The browser bridge can legitimately take up to 300 seconds to finish
        # rendering and extracting an image. Do not time out its HTTP request
        # earlier and silently fall through to another provider while it is still
        # generating the requested frame.
        provider_timeout = max(settings.image_provider_timeout_seconds, 330)
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
                    if operation.get("phase") != last_phase and progress:
                        progress({"provider": provider, **operation})
                    last_phase = operation.get("phase", last_phase)
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.75)
            response = future.result()
    else:
        response = httpx.post(
            f"{base_url.rstrip('/')}/images/generations",
            headers=request_headers,
            json=payload,
            timeout=httpx.Timeout(settings.image_provider_timeout_seconds, connect=3),
        )
    response.raise_for_status()
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
) -> tuple[str, str, str]:
    references = list(references or [])
    prompt = prepare_image_prompt(prompt, aspect_ratio)
    prompt = prepare_face_reference_prompt(prompt, references)
    has_visual_reference = any(isinstance(ref, VisualReferenceImage) for ref in references)
    if has_visual_reference:
        prompt = prepare_visual_reference_prompt(prompt)
    providers = settings.image_provider_order
    if not providers:
        if has_visual_reference:
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
            image, _model, metadata = _request_provider_image(
                provider,
                prompt,
                aspect_ratio,
                progress,
                references=references,
            )
            if progress:
                progress({"provider": provider, "phase": "saving", **metadata})
            extension, mime_type = _image_format(image)
            filename = f"generated-{uuid.uuid4()}{extension}"
            (settings.media_dir / filename).write_bytes(image)
            return filename, mime_type, provider
        except httpx.TimeoutException:
            failure = f"{label}: timed out after {settings.image_provider_timeout_seconds}s"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = str(exc.response.json().get("detail") or "")
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
            failure = f"{label}: HTTP {exc.response.status_code}"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            failure = f"{label}: {exc}"
            failures.append(failure)
            if progress:
                progress({"provider": provider, "phase": "failed", "error": failure})
    if references and not has_visual_reference:
        for provider in providers:
            status = statuses.get(provider, {})
            if not status.get("available"):
                continue
            label = status.get("label", provider)
            logger.warning(
                "All reference-based attempts failed; retrying %s without binary attachments using prompt likeness.",
                label,
            )
            if progress:
                progress({"provider": provider, "phase": "submitting", "message": f"{label}: Retrying with prompt-described likeness..."})
            try:
                image, _model, metadata = _request_provider_image(
                    provider,
                    prompt,
                    aspect_ratio,
                    progress,
                    references=[],
                )
                if progress:
                    progress({"provider": provider, "phase": "saving", **metadata})
                extension, mime_type = _image_format(image)
                filename = f"generated-{uuid.uuid4()}{extension}"
                (settings.media_dir / filename).write_bytes(image)
                return filename, mime_type, provider
            except Exception as retry_exc:
                logger.warning("Retry without references on %s failed: %s", label, retry_exc)
                break
    raise RuntimeError("Image generation failed. " + "; ".join(failures) + ". Refresh bridge sessions or upload an image manually.")


def delete_media_file(filename: str | None, db: object = None) -> None:
    if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
        return
    if db is not None:
        try:
            from sqlalchemy import select
            from .models import ShotTake
            # Never delete files that are referenced in take history
            if db.scalar(select(ShotTake.id).where(ShotTake.filename == filename).limit(1)) is not None:
                return
        except Exception:
            pass
    media_root = settings.media_dir.resolve()
    path = (settings.media_dir / filename).resolve()
    if not path.is_relative_to(media_root):
        return
    if path.is_file():
        path.unlink()
