from io import BytesIO
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend import api, media, continuity, tasks
from backend.crop import crop_and_enhance_frame
from backend.database import Base, get_db
from backend.models import Character, CharacterReference, Project, Scene, ScriptVersion, Shot, ShotTake
from backend.settings import settings


def make_test_image(width=1920, height=1080, color="blue") -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", (width, height), color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_lock_blocking_prompt_directives():
    # 1. When lock_blocking is False (default)
    prompt = "Four friends chatting on the sofa"
    ref = media.VisualReferenceImage(
        mime_type="image/png",
        data=b"abc",
        reference_type="image_reference",
    )
    unlocked = media.prepare_visual_reference_prompt(prompt, references=[ref], lock_blocking=False)
    assert "Do not duplicate the reference composition" in unlocked
    assert "SPATIAL BLOCKING CONSTRAINT" not in unlocked

    # 2. When lock_blocking is True without focal character
    locked = media.prepare_visual_reference_prompt(prompt, references=[ref], lock_blocking=True)
    assert "Do not duplicate the reference composition" not in locked
    assert "SPATIAL BLOCKING CONSTRAINT: Maintain the continuous horizontal stage arrangement" in locked

    # 3. When lock_blocking is True with focal character
    locked_focal = media.prepare_visual_reference_prompt(
        prompt, references=[ref], lock_blocking=True, focal_name="Tobi"
    )
    assert "This is a close-up of Tobi ONLY" in locked_focal


def test_continuity_image_prompt_blocking():
    characters = [
        {"id": "c1", "name": "Amaka", "kind": "character"},
        {"id": "c2", "name": "Zara", "kind": "character"},
        {"id": "c3", "name": "Tobi", "kind": "character"},
    ]
    scene = {
        "location": "LIVING ROOM",
        "time_of_day": "DAY",
        "set_notes": "Sofa and coffee table",
        "lighting_notes": "Warm sunlight",
        "wardrobe_notes": "",
        "props_notes": "",
    }
    # Unlocked
    unlocked = continuity._image_prompt(
        "Discussion", characters, scene, "Close-up", "Listening", lock_blocking=False
    )
    assert "Camera is tightly cropped and angled to frame ONLY Amaka, Zara, Tobi" in unlocked
    assert "Spatial blocking lock" not in unlocked

    # Locked with focal character
    locked = continuity._image_prompt(
        "Discussion", characters, scene, "Close-up", "Listening",
        lock_blocking=True, focal_character="Tobi",
    )
    assert "Spatial blocking lock: This shot is a single close-up punch-in on Tobi only" in locked
    assert "DO NOT swap, mirror, or rearrange characters" in locked
    assert "Frame ONLY Tobi as the sole subject" in locked


def test_crop_and_enhance_frame(tmp_path):
    src_file = tmp_path / "source.png"
    src_file.write_bytes(make_test_image(1920, 1080, "green"))
    dest_file = tmp_path / "cropped_16_9.png"

    # Crop right 30% of the image
    dims = crop_and_enhance_frame(
        src_path=src_file,
        dest_path=dest_file,
        aspect_ratio="16:9",
        x=0.6,
        y=0.1,
        width=0.35,
        height=0.6,
    )
    assert dims == (1920, 1080)
    assert dest_file.exists()
    with Image.open(dest_file) as img:
        assert img.size == (1920, 1080)

    # Crop for 9:16
    dest_file_916 = tmp_path / "cropped_9_16.png"
    dims_916 = crop_and_enhance_frame(
        src_path=src_file,
        dest_path=dest_file_916,
        aspect_ratio="9:16",
        x=0.6,
        y=0.1,
        width=0.35,
        height=0.6,
    )
    assert dims_916 == (1080, 1920)
    assert dest_file_916.exists()
    with Image.open(dest_file_916) as img:
        assert img.size == (1080, 1920)


