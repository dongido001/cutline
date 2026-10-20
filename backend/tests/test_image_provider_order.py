from fastapi.testclient import TestClient

from backend import api
from backend.settings import settings


def _statuses():
    return [
        {"id": p, "label": p, "configured": True, "available": True, "model": "m", "detail": "ok",
         "priority": i, "image_generation": True}
        for i, p in enumerate(settings.image_provider_order, start=1)
    ]


def test_saved_order_reorders_enabled_providers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    assert settings.image_provider_order == ["chatgpt_web", "gemini_web"]

    settings.image_provider_order_path.write_text('{"order": ["gemini_web", "chatgpt_web"]}')
    assert settings.image_provider_order == ["gemini_web", "chatgpt_web"]

    monkeypatch.setattr(settings, "image_providers", "chatgpt_web")
    assert settings.image_provider_order == ["chatgpt_web"]


def test_unreadable_saved_order_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    settings.image_provider_order_path.write_text("not json")
    assert settings.image_provider_order == ["chatgpt_web", "gemini_web"]


def test_order_endpoint_saves_and_returns_statuses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web,gemini_web")
    monkeypatch.setattr(api, "image_provider_statuses", _statuses)
    client = TestClient(api.app)

    response = client.post("/api/image-providers/order", json={"order": ["gemini_web"]})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["gemini_web", "chatgpt_web"]
    assert settings.image_provider_order == ["gemini_web", "chatgpt_web"]


def test_order_endpoint_rejects_disabled_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "image_providers", "chatgpt_web")
    client = TestClient(api.app)

    response = client.post("/api/image-providers/order", json={"order": ["gemini_web"]})

    assert response.status_code == 400
    assert not settings.image_provider_order_path.exists()
