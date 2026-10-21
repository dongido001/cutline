from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.continuity import get_or_create_shot_snapshot, video_continuity_prompt
from backend.database import Base
from backend.models import Character, CharacterReference, Project, Scene, ScriptVersion, Shot
from backend.settings import settings


def test_snapshot_automatically_attaches_only_the_current_room_even_with_empty_cast(tmp_path, monkeypatch):
    from backend.cast import load_cast_references
    import pytest

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'sets.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title="Compound")
        version = ScriptVersion(project=project, source_text="test")
        scene = Scene(script_version=version, order_index=0, title="Night visit", location="INT. COMPOUND KITCHEN - NIGHT", lighting_notes="Warm lamp")
        shot = Shot(scene=scene, order_index=0, image_prompt="Empty kitchen", character_ids=[])
        kitchen = Character(project=project, kind="scene", name="@COMPOUND_KITCHEN", notes="Blue tiled walls, door on the left")
        parlour = Character(project=project, kind="scene", name="COMPOUND PARLOUR", notes="Red sofa")
        kitchen.references.append(CharacterReference(filename="kitchen.png", mime_type="image/png", view_type="scene"))
        (settings.media_dir / "kitchen.png").write_bytes(b"reference")
        db.add(project)
        db.flush()
        # A stale room selection must not override the current scene location.
        shot.character_ids = [parlour.id]
        snapshot = get_or_create_shot_snapshot(db, project, shot, "16:9", character_ids=[], require_cast_references=True)
        assert snapshot["character_ids"] == [kitchen.id]
        refs = load_cast_references(db, project.id, snapshot["character_ids"])
        assert [(ref.character_name, ref.view_type) for ref in refs] == [("@COMPOUND_KITCHEN", "scene")]
        assert snapshot["character_voices"] == []
        assert "Blue tiled walls" in snapshot["effective_image_prompt"]
        assert snapshot["scene"]["lighting_notes"] == "Warm lamp"
        assert "Character identity continuity" not in video_continuity_prompt(snapshot, "A slow pan", "16:9", shot=shot)
        scene.location = "COMPOUND PARLOUR"
        with pytest.raises(RuntimeError, match="COMPOUND PARLOUR"):
            get_or_create_shot_snapshot(db, project, shot, "16:9", require_cast_references=True)


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
            assert first["characters"] == [
                {"id": character.id, "name": "MARA", "kind": "character", "reference_ids": [reference.id]}
            ]
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


def test_phone_call_offscreen_caller_voice_and_lipsync_lock():
    from backend.continuity import current_shot_snapshot
    from backend.tasks import compose_video_prompt

    project = Project(title="Coastal Hold", aspect_ratio="9:16")
    gabe = Character(
        id="char-gabe",
        project=project,
        name="GABE",
        notes="29 years old. Filipino man with warm tan skin.",
        voice_description="Steady, resonant 29-year-old Filipino baritone, composed, unhurried delivery",
    )
    mateo = Character(
        id="char-mateo",
        project=project,
        name="MATEO",
        notes="Older Filipino field coordinator.",
        voice_description="Deep, weathered older Filipino baritone, low-key coastal delivery",
    )
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="HIDDEN CALL", location="Walkway", time_of_day="Morning")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="HIS FATHER OVERRIDES HIM",
        framing="Medium exterior profile shot",
        action="Gabe steps onto the walkway and answers the phone. He listens to Mateo's report from the handset and responds firmly. Mateo is not physically present.",
        dialogue=[{"character": "GABE", "text": "No. My father can wait."}],
        sound='Coastal breeze, Mateo\'s filtered phone voice says, "The chairman overrode your hold. He wants the Santos purchase offer delivered today.", Gabe\'s audible dialogue.',
        video_prompt=(
            "Gabe answers the phone and raises it to his right ear. "
            'Keep Gabe\'s mouth still while Mateo\'s filtered line plays: "The chairman overrode your hold. He wants the Santos purchase offer delivered today." '
            'Only then does Gabe speak with accurate lip synchronization: "No. My father can wait."'
        ),
        character_ids=[gabe.id],
    )

    snapshot = current_shot_snapshot(shot, "9:16")
    continuity_prompt = video_continuity_prompt(snapshot, shot.video_prompt, "9:16", shot=shot)
    effective_prompt = compose_video_prompt(continuity_prompt, shot.sound)

    assert "Keep Gabe's mouth still while" not in effective_prompt
    assert "Gabe listens silently with lips firmly closed and jaw motionless." in effective_prompt
    assert "MATEO (off-screen phone caller, unseen filtered telephone earpiece voice-over only — never lip-sync on-camera face to this voice): Deep, weathered older Filipino baritone" in effective_prompt
    assert 'Lip-sync lock: MATEO is an unseen off-screen telephone caller heard ONLY as filtered, band-limited earpiece voice-over' in effective_prompt
    assert 'GABE\'s own on-camera dialogue ("No. My father can wait.")' in effective_prompt
    assert effective_prompt.count("The chairman overrode your hold. He wants the Santos purchase offer delivered today.") == 1


