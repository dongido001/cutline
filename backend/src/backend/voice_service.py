"""Centralized Voice & Dialogue Service for Cutline.

Supports VibeVoice (default local zero-cost engine), ElevenLabs, and OpenAI TTS.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .final_cut import probe_duration_ms
from .models import Character, Project, Shot
from .schemas import VoiceAuditionResponse, VoicePresetRead, VoiceProviderInfo
from .settings import settings
from .vibevoice_tts import format_dialogue_script, synthesize_dialogue, vibevoice_status

logger = logging.getLogger(__name__)

VIBEVOICE_METADATA: dict[str, dict[str, str]] = {
    "ng-Gideon_man": {
        "name": "Gideon (Nigerian Male)",
        "gender": "male",
        "language": "en-NG",
        "description": "Authentic Nigerian English male voice with natural cadence",
    },
    "en-Alice_woman": {
        "name": "Alice",
        "gender": "female",
        "language": "en",
        "description": "Clear, youthful & natural English female voice",
    },
    "en-Carter_man": {
        "name": "Carter",
        "gender": "male",
        "language": "en",
        "description": "Deep, authoritative & commanding male voice",
    },
    "en-Frank_man": {
        "name": "Frank",
        "gender": "male",
        "language": "en",
        "description": "Calm, grounded & mature adult male voice",
    },
    "en-Mary_woman_bgm": {
        "name": "Mary",
        "gender": "female",
        "language": "en",
        "description": "Dramatic female voice with ambient resonance",
    },
    "en-Maya_woman": {
        "name": "Maya",
        "gender": "female",
        "language": "en",
        "description": "Warm, resonant & emotional adult female voice",
    },
    "in-Samuel_man": {
        "name": "Samuel",
        "gender": "male",
        "language": "en",
        "description": "Rhythmic character male voice with international cadence",
    },
    "zh-Anchen_man_bgm": {
        "name": "Anchen",
        "gender": "male",
        "language": "zh",
        "description": "Articulate, measured narrative male voice",
    },
    "zh-Bowen_man": {
        "name": "Bowen",
        "gender": "male",
        "language": "zh",
        "description": "Crisp and standard conversational male voice",
    },
    "zh-Xinran_woman": {
        "name": "Xinran",
        "gender": "female",
        "language": "zh",
        "description": "Gentle, composed conversational female voice",
    },
}

ELEVENLABS_CURATED_VOICES: list[dict[str, str]] = [
    {
        "id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "gender": "female",
        "language": "en",
        "description": "Calm, natural & conversational American female",
    },
    {
        "id": "pNInz6obpgDQGcFmaJgB",
        "name": "Adam",
        "gender": "male",
        "language": "en",
        "description": "Deep, authoritative & narrative male",
    },
    {
        "id": "AZnzlk1XvdvUeBnXmlld",
        "name": "Domi",
        "gender": "female",
        "language": "en",
        "description": "Strong, emphatic & expressive female",
    },
    {
        "id": "ErXwobaYiN019PkySvjV",
        "name": "Antoni",
        "gender": "male",
        "language": "en",
        "description": "Well-rounded, warm & resonant male",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Bella",
        "gender": "female",
        "language": "en",
        "description": "Soft, expressive & emotional female",
    },
    {
        "id": "VR6AewLTigWG4xSOukaG",
        "name": "Arnold",
        "gender": "male",
        "language": "en",
        "description": "Crisp, mature & distinguished narrator",
    },
]

OPENAI_VOICES: list[dict[str, str]] = [
    {"id": "alloy", "name": "Alloy", "gender": "neutral", "language": "en", "description": "Versatile, balanced neutral voice"},
    {"id": "echo", "name": "Echo", "gender": "male", "language": "en", "description": "Warm, conversational male voice"},
    {"id": "fable", "name": "Fable", "gender": "neutral", "language": "en", "description": "Expressive British-accented voice"},
    {"id": "onyx", "name": "Onyx", "gender": "male", "language": "en", "description": "Deep, smooth & authoritative male voice"},
    {"id": "nova", "name": "Nova", "gender": "female", "language": "en", "description": "Energetic, bright female voice"},
    {"id": "shimmer", "name": "Shimmer", "gender": "female", "language": "en", "description": "Clear, resonant emotional female voice"},
]



def load_custom_voices_metadata() -> dict[str, dict[str, str]]:
    meta_file = settings.data_dir / "custom_voices.json"
    if meta_file.is_file():
        try:
            import json
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_custom_voice_metadata(voice_id: str, meta: dict[str, str]) -> None:
    meta_file = settings.data_dir / "custom_voices.json"
    data = load_custom_voices_metadata()
    data[voice_id] = meta
    import json
    meta_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_custom_voice(
    file_bytes: bytes,
    name: str,
    gender: str = "male",
    language: str = "en-NG",
    description: str = "",
) -> VoicePresetRead:
    voices_dir = Path(settings.vibevoice_root) / "demo" / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c for c in name if c.isalnum() or c in ("-", "_")).lower()
    voice_id = f"custom-{slug}_{gender}"
    dest_path = voices_dir / f"{voice_id}.wav"

    with tempfile.NamedTemporaryFile(suffix=".upload", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_name = tmp.name

    try:
        cmd = [
            "ffmpeg", "-y", "-i", tmp_name,
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(dest_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    meta = {
        "name": name,
        "gender": gender,
        "language": language or "en-NG",
        "description": description or f"Custom cloned {gender} voice ({language or 'Nigerian'})",
    }
    save_custom_voice_metadata(voice_id, meta)

    return VoicePresetRead(
        id=voice_id,
        provider="vibevoice",
        name=meta["name"],
        gender=meta["gender"],  # type: ignore[arg-type]
        language=meta["language"],
        description=meta["description"],
        preview_url=f"/api/voices/vibevoice/{voice_id}/sample",
    )


def list_voice_providers() -> list[VoiceProviderInfo]:
    """Return all available voice providers and their voice presets."""
    providers: list[VoiceProviderInfo] = []

    # 1. VibeVoice (Default Local)
    configured, available, detail = vibevoice_status()
    vv_voices: list[VoicePresetRead] = []
    custom_meta = load_custom_voices_metadata()
    combined_meta = {**VIBEVOICE_METADATA, **custom_meta}
    voices_dir = Path(settings.vibevoice_root) / "demo" / "voices"
    if voices_dir.is_dir():
        for path in sorted(voices_dir.glob("*.wav")):
            stem = path.stem
            meta = combined_meta.get(
                stem,
                {
                    "name": stem.replace("_", " ").title(),
                    "gender": "female" if "woman" in stem.lower() or "female" in stem.lower() else "male",
                    "language": "zh" if stem.startswith("zh") else "en",
                    "description": "VibeVoice local neural voice preset",
                },
            )
            vv_voices.append(
                VoicePresetRead(
                    id=stem,
                    provider="vibevoice",
                    name=meta["name"],
                    gender=meta["gender"],  # type: ignore[arg-type]
                    language=meta["language"],
                    description=meta["description"],
                    preview_url=f"/api/voices/vibevoice/{stem}/sample",
                )
            )

    providers.append(
        VoiceProviderInfo(
            id="vibevoice",
            label="VibeVoice (Local 1.5B TTS)",
            configured=configured,
            available=available,
            detail=detail,
            voices=vv_voices,
        )
    )

    # 2. ElevenLabs
    el_key = (settings.elevenlabs_api_key or os.getenv("ELEVENLABS_API_KEY", "")).strip()
    el_configured = bool(el_key)
    el_voices: list[VoicePresetRead] = []

    if el_configured:
        try:
            with httpx.Client(timeout=10) as client:
                res = client.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": el_key})
                if res.status_code == 200:
                    data = res.json()
                    for v in data.get("voices", []):
                        vid = v.get("voice_id")
                        vname = v.get("name")
                        labels = v.get("labels") or {}
                        gender = labels.get("gender", "neutral").lower()
                        if gender not in {"male", "female"}:
                            gender = "neutral"
                        desc = f"{labels.get('accent', '')} {labels.get('description', '')}".strip() or "ElevenLabs voice"
                        preview_url = v.get("preview_url")
                        el_voices.append(
                            VoicePresetRead(
                                id=vid,
                                provider="elevenlabs",
                                name=vname,
                                gender=gender,  # type: ignore[arg-type]
                                language=labels.get("language", "en"),
                                description=desc,
                                preview_url=preview_url,
                            )
                        )
        except Exception as exc:
            logger.warning(f"Failed to fetch dynamic ElevenLabs voices: {exc}")

    if not el_voices:
        for v in ELEVENLABS_CURATED_VOICES:
            el_voices.append(
                VoicePresetRead(
                    id=v["id"],
                    provider="elevenlabs",
                    name=v["name"],
                    gender=v["gender"],  # type: ignore[arg-type]
                    language=v["language"],
                    description=v["description"],
                    preview_url=None,
                )
            )

    providers.append(
        VoiceProviderInfo(
            id="elevenlabs",
            label="ElevenLabs (Cloud AI)",
            configured=el_configured,
            available=el_configured,
            detail="Ready · High-fidelity cloud voice actor library"
            if el_configured
            else "Requires ELEVENLABS_API_KEY in settings or environment",
            voices=el_voices,
        )
    )

    # 3. OpenAI TTS
    oai_key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    oai_configured = bool(oai_key)
    providers.append(
        VoiceProviderInfo(
            id="openai",
            label="OpenAI TTS",
            configured=oai_configured,
            available=oai_configured,
            detail="Ready · OpenAI Speech synthesis" if oai_configured else "Requires OPENAI_API_KEY",
            voices=[
                VoicePresetRead(
                    id=v["id"],
                    provider="openai",
                    name=v["name"],
                    gender=v["gender"],  # type: ignore[arg-type]
                    language=v["language"],
                    description=v["description"],
                    preview_url=None,
                )
                for v in OPENAI_VOICES
            ],
        )
    )

    return providers


def get_vibevoice_sample_path(voice_id: str) -> Path | None:
    """Return the raw preset WAV file for instant zero-latency preview."""
    voices_dir = Path(settings.vibevoice_root) / "demo" / "voices"
    candidate = voices_dir / f"{voice_id}.wav"
    if candidate.is_file():
        return candidate
    candidate_raw = voices_dir / voice_id
    if candidate_raw.is_file():
        return candidate_raw
    return None


def synthesize_audition(
    provider: str,
    voice_id: str,
    text: str,
    settings_dict: dict[str, Any] | None = None,
) -> VoiceAuditionResponse:
    """Generate an audition speech sample for a specific voice preset."""
    provider = provider.lower().strip()
    clean_text = text.strip() or "This is a voice audition test for Cutline Studio."
    filename = f"audition-{uuid.uuid4()}"

    if provider == "elevenlabs":
        el_key = (settings.elevenlabs_api_key or os.getenv("ELEVENLABS_API_KEY", "")).strip()
        if not el_key:
            raise RuntimeError("ElevenLabs API key is not configured")
        destination = settings.media_dir / f"{filename}.mp3"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {"xi-api-key": el_key, "Content-Type": "application/json"}
        payload = {
            "text": clean_text,
            "model_id": settings.elevenlabs_model or "eleven_multilingual_v2",
            "voice_settings": settings_dict or {"stability": 0.5, "similarity_boost": 0.75},
        }
        with httpx.Client(timeout=30) as client:
            res = client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            destination.write_bytes(res.content)
        dur_ms = probe_duration_ms(destination)
        return VoiceAuditionResponse(
            url=f"/media/{destination.name}",
            provider="elevenlabs",
            voice_id=voice_id,
            duration_ms=dur_ms,
        )

    if provider == "openai":
        api_key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured")
        destination = settings.media_dir / f"{filename}.mp3"
        options = settings_dict or {}
        payload = {
            "model": options.get("model", "gpt-4o-mini-tts"),
            "voice": voice_id,
            "input": clean_text,
            "response_format": "mp3",
        }
        if options.get("instructions"):
            payload["instructions"] = options["instructions"]
        with httpx.Client(timeout=60) as client:
            res = client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            res.raise_for_status()
            destination.write_bytes(res.content)
        return VoiceAuditionResponse(
            url=f"/media/{destination.name}",
            provider="openai",
            voice_id=voice_id,
            duration_ms=probe_duration_ms(destination),
        )

    # Default to VibeVoice
    destination = settings.media_dir / f"{filename}.wav"
    sample = get_vibevoice_sample_path(voice_id)
    if clean_text.lower() in {"sample", "preview", "audition"} and sample:
        destination.write_bytes(sample.read_bytes())
        return VoiceAuditionResponse(
            url=f"/media/{destination.name}",
            provider="vibevoice",
            voice_id=voice_id,
            duration_ms=probe_duration_ms(destination),
        )

    configured, available, detail = vibevoice_status()
    if not configured or not available:
        if sample:
            destination.write_bytes(sample.read_bytes())
            return VoiceAuditionResponse(
                url=f"/media/{destination.name}",
                provider="vibevoice",
                voice_id=voice_id,
                duration_ms=probe_duration_ms(destination),
            )
        raise RuntimeError(f"VibeVoice is not ready: {detail}")

    root = Path(settings.vibevoice_root)
    inference = root / "demo" / "inference_from_file.py"
    with tempfile.TemporaryDirectory(prefix="cutline-audition-") as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "script.txt"
        script_path.write_text(f"Speaker 1: {clean_text}\n", encoding="utf-8")
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
            voice_id,
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
            raise RuntimeError(f"VibeVoice failed: {(result.stderr or result.stdout)[-400:]}")
        produced = sorted(out_dir.rglob("*.wav")) + sorted(out_dir.rglob("*.mp3"))
        if not produced:
            raise RuntimeError("VibeVoice completed without an audio file")
        destination.write_bytes(produced[0].read_bytes())

    dur_ms = probe_duration_ms(destination)
    return VoiceAuditionResponse(
        url=f"/media/{destination.name}",
        provider="vibevoice",
        voice_id=voice_id,
        duration_ms=dur_ms,
    )


def synthesize_shot_dialogue(shot: Shot, db: Session) -> tuple[str, str, str]:
    """Synthesize multi-character dialogue for a shot using assigned cast voices."""
    if not shot.dialogue:
        raise RuntimeError(f"{shot.title or 'Shot'} has no dialogue")

    project = shot.scene.script_version.project
    characters = {c.name.strip().upper(): c for c in project.characters}

    # Map each character appearing in the dialogue to their designated voice preset
    # Default fallback presets if unassigned: Maya for female/Nwanyeruwa, Carter for male/Census/Chief
    speaker_map: dict[str, str] = {}
    speaker_names_ordered: list[str] = []
    seen: set[str] = set()

    has_external_provider = False
    for line in shot.dialogue:
        if not isinstance(line, dict):
            continue
        cname = str(line.get("character") or line.get("speaker") or "Character").strip().upper()
        if cname in seen:
            continue
        seen.add(cname)
        char_obj = characters.get(cname)
        assigned_voice = char_obj.voice_id if char_obj and char_obj.voice_id else None
        char_prov = (char_obj.voice_provider if char_obj else "vibevoice").lower().strip()

        if char_prov in {"elevenlabs", "openai"} and assigned_voice:
            has_external_provider = True

        if not assigned_voice:
            # Intelligent fallback by name and character notes
            notes_lower = (char_obj.notes if char_obj and char_obj.notes else "").lower()
            lower = f"{cname.lower()} {notes_lower}"

            is_female = any(
                w in lower
                for w in (
                    "woman", "women", "female", "mother", "lady", "girl", "daughter",
                    "mrs", "ms", "sister", "aunt", "she", "her", "nwanyeruw",
                )
            )
            is_older_authoritative = any(
                w in lower
                for w in (
                    "chief", "officer", "chairman", "senior", "elder", "executive",
                    "commanding", "father", "60", "61", "62", "65", "70", "52", "matriarch",
                )
            )

            # Available voice pools for VibeVoice
            female_pool = ["en-Maya_woman", "en-Alice_woman", "en-Mary_woman_bgm"]
            male_pool = ["en-Carter_man" if is_older_authoritative else "en-Frank_man", "en-Carter_man", "in-Samuel_man", "en-Frank_man", "ng-Gideon_man"]

            pool = female_pool if is_female else male_pool
            used_in_shot = set(speaker_map.values())

            # Find the best unused voice from the pool
            candidate = next((v for v in pool if v not in used_in_shot), pool[0])
            assigned_voice = candidate

        speaker_map[cname] = assigned_voice
        speaker_names_ordered.append(assigned_voice)

    if has_external_provider:
        # Route to external provider synthesis (ElevenLabs / OpenAI)
        rendered_parts: list[Path] = []
        active_provider = "vibevoice"
        for line in shot.dialogue:
            if not isinstance(line, dict):
                continue
            txt = str(line.get("text") or line.get("line") or "").strip()
            if not txt:
                continue
            cname = str(line.get("character") or line.get("speaker") or "Character").strip().upper()
            char_obj = characters.get(cname)
            char_prov = (char_obj.voice_provider if char_obj else "vibevoice").lower().strip()
            vid = char_obj.voice_id if char_obj and char_obj.voice_id else speaker_map.get(cname, "en-Carter_man")
            settings_dict = char_obj.voice_settings if char_obj else None

            if char_prov in {"elevenlabs", "openai"} and vid:
                active_provider = char_prov
                aud = synthesize_audition(char_prov, vid, txt, settings_dict)
                rendered_parts.append(settings.media_dir / Path(aud.url).name)
            else:
                aud = synthesize_audition("vibevoice", vid, txt, settings_dict)
                rendered_parts.append(settings.media_dir / Path(aud.url).name)

        if rendered_parts:
            if len(rendered_parts) == 1:
                p = rendered_parts[0]
                mime = "audio/mpeg" if p.suffix == ".mp3" else "audio/wav"
                return p.name, mime, active_provider
            
            combined_name = f"dialogue-{uuid.uuid4()}.mp3"
            combined_path = settings.media_dir / combined_name
            filter_inputs = "".join(f"[{i}:a]" for i in range(len(rendered_parts)))
            cmd = ["ffmpeg", "-y"]
            for p in rendered_parts:
                cmd.extend(["-i", str(p)])
            cmd.extend([
                "-filter_complex", f"{filter_inputs}concat=n={len(rendered_parts)}:v=0:a=1[out]",
                "-map", "[out]",
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                str(combined_path),
            ])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and combined_path.is_file():
                return combined_name, "audio/mpeg", active_provider
            return rendered_parts[0].name, "audio/mpeg", active_provider

    return synthesize_dialogue(
        shot.dialogue,
        duration_hint_ms=shot.duration_ms,
        speaker_map=speaker_map,
    )
