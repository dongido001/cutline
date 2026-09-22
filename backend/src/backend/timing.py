"""Shot timing helpers — speech-rate duration from dialogue, Runway clip buckets."""

from __future__ import annotations

import re
from typing import Any

# Spoken English ≈ 2.5 words/sec; CJK ≈ 2.8 chars/sec (Wonderclip-like).
_WORDS_PER_SEC = 2.5
_CJK_CHARS_PER_SEC = 2.8
_PADDING_MS = 400
_MIN_MS = 2000
_MAX_MS = 10000

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _dialogue_lines(dialogue: Any) -> list[str]:
    if not dialogue:
        return []
    if isinstance(dialogue, str):
        text = dialogue.strip()
        return [text] if text else []
    lines: list[str] = []
    if isinstance(dialogue, list):
        for item in dialogue:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(text)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
    return lines


def estimate_dialogue_duration_ms(
    dialogue: Any,
    *,
    min_ms: int = _MIN_MS,
    max_ms: int = _MAX_MS,
) -> int | None:
    """Estimate on-screen duration from spoken dialogue. None if no dialogue text."""
    lines = _dialogue_lines(dialogue)
    if not lines:
        return None
    full = " ".join(lines)
    cjk_chars = len(_CJK_RE.findall(full))
    if cjk_chars >= max(1, len(full) // 3):
        seconds = cjk_chars / _CJK_CHARS_PER_SEC
    else:
        words = len(_WORD_RE.findall(full))
        if words == 0:
            seconds = max(1.0, len(full) / 12.0)
        else:
            seconds = words / _WORDS_PER_SEC
    ms = int(round(seconds * 1000)) + _PADDING_MS
    return max(min_ms, min(max_ms, ms))


def resolve_shot_duration_ms(
    *,
    start_ms: int | None,
    end_ms: int | None,
    dialogue: Any,
    default_ms: int = 5000,
    min_ms: int = _MIN_MS,
    max_ms: int = _MAX_MS,
) -> tuple[int, bool]:
    """
    Prefer explicit Time: range when present.
    Otherwise estimate from dialogue, else default.
    Returns (duration_ms, used_dialogue_estimate).
    """
    if start_ms is not None and end_ms is not None and end_ms > start_ms:
        return end_ms - start_ms, False
    estimated = estimate_dialogue_duration_ms(dialogue, min_ms=min_ms, max_ms=max_ms)
    if estimated is not None:
        return estimated, True
    return max(min_ms, min(max_ms, default_ms)), False


def runway_clip_duration_seconds(
    duration_ms: int,
    *,
    max_seconds: int = 5,
) -> int:
    """
    Map shot duration to Runway i2v buckets (5 or 10 seconds).
    Prefer the shorter bucket for long-form cost control.
    """
    requested = max(1, round(duration_ms / 1000))
    cap = 5 if max_seconds <= 5 else 10
    if requested <= 5 or cap <= 5:
        return 5
    return 10
