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


def test_time_of_day_does_not_split_an_explicit_scene() -> None:
    script = SCRIPT.replace(
        "Time: 00:00-00:06",
        "Scene: FAMILY HOME\nTime of day: Morning\nTime: 00:00-00:06",
    ).replace(
        "Time: 00:06-00:12",
        "Scene: FAMILY HOME\nTime of day: Evening\nTime: 00:06-00:12",
    )

    result = parse_structured_screenplay(script)

    assert result is not None
    assert len(result["scenes"]) == 1
    assert len(result["scenes"][0]["shots"]) == 2
    assert result["scenes"][0]["time_of_day"] == "Morning"


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


def test_interleaved_stage_directions_and_transitions_do_not_leak_into_dialogue() -> None:
    script = """WHEN THE TIDE RETURNS

Duration: 18 seconds
Format: 2 shots x 9 seconds
Aspect ratio: 9:16

Character consistency

MAYA SANTOS, 25 — Filipina woman in pale-yellow blouse.
GABRIEL “GABE” DE LEON, 29 — Filipino man in white rolled-sleeve shirt.
ROSA SANTOS, 52 — Maya's mother in floral house dress.

Location: Santos Seaside Café.

SHOT 1 - BACK TO MAYA

Scene: CAFÉ RETURN
Time: 00:00-00:09
Framing: Medium counter two-shot

Action:
Gabe returns to the café.

MAYA:
“Busy morning?”

GABE:
“Work.”

Maya picks up the seventy pesos and places them into the cash drawer.

Sound: Dialogue and cash drawer movement.

Image prompt:
Medium frame inside Santos Seaside Café.

Video prompt:
Maya asks a question and stores the cash.

SHOT 2 - ROSA LEARNS THE TRUTH

Scene: HIDDEN CALL
Time: 00:09-00:18
Framing: Layered suspense shot

Action:
Rosa approaches the doorway.

GABE:
“Maya doesn’t know I’m involved in this project.”

A beat.

GABE:
“I need time to tell her myself.”

Rosa freezes.

Recognition becomes hurt.

CUT TO BLACK.

Sound: Gabe's dialogue and rising tension.

Image prompt:
Rosa in corridor, Gabe outside.

Video prompt:
Rosa overhears Gabe and freezes.
"""
    result = parse_structured_screenplay(script)
    assert result is not None
    assert [c["name"] for c in result["characters"]] == ["MAYA", "GABE", "ROSA"]

    shot1 = result["scenes"][0]["shots"][0]
    assert shot1["dialogue"] == [
        {"character": "MAYA", "text": "Busy morning?"},
        {"character": "GABE", "text": "Work."},
    ]
    assert "Maya picks up the seventy pesos" in shot1["action"]

    shot2 = result["scenes"][1]["shots"][0]
    assert shot2["dialogue"] == [
        {"character": "GABE", "text": "Maya doesn’t know I’m involved in this project."},
        {"character": "GABE", "text": "I need time to tell her myself."},
    ]
    assert "A beat." in shot2["action"]
    assert "Rosa freezes." in shot2["action"]
    assert "CUT TO BLACK." in shot2["action"]


def test_uppercase_quoted_dialogue_and_scene_dialogue_deduplication() -> None:
    script = """COLOR HUNT

Duration: 12 seconds
Format: 2 shots x 6 seconds
Aspect ratio: 9:16
Location: Playroom.

SHOT 1 - FIND RED

Scene: RED HUNT
Time: 00:00-00:06
Framing: Medium two-shot

Action:
Jasmine asks a question.

JASMINE:
"Can you find something RED?"

Sound: Chime.

Image prompt:
Jasmine and Skylar in playroom.

Video prompt:
Jasmine asks the viewer.

SHOT 2 - RED IS BACK!

Scene: RED HUNT
Time: 00:06-00:12
Framing: Medium tracking shot

Action:
Skylar points at the ball.

SKYLAR:
"RED BALL!"

JASMINE:
"Yes! Red!"

Sound: Bell.

Image prompt:
Skylar pointing at red ball.

Video prompt:
Skylar shouts RED BALL and Jasmine confirms.
"""
    result = parse_structured_screenplay(script)
    assert result is not None
    assert len(result["scenes"]) == 1
    scene = result["scenes"][0]
    assert scene["shots"][0]["dialogue"] == [
        {"character": "JASMINE", "text": "Can you find something RED?"}
    ]
    assert scene["shots"][1]["dialogue"] == [
        {"character": "SKYLAR", "text": "RED BALL!"},
        {"character": "JASMINE", "text": "Yes! Red!"},
    ]
    assert scene["dialogue"] == [
        {"character": "JASMINE", "text": "Can you find something RED?"},
        {"character": "SKYLAR", "text": "RED BALL!"},
        {"character": "JASMINE", "text": "Yes! Red!"},
    ]

