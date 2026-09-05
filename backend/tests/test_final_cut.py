import shutil
import subprocess

import pytest

from backend.final_cut import FinalCutSource, assemble_final_cut
from backend.settings import settings


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_assemble_final_cut_preserves_audio_and_image_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    image = tmp_path / "frame.ppm"
    image.write_text("P3\n2 2\n255\n120 20 20  120 20 20\n120 20 20  120 20 20\n")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:r=30:d=0.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )

    result = assemble_final_cut(
        [
            FinalCutSource("Clip", clip, 400, True),
            FinalCutSource(
                "Frame",
                image,
                600,
                False,
                title_card_text="Time does not wait.",
            ),
        ],
        "16:9",
    )

    output = tmp_path / "media" / result.filename
    assert output.is_file()
    assert result.clip_count == 1
    assert result.fallback_count == 1
    assert result.width == 1280
    assert result.height == 720
    assert 950 <= result.duration_ms <= 1100
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "aac"
