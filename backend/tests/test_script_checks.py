from backend.script_checks import script_quality_suggestions


def _scene(
    *,
    action: str = "Temilade walks down the long hallway.",
    framing: str = "Wide shot",
    dialogue: list[dict[str, str]] | None = None,
    sound: str = "Rain",
    duration_ms: int = 4000,
    location: str = "Hallway",
    time_of_day: str = "Night",
    cast: list[str] | None = None,
) -> dict:
    return {
        "location": location,
        "time_of_day": time_of_day,
        "shots": [
            {
                "action": action,
                "framing": framing,
                "dialogue": dialogue or [],
                "sound": sound,
                "duration_ms": duration_ms,
                "explicit_cast": cast,
            }
        ],
    }


def _titles(items: list[dict]) -> list[str]:
    return [item["title"] for item in items]


def test_clean_script_has_no_advisories() -> None:
    scenes = [_scene(), _scene(action="She opens the door and freezes.", framing="Close-up")]
    assert script_quality_suggestions(scenes, []) == []


def test_advisories_never_block_or_rewrite() -> None:
    scenes = [_scene(sound="", action="They talk.", framing="Unspecified")]
    items = script_quality_suggestions(scenes, [])
    assert items
    assert all(i["severity"] == "warning" for i in items)
    assert all(i["target"]["field"] == "" and i["after_value"] is None for i in items)


def test_flags_undeclared_speaker_once_and_skips_offscreen_voices() -> None:
    line = lambda who: [{"character": who, "text": "Hello there."}]  # noqa: E731
    scenes = [
        _scene(dialogue=line("TEMILADE"), action="Temilade walks down the long hallway."),
        _scene(dialogue=line("STRANGER"), framing="Close-up", action="A stranger steps out of the shadows."),
        _scene(dialogue=line("STRANGER"), framing="Wide shot", action="The stranger blocks her way forward."),
        _scene(dialogue=line("PHONE - TEMILADE'S VOICE"), framing="Close-up", action="Her phone buzzes on the table."),
    ]
    items = script_quality_suggestions(scenes, [{"name": "TEMILADE", "notes": "", "voice_description": ""}])
    assert _titles(items) == ["Stranger speaks in Shot 2 but is never introduced"]
    assert items[0]["target"]["scene_index"] == 1


def test_character_named_in_a_cast_line_counts_as_appearing() -> None:
    scenes = [_scene(cast=["TEMILADE", "BOLA"])]
    items = script_quality_suggestions(
        scenes,
        [{"name": "TEMILADE", "notes": "", "voice_description": ""}, {"name": "BOLA", "notes": "", "voice_description": ""}],
        [""],
    )
    assert items == []


def test_speaker_missing_from_explicit_cast() -> None:
    scenes = [_scene(dialogue=[{"character": "TEMILADE", "text": "Hi."}], cast=["Bola"])]
    assert _titles(script_quality_suggestions(scenes, [])) == ["Temilade speaks in Shot 1 but is not in its cast list"]


def test_flags_vague_action_missing_framing_and_repeated_action() -> None:
    scenes = [
        _scene(action="They talk."),
        _scene(action="They talk.", framing="Unspecified"),
    ]
    titles = _titles(script_quality_suggestions(scenes, []))
    assert "Shot 1 action is too vague to direct" in titles
    assert "Shot 2 has no framing" in titles
    assert "Shot 2 repeats the action of the previous shot" in titles


def test_time_of_day_jump_in_same_location_only() -> None:
    same_place = [_scene(), _scene(time_of_day="Morning", action="Sunlight fills the hallway slowly.")]
    assert any("Time of day jumps" in t for t in _titles(script_quality_suggestions(same_place, [])))
    moved = [_scene(), _scene(location="Garden", time_of_day="Morning", action="Sunlight fills the garden slowly.")]
    assert not any("Time of day jumps" in t for t in _titles(script_quality_suggestions(moved, [])))


def test_repeated_framing_run_flagged_once_at_the_fourth_shot() -> None:
    scenes = [_scene(action=f"Temilade takes step number {n} forward.") for n in range(6)]
    items = [i for i in script_quality_suggestions(scenes, []) if "same framing" in i["title"]]
    assert len(items) == 1
    assert items[0]["target"]["scene_index"] == 3


def test_silent_opening_and_long_silent_stretch() -> None:
    scenes = [_scene(sound="", duration_ms=8000, framing=f"Frame {n}", action=f"Temilade waits at spot {n}.") for n in range(4)]
    titles = _titles(script_quality_suggestions(scenes, []))
    assert "The opening shot has no sound or dialogue" in titles
    assert sum("without sound or dialogue" in t for t in titles) == 1


def test_declared_character_who_never_appears() -> None:
    scenes = [_scene()]
    items = script_quality_suggestions(
        scenes,
        [{"name": "TEMILADE", "notes": "", "voice_description": ""}, {"name": "BOLA", "notes": "", "voice_description": ""}],
        ["Temilade walks down the long hallway."],
    )
    assert _titles(items) == ["Bola is in the cast list but never appears"]
