from __future__ import annotations

from backend.luts import (
    LUT_PRESETS,
    build_lut_ffmpeg_filter,
    get_lut_cube_path,
    list_lut_presets,
)


def test_lut_presets_have_unique_ids_and_required_fields() -> None:
    ids = [p.id for p in LUT_PRESETS]
    assert len(ids) == len(set(ids))
    assert len(LUT_PRESETS) >= 18
    for preset in LUT_PRESETS:
        assert preset.id
        assert preset.label
        assert preset.description
        assert preset.category
        assert preset.css_filter
        # Identity should roughly pass through mid-gray.
        r, g, b = preset.transform(0.5, 0.5, 0.5)
        assert 0.0 <= r <= 1.0
        assert 0.0 <= g <= 1.0
        assert 0.0 <= b <= 1.0


def test_list_lut_presets_matches_catalog() -> None:
    listed = list_lut_presets()
    assert len(listed) == len(LUT_PRESETS)
    assert {p["id"] for p in listed} == {p.id for p in LUT_PRESETS}
    expected_new = {
        "bleach_bypass",
        "noir_bw",
        "vhs_tape",
        "soft_pastel",
        "matrix_code",
        "horror_sickly",
        "documentary_natural",
        "crimson_ember",
        "ice_blue_hour",
        "anime_punch",
    }
    assert expected_new.issubset({p["id"] for p in listed})


def test_get_lut_cube_path_none_and_low_strength(tmp_path, monkeypatch) -> None:
    from backend import luts as luts_mod
    from backend.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert get_lut_cube_path("none") is None
    assert get_lut_cube_path("missing_preset") is None
    assert get_lut_cube_path("vibrant_gaming", 0.0) is None
    assert get_lut_cube_path("vibrant_gaming", 0.01) is None
    assert luts_mod.build_lut_ffmpeg_filter("none") == ""


def test_cube_generation_and_cache(tmp_path, monkeypatch) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = get_lut_cube_path("noir_bw", 0.75)
    assert path is not None
    assert path.name == "noir_bw_75.cube"
    text = path.read_text(encoding="utf-8")
    assert "LUT_3D_SIZE 17" in text
    assert "DOMAIN_MIN" in text
    assert "DOMAIN_MAX" in text
    # 17^3 data rows + headers
    data_lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("TITLE", "LUT_", "DOMAIN_"))]
    assert len(data_lines) == 17 * 17 * 17

    # Second call hits cache (same path, no rewrite race).
    path2 = get_lut_cube_path("noir_bw", 0.75)
    assert path2 == path

    filt = build_lut_ffmpeg_filter("noir_bw", 0.75)
    assert filt.startswith(",lut3d=file=")
    assert "interp=tetrahedral" in filt
    assert "noir_bw_75.cube" in filt


def test_noir_is_achromatic_and_night_vision_is_green_dominant() -> None:
    noir = next(p for p in LUT_PRESETS if p.id == "noir_bw")
    r, g, b = noir.transform(0.8, 0.2, 0.3)
    assert abs(r - g) < 1e-6 and abs(g - b) < 1e-6

    nvg = next(p for p in LUT_PRESETS if p.id == "night_vision_stealth")
    r2, g2, b2 = nvg.transform(0.6, 0.5, 0.4)
    assert g2 > r2 and g2 > b2
