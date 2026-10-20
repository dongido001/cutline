"""Music/voice separation through LALAL.AI (cloud), Demucs (local) or UVR models via audio-separator (local)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import httpx

from .settings import STEM_SEPARATORS, settings

Progress = Callable[[float, str], None]

SEPARATOR_LABELS = {
    "lalal": "LALAL.AI",
    "demucs": "Demucs",
    "uvr": "UVR (audio-separator)",
}


class StemSeparationError(RuntimeError):
    pass


def _noop(_fraction: float, _message: str) -> None:
    return None


def _stems_bin(name: str) -> Path:
    return settings.stems_venv / "bin" / name


def _models_dir() -> Path:
    return settings.stems_venv.parent / "models"


def set_stem_separator(provider: str) -> None:
    if provider not in STEM_SEPARATORS:
        raise ValueError(f"Unknown music separator: {provider}")
    settings.stem_separator_path.write_text(json.dumps({"provider": provider}, indent=2))


def _lalal_headers() -> dict[str, str]:
    return {"X-License-Key": settings.lalal_api_key}


def _lalal_url(path: str) -> str:
    return f"{settings.lalal_base_url.rstrip('/')}/api/v1/{path.lstrip('/')}"


def _lalal_error(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg") or item) for item in detail if item)
    return str(detail or response.text[:200] or f"HTTP {response.status_code}")


def lalal_minutes_left() -> float | None:
    if not settings.lalal_api_key:
        return None
    response = httpx.post(_lalal_url("limits/minutes_left/"), headers=_lalal_headers(), timeout=5)
    if not response.is_success:
        raise StemSeparationError(f"LALAL.AI: {_lalal_error(response)}")
    return float(response.json().get("minutes_left") or 0)


def separate_with_lalal(source: Path, out_dir: Path, progress: Progress = _noop) -> tuple[Path, Path]:
    if not settings.lalal_api_key:
        raise StemSeparationError("LALAL.AI is not configured: set MOVIE_LALAL_API_KEY, or switch to Demucs or UVR.")
    headers = _lalal_headers()
    progress(0.1, "Uploading to LALAL.AI")
    upload = httpx.post(
        _lalal_url("upload/"),
        headers={**headers, "Content-Disposition": f"attachment; filename={source.name}"},
        content=source.read_bytes(),
        timeout=120,
    )
    if not upload.is_success:
        raise StemSeparationError(f"LALAL.AI upload failed: {_lalal_error(upload)}. Switch to Demucs or UVR to keep going.")
    source_id = upload.json()["id"]
    try:
        split = httpx.post(
            _lalal_url("split/stem_separator/"),
            headers=headers,
            json={"source_id": source_id, "presets": {"stem": "vocals", "splitter": "auto", "encoder_format": "wav"}},
            timeout=60,
        )
        if not split.is_success:
            raise StemSeparationError(f"LALAL.AI split failed: {_lalal_error(split)}. Switch to Demucs or UVR to keep going.")
        task_id = split.json()["task_id"]
        deadline = time.monotonic() + settings.lalal_timeout_seconds
        tracks: list[dict] = []
        while True:
            check = httpx.post(_lalal_url("check/"), headers=headers, json={"task_ids": [task_id]}, timeout=30)
            if not check.is_success:
                raise StemSeparationError(f"LALAL.AI status check failed: {_lalal_error(check)}")
            result = (check.json().get("result") or {}).get(task_id) or {}
            state = result.get("status")
            if state == "success":
                tracks = (result.get("result") or {}).get("tracks") or []
                break
            if state in {"error", "server_error", "cancelled"}:
                error = result.get("error") or {}
                detail = error.get("detail") if isinstance(error, dict) else error
                raise StemSeparationError(f"LALAL.AI could not split this clip: {detail or state}")
            if time.monotonic() > deadline:
                raise StemSeparationError("LALAL.AI took too long to split this clip")
            percent = int(result.get("progress") or 0)
            progress(0.2 + 0.6 * percent / 100, f"LALAL.AI separating ({percent}%)" if percent else "Queued at LALAL.AI")
            time.sleep(2)
        by_type = {track.get("type"): track for track in tracks}
        if "back" not in by_type or "stem" not in by_type:
            raise StemSeparationError("LALAL.AI returned no music/voice tracks")
        progress(0.85, "Downloading LALAL.AI tracks")
        music = out_dir / "lalal-music.wav"
        voice = out_dir / "lalal-voice.wav"
        for track, target in ((by_type["back"], music), (by_type["stem"], voice)):
            download = httpx.get(track["url"], timeout=120, follow_redirects=True)
            download.raise_for_status()
            target.write_bytes(download.content)
        return music, voice
    finally:
        try:
            httpx.post(_lalal_url("delete/"), headers=headers, json={"source_id": source_id}, timeout=15)
        except httpx.HTTPError:
            pass


def _run_local(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=settings.stems_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise StemSeparationError(f"{label} took too long to separate this clip") from exc
    if result.returncode:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        raise StemSeparationError(f"{label} failed: {lines[-1] if lines else 'unknown error'}")


def separate_with_demucs(source: Path, out_dir: Path, progress: Progress = _noop) -> tuple[Path, Path]:
    binary = _stems_bin("demucs")
    if not binary.is_file():
        raise StemSeparationError("Demucs is not installed: run scripts/setup-stems, or switch to LALAL.AI.")
    progress(0.2, "Separating with Demucs")
    _run_local([str(binary), "--two-stems=vocals", "-n", settings.demucs_model, "-o", str(out_dir), str(source)], "Demucs")
    stem_dir = out_dir / settings.demucs_model / source.stem
    music, voice = stem_dir / "no_vocals.wav", stem_dir / "vocals.wav"
    if not music.is_file() or not voice.is_file():
        raise StemSeparationError("Demucs finished without writing music/voice files")
    return music, voice


def separate_with_uvr(source: Path, out_dir: Path, progress: Progress = _noop) -> tuple[Path, Path]:
    binary = _stems_bin("audio-separator")
    if not binary.is_file():
        raise StemSeparationError("UVR (audio-separator) is not installed: run scripts/setup-stems, or switch to LALAL.AI.")
    progress(0.2, "Separating with UVR")
    _run_local(
        [
            str(binary), str(source),
            "--model_filename", settings.uvr_model,
            "--model_file_dir", str(_models_dir()),
            "--output_dir", str(out_dir),
            "--output_format", "WAV",
        ],
        "UVR",
    )
    music = next(iter(sorted(out_dir.glob("*(Instrumental)*.wav"))), None)
    voice = next(iter(sorted(out_dir.glob("*(Vocals)*.wav"))), None)
    if not music or not voice:
        raise StemSeparationError("UVR finished without writing music/voice files")
    return music, voice


SEPARATORS: dict[str, Callable[[Path, Path, Progress], tuple[Path, Path]]] = {
    "lalal": separate_with_lalal,
    "demucs": separate_with_demucs,
    "uvr": separate_with_uvr,
}


def separate(provider: str, source: Path, out_dir: Path, progress: Progress = _noop) -> tuple[Path, Path]:
    runner = SEPARATORS.get(provider)
    if runner is None:
        raise StemSeparationError(f"Unknown music separator: {provider}")
    return runner(source, out_dir, progress)


def separator_statuses() -> list[dict]:
    selected = settings.stem_separator
    statuses: list[dict] = []
    for provider in STEM_SEPARATORS:
        if provider == "lalal":
            configured = bool(settings.lalal_api_key)
            available = False
            detail = "Set MOVIE_LALAL_API_KEY in backend/.env"
            if configured:
                try:
                    minutes = lalal_minutes_left()
                    available = True
                    detail = f"Cloud · {minutes:g} min left"
                except (StemSeparationError, httpx.HTTPError, ValueError) as exc:
                    detail = str(exc)[:160] or "LALAL.AI unreachable"
        else:
            binary = _stems_bin("demucs" if provider == "demucs" else "audio-separator")
            configured = available = binary.is_file()
            model = settings.demucs_model if provider == "demucs" else settings.uvr_model
            detail = f"Local · {model}" if available else "Run scripts/setup-stems to install"
        statuses.append({
            "id": provider,
            "label": SEPARATOR_LABELS[provider],
            "configured": configured,
            "available": available,
            "selected": provider == selected,
            "detail": detail,
        })
    return statuses
