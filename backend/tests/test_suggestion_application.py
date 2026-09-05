import pytest
from fastapi import HTTPException

from backend.api import _suggestion_value


def test_suggestion_replaces_only_quoted_prompt_text() -> None:
    prompt = "Keep the room stable. Rachel retains her exact appearance and clothing. Use soft light."

    result = _suggestion_value(
        prompt,
        "Rachel retains her exact appearance and clothing.",
        "Rachel appears fifteen years older while retaining her identity and clothing.",
    )

    assert result == (
        "Keep the room stable. Rachel appears fifteen years older while retaining her identity "
        "and clothing. Use soft light."
    )


def test_suggestion_can_replace_an_entire_prompt() -> None:
    assert _suggestion_value("Old prompt", "Old prompt", "New prompt") == "New prompt"


def test_suggestion_rejects_text_for_numeric_field() -> None:
    with pytest.raises(HTTPException) as error:
        _suggestion_value(48_000, 48_000, "Extend the timeline to 60 seconds")

    assert error.value.status_code == 422
