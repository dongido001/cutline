from backend.timing import (
    estimate_dialogue_duration_ms,
    resolve_shot_duration_ms,
    runway_clip_duration_seconds,
)


def test_estimate_english_dialogue_by_word_rate() -> None:
    dialogue = [
        {"character": "MARA", "text": "I told you not to open that door tonight."},
    ]
    ms = estimate_dialogue_duration_ms(dialogue)
    assert ms is not None
    # 9 words / 2.5 + 0.4s padding ≈ 4.0s
    assert 3000 <= ms <= 6000


def test_estimate_cjk_dialogue_by_char_rate() -> None:
    dialogue = [{"character": "A", "text": "不要打开那扇门今晚一定要小心"}]
    ms = estimate_dialogue_duration_ms(dialogue)
    assert ms is not None
    assert 2000 <= ms <= 10000


def test_resolve_prefers_explicit_time_range() -> None:
    duration, used_estimate = resolve_shot_duration_ms(
        start_ms=0,
        end_ms=6000,
        dialogue=[{"character": "A", "text": "Hello there my friend how are you doing today?"}],
    )
    assert duration == 6000
    assert used_estimate is False


def test_resolve_estimates_when_time_missing() -> None:
    duration, used_estimate = resolve_shot_duration_ms(
        start_ms=None,
        end_ms=None,
        dialogue=[{"character": "A", "text": "Don't open that door."}],
    )
    assert used_estimate is True
    assert 2000 <= duration <= 10000


def test_runway_prefers_five_second_bucket() -> None:
    assert runway_clip_duration_seconds(3200, max_seconds=5) == 5
    assert runway_clip_duration_seconds(9000, max_seconds=5) == 5
    assert runway_clip_duration_seconds(9000, max_seconds=10) == 10
