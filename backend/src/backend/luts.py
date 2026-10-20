from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .settings import settings


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _s_curve(x: float, k: float = 1.8) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    sig = 1.0 / (1.0 + math.exp(-k * (x - 0.5) * 4))
    sig0 = 1.0 / (1.0 + math.exp(2.0 * k))
    sig1 = 1.0 / (1.0 + math.exp(-2.0 * k))
    return (sig - sig0) / (sig1 - sig0)


def _sat(r: float, g: float, b: float, factor: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (
        _clamp(luma + factor * (r - luma)),
        _clamp(luma + factor * (g - luma)),
        _clamp(luma + factor * (b - luma)),
    )


@dataclass(frozen=True)
class LUTPreset:
    id: str
    label: str
    description: str
    category: str
    css_filter: str
    transform: Callable[[float, float, float], tuple[float, float, float]]


def _transform_vibrant_gaming(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 1.40)
    return (_s_curve(r1, 1.5), _s_curve(g1, 1.5), _s_curve(b1, 1.5))


def _transform_warzone_crisp(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1 = _s_curve(_clamp(r**1.15), 1.7)
    g1 = _s_curve(_clamp(g**1.12), 1.7)
    b1 = _s_curve(_clamp(b**1.20), 1.7)
    return _sat(r1, g1, b1, 1.25)


def _transform_teal_orange(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 1.25)
    out_r = _clamp(_s_curve(r1 + (0.12 * luma - 0.08 * (1.0 - luma)), 1.6))
    out_g = _clamp(_s_curve(g1 + (0.04 * luma + 0.03 * (1.0 - luma)), 1.6))
    out_b = _clamp(_s_curve(b1 + (-0.12 * luma + 0.14 * (1.0 - luma)), 1.6))
    return (out_r, out_g, out_b)


def _transform_cyberpunk_neon(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 1.45)
    out_r = _clamp(_s_curve(r1 + (0.10 * (1.0 - luma) if luma < 0.5 else 0.12), 2.0))
    out_g = _clamp(_s_curve(g1 * 0.95, 2.0))
    out_b = _clamp(_s_curve(b1 + (0.18 * (1.0 - luma) if luma < 0.5 else 0.08), 2.0))
    return (out_r, out_g, out_b)


def _transform_warm_sunshine(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 1.28)
    out_r = _clamp(_s_curve(0.02 + 0.98 * r1 * 1.10, 1.3))
    out_g = _clamp(_s_curve(0.02 + 0.98 * g1 * 1.04, 1.3))
    out_b = _clamp(_s_curve(0.01 + 0.99 * b1 * 0.90, 1.3))
    return (out_r, out_g, out_b)


def _transform_cold_tactical(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 0.75)
    out_r = _clamp(_s_curve(r1 * 0.92, 1.9))
    out_g = _clamp(_s_curve(g1 * 0.96, 1.9))
    out_b = _clamp(_s_curve(b1 * 1.12, 1.9))
    return (out_r, out_g, out_b)


def _transform_high_contrast_pop(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 1.25)
    return (_clamp(_s_curve(r1, 2.3)), _clamp(_s_curve(g1, 2.3)), _clamp(_s_curve(b1, 2.3)))


def _transform_sunset_glow(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 1.35)
    out_r = _clamp(_s_curve(r1 + (0.16 * luma + 0.05 * (1.0 - luma)), 1.5))
    out_g = _clamp(_s_curve(g1 + (0.07 * luma), 1.5))
    out_b = _clamp(_s_curve(b1 + (-0.12 * luma + 0.12 * (1.0 - luma)), 1.5))
    return (out_r, out_g, out_b)


def _transform_retro_arcade(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 1.35)
    out_r = _clamp(_s_curve(0.05 + 0.95 * r1 + (0.06 * luma), 1.6))
    out_g = _clamp(_s_curve(0.05 + 0.95 * g1 + (0.04 * (1.0 - luma)), 1.6))
    out_b = _clamp(_s_curve(0.05 + 0.95 * b1 + (0.08 * (1.0 - luma)), 1.6))
    return (out_r, out_g, out_b)


def _transform_night_vision_stealth(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    out_r = _clamp(_s_curve(luma * 0.40, 2.0))
    out_g = _clamp(_s_curve(luma * 1.40, 2.0))
    out_b = _clamp(_s_curve(luma * 0.35, 2.0))
    return (out_r, out_g, out_b)


def _transform_bleach_bypass(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Gritty action look: keep luminance punch, crush chroma."""
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 0.55)
    mix = 0.55
    r2 = _clamp(r1 * (1.0 - mix) + luma * mix)
    g2 = _clamp(g1 * (1.0 - mix) + luma * mix)
    b2 = _clamp(b1 * (1.0 - mix) + luma * mix)
    return (_s_curve(r2, 2.1), _s_curve(g2, 2.1), _s_curve(b2, 2.1))


def _transform_noir_bw(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    # Slightly cooler B&W with crushed shadows / bright highlights.
    v = _s_curve(_clamp(luma**1.08), 2.2)
    return (v, v, v)


def _transform_vhs_tape(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    # Lifted blacks, soft contrast, magenta/cyan channel drift.
    r1 = _clamp(0.06 + 0.88 * r + 0.04 * (1.0 - luma))
    g1 = _clamp(0.05 + 0.90 * g)
    b1 = _clamp(0.07 + 0.86 * b + 0.05 * luma)
    r2, g2, b2 = _sat(r1, g1, b1, 1.15)
    return (_s_curve(r2, 1.15), _s_curve(g2, 1.15), _s_curve(b2, 1.15))


def _transform_soft_pastel(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 0.85)
    # Lift shadows, tame highlights, gentle pastel wash.
    out_r = _clamp(0.08 + 0.84 * r1 + 0.04)
    out_g = _clamp(0.07 + 0.86 * g1 + 0.02)
    out_b = _clamp(0.09 + 0.84 * b1 + 0.03)
    return (_s_curve(out_r, 1.05), _s_curve(out_g, 1.05), _s_curve(out_b, 1.05))


def _transform_matrix_code(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    # Keep some color but bias hard into digital green.
    r1 = _clamp(_s_curve(luma * 0.55 + r * 0.15, 1.8))
    g1 = _clamp(_s_curve(luma * 1.15 + g * 0.25, 1.8))
    b1 = _clamp(_s_curve(luma * 0.45 + b * 0.10, 1.8))
    return _sat(r1, g1, b1, 1.20)


def _transform_horror_sickly(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 0.70)
    out_r = _clamp(_s_curve(r1 * 0.95 + 0.04 * (1.0 - luma), 1.7))
    out_g = _clamp(_s_curve(g1 * 1.08 + 0.06 * (1.0 - luma), 1.7))
    out_b = _clamp(_s_curve(b1 * 0.78, 1.7))
    return (out_r, out_g, out_b)


def _transform_documentary_natural(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 0.88)
    out_r = _clamp(_s_curve(r1 * 0.98, 1.25))
    out_g = _clamp(_s_curve(g1 * 1.00, 1.25))
    out_b = _clamp(_s_curve(b1 * 1.02, 1.25))
    return (out_r, out_g, out_b)


def _transform_crimson_ember(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 1.20)
    out_r = _clamp(_s_curve(r1 + 0.14 * luma + 0.04 * (1.0 - luma), 1.6))
    out_g = _clamp(_s_curve(g1 * 0.92 + 0.02 * luma, 1.6))
    out_b = _clamp(_s_curve(b1 * 0.78 + 0.06 * (1.0 - luma), 1.6))
    return (out_r, out_g, out_b)


def _transform_ice_blue_hour(r: float, g: float, b: float) -> tuple[float, float, float]:
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r1, g1, b1 = _sat(r, g, b, 0.90)
    out_r = _clamp(_s_curve(r1 * 0.88, 1.55))
    out_g = _clamp(_s_curve(g1 * 0.96 + 0.02 * (1.0 - luma), 1.55))
    out_b = _clamp(_s_curve(b1 * 1.14 + 0.06 * (1.0 - luma), 1.55))
    return (out_r, out_g, out_b)


def _transform_anime_punch(r: float, g: float, b: float) -> tuple[float, float, float]:
    r1, g1, b1 = _sat(r, g, b, 1.55)
    # Harder midtone contrast for cel-shaded / cartoon game looks.
    return (
        _clamp(_s_curve(r1**0.92, 2.0)),
        _clamp(_s_curve(g1**0.92, 2.0)),
        _clamp(_s_curve(b1**0.92, 2.0)),
    )


LUT_PRESETS: list[LUTPreset] = [
    LUTPreset(
        id="vibrant_gaming",
        label="Vibrant Gaming",
        description="Punchy saturation and clarity for colorful games",
        category="Gaming",
        css_filter="saturate(1.4) contrast(1.15) brightness(1.02)",
        transform=_transform_vibrant_gaming,
    ),
    LUTPreset(
        id="warzone_crisp",
        label="Warzone Crisp",
        description="Cuts through dust and fog haze with deep shadows",
        category="Gaming",
        css_filter="contrast(1.22) saturate(1.2) brightness(0.98)",
        transform=_transform_warzone_crisp,
    ),
    LUTPreset(
        id="teal_orange",
        label="Teal & Orange",
        description="Cinematic Hollywood action block with cool shadows and warm skin tones",
        category="Cinematic",
        css_filter="contrast(1.18) saturate(1.25) sepia(0.2) hue-rotate(-15deg)",
        transform=_transform_teal_orange,
    ),
    LUTPreset(
        id="cyberpunk_neon",
        label="Cyberpunk Neon",
        description="Deep midnight blues, electric cyan, and rich neon highlights",
        category="Creative",
        css_filter="contrast(1.25) saturate(1.5) hue-rotate(18deg)",
        transform=_transform_cyberpunk_neon,
    ),
    LUTPreset(
        id="warm_sunshine",
        label="Warm Sunshine",
        description="Golden hour warmth with lifted soft shadows",
        category="Atmospheric",
        css_filter="sepia(0.22) saturate(1.28) contrast(1.08) brightness(1.03)",
        transform=_transform_warm_sunshine,
    ),
    LUTPreset(
        id="cold_tactical",
        label="Cold Tactical",
        description="Militaristic desaturated slate blues with high edge contrast",
        category="Gaming",
        css_filter="saturate(0.75) contrast(1.2) hue-rotate(-10deg) brightness(0.97)",
        transform=_transform_cold_tactical,
    ),
    LUTPreset(
        id="high_contrast_pop",
        label="High Contrast Pop",
        description="Deep crushed blacks and gleaming bright highlights for social clips",
        category="Social",
        css_filter="contrast(1.35) saturate(1.2)",
        transform=_transform_high_contrast_pop,
    ),
    LUTPreset(
        id="sunset_glow",
        label="Sunset Glow",
        description="Rich amber-peach highlights with deep violet shadows",
        category="Atmospheric",
        css_filter="sepia(0.25) saturate(1.35) hue-rotate(-25deg) contrast(1.12)",
        transform=_transform_sunset_glow,
    ),
    LUTPreset(
        id="retro_arcade",
        label="Retro 80s Arcade",
        description="Vintage black-point lift and nostalgic synthwave cross-processing",
        category="Creative",
        css_filter="contrast(1.2) saturate(1.3) sepia(0.15) hue-rotate(10deg)",
        transform=_transform_retro_arcade,
    ),
    LUTPreset(
        id="night_vision_stealth",
        label="Night Vision / Stealth",
        description="Tactical phosphor green monochrome with high silhouette clarity",
        category="Gaming",
        # Must approximate mono green (not a mere hue shift of full-color footage).
        css_filter="grayscale(1) brightness(0.95) contrast(1.25) sepia(1) hue-rotate(70deg) saturate(2.8)",
        transform=_transform_night_vision_stealth,
    ),
    LUTPreset(
        id="bleach_bypass",
        label="Bleach Bypass",
        description="Gritty action silver-retention: hard contrast, drained chroma",
        category="Cinematic",
        css_filter="saturate(0.55) contrast(1.35) brightness(0.98)",
        transform=_transform_bleach_bypass,
    ),
    LUTPreset(
        id="noir_bw",
        label="Noir B&W",
        description="Classic high-contrast black and white with crushed shadows",
        category="Cinematic",
        css_filter="grayscale(1) contrast(1.35) brightness(0.98)",
        transform=_transform_noir_bw,
    ),
    LUTPreset(
        id="vhs_tape",
        label="VHS Tape",
        description="Lifted blacks, soft contrast, and nostalgic magenta/cyan drift",
        category="Creative",
        css_filter="contrast(0.92) saturate(1.12) sepia(0.18) hue-rotate(-8deg) brightness(1.04)",
        transform=_transform_vhs_tape,
    ),
    LUTPreset(
        id="soft_pastel",
        label="Soft Pastel",
        description="Dreamy lifted shadows and gentle desaturated pastel wash",
        category="Atmospheric",
        css_filter="saturate(0.85) contrast(0.92) brightness(1.06) sepia(0.08)",
        transform=_transform_soft_pastel,
    ),
    LUTPreset(
        id="matrix_code",
        label="Matrix Code",
        description="Digital green bias with residual color — hacker / sim aesthetic",
        category="Gaming",
        css_filter="grayscale(0.35) sepia(0.45) hue-rotate(70deg) saturate(1.8) contrast(1.18)",
        transform=_transform_matrix_code,
    ),
    LUTPreset(
        id="horror_sickly",
        label="Horror Sickly",
        description="Uneasy yellow-green cast with drained warmth for dread",
        category="Creative",
        css_filter="saturate(0.7) contrast(1.15) sepia(0.2) hue-rotate(35deg) brightness(0.96)",
        transform=_transform_horror_sickly,
    ),
    LUTPreset(
        id="documentary_natural",
        label="Documentary Natural",
        description="Slightly restrained chroma and gentle contrast for interview/doc cuts",
        category="Social",
        css_filter="saturate(0.88) contrast(1.06) brightness(1.01)",
        transform=_transform_documentary_natural,
    ),
    LUTPreset(
        id="crimson_ember",
        label="Crimson Ember",
        description="Deep ember reds and smoky shadows for intense trailers",
        category="Atmospheric",
        css_filter="sepia(0.28) saturate(1.3) hue-rotate(-18deg) contrast(1.16) brightness(0.97)",
        transform=_transform_crimson_ember,
    ),
    LUTPreset(
        id="ice_blue_hour",
        label="Ice Blue Hour",
        description="Cold twilight blues with restrained warmth — night city / snow",
        category="Atmospheric",
        css_filter="saturate(0.9) contrast(1.12) hue-rotate(-18deg) brightness(0.98)",
        transform=_transform_ice_blue_hour,
    ),
    LUTPreset(
        id="anime_punch",
        label="Anime Punch",
        description="Cel-shaded saturation and hard midtone snap for stylized games",
        category="Gaming",
        css_filter="saturate(1.55) contrast(1.28) brightness(1.02)",
        transform=_transform_anime_punch,
    ),
]

_PRESET_MAP = {preset.id: preset for preset in LUT_PRESETS}


def list_lut_presets() -> list[dict[str, str]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "category": p.category,
            "css_filter": p.css_filter,
        }
        for p in LUT_PRESETS
    ]


def _ensure_luts_dir() -> Path:
    luts_dir = settings.data_dir / "luts"
    luts_dir.mkdir(parents=True, exist_ok=True)
    return luts_dir


def get_lut_cube_path(lut_id: str, strength: float = 1.0) -> Path | None:
    if not lut_id or lut_id == "none" or lut_id not in _PRESET_MAP:
        return None
    s = _clamp(strength)
    if s < 0.02:
        return None

    # Quantize strength to 5% increments for caching
    pct = int(round(s * 20) * 5)
    if pct <= 0:
        return None

    luts_dir = _ensure_luts_dir()
    cube_path = luts_dir / f"{lut_id}_{pct}.cube"
    if cube_path.is_file():
        return cube_path

    # Generate the 3D LUT .cube file with the specified strength
    preset = _PRESET_MAP[lut_id]
    size = 17
    lines: list[str] = [
        f'TITLE "{preset.label} {pct}%"\n',
        f"LUT_3D_SIZE {size}\n",
        "DOMAIN_MIN 0.0 0.0 0.0\n",
        "DOMAIN_MAX 1.0 1.0 1.0\n",
    ]
    blend_s = pct / 100.0
    for b_idx in range(size):
        b_in = b_idx / (size - 1)
        for g_idx in range(size):
            g_in = g_idx / (size - 1)
            for r_idx in range(size):
                r_in = r_idx / (size - 1)
                r_full, g_full, b_full = preset.transform(r_in, g_in, b_in)
                r_out = _clamp(r_in * (1.0 - blend_s) + r_full * blend_s)
                g_out = _clamp(g_in * (1.0 - blend_s) + g_full * blend_s)
                b_out = _clamp(b_in * (1.0 - blend_s) + b_full * blend_s)
                lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}\n")

    temp_path = cube_path.with_suffix(f".tmp.{pct}")
    temp_path.write_text("".join(lines), encoding="utf-8")
    temp_path.replace(cube_path)
    return cube_path


def build_lut_ffmpeg_filter(lut_id: str, strength: float = 1.0) -> str:
    path = get_lut_cube_path(lut_id, strength)
    if not path:
        return ""
    # Safe POSIX path for FFmpeg filter parameter
    escaped = str(path.resolve()).replace(":", r"\:").replace("'", r"\'")
    return f",lut3d=file='{escaped}':interp=tetrahedral"
