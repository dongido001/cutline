from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.continuity import get_or_create_shot_snapshot, video_continuity_prompt
from backend.database import Base
from backend.models import Character, CharacterReference, Project, Scene, ScriptVersion, Shot
from backend.settings import settings


def test_shot_snapshot_captures_exact_package_and_reuses_until_explicit_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'continuity.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            project = Project(title="Continuity", aspect_ratio="16:9")
            version = ScriptVersion(project=project, source_text="test")
            scene = Scene(
                script_version=version, order_index=0, title="INT. STUDIO - NIGHT",
                location="Studio", time_of_day="Night", set_notes="Rain window",
                lighting_notes="Blue practical", wardrobe_notes="Green coat", props_notes="Red mug",
            )
            shot = Shot(
                scene=scene, order_index=0, title="Mara close-up", framing="Close-up",
                action="She turns toward the door.", image_prompt="35mm film, warm practical light",
            )
            character = Character(project=project, name="MARA", notes="freckled, dark hair")
            reference = CharacterReference(filename="mara.png", mime_type="image/png")
            character.references.append(reference)
            (settings.media_dir / "mara.png").write_bytes(b"reference")
            db.add(project)
            db.flush()
            shot.character_ids = [character.id]
            first = get_or_create_shot_snapshot(db, project, shot, "16:9")
            assert first["character_ids"] == [character.id]
            assert first["characters"] == [{"id": character.id, "name": "MARA", "reference_ids": [reference.id]}]
            assert first["scene"] == {
                "title": "INT. STUDIO - NIGHT", "location": "Studio", "time_of_day": "Night",
                "set_notes": "Rain window", "lighting_notes": "Blue practical",
                "wardrobe_notes": "Green coat", "props_notes": "Red mug",
            }
            assert first["framing"] == "Close-up"
            assert first["action"] == "She turns toward the door."
            assert first["aspect_ratio"] == "16:9"
            assert "Character identity references: MARA" in first["effective_image_prompt"]
            assert "Wardrobe: Green coat." in first["effective_image_prompt"]
            assert "Visible shot action: She turns toward the door." in first["effective_image_prompt"]

            scene.wardrobe_notes = "Black jacket"
            reused = get_or_create_shot_snapshot(db, project, shot, "16:9")
            assert reused is first
            assert reused["scene"]["wardrobe_notes"] == "Green coat"
            refreshed = get_or_create_shot_snapshot(db, project, shot, "16:9", refresh=True)
            assert refreshed["scene"]["wardrobe_notes"] == "Black jacket"
            assert shot.continuity_snapshot == refreshed
    finally:
        engine.dispose()


def test_video_prompt_anchors_frame_and_uses_saved_constraints():
    snapshot = {
        "framing": "Close-up", "action": "She turns toward the door.",
        "characters": [{"name": "MARA"}],
        "scene": {"location": "Studio", "time_of_day": "Night", "set_notes": "Rain window",
                  "lighting_notes": "Blue practical", "wardrobe_notes": "Green coat", "props_notes": "Red mug"},
    }
    prompt = video_continuity_prompt(snapshot, "Slow push-in.", "16:9")
    assert "exact opening frame and visual anchor" in prompt
    assert "Character identity continuity: MARA; preserve canonical identities without face, age, hair, body, or voice changes." in prompt
    assert "Wardrobe continuity: Green coat." in prompt
    assert "Shot action: She turns toward the door." in prompt


