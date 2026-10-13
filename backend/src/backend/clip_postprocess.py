"""Post-processing for freshly generated video clips.

Two problems seen with generated clips:
* a long dead black tail after a "snap to black" (the clip stays black until
  its requested duration ends);
* a steady "shhhh" hiss in the model-generated audio bed.

Both are fixed here with ffmpeg. Every step is best-effort: on any failure the
original file is left untouched.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .settings import settings

logger = logging.getLogger(__name__)

# Seconds of black kept after the last visible frame so the cut still feels intentional.
BLACK_TAIL_KEEP_SECONDS = 0.4
# Only trim when the black tail is at least this long and real content precedes it.
MIN_TRAILING_BLACK_SECONDS = 1.0
MIN_CONTENT_SECONDS = 0.5

# Gentle spectral denoise plus rumble cut. Conservative so dialogue stays natural.
AUDIO_DENOISE_FILTER = "highpass=f=70,afftdn=nr=10:nf=-45:tn=1"

_BLACK_RE = re.compile(r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)")


def _probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return None


def _has_audio(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return "audio" in out
    except Exception:  # noqa: BLE001
        return False


def trailing_black_start(path: Path, duration: float) -> float | None:
    """Return when the final uninterrupted black run starts, or None if the clip doesn't end black."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.1:pix_th=0.05",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
    except Exception:  # noqa: BLE001
        return None
    runs = [(float(m["start"]), float(m["end"])) for m in _BLACK_RE.finditer(proc.stderr)]
    if not runs:
        return None
    start, end = runs[-1]
    if end < duration - 0.15:  # black ends before the clip does -> not a dead tail
        return None
    if duration - start < MIN_TRAILING_BLACK_SECONDS or start < MIN_CONTENT_SECONDS:
        return None
    return start


def postprocess_clip(path: Path) -> bool:
    """Trim a dead black tail and denoise audio in place. Returns True if the file was rewritten."""
    if not (settings.clip_trim_trailing_black or settings.clip_audio_denoise):
        return False
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None or not path.is_file():
        return False
    duration = _probe_duration(path)
    if not duration:
        return False

    trim_to: float | None = None
    if settings.clip_trim_trailing_black:
        black_start = trailing_black_start(path, duration)
        if black_start is not None:
            trim_to = min(duration, black_start + BLACK_TAIL_KEEP_SECONDS)

    denoise = settings.clip_audio_denoise and _has_audio(path)
    if trim_to is None and not denoise:
        return False

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path)]
    if trim_to is not None:
        cmd += ["-t", f"{trim_to:.3f}", "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    if denoise:
        cmd += ["-af", AUDIO_DENOISE_FILTER, "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:a", "copy"]
    cmd += ["-movflags", "+faststart"]

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=path.suffix, dir=path.parent, delete=False) as handle:
            tmp_path = Path(handle.name)
        subprocess.run(cmd + [str(tmp_path)], capture_output=True, text=True, timeout=300, check=True)
        new_duration = _probe_duration(tmp_path)
        if not new_duration or new_duration < MIN_CONTENT_SECONDS:
            return False
        tmp_path.replace(path)
        tmp_path = None
        logger.info(
            "Post-processed %s (trim_to=%s, denoise=%s)", path.name,
            f"{trim_to:.2f}s" if trim_to is not None else "no", denoise,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clip post-processing skipped for %s: %s", path.name, exc)
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
