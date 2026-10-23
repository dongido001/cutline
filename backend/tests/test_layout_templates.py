from backend.layout_templates import is_full_bleed, public_catalog, resolve_template, video_rect_px
from backend.schemas import TimelineCanvasLayout, TimelineDocument, TimelinePrimaryItem


def test_shorts_catalog_has_band_templates():
    catalog = public_catalog()
    ids = {item["id"] for item in catalog["shorts"]}
    assert "full_bleed" in ids
    assert "boxed" in ids
    assert "center_stage" in ids
    assert "top_hero" in ids
    assert "bottom_anchor" in ids


def test_boxed_template_rect_centers_16_9():
    template = resolve_template("boxed", "shorts")
    assert not is_full_bleed(template)
    x, y, w, h = video_rect_px(template, 1080, 1920)
    assert x == 0
    assert w == 1080
    assert y == 656
    assert h == 607
    # 656 top + 607 video + 657 bottom = 1920
    assert y + h + 657 == 1920


def test_center_stage_rect_leaves_bands():
    template = resolve_template("center_stage", "shorts")
    assert not is_full_bleed(template)
    x, y, w, h = video_rect_px(template, 1080, 1920)
    assert x == 0
    assert y > 0
    assert y + h < 1920
    assert w == 1080


def test_timeline_document_accepts_layout():
    doc = TimelineDocument(
        primary=[
            TimelinePrimaryItem(
                id="p1",
                kind="title",
                label="Title",
                duration_ms=1000,
                text="Hello",
            )
        ],
        layout=TimelineCanvasLayout(
            template="top_hero",
            matte="gradient",
            top_text="48 HOURS EARLIER",
            bottom_text="Watch the full story",
            brand_text="EDGEFRAME",
            accent_bar=True,
        ),
    )
    assert doc.layout.template == "top_hero"
    assert doc.layout.matte == "gradient"
