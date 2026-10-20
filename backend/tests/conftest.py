import pytest

from backend.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_image_provider_order(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("image-order") / "image_provider_order.json"
    monkeypatch.setattr(Settings, "image_provider_order_path", property(lambda self: path))


@pytest.fixture(autouse=True)
def _isolated_stem_separator(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("stem-separator") / "stem_separator.json"
    monkeypatch.setattr(Settings, "stem_separator_path", property(lambda self: path))
