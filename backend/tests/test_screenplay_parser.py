from backend.screenplay_parser import parse_structured_screenplay


SCRIPT = """THE VOICE BEHIND THE DOOR

Duration: 12 seconds
Format: 2 shots x 6 seconds
Location: An old Yoruba family house in Lagos.

SHOT 1 - RETURN FROM THE FUNERAL

Time: 00:00-00:06
Framing: Wide establishing shot

Action:
Temilade enters the hallway.

Dialogue: None.

Sound: Rain and a ticking clock.

Image prompt:
Cinematic wide shot of Temilade in the hallway.

Video prompt:
Temilade enters slowly while the camera pushes forward.

SHOT 2 - THE VOICE MESSAGE

Time: 00:06-00:12
Framing: Medium close-up

Action:
Her phone vibrates and she looks down.

PHONE - TEMILADE'S VOICE:
"Temilade, don't open that door."

Sound: Phone vibration and distorted speech.

Image prompt:
Medium close-up of Temilade holding her phone.

Video prompt:
The phone vibrates and Temilade reacts with confusion.
"""


def test_parses_explicit_shots_without_model_reconstruction() -> None:
    result = parse_structured_screenplay(SCRIPT)

    assert result is not None
    assert result["parser"] == "structured-shot-v2"
    assert len(result["scenes"]) == 1
    assert result["suggestions"] == []

    first = result["scenes"][0]["shots"][0]
    second = result["scenes"][0]["shots"][1]
    assert first["start_ms"] == 0
    assert first["end_ms"] == 6000
    assert first["dialogue"] == []
    assert first["sound"] == "Rain and a ticking clock."
    assert second["title"] == "THE VOICE MESSAGE"
    assert second["duration_ms"] == 6000
    assert second["dialogue"] == [
        {
            "character": "PHONE - TEMILADE'S VOICE",
            "text": "Temilade, don't open that door.",
        }
    ]


def test_groups_consecutive_shots_by_explicit_scene_label() -> None:
    script = SCRIPT.replace(
        "Time: 00:00-00:06", "Scene: DINING ROOM\nLocation: Family dining room\nTime: 00:00-00:06"
    ).replace(
        "Time: 00:06-00:12", "Scene: HALLWAY\nLocation: Family hallway\nTime: 00:06-00:12"
    )

    result = parse_structured_screenplay(script)

    assert result is not None
    assert [scene["title"] for scene in result["scenes"]] == ["DINING ROOM", "HALLWAY"]
    assert [scene["location"] for scene in result["scenes"]] == [
        "Family dining room",
        "Family hallway",
    ]


def test_flags_declared_shot_count_and_timeline_gaps() -> None:
    broken = SCRIPT.replace("Format: 2 shots", "Format: 3 shots").replace(
        "Time: 00:06-00:12", "Time: 00:08-00:14"
    )

    result = parse_structured_screenplay(broken)

    assert result is not None
    titles = {suggestion["title"] for suggestion in result["suggestions"]}
    assert "Shot count does not match" in titles
    assert "Fix the timeline before Shot 2" in titles
    assert "Timeline does not match declared duration" in titles


def test_estimates_duration_from_dialogue_when_time_missing() -> None:
    script = """UNTIMED BEAT

Format: 1 shot

SHOT 1 - LONG LINE

Framing: Close-up

Action:
She speaks carefully.

Dialogue:
MARA:
"I need you to listen carefully because what happens next will change everything for both of us."

Image prompt:
Close-up of Mara.

Video prompt:
Subtle head movement.
"""
    result = parse_structured_screenplay(script)
    assert result is not None
    shot = result["scenes"][0]["shots"][0]
    assert shot["duration_ms"] > 5000
    assert shot["start_ms"] == 0
    assert shot["end_ms"] == shot["duration_ms"]
    assert any("Estimated timing" in item["title"] for item in result["suggestions"])