def test_video_prompt_includes_flowkit_character_voices_and_phone_call_routing():
    from backend.screenplay_parser import parse_character_consistency

    script = (
        "Character consistency:\n"
        'GABRIEL "GABE" DE LEON, 29 — Filipino man with warm tan skin, defined jawline, composed expression.\n\n'
        "SHOT 1 — GABE IN SUV\n"
    )
    parsed_cast = parse_character_consistency(script)
    assert len(parsed_cast) == 1
    assert parsed_cast[0]["name"] == "GABE"
    assert "29 years old." in parsed_cast[0]["notes"]
    assert "29-year-old" in parsed_cast[0]["voice_description"]
    assert "baritone" in parsed_cast[0]["voice_description"]

    snapshot = {
        "framing": "Tight interior close-up",
        "action": "Gabe sits inside the SUV and lowers his phone slightly as he speaks into the phone.",
        "characters": [{"id": "char-gabe", "name": "GABE"}],
        "character_voices": [
            {
                "id": "char-gabe",
                "name": "GABE",
                "voice_description": "Steady, resonant 29-year-old Filipino baritone, subtle Filipino-English cadence, composed, controlled, unhurried delivery",
            }
        ],
        "dialogue": [{"character": "GABE", "text": "Four minutes. Wait for the horn, then unlock the side gate."}],
        "scene": {"location": "SUV interior", "time_of_day": "Morning"},
    }
    prompt = video_continuity_prompt(
        snapshot,
        'Camera holds in a tight interior close-up as Gabe lowers the phone slightly and speaks: "Four minutes. Wait for the horn, then unlock the side gate."',
        "16:9",
    )
    assert (
        "Character voices: GABE: Steady, resonant 29-year-old Filipino baritone, subtle Filipino-English cadence, composed, controlled, unhurried delivery."
        in prompt
    )
    assert (
        "Voice routing: GABE speaks on camera with their natural, direct, unfiltered chest voice in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech."
        in prompt
    )


def test_child_voices_and_duet_cue_expansion():
    from backend.cast import cast_portrait_prompt, shot_mentioned_names
    from backend.screenplay_parser import parse_character_consistency

    script = (
        "Character consistency\n\n"
        "JASMINE: Older sister, approximately 6 years old, warm brown skin, cheerful, confident, patient, and encouraging.\n\n"
        "SKYLAR: Younger sister, approximately 4 years old, warm brown skin, curious, playful, energetic.\n\n"
        "RAINBOW: Small magical animated rainbow character with six rounded bands.\n\n"
        "JASMINE AND SKYLAR: When this cue appears, it means the existing two sisters sing together as a duet. It is not a separate voice or character.\n\n"
        "SHOT 1 - WHERE DID THE COLORS GO?\n"
    )
    parsed = parse_character_consistency(script)
    assert [c["name"] for c in parsed] == ["JASMINE", "SKYLAR", "RAINBOW"]
    by_name = {c["name"]: c for c in parsed}
    assert "6-year-old young girl voice" in by_name["JASMINE"]["voice_description"]
    assert "contralto" not in by_name["JASMINE"]["voice_description"]
    assert "4-year-old young girl voice" in by_name["SKYLAR"]["voice_description"]
    assert "animated character voice" in by_name["RAINBOW"]["voice_description"]

    jasmine_char = Character(name="JASMINE", notes=by_name["JASMINE"]["notes"])
    assert "adults age 18 or older" not in cast_portrait_prompt(jasmine_char)

    duet_shot = {
        "title": "RED SONG BEAT",
        "action": "Jasmine and Skylar sing together.",
        "dialogue": [{"character": "JASMINE AND SKYLAR", "text": "Red, red, bright and fun!"}],
        "image_prompt": "Jasmine and Skylar in the playroom.",
        "video_prompt": "Both sisters sing together.",
    }
    mentioned_names, _ = shot_mentioned_names(duet_shot)
    assert mentioned_names == ["JASMINE", "SKYLAR"]

    snapshot = {
        "framing": "Medium two-shot",
        "action": "Jasmine and Skylar sing together.",
        "characters": [
            {"id": "c-jasmine", "name": "JASMINE"},
            {"id": "c-skylar", "name": "SKYLAR"},
        ],
        "character_voices": [
            {
                "id": "c-jasmine",
                "name": "JASMINE",
                "voice_description": by_name["JASMINE"]["voice_description"],
            },
            {
                "id": "c-skylar",
                "name": "SKYLAR",
                "voice_description": by_name["SKYLAR"]["voice_description"],
            },
        ],
        "dialogue": [{"character": "JASMINE AND SKYLAR", "text": "Red, red, bright and fun!"}],
        "scene": {"location": "Playroom", "time_of_day": "Morning"},
    }
    prompt = video_continuity_prompt(snapshot, "Both sisters sing.", "9:16")
    assert "JASMINE: Bright, cheerful 6-year-old young girl voice" in prompt
    assert "SKYLAR: Bright, cheerful 4-year-old young girl voice" in prompt