def test_vocative_phone_address_does_not_create_offscreen_caller(tmp_path, monkeypatch) -> None:
    from backend.models import Character, Project, Scene, ScriptVersion, Shot
    from backend.continuity import current_shot_snapshot, video_continuity_prompt

    project = Project(title="Vocative phone test", aspect_ratio="16:9")
    gabe = Character(id="char-gabe", project=project, name="GABE", voice_description="Steady baritone")
    rosa = Character(id="char-rosa", project=project, name="ROSA", voice_description="Gentle Filipina")
    isabel = Character(id="char-isabel", project=project, name="ISABEL", voice_description="Clear phone voice")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="THE DECISION", location="Roadside", time_of_day="Late afternoon")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="SEND A CAR",
        framing="Medium Gabe-Rosa two-shot",
        action="Gabe holds phone to ear. Rosa watches silently.",
        dialogue=[{"character": "GABE", "text": "Isabel, send a car to San Aurelio. I'm coming to see my father."}],
        sound="Gabe's dialogue, coastal ambience.",
        video_prompt='Gabe says, "Isabel, send a car to San Aurelio. I\'m coming to see my father." Rosa watches silently.',
        character_ids=[gabe.id, rosa.id],
    )

    snapshot = current_shot_snapshot(shot, "16:9")
    continuity_prompt = video_continuity_prompt(snapshot, shot.video_prompt, "16:9", shot=shot)

    assert "ISABEL (off-screen phone caller" not in continuity_prompt
    assert "Lip-sync lock: ISABEL" not in continuity_prompt
    assert "Silent character lock: ROSA's lips and jaw MUST remain completely closed and motionless at all times — do NOT lip-sync ROSA to any speech or audio." in continuity_prompt
    # On-camera "Gabe says, ..." must not be rewritten into a filtered phone line.
    assert 'Gabe says, "Isabel, send a car to San Aurelio. I\'m coming to see my father."' in continuity_prompt
    assert "unseen caller Gabe" not in continuity_prompt


def test_props_notes_only_include_props_mentioned_in_shot() -> None:
    from backend.continuity import _filter_props_notes_for_shot, video_continuity_prompt

    props = (
        "@OLD_AGREEMENT is the exact folded aged cream Santos-De Leon historical agreement. "
        "@GABE begins Episode 12 holding it, then places it on the counter. "
        "@GABE_PHONE is Gabe's exact black smartphone and must be visibly established before use."
    )
    assert "@OLD_AGREEMENT" in _filter_props_notes_for_shot(
        props, '@GABE holds @OLD_AGREEMENT in his LEFT hand.'
    )
    assert "@GABE_PHONE" not in _filter_props_notes_for_shot(
        props, '@GABE holds @OLD_AGREEMENT in his LEFT hand.'
    )
    assert _filter_props_notes_for_shot(props, "@GABE and @MAYA speak face to face.") == ""
    assert _filter_props_notes_for_shot("Red mug on the table.", "She turns.") == "Red mug on the table."

    snapshot = {
        "characters": [{"id": "1", "name": "EDUARDO"}, {"id": "2", "name": "ISABEL"}],
        "dialogue": [{"character": "EDUARDO", "text": "The project continues."}],
        "scene": {
            "location": "@DE_LEON_OFFICE",
            "time_of_day": "Afternoon",
            "props_notes": props,
        },
        "framing": "Medium two-shot",
        "action": "@EDUARDO stands screen-LEFT. @ISABEL stands screen-RIGHT.",
    }
    prompt = video_continuity_prompt(
        snapshot,
        '@EDUARDO says, "The project continues."',
        "9:16",
    )
    assert "Props continuity:" not in prompt
    assert "@GABE_PHONE" not in prompt
    assert "@OLD_AGREEMENT" not in prompt


