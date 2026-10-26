from backend.schemas import ProjectCreate
from backend.tasks import _planning_duration_seconds
from backend.timing import flow_clip_duration_seconds


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


def test_flow_clip_duration_breaks_ties_upward_and_uses_ceiling() -> None:
    # 5s tied between 4 and 6 -> must round UP to 6s so dialogue is never cut off
    assert flow_clip_duration_seconds(5000, model_family="omni_flash") == 6
    # 7s tied between 6 and 8 -> must round UP to 8s
    assert flow_clip_duration_seconds(7000, model_family="omni_flash") == 8
    # 9s tied between 8 and 10 -> must round UP to 10s
    assert flow_clip_duration_seconds(9000, model_family="omni_flash") == 10
    # 3s rounds up to 4s
    assert flow_clip_duration_seconds(3000, model_family="omni_flash") == 4
    # Exact even numbers preserve buckets
    assert flow_clip_duration_seconds(4000, model_family="omni_flash") == 4
    assert flow_clip_duration_seconds(6000, model_family="omni_flash") == 6
    assert flow_clip_duration_seconds(8000, model_family="omni_flash") == 8
    assert flow_clip_duration_seconds(10000, model_family="omni_flash") == 10
    # Millisecond spill past boundary uses ceiling to avoid truncating speech
    assert flow_clip_duration_seconds(4200, model_family="omni_flash") == 6

