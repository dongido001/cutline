from backend.screenplay_parser import parse_structured_screenplay
from backend.takes import continuity_prompt_suffix
from backend.vibevoice_tts import format_dialogue_script
from backend.video import video_provider_status
from backend.settings import settings


def test_groups_by_location_change_without_scene_labels() -> None:
    script = """FILM TITLE

Location: Gate exterior

SHOT 1 - ARRIVAL
Location: Gate exterior
Time: 00:00-00:05
Framing: Wide
Action: A visitor approaches the gate.
Dialogue: None.
Sound: Wind.
Image prompt: Wide gate.
Video prompt: Visitor walks in.

SHOT 2 - INSIDE
Location: Guard booth
Time: 00:05-00:10
Framing: Close-up
Action: The guard watches.
Dialogue:
GUARD:
"State your business."
Sound: Radio static.
Image prompt: Guard close-up.
Video prompt: Guard turns.
"""
    result = parse_structured_screenplay(script)
    assert result is not None
    assert len(result["scenes"]) == 2
    assert result["scenes"][0]["location"].lower() == "gate exterior"
    assert result["scenes"][1]["location"].lower() == "guard booth"


def test_continuity_prompt_suffix_includes_scene_notes() -> None:
    class Scene:
        set_notes = "Iron gate, dusty road"
        lighting_notes = "Harsh noon sun"
        wardrobe_notes = "Guard uniform"
        props_notes = "Clipboard"
        location = "Gate"
        time_of_day = "Day"

    suffix = continuity_prompt_suffix(Scene())
    assert "Iron gate" in suffix
    assert "Harsh noon sun" in suffix


def test_format_dialogue_script_uses_speaker_numbers() -> None:
    script = format_dialogue_script(
        [
            {"character": "GUARD", "text": "Stop."},
            {"character": "VISITOR", "text": "Please."},
            {"character": "GUARD", "text": "Papers."},
        ]
    )
    assert "Speaker 1: Stop." in script
    assert "Speaker 2: Please." in script
    assert "Speaker 1: Papers." in script


def test_video_provider_defaults_to_runway_when_configured_via_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "video_provider", "runway")
    monkeypatch.setattr(settings, "runway_api_key", "")
    provider_id, label, configured, available, _, detail = video_provider_status()
    assert provider_id == "runway"
    assert label == "Runway"
    assert configured is False
    assert available is False
    assert "RUNWAY" in detail.upper() or "key" in detail.lower()