def test_on_camera_says_dialogue_is_not_rewritten_as_phone_call() -> None:
    from backend.continuity import _sanitize_phone_video_prompt, video_continuity_prompt

    video_prompt = (
        '@GABE says, "I\'ll help." '
        '@MAYA answers, "Help Nico get the older residents home. Come back before the rain gets bad." '
        '@NICO says, "Come on." '
        "@GABE and @NICO turn toward the doorway. End before they completely leave frame."
    )
    sanitized = _sanitize_phone_video_prompt(video_prompt)
    assert '@GABE says, "I\'ll help."' in sanitized
    assert '@NICO says, "Come on."' in sanitized
    assert "unseen caller" not in sanitized
    assert "telephone earpiece" not in sanitized

    # Explicit off-screen phone cues still sanitize.
    phone_prompt = (
        'Over the filtered telephone earpiece, unseen caller Isabel says, '
        '"Yes. But the acquisition continues." Gabe listens.'
    )
    phone_sanitized = _sanitize_phone_video_prompt(phone_prompt)
    assert "unseen caller Isabel speaks while the listener remains completely silent" in phone_sanitized

    snapshot = {
        "characters": [
            {"id": "1", "name": "GABE"},
            {"id": "2", "name": "MAYA"},
            {"id": "3", "name": "NICO"},
        ],
        "dialogue": [
            {"character": "GABE", "text": "I'll help."},
            {"character": "MAYA", "text": "Help Nico get the older residents home."},
            {"character": "NICO", "text": "Come on."},
        ],
        "scene": {
            "location": "@SANTOS_CAFE",
            "time_of_day": "Early Afternoon",
            "props_notes": "@GABE_PHONE is Gabe's exact black smartphone.",
        },
        "framing": "Medium three-shot",
        "action": "@GABE stands screen-LEFT with empty hands. @NICO stands CENTER. @MAYA stands screen-RIGHT.",
    }
    continuity_prompt = video_continuity_prompt(snapshot, video_prompt, "9:16")
    assert '@GABE says, "I\'ll help."' in continuity_prompt
    assert "unseen caller GABE" not in continuity_prompt
    assert "unseen caller NICO" not in continuity_prompt




def test_speaker_lock_pins_dialogue_to_screen_positions() -> None:
    from backend.continuity import current_shot_snapshot

    project = Project(title="Coastal Hold", aspect_ratio="16:9")
    gabe = Character(id="char-gabe", project=project, name="GABE", notes="29 years old.")
    eduardo = Character(id="char-eduardo", project=project, name="EDUARDO", notes="61 years old.")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="OFFICE", location="Office", time_of_day="Morning")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="I RESIGN",
        framing="Medium Gabe-primary two-shot",
        action="@GABE remains screen-LEFT facing @EDUARDO. His decision is already made.",
        image_prompt="Medium two-shot with @GABE dominant screen-LEFT and @EDUARDO screen-RIGHT.",
        dialogue=[
            {"character": "GABE", "text": "Then take what I have left. I resign today."},
            {"character": "EDUARDO", "text": "You'd walk away?"},
        ],
        video_prompt='@GABE says, "Then take what I have left. I resign today." @EDUARDO asks, "You\'d walk away?"',
        character_ids=[gabe.id, eduardo.id],
    )

    snapshot = current_shot_snapshot(shot, "16:9")
    prompt = video_continuity_prompt(snapshot, shot.video_prompt, "16:9", shot=shot)

    assert (
        'Speaker lock: GABE on screen-left speaks "Then take what I have left. I resign today."; '
        'EDUARDO on screen-right speaks "You\'d walk away?".'
    ) in prompt


def test_speaker_lock_skipped_without_distinct_positions() -> None:
    from backend.continuity import _speaker_position_lock

    snapshot = {
        "characters": [{"name": "GABE"}, {"name": "MAYA"}],
        "action": "Gabe and Maya sit at the counter.",
        "dialogue": [{"character": "GABE", "text": "Hi."}],
    }
    assert _speaker_position_lock(snapshot) == ""


def test_episode_timecodes_become_clip_relative() -> None:
    from backend.continuity import _clip_relative_timecodes

    shot = Shot(start_ms=170_000, end_ms=180_000, duration_ms=10_000)

    assert _clip_relative_timecodes("Hold on Maya. Snap to black exactly at 03:00.", shot) == (
        "Hold on Maya. Snap to black exactly at the very end of the clip."
    )
    assert _clip_relative_timecodes("The lights flicker at 02:54.", shot) == "The lights flicker at 4 seconds into the clip."
    assert _clip_relative_timecodes("Recall the storm at 01:10.", shot) == "Recall the storm at 01:10."


def test_cast_continuity_flags_missing_person_and_accepts_explicit_coverage():
    from backend.continuity import cast_continuity_warnings
    project = Project(title='Film')
    project.characters = [Character(id='a', name='AMAKA', kind='character'), Character(id='b', name='MR. BAMIDELE', kind='character')]
    version = ScriptVersion(project=project)
    scene = Scene(script_version=version, order_index=0)
    first = Shot(scene=scene, order_index=0, character_ids=['a'], action='Amaka waits.')
    shot = Shot(scene=scene, order_index=1, character_ids=['b'], framing='Medium shot of the landlord.')
    assert 'AMAKA' in cast_continuity_warnings(shot)[0]
    shot.image_prompt = 'AMAKA remains off camera beside the table.'
    assert cast_continuity_warnings(shot) == []
    shot.image_prompt = ''
    first.action = 'AMAKA leaves the courtyard.'
    assert cast_continuity_warnings(shot) == []
    first.action = 'Amaka waits.'
    shot.character_ids = ['a', 'b']
    assert cast_continuity_warnings(shot) == []
    other_scene = Scene(script_version=version, order_index=1)
    other = Shot(scene=other_scene, order_index=0, character_ids=['b'])
    assert cast_continuity_warnings(other) == []
