"""Optical punch-in and direct frame crop utilities."""

from __future__ import annotations

from pathlib import Path
from PIL import Image


def crop_and_enhance_frame(
    src_path: Path,
    dest_path: Path,
    aspect_ratio: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[int, int]:
    """Crops a region from src_path, fits to aspect_ratio, and upscales cleanly."""
    with Image.open(src_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        orig_w, orig_h = img.size

        # Clamp normalized coordinates to image bounds
        crop_x = max(0, min(int(round(x * orig_w)), orig_w - 1))
        crop_y = max(0, min(int(round(y * orig_h)), orig_h - 1))
        crop_w = max(1, min(int(round(width * orig_w)), orig_w - crop_x))
        crop_h = max(1, min(int(round(height * orig_h)), orig_h - crop_y))

        cropped = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))

        # Target aspect ratio
        target_ratio = (16.0 / 9.0) if aspect_ratio == "16:9" else (9.0 / 16.0)
        actual_ratio = crop_w / crop_h

        # Center-crop to exact aspect ratio if slightly off to prevent any distortion
        if actual_ratio > target_ratio:
            new_w = max(1, int(round(crop_h * target_ratio)))
            diff = (crop_w - new_w) // 2
            cropped = cropped.crop((diff, 0, diff + new_w, crop_h))
        elif actual_ratio < target_ratio:
            new_h = max(1, int(round(crop_w / target_ratio)))
            diff = (crop_h - new_h) // 2
            cropped = cropped.crop((0, diff, crop_w, diff + new_h))

        # Standard clean delivery resolution
        target_size = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        enhanced = cropped.resize(target_size, resample=Image.Resampling.LANCZOS)
        enhanced.save(dest_path, format="PNG", optimize=True)

        return target_size
