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