@pytest.fixture
def setup_api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "SessionLocal", sessionmaker(engine))
    monkeypatch.setattr(api, "enqueue_shot_image", lambda *a, **kw: SimpleNamespace(id="queued-img"))

    with Session(engine) as db:
        project = Project(title="Blocking Test", status="shot_plan", aspect_ratio="16:9")
        char_amaka = Character(project=project, name="Amaka")
        char_tobi = Character(project=project, name="Tobi")
        tobi_ref_fn = "tobi_portrait.png"
        (settings.media_dir / tobi_ref_fn).write_bytes(make_test_image(512, 512, "yellow"))
        char_tobi_ref = CharacterReference(character=char_tobi, filename=tobi_ref_fn, mime_type="image/png")
        version = ScriptVersion(project=project, source_text="test")
        scene = Scene(script_version=version, title="Living Room", order_index=0)
        shot1 = Shot(
            scene=scene, order_index=0, title="Shot 1 - Ensemble",
            image_prompt="Amaka and Tobi on sofa", framing="Wide",
        )
        shot2 = Shot(
            scene=scene, order_index=1, title="Shot 2 - Close-Up",
            image_prompt="Tobi nodding", framing="Close-up",
        )
        db.add_all([project, char_amaka, char_tobi, char_tobi_ref, version, scene, shot1, shot2])
        db.commit()

        # Create source image for shot 1
        src_fn = "shot1_source.png"
        (settings.media_dir / src_fn).write_bytes(make_test_image(1920, 1080, "red"))
        take1 = ShotTake(
            shot_id=shot1.id,
            media_type="image",
            aspect_ratio="16:9",
            filename=src_fn,
            mime_type="image/png",
            source="generated",
            provider="mock",
            selected=True,
        )
        db.add(take1)
        db.commit()

        p_id = project.id
        s1_id = shot1.id
        s2_id = shot2.id
        tobi_id = char_tobi.id
        take1_id = take1.id

    client = TestClient(api.app)
    client.app.dependency_overrides[get_db] = lambda: Session(engine)
    return client, p_id, s1_id, s2_id, tobi_id, take1_id


def test_derive_take_crop_endpoint(setup_api):
    client, p_id, s1_id, s2_id, tobi_id, take1_id = setup_api

    # Derive take for shot 2 directly from shot 1's take
    res = client.post(
        f"/api/shots/{s2_id}/derive-take",
        json={
            "aspect_ratio": "16:9",
            "x": 0.5,
            "y": 0.2,
            "width": 0.4,
            "height": 0.6,
            "source_type": "take",
            "source_id": take1_id,
            "label": "Tobi punch-in",
        },
    )
    assert res.status_code in {200, 202}, res.text
    data = res.json()
    assert len(data["takes"]) >= 1
    take = data["takes"][-1]
    assert take["source"] == "cropped"
    assert take["provider"] == "optical_punch"
    assert take["selected"] is True
    assert "Tobi punch-in" in take["prompt"]
    assert take["url"]


def test_image_generate_with_lock_blocking(setup_api):
    client, p_id, s1_id, s2_id, tobi_id, take1_id = setup_api

    res = client.post(
        f"/api/shots/{s2_id}/image/generate",
        json={
            "aspect_ratio": "16:9",
            "character_ids": [tobi_id],
            "lock_blocking": True,
            "focal_character_id": tobi_id,
        },
    )
    assert res.status_code in {200, 202}, res.text
    job = res.json()
    meta = job["attempts"][0]
    assert meta["lock_blocking"] is True
    assert meta["focal_character_id"] == tobi_id
    assert "Spatial blocking lock" in meta["continuity_snapshot"]["effective_image_prompt"]


def test_focal_lock_limits_identity_references_to_focal_character():
    characters = [
        {"id": "c1", "name": "Amaka", "kind": "character"},
        {"id": "c2", "name": "Zara", "kind": "character"},
        {"id": "c3", "name": "Landlord", "kind": "character"},
    ]
    scene = {"location": "OFFICE", "time_of_day": "DAY", "set_notes": "", "lighting_notes": "",
             "wardrobe_notes": "", "props_notes": ""}
    locked = continuity._image_prompt(
        "Talk", characters, scene, "Close-up", "Listening",
        lock_blocking=True, focal_character="Zara",
    )
    assert "Character identity references: Zara." in locked
    assert "Amaka" not in locked and "Landlord" not in locked
    # Without a focal character, all people remain identity references.
    unfocused = continuity._image_prompt(
        "Talk", characters, scene, "Wide", "Talking", lock_blocking=True,
    )
    assert "Character identity references: Amaka, Zara, Landlord." in unfocused


def test_focal_lock_labels_scene_reference_as_setting_only():
    ref = media.VisualReferenceImage(mime_type="image/png", data=b"abc", reference_type="image_reference")
    out = media.prepare_visual_reference_prompt("Zara listens", references=[ref], lock_blocking=True, focal_name="Zara")
    assert "SETTING, LIGHTING, and Zara's SCREEN POSITION ONLY" in out
    assert "Do NOT reproduce the other people visible in it" in out


