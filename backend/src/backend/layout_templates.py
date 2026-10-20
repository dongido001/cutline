"""Canvas layout templates for Shorts / landscape exports.

Templates place the primary footage in a rect inside the frame and fill the
remaining bands with matte styles + optional hook/caption/brand text.
"""

from __future__ import annotations

from typing import Any, Literal

AspectFamily = Literal["shorts", "landscape"]

# Video window as fractions of the canvas (x, y, w, h).
TemplateDef = dict[str, Any]

SHORTS_TEMPLATES: dict[str, TemplateDef] = {
    "full_bleed": {
        "id": "full_bleed",
        "label": "Full bleed",
        "blurb": "Edge-to-edge — classic Shorts fill",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        "default_matte": "solid",
    },
    "center_stage": {
        "id": "center_stage",
        "label": "Center stage",
        "blurb": "Video mid-frame · room for hook + caption",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.16, "w": 1.0, "h": 0.68},
        "default_matte": "blur",
        "slots": {"top": "hook", "bottom": "caption"},
    },
    "top_hero": {
        "id": "top_hero",
        "label": "Top hero",
        "blurb": "Footage up top · CTA / caption band below",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.70},
        "default_matte": "gradient",
        "slots": {"bottom": "caption"},
    },
    "bottom_anchor": {
        "id": "bottom_anchor",
        "label": "Bottom anchor",
        "blurb": "Hook band on top · video locked low",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.30, "w": 1.0, "h": 0.70},
        "default_matte": "gradient",
        "slots": {"top": "hook"},
    },
    "caption_stack": {
        "id": "caption_stack",
        "label": "Caption stack",
        "blurb": "Taller bottom band for multi-line captions",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.58},
        "default_matte": "solid",
        "slots": {"bottom": "caption"},
    },
    "split_billboard": {
        "id": "split_billboard",
        "label": "Split billboard",
        "blurb": "Equal bands — title card energy",
        "family": "shorts",
        "video": {"x": 0.0, "y": 0.22, "w": 1.0, "h": 0.56},
        "default_matte": "duotone",
        "slots": {"top": "hook", "bottom": "brand"},
        "accent_bar": True,
    },
}

LANDSCAPE_TEMPLATES: dict[str, TemplateDef] = {
    "full_bleed": {
        "id": "full_bleed",
        "label": "Full bleed",
        "blurb": "Edge-to-edge widescreen",
        "family": "landscape",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        "default_matte": "solid",
    },
    "cinematic_bars": {
        "id": "cinematic_bars",
        "label": "Cinematic bars",
        "blurb": "Letterbox bands for prestige / title cards",
        "family": "landscape",
        "video": {"x": 0.0, "y": 0.12, "w": 1.0, "h": 0.76},
        "default_matte": "solid",
        "slots": {"top": "hook", "bottom": "brand"},
        "accent_bar": True,
    },
    "brand_footer": {
        "id": "brand_footer",
        "label": "Brand footer",
        "blurb": "Thin footer strip for logo / channel name",
        "family": "landscape",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.88},
        "default_matte": "gradient",
        "slots": {"bottom": "brand"},
    },
    "lower_third_room": {
        "id": "lower_third_room",
        "label": "Lower-third room",
        "blurb": "Extra bottom space for nameplates & CTAs",
        "family": "landscape",
        "video": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.78},
        "default_matte": "blur",
        "slots": {"bottom": "caption"},
    },
}


def templates_for(family: AspectFamily) -> list[TemplateDef]:
    catalog = SHORTS_TEMPLATES if family == "shorts" else LANDSCAPE_TEMPLATES
    return list(catalog.values())


def resolve_template(template_id: str | None, family: AspectFamily) -> TemplateDef:
    catalog = SHORTS_TEMPLATES if family == "shorts" else LANDSCAPE_TEMPLATES
    key = (template_id or "full_bleed").strip() or "full_bleed"
    return catalog.get(key) or catalog["full_bleed"]


def video_rect_px(template: TemplateDef, width: int, height: int) -> tuple[int, int, int, int]:
    rect = template.get("video") or {"x": 0, "y": 0, "w": 1, "h": 1}
    x = max(0, min(width - 2, round(width * float(rect.get("x", 0)))))
    y = max(0, min(height - 2, round(height * float(rect.get("y", 0)))))
    w = max(16, min(width - x, round(width * float(rect.get("w", 1)))))
    h = max(16, min(height - y, round(height * float(rect.get("h", 1)))))
    return x, y, w, h


def is_full_bleed(template: TemplateDef) -> bool:
    rect = template.get("video") or {}
    return (
        abs(float(rect.get("x", 0))) < 1e-6
        and abs(float(rect.get("y", 0))) < 1e-6
        and abs(float(rect.get("w", 1)) - 1.0) < 1e-6
        and abs(float(rect.get("h", 1)) - 1.0) < 1e-6
    )


def public_catalog() -> dict[str, list[dict[str, Any]]]:
    def slim(item: TemplateDef) -> dict[str, Any]:
        return {
            "id": item["id"],
            "label": item["label"],
            "blurb": item["blurb"],
            "family": item["family"],
            "video": item["video"],
            "default_matte": item.get("default_matte", "solid"),
            "slots": item.get("slots") or {},
            "accent_bar": bool(item.get("accent_bar")),
        }

    return {
        "shorts": [slim(t) for t in SHORTS_TEMPLATES.values()],
        "landscape": [slim(t) for t in LANDSCAPE_TEMPLATES.values()],
    }
