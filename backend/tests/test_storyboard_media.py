import base64
import httpx
import pytest

from backend import media
from backend.media import generate_storyboard_image, prepare_image_prompt
from backend.settings import settings


def test_local_storyboard_renderer_creates_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "image_providers", "")

    filename, mime_type, provider = generate_storyboard_image(
        "A rain-soaked kitchen at night",
        "Kitchen",
        "Wide",
        "16:9",
    )

    assert mime_type == "image/svg+xml"
    assert provider == "local-prototype"
    assert (settings.media_dir / filename).is_file()


def test_prepare_image_prompt_replaces_conflicting_aspect_instructions() -> None:
    prepared = prepare_image_prompt(
        "Two adult women on a bench, vertical 9:16, no text, 1344x768 format.",
        "9:16",
    )

    assert "1344x768" not in prepared
    assert prepared.count("9:16") == 1
    assert "768x1344" in prepared
    assert "adults age 18 or older" in prepared


def test_prepare_image_prompt_does_not_relabel_explicit_minors() -> None:
    prepared = prepare_image_prompt("A mother and child reading together", "16:9")

    assert "adults age 18 or older" not in prepared
    assert "horizontal landscape 16:9" in prepared


def test_image_generation_falls_back_in_configured_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": "chatgpt_web", "label": "ChatGPT Web", "available": True},
        {"id": "gemini_web", "label": "Gemini Web", "available": True},
    ])
    calls = []

    def request(provider, prompt, aspect_ratio, progress=None, references=None, conversation_url=None):
        calls.append(provider)
        if provider == "chatgpt_web":
            raise RuntimeError("no generated image")
        return b"\x89PNG\r\n\x1a\nimage", "model", {}

    monkeypatch.setattr(media, "_request_provider_image", request)
    messages = []
    filename, mime_type, provider = generate_storyboard_image("prompt", "scene", "wide", "9:16", messages.append)

    assert calls == ["chatgpt_web", "gemini_web"]
    assert [message["message"] for message in messages if message.get("message")] == ["Generating with ChatGPT Web", "Trying fallback: Gemini Web"]
    assert provider == "gemini_web"
    assert mime_type == "image/png"
    assert (settings.media_dir / filename).is_file()


def test_image_generation_reports_all_provider_failures(monkeypatch) -> None:
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": "chatgpt_web", "label": "ChatGPT Web", "available": False},
        {"id": "gemini_web", "label": "Gemini Web", "available": False},
    ])

    with pytest.raises(RuntimeError, match="ChatGPT Web.*Gemini Web.*upload"):
        generate_storyboard_image("prompt", "scene", "wide", "16:9")


def test_reference_failures_never_retry_without_attachments(tmp_path, monkeypatch) -> None:
    from backend.media import FaceReferenceImage

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": provider, "label": provider, "available": True}
        for provider in ("chatgpt_web", "gemini_web")
    ])
    refs = [FaceReferenceImage(character_name="GABE", mime_type="image/png", data=b"face")]
    calls = []

    def request(provider, prompt, aspect_ratio, progress=None, references=None, conversation_url=None):
        calls.append((provider, references))
        if not references:
            return b"\x89PNG\r\n\x1a\nimage", "model", {}
        raise RuntimeError("reference_attach_failed")

    monkeypatch.setattr(media, "_request_provider_image", request)
    with pytest.raises(RuntimeError, match="reference_attach_failed"):
        generate_storyboard_image("GABE waits", "Office", "Medium", "9:16", references=refs)

    assert calls == [("chatgpt_web", refs), ("gemini_web", refs)]
    assert not list(settings.media_dir.glob("generated-*"))


def test_prepare_face_reference_prompt_names_characters() -> None:
    from backend.media import FaceReferenceImage, prepare_face_reference_prompt

    prepared = prepare_face_reference_prompt(
        "MARA walks into the room",
        [
            FaceReferenceImage(character_name="MARA", mime_type="image/png", data=b"abc"),
            FaceReferenceImage(character_name="MARA", mime_type="image/png", data=b"def"),
            FaceReferenceImage(character_name="ELIAS", mime_type="image/jpeg", data=b"ghi"),
        ],
    )

    assert "Identity references are attached for: MARA, ELIAS" in prepared
    assert "exact face and likeness" in prepared
    assert prepared.endswith("MARA walks into the room")