def test_focal_lock_strips_non_focal_characters_from_base_prompt_and_wardrobe():
    characters = [
        {"id": "c1", "name": "ZARA", "kind": "character"},
        {"id": "c2", "name": "MR. BAMIDELE", "kind": "character"},
    ]
    scene = {
        "location": "LANDLORD'S OFFICE",
        "time_of_day": "Late Afternoon",
        "set_notes": "Wooden desk and window",
        "lighting_notes": "Warm daylight",
        "wardrobe_notes": (
            "MR. BAMIDELE wears a pale-blue short-sleeved shirt, charcoal trousers and black sandals. "
            "ZARA wears a forest-green blouse and black trousers."
        ),
        "props_notes": "",
    }
    base_prompt = (
        "Visible character references: @MR_BAMIDELE, @ZARA. Set reference: @LANDLORD_S_OFFICE.\n"
        "Medium shot, eye level; @MR_BAMIDELE behind his wooden office desk screen-left, "
        "@ZARA beside the curtained office louvre window screen-right. "
        "@MR_BAMIDELE: 48-year-old Nigerian man, pale-blue shirt. "
        "@ZARA: 27-year-old Nigerian woman, forest-green blouse."
    )
    framing = "Medium shot, eye level; MR. BAMIDELE behind his wooden office desk screen-left, ZARA beside the curtained office louvre window screen-right."
    locked = continuity._image_prompt(
        base_prompt, characters, scene, framing, "Zara gestures calmly",
        lock_blocking=True, focal_character="ZARA",
    )
    assert "48-year-old Nigerian man" not in locked
    assert "pale-blue" not in locked
    assert "behind his wooden office desk screen-left" not in locked
    assert "Single medium close-up on ZARA" in locked
    assert "forest-green blouse" in locked


def test_video_continuity_prompt_excludes_unchecked_and_non_focal_characters():
    snapshot = {
        "lock_blocking": True,
        "focal_character_id": "c_zara",
        "focal_character_name": "ZARA",
        "characters": [
            {"id": "c_zara", "name": "ZARA", "kind": "character"},
            {"id": "c_bam", "name": "MR. BAMIDELE", "kind": "character"},
            {"id": "c_set", "name": "LANDLORD'S OFFICE", "kind": "scene"},
        ],
        "character_voices": [
            {"id": "c_zara", "name": "ZARA", "notes": "27-year-old Nigerian woman; forest-green blouse.", "voice_description": "soft clear middle register"},
            {"id": "c_bam", "name": "MR. BAMIDELE", "notes": "48-year-old Nigerian man; pale-blue shirt.", "voice_description": "firm low register"},
        ],
        "dialogue": [{"character": "ZARA", "text": "You already had one."}],
        "scene": {
            "location": "LANDLORD'S OFFICE",
            "time_of_day": "Late Afternoon",
            "set_notes": "Heavy wooden desk and louvre window",
            "lighting_notes": "Warm late-afternoon daylight",
            "wardrobe_notes": "MR. BAMIDELE wears a pale-blue shirt. ZARA wears a forest-green blouse.",
            "props_notes": "METAL KEY RING — metal key ring clipped to Bamidele's right belt loop.",
        },
        "framing": "Medium shot, eye level; MR. BAMIDELE behind his wooden office desk screen-left, ZARA beside the curtained office louvre window screen-right.",
        "action": "Zara opens one hand in a measured, calm gesture toward the desk.",
    }
    video_prompt = (
        "Visible character references: @MR_BAMIDELE, @ZARA. Set reference: @LANDLORD_S_OFFICE.\n"
        "One continuous 8-second setup from the opening frame. @ZARA opens one hand. "
        'Other visible residents listen silently with lips closed; keep their marked positions.'
    )
    fake_shot = SimpleNamespace(
        character_ids=["c_zara", "c_set"],
        framing=snapshot["framing"],
        action=snapshot["action"],
        dialogue=snapshot["dialogue"],
        video_prompt=video_prompt,
        sound="",
        duration_seconds=8,
        start_ms=None,
        duration_ms=8000,
        scene=None,
    )
    vp = continuity.video_continuity_prompt(snapshot, video_prompt, "16:9", shot=fake_shot)
    assert "BAMIDELE" not in vp.upper()
    assert "pale-blue" not in vp
    assert "METAL KEY RING" not in vp
    assert "Other visible residents" not in vp
    assert "Single-subject frame: ONLY ZARA is visible on camera" in vp
    assert "Character identity continuity: ZARA;" in vp


