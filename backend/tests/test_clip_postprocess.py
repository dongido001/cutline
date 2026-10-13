import shutil
import subprocess
from pathlib import Path

import pytest

from backend import clip_postprocess
from backend.settings import settings
from backend.tasks import compose_video_prompt

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)


def _make_clip(path: Path, content_s: float, black_s: float) -> None:
    """Colour-bars for `content_s`, then black for `black_s`, with a tone for the audio."""
    total = content_s + black_s
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=12:duration={content_s}",
            "-f", "lavfi", "-i", f"color=c=black:size=160x90:rate=12:duration={black_s}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


@needs_ffmpeg
def test_trailing_black_is_trimmed(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    _make_clip(clip, content_s=2.0, black_s=5.0)
    assert clip_postprocess._probe_duration(clip) == pytest.approx(7.0, abs=0.3)

    assert clip_postprocess.postprocess_clip(clip) is True

    duration = clip_postprocess._probe_duration(clip)
    assert duration is not None
    assert 2.0 <= duration <= 3.0  # content + a short intentional black beat


@needs_ffmpeg
def test_clip_without_black_tail_keeps_its_length(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "clip_audio_denoise", False)
    clip = tmp_path / "clip.mp4"
    _make_clip(clip, content_s=3.0, black_s=0.2)
    assert clip_postprocess.postprocess_clip(clip) is False
    assert clip_postprocess._probe_duration(clip) == pytest.approx(3.2, abs=0.3)


def test_postprocess_ignores_unreadable_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert clip_postprocess.postprocess_clip(broken) is False
    assert broken.read_bytes() == b"not a video"


def test_sound_direction_is_preserved_faithfully() -> None:
    result = compose_video_prompt(
        "Rosa steps forward.",
        "Quiet late-afternoon café room tone, distant surf, Rosa's slippers on the floor, dialogue.",
    )
    assert "room tone" in result.lower()
    assert "distant surf" in result.lower()
    assert "Rosa's slippers on the floor" in result


def test_audio_direction_without_sound_is_unchanged() -> None:
    assert compose_video_prompt("Animate the frame.", "") == "Animate the frame."

