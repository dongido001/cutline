"""VibeVoice (Microsoft community TTS) dialogue synthesis for Cutline."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any

from .settings import settings


def vibevoice_status() -> tuple[bool, bool, str]:
    root = Path(settings.vibevoice_root)
    voices = root / "demo" / "voices"
    configured = root.is_dir() and voices.is_dir()
    if settings.vibevoice_mode == "mock":
        return True, True, "Mock VibeVoice (test mode)"
    if not configured:
        return False, False, f"VibeVoice not found at {root}"
    voice_count = len(list(voices.glob("*.wav")))
    if voice_count == 0:
        return True, False, "VibeVoice voices directory is empty"
    script = root / "demo" / "inference_from_file.py"
    if not script.is_file():
        return True, False, "VibeVoice inference script missing"
    return True, True, f"Ready · {voice_count} voice presets"


def format_dialogue_script(dialogue: list[dict[str, Any]], speaker_map: dict[str, str] | None = None) -> str:
    """Format dialogue for VibeVoice: 'Speaker N: text' lines."""
    lines: list[str] = []
    speaker_map = speaker_map or {}
    speaker_ids: dict[str, int] = {}
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("line") or item.get("dialogue") or "").strip()
        if not text:
            continue
        character = str(item.get("character") or item.get("speaker") or "Speaker").strip()
        key = character.upper()
        if key not in speaker_ids:
            speaker_ids[key] = len(speaker_ids) + 1
        number = speaker_ids[key]
        lines.append(f"Speaker {number}: {text}")
    return "\n".join(lines)


def _list_voice_presets() -> list[Path]:
    voices = Path(settings.vibevoice_root) / "demo" / "voices"
    if not voices.is_dir():
        return []
    return sorted(voices.glob("*.wav"))


def _write_silent_wav(path: Path, duration_ms: int = 1000) -> None:
    frames = max(1, int(24000 * (duration_ms / 1000)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * frames)


def synthesize_dialogue(
    dialogue: list[dict[str, Any]],
    *,
    duration_hint_ms: int = 3000,
    speaker_map: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Return (filename, mime_type, provider) written into media_dir."""
    script = format_dialogue_script(dialogue, speaker_map)
    if not script.strip():
        raise RuntimeError("No dialogue lines to synthesize")

    filename = f"dialogue-{uuid.uuid4()}.wav"
    destination = settings.media_dir / filename

    if settings.vibevoice_mode == "mock":
        _write_silent_wav(destination, max(800, min(duration_hint_ms, 8000)))
        return filename, "audio/wav", "vibevoice-mock"

    configured, available, detail = vibevoice_status()
    if not configured or not available:
        raise RuntimeError(detail)

    root = Path(settings.vibevoice_root)
    inference = root / "demo" / "inference_from_file.py"
    presets = _list_voice_presets()
    speaker_names: list[str] = []
    seen: set[str] = set()
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("line") or item.get("dialogue") or "").strip()
        if not text:
            continue
        character = str(item.get("character") or item.get("speaker") or "Speaker").strip()
        key = character.upper()
        if key in seen:
            continue
        seen.add(key)
        # 1. Use explicit assigned voice from speaker_map if provided
        assigned = speaker_map.get(key) or speaker_map.get(character) if speaker_map else None
        if assigned:
            # Strip file extension if passed
            assigned_stem = Path(assigned).stem
            speaker_names.append(assigned_stem)
        else:
            # Prefer matching voice preset stems; fall back to Andrew/Ava style names.
            match = next((path.stem for path in presets if character.lower() in path.stem.lower()), None)
            speaker_names.append(match or (presets[len(speaker_names) % len(presets)].stem if presets else character))
        if len(speaker_names) >= 4:
            break
    if not speaker_names:
        speaker_names = [presets[0].stem if presets else "Andrew"]

    with tempfile.TemporaryDirectory(prefix="cutline-vv-") as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "dialogue.txt"
        script_path.write_text(script, encoding="utf-8")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        command = [
            settings.vibevoice_python,
            str(inference),
            "--model_path",
            settings.vibevoice_model,
            "--txt_path",
            str(script_path),
            "--speaker_names",
            *speaker_names,
            "--output_dir",
            str(out_dir),
        ]
        result = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=settings.vibevoice_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"VibeVoice failed ({result.returncode}): {(result.stderr or result.stdout)[-800:]}"
            )
        produced = sorted(out_dir.rglob("*.wav")) + sorted(out_dir.rglob("*.mp3"))
        if not produced:
            raise RuntimeError("VibeVoice completed without an audio file")
        destination.write_bytes(produced[0].read_bytes())
        mime = "audio/wav" if produced[0].suffix.lower() == ".wav" else "audio/mpeg"
        return filename, mime, "vibevoice"