def test_prepare_face_reference_prompt_with_3d_turnaround() -> None:
    from backend.media import FaceReferenceImage, _reference_payload, prepare_face_reference_prompt

    refs = [
        FaceReferenceImage(character_name="GABE", mime_type="image/png", data=b"turnaround_bytes", view_type="3d_view"),
        FaceReferenceImage(character_name="GABE", mime_type="image/png", data=b"face_bytes", view_type="main"),
        FaceReferenceImage(character_name="ROSA", mime_type="image/jpeg", data=b"rosa_face", view_type="main"),
    ]
    prepared = prepare_face_reference_prompt(
        "GABE sits by ROSA",
        refs,
    )

    assert "Character 3D turnaround reference sheet(s)" in prepared
    assert "never for pose" in prepared
    assert "attached for: GABE" in prepared
    assert "clothing/wardrobe, body build, and proportions" in prepared
    assert "Identity references are attached for: ROSA" in prepared
    assert prepared.endswith("GABE sits by ROSA")

    payload = _reference_payload(refs)
    assert payload[0]["reference_type"] == "character_3d"
    assert payload[1]["reference_type"] == "face_identity"
    assert payload[2]["reference_type"] == "face_identity"


def test_prepare_face_reference_prompt_prop_and_scene() -> None:
    from backend.media import FaceReferenceImage, _reference_payload, prepare_face_reference_prompt

    refs = [
        FaceReferenceImage(character_name="BISCUIT PACK", mime_type="image/png", data=b"prop", view_type="prop"),
        FaceReferenceImage(character_name="KITCHEN", mime_type="image/png", data=b"scene", view_type="scene"),
        FaceReferenceImage(character_name="MARA", mime_type="image/png", data=b"face", view_type="main"),
    ]
    prepared = prepare_face_reference_prompt("MARA holds @BISCUIT_PACK in @KITCHEN", refs)
    assert "Prop object references are attached for: BISCUIT PACK" in prepared
    assert "Scene/set environment references are attached for: KITCHEN" in prepared
    assert "Identity references are attached for: MARA" in prepared
    assert "exact face and likeness" in prepared
    assert "shape, proportions, materials" in prepared

    payload = _reference_payload(refs)
    assert [item["reference_type"] for item in payload] == [
        "prop_object",
        "scene_environment",
        "face_identity",
    ]



def test_image_generation_passes_references_to_provider(tmp_path, monkeypatch) -> None:
    from backend.media import FaceReferenceImage

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": "chatgpt_web", "label": "ChatGPT Web", "available": True},
    ])
    seen = {}

    def request(provider, prompt, aspect_ratio, progress=None, references=None, conversation_url=None):
        seen["provider"] = provider
        seen["prompt"] = prompt
        seen["references"] = list(references or [])
        seen["conversation_url"] = conversation_url
        return b"\x89PNG\r\n\x1a\nimage", "model", {"conversation_url": "https://chatgpt.com/c/project"}

    monkeypatch.setattr(media, "_request_provider_image", request)
    refs = [FaceReferenceImage(character_name="MARA", mime_type="image/png", data=b"face")]
    filename, mime_type, provider = generate_storyboard_image(
        "MARA smiles",
        "scene",
        "wide",
        "16:9",
        references=refs,
    )

    assert provider == "chatgpt_web"
    assert mime_type == "image/png"
    assert seen["provider"] == "chatgpt_web"
    assert seen["references"] == refs
    assert seen["conversation_url"] is None
    assert "Identity references are attached for: MARA" in seen["prompt"]
    assert (settings.media_dir / filename).is_file()


