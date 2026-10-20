import json
import subprocess
import tempfile
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from .settings import settings


BITMAP_FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00110", "00100"),
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    " ": ("00000",) * 7,
}


def _render_title_card(text: str, path: Path, width: int, height: int) -> None:
    line_width = 26 if height > width else 48
    lines = textwrap.wrap(text.upper(), width=line_width) or [""]
    longest = max(len(line) for line in lines)
    scale = max(2, min(8, width // max(1, longest * 6), height // max(1, len(lines) * 10)))
    pixels = bytearray(width * height * 3)
    line_height = 9 * scale
    top = (height - len(lines) * line_height) // 2
    for line_index, line in enumerate(lines):
        line_width_px = max(0, len(line) * 6 * scale - scale)
        left = (width - line_width_px) // 2
        for character_index, character in enumerate(line):
            glyph = BITMAP_FONT.get(character, BITMAP_FONT[" "])
            glyph_left = left + character_index * 6 * scale
            glyph_top = top + line_index * line_height
            for row_index, row in enumerate(glyph):
                for column_index, enabled in enumerate(row):
                    if enabled != "1":
                        continue
                    for offset_y in range(scale):
                        y = glyph_top + row_index * scale + offset_y
                        for offset_x in range(scale):
                            x = glyph_left + column_index * scale + offset_x
                            pixel = (y * width + x) * 3
                            pixels[pixel : pixel + 3] = b"\xf4\xf4\xf2"
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + pixels)


def invalidate_final_cut(project, db: Session) -> str | None:
    if not project.final_cut:
        return None
    filename = project.final_cut.filename
    db.delete(project.final_cut)
    return filename


@dataclass(frozen=True)
class FinalCutSource:
    title: str
    path: Path
    duration_ms: int
    is_clip: bool
    title_card_text: str = ""


@dataclass(frozen=True)
class FinalCutResult:
    filename: str
    duration_ms: int
    width: int
    height: int
    clip_count: int
    fallback_count: int


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
        detail = "\n".join(lines[-15:]) if lines else "Unknown FFmpeg error"
        raise RuntimeError(f"FFmpeg failed: {detail}")


def export_encode_args(*, include_audio_rate: bool = True, fast_intermediate: bool = False) -> list[str]:
    """Shared H.264/AAC args for timeline + final-cut exports."""
    if fast_intermediate:
        preset = "veryfast"
        crf = max(18, min(51, int(settings.export_video_crf)))
    else:
        preset = (settings.export_video_preset or "veryfast").strip() or "veryfast"
        crf = max(0, min(51, int(settings.export_video_crf)))
    audio_k = max(64, min(512, int(settings.export_audio_bitrate_k)))
    args = [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        # Force SDR tags so HDR source metadata (BT.2020/PQ) is not copied onto 8-bit exports.
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-color_range", "tv",
        "-c:a", "aac",
        "-b:a", f"{audio_k}k",
    ]
    if include_audio_rate:
        args.extend(["-ar", "48000"])
    args.extend(["-movflags", "+faststart"])
    return args



def _has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _normalize_source(
    source: FinalCutSource,
    destination: Path,
    width: int,
    height: int,
) -> None:
    duration = source.duration_ms / 1000
    video_filter = (
        "[0:v]"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,fps=30,"
    )
    command = ["ffmpeg", "-y"]
    if source.is_clip:
        command.extend(["-i", str(source.path)])
        video_filter += f"tpad=stop_mode=clone:stop_duration={duration},"
    else:
        command.extend(["-loop", "1", "-i", str(source.path)])
    video_filter += f"trim=duration={duration},setpts=PTS-STARTPTS[base]"

    title_input_index: int | None = None
    if source.title_card_text.strip():
        title_input_index = 1
        title_path = destination.with_suffix(".title.ppm")
        _render_title_card(source.title_card_text, title_path, width, height)
        command.extend(["-loop", "1", "-i", str(title_path)])
        card_start = max(0.0, duration - 2.0)
        video_filter += (
            f";[{title_input_index}:v]fps=30,trim=duration={duration},"
            "setpts=PTS-STARTPTS[card];"
            f"[base][card]overlay=enable='gte(t,{card_start})'[v]"
        )
    else:
        video_filter += ";[base]null[v]"

    has_audio = source.is_clip and _has_audio(source.path)
    if has_audio:
        filter_complex = (
            f"{video_filter};"
            f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a]"
        )
        audio_map = ["-map", "[a]"]
    else:
        silence_input_index = 2 if title_input_index is not None else 1
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                str(duration),
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )
        filter_complex = video_filter
        audio_map = ["-map", f"{silence_input_index}:a:0"]

    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            *audio_map,
            "-t",
            str(duration),
            *export_encode_args(),
            str(destination),
        ]
    )
    _run(command)


def probe_duration_ms(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return round(float(json.loads(result.stdout)["format"]["duration"]) * 1000)


def assemble_final_cut(
    sources: list[FinalCutSource],
    aspect_ratio: str,
    progress: Callable[[int, int], None] | None = None,
) -> FinalCutResult:
    if not sources:
        raise ValueError("The project has no shots to assemble")
    missing = [source.title for source in sources if not source.path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing media for: {', '.join(missing)}")

    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    filename = f"final-cut-{uuid.uuid4()}.mp4"
    output = settings.media_dir / filename
    with tempfile.TemporaryDirectory(prefix="cutline-final-") as directory:
        workspace = Path(directory)
        segments: list[Path] = []
        for index, source in enumerate(sources):
            segment = workspace / f"segment-{index:04d}.mp4"
            _normalize_source(source, segment, width, height)
            segments.append(segment)
            if progress:
                progress(index + 1, len(sources))

        concat_file = workspace / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{segment.as_posix()}'\n" for segment in segments)
        )
        temporary_output = workspace / "final.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(temporary_output),
            ]
        )
        temporary_output.replace(output)

    return FinalCutResult(
        filename=filename,
        duration_ms=probe_duration_ms(output),
        width=width,
        height=height,
        clip_count=sum(source.is_clip for source in sources),
        fallback_count=sum(not source.is_clip for source in sources),
    )