def test_unselected_character_removed_and_swapped_without_lock_blocking():
    characters = [
        {"id": "c_bam", "name": "MR. BAMIDELE", "kind": "character"},
        {"id": "c_tobi", "name": "TOBI", "kind": "character"},
        {"id": "c_set", "name": "LANDLORD'S OFFICE", "kind": "scene"},
    ]
    scene = {
        "location": "LANDLORD'S OFFICE",
        "time_of_day": "Late Afternoon",
        "set_notes": "Wooden desk and window",
        "lighting_notes": "Warm daylight",
        "wardrobe_notes": (
            "TOBI wears a burgundy polo and black trousers. "
            "AMAKA wears a mustard-yellow blouse and dark fitted jeans. "
            "MR. BAMIDELE wears a pale-blue shirt."
        ),
        "props_notes": "",
    }
    base_prompt = (
        "Visible character references: @MR_BAMIDELE, @AMAKA. Set reference: @LANDLORD_S_OFFICE.\n"
        "Medium close-up, eye level; @MR_BAMIDELE seated behind his wooden office desk screen-left, "
        "@AMAKA standing inside the office screen-right. "
        "@MR_BAMIDELE: 48-year-old Nigerian man, pale-blue shirt. "
        "@AMAKA: 29-year-old Nigerian woman, mustard-yellow blouse, dark fitted jeans. "
        "@MR_BAMIDELE stands behind his wooden office desk looking directly at @AMAKA. "
        "Environment: Compact ground-floor landlord office."
    )
    framing = (
        "Medium close-up, eye level; MR. BAMIDELE seated behind his wooden office desk screen-left, "
        "AMAKA standing inside the office screen-right."
    )
    compiled = continuity._image_prompt(
        base_prompt,
        characters,
        scene,
        framing,
        "Mr. Bamidele nods once.",
        lock_blocking=False,
        focal_character=None,
        all_project_people=["MR. BAMIDELE", "TOBI", "AMAKA", "ZARA"],
        character_bios={
            "TOBI": "28-year-old Nigerian man, burgundy polo, black tailored trousers",
            "AMAKA": "29-year-old Nigerian woman, mustard-yellow blouse, dark fitted jeans",
            "MR. BAMIDELE": "48-year-old Nigerian man, pale-blue shirt",
        },
    )
    assert "amaka" not in compiled.lower()
    assert "mustard-yellow" not in compiled.lower()
    assert "28-year-old Nigerian man, burgundy polo" in compiled
    assert "TOBI standing inside the office screen-right" in compiled
    assert "Character identity references: MR. BAMIDELE, TOBI." in compiled


def test_build_locked_blocking_camera_zoom_prompt_excludes_continuity_boilerplate():
    snapshot = {
        "lock_blocking": True,
        "focal_character_id": None,
        "focal_character_name": None,
        "characters": [
            {"id": "c_bam", "name": "MR. BAMIDELE", "kind": "character"},
            {"id": "c_tobi", "name": "TOBI", "kind": "character"},
            {"id": "c_set", "name": "COMPOUND COURTYARD", "kind": "scene"},
        ],
        "character_voices": [
            {"id": "c_bam", "name": "MR. BAMIDELE", "notes": "48-year-old Nigerian man; crisp pale-blue short-sleeved button-down shirt, charcoal trousers, black sandals."},
            {"id": "c_tobi", "name": "TOBI", "notes": "28-year-old Nigerian man; fitted burgundy polo shirt, black chino trousers, white sneakers."},
        ],
        "scene": {
            "location": "COMPOUND COURTYARD",
            "set_notes": "Small brown courtyard side table at center with three white plastic chairs on the gate, stair and office sides.",
            "wardrobe_notes": (
                "TOBI wears a fitted burgundy polo shirt, black chino trousers, and white sneakers. "
                "MR. BAMIDELE wears a crisp pale-blue short-sleeved button-down shirt, charcoal dress trousers, black sandals."
            ),
            "props_notes": "THREE WHITE PLASTIC CHAIRS — three white molded plastic armchairs.",
        },
    }
    zoom_prompt = continuity.build_locked_blocking_camera_zoom_prompt(snapshot)
    assert "From this image, turn your camera to capture these two characters" in zoom_prompt
    assert "MR. BAMIDELE (the man in the crisp pale-blue short-sleeved button-down shirt)" in zoom_prompt
    assert "TOBI (the man in the fitted burgundy polo shirt)" in zoom_prompt
    assert "without changing anything in the image sent" in zoom_prompt
    assert "three white plastic chairs" not in zoom_prompt.lower()
    assert "sandals" not in zoom_prompt.lower()
    assert "sneakers" not in zoom_prompt.lower()
    assert "Continuity constraints:" not in zoom_prompt


def test_build_locked_blocking_camera_zoom_prompt_single_female_character_pronoun():
    snapshot = {
        "lock_blocking": True,
        "focal_character_id": "c_amaka",
        "focal_character_name": "AMAKA",
        "characters": [
            {"id": "c_amaka", "name": "AMAKA", "kind": "character"},
            {"id": "c_bam", "name": "MR. BAMIDELE", "kind": "character"},
        ],
        "character_voices": [
            {"id": "c_amaka", "name": "AMAKA", "notes": "29-year-old Nigerian woman; mustard-yellow blouse, dark fitted jeans."},
        ],
        "scene": {
            "wardrobe_notes": "AMAKA wears a mustard-yellow blouse.",
        },
    }
    zoom_prompt = continuity.build_locked_blocking_camera_zoom_prompt(snapshot)
    assert "((" not in zoom_prompt
    assert "))" not in zoom_prompt
    assert "this character (AMAKA, the woman in the mustard-yellow blouse)" in zoom_prompt
    assert "Just turn your camera towards her, zoom if possible." in zoom_prompt
    assert "towards them" not in zoom_prompt