def test_image_generation_passes_conversation_url_without_followup_edit(tmp_path, monkeypatch) -> None:
    from backend.media import VisualReferenceImage

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web")
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": "chatgpt_web", "label": "ChatGPT Web", "available": True},
    ])
    seen = {}

    def request(provider, prompt, aspect_ratio, progress=None, references=None, conversation_url=None):
        seen["conversation_url"] = conversation_url
        seen["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\nimage", "model", {}

    monkeypatch.setattr(media, "_request_provider_image", request)
    ref = VisualReferenceImage(data=b"frame", mime_type="image/png", reference_type="edit_target_fallback")
    filename, mime_type, provider = generate_storyboard_image(
        "A quiet morning by the shore",
        "Shore",
        "Medium",
        "16:9",
        references=[ref],
        conversation_url="https://chatgpt.com/c/active-thread",
        is_followup_edit=False,
    )

    assert provider == "chatgpt_web"
    assert seen["conversation_url"] == "https://chatgpt.com/c/active-thread"
    assert "Aspect ratio conversion: Reframe and expand/outpaint" in seen["prompt"]
    assert (settings.media_dir / filename).is_file()


def test_generate_storyboard_chatgpt_dual(tmp_path, monkeypatch) -> None:
    import base64
    from io import BytesIO
    from PIL import Image
    import httpx
    from backend.media import generate_storyboard_chatgpt_dual

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(media, "image_provider_statuses", lambda: [
        {"id": "chatgpt_web", "label": "ChatGPT Web", "available": True},
    ])

    buf1 = BytesIO()
    Image.new("RGB", (32, 18), color="red").save(buf1, format="PNG")
    b64_16_9 = base64.b64encode(buf1.getvalue()).decode("ascii")

    buf2 = BytesIO()
    Image.new("RGB", (18, 32), color="blue").save(buf2, format="PNG")
    b64_9_16 = base64.b64encode(buf2.getvalue()).decode("ascii")

    sent_payload = {}

    def mock_post(url, headers=None, json=None, timeout=None):
        sent_payload.update(json or {})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": b64_9_16},  # Send 9:16 first to test aspect ratio classifier
                    {"b64_json": b64_16_9},
                ],
                "provider_metadata": {"conversation_url": "https://chatgpt.com/c/dual-test"},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", mock_post)

    (fn_16_9, mime_16_9), (fn_9_16, mime_9_16), provider = generate_storyboard_chatgpt_dual(
        "A detective in an alley",
        "Alley",
        "Medium",
    )

    assert provider == "chatgpt_web"
    assert "Create two images, first 16:9 and 9:16" in sent_payload["prompt"]
    assert sent_payload["n"] == 2
    assert (settings.media_dir / fn_16_9).is_file()
    assert (settings.media_dir / fn_9_16).is_file()

    # Verify that fn_16_9 is actually the landscape image and fn_9_16 is portrait
    with Image.open(settings.media_dir / fn_16_9) as im:
        assert im.width > im.height
    with Image.open(settings.media_dir / fn_9_16) as im:
        assert im.height > im.width


def test_gemini_web_retries_on_client_not_initialized(monkeypatch) -> None:
    calls = []
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    b64_png = base64.b64encode(png_bytes).decode()

    def mock_post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            req = httpx.Request("POST", url)
            resp = httpx.Response(500, text='{"error":"client not initialized"}', request=req)
            raise httpx.HTTPStatusError("500 client not initialized", request=req, response=resp)
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": b64_png}],
                "provider_metadata": {"conversation_url": "https://gemini.google.com/app/c_test"},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", mock_post)
    monkeypatch.setattr(media.time, "sleep", lambda s: None)

    image, model, metadata = media._request_provider_image(
        "gemini_web",
        "A Nigerian provision shop",
        "16:9",
    )

    assert len(calls) == 2
    assert image == png_bytes
    assert metadata.get("conversation_url") == "https://gemini.google.com/app/test"


def test_chatgpt_web_returns_decoded_image_and_metadata(monkeypatch) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    b64_png = base64.b64encode(png_bytes).decode()

    def mock_post(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": b64_png}],
                "provider_metadata": {"conversation_url": "https://chatgpt.com/c/chatgpt-test"},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", mock_post)

    image, model, metadata = media._request_provider_image(
        "chatgpt_web",
        "A Nigerian provision shop",
        "16:9",
    )

    assert image == png_bytes
    assert metadata.get("conversation_url") == "https://chatgpt.com/c/chatgpt-test"
