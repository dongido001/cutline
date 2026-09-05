from backend.schemas import ProjectCreate
from backend.tasks import _planning_duration_seconds


def test_project_duration_defaults_to_automatic() -> None:
    payload = ProjectCreate(title="Test", script="INT. ROOM - DAY\nAction.")

    assert payload.target_duration_seconds == 0


def test_automatic_duration_uses_structured_timestamps() -> None:
    parsed = {
        "scenes": [
            {"shots": [
                {"start_ms": 0, "end_ms": 5_000, "duration_ms": 5_000},
                {"start_ms": 5_000, "end_ms": 18_000, "duration_ms": 13_000},
            ]}
        ]
    }

    assert _planning_duration_seconds("", parsed, 0) == 18


def test_automatic_duration_uses_declared_script_duration() -> None:
    assert _planning_duration_seconds("Duration: 3 minutes\nINT. ROOM - DAY", None, 0) == 180


def test_optional_target_remains_available_as_guidance() -> None:
    assert _planning_duration_seconds("Duration: 3 minutes", None, 60) == 60
