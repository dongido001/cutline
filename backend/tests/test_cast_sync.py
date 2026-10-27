from backend.cast import (
    cast_portrait_prompt,
    element_mention_slug,
    extract_mention_element_ids,
    match_character_ids,
    prompt_element_details,
    prompt_element_title,
    shot_mentioned_names,
    sync_project_cast,
    sync_project_elements,
)
from backend.models import Character, Project, Scene, ScriptVersion, Shot
from backend.screenplay_parser import parse_character_consistency, parse_structured_screenplay


def test_cast_inference_includes_people_declared_only_in_framing():
    characters = [
        Character(id="amaka", project_id="p", name="AMAKA"),
        Character(id="tobi", project_id="p", name="TOBI"),
    ]
    fields = dict(framing="Medium two-shot of AMAKA and TOBI at the table.",
                  action="They exchange a glance.", dialogue=[])
    for shot in (fields, Shot(scene_id="s", order_index=0, **fields)):
        names, text = shot_mentioned_names(shot)
        assert match_character_ids(characters, names, text) == ["amaka", "tobi"]


def test_sync_keeps_rooms_separate_and_reuses_location_across_time_changes(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'sets.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title="Compound")
        version = ScriptVersion(project=project, source_text="test")
        for index, location in enumerate(["INT. COMPOUND KITCHEN - DAY", "@COMPOUND_KITCHEN", "INT. COMPOUND PARLOUR - NIGHT", "COMPOUND COURTYARD", "Unspecified"]):
            db.add(Scene(script_version=version, order_index=index, title=f"Beat {index}", location=location,
                         set_notes="Fixed doors and furniture", lighting_notes="Red evening light", action="MAYA enters"))
        db.add(project)
        db.commit()
        sets = [item for item in sync_project_elements(project, db) if item.kind == "scene"]
        assert {item.name for item in sets} == {"COMPOUND KITCHEN", "COMPOUND PARLOUR", "COMPOUND COURTYARD"}
        assert all(item.notes == "Fixed doors and furniture" for item in sets)
        assert len([item for item in sync_project_elements(project, db) if item.kind == "scene"]) == 3


def test_cast_portrait_prompt_locks_face() -> None:
    character = Character(
        id="1",
        project_id="p",
        name="DANIEL",
        notes="34-year-old Nigerian man, low fade, mole on left cheek. WEALTHY LOOK: suit. POOR LOOK: faded shirt.",
    )
    prompt = cast_portrait_prompt(character)
    assert "DANIEL" in prompt
    assert "identity reference" in prompt.lower()
    assert "mole on left cheek" in prompt
    assert "neutral" in prompt.lower() or "plain" in prompt.lower()


def test_prop_prompt_resolves_at_tag_to_natural_language() -> None:
    prop = Character(
        id="prop-1",
        project_id="p",
        kind="prop",
        name="@BISCUIT_PACK — BRIGHT RED",
        notes="@BISCUIT_PACK — bright red sealed biscuit packet",
    )
    prompt = cast_portrait_prompt(prop, look="prop", has_attached_reference=False)
    assert "@BISCUIT_PACK" not in prompt
    assert "BISCUIT PACK" in prompt or "biscuit pack" in prompt.lower()
    assert "bright red sealed biscuit packet" in prompt
    assert "attached image" not in prompt.lower()

    with_seed = cast_portrait_prompt(prop, look="prop", has_attached_reference=True)
    assert "attached image is the prop identity reference" in with_seed.lower()


def test_element_mention_slug_and_details() -> None:
    assert element_mention_slug("@BISCUIT_PACK — BRIGHT RED") == "BISCUIT_PACK"
    assert prompt_element_title("@BISCUIT_PACK — BRIGHT RED") == "BISCUIT PACK"
    assert (
        prompt_element_details(
            "@BISCUIT_PACK — bright red sealed biscuit packet",
            "BISCUIT PACK",
            kind="prop",
        )
        == "bright red sealed biscuit packet"
    )


def test_extract_mention_matches_prop_slug() -> None:
    prop = Character(id="p1", project_id="proj", kind="prop", name="BISCUIT PACK", notes="red packet")
    scene = Character(id="s1", project_id="proj", kind="scene", name="KITCHEN", notes="warm set")
    ids = extract_mention_element_ids([prop, scene], "Hero holds @BISCUIT_PACK in the @KITCHEN")
    assert ids == ["p1", "s1"]


def test_sync_project_elements_strips_at_from_prop_names(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.database import Base
    from backend.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{tmp_path / 'cast.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Props", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(
        script_version=version,
        order_index=0,
        title="INT. KITCHEN - DAY",
        location="INT. KITCHEN - DAY",
        props_notes="@BISCUIT_PACK — bright red sealed biscuit packet; Brass Compass",
    )
    session.add(project)
    session.commit()

    elements = sync_project_elements(project, session)
    props = [e for e in elements if e.kind == "prop"]
    by_name = {e.name: e for e in props}
    assert "BISCUIT PACK" in by_name
    assert by_name["BISCUIT PACK"].notes == "bright red sealed biscuit packet"
    assert not by_name["BISCUIT PACK"].name.startswith("@")
    assert "BRASS COMPASS" in by_name
    session.close()


CAST_SCRIPT = """THE POOR MAN SHE CHOSE

Genre: Romance / Drama / Moral Story
Duration: 12 seconds
Format: 2 shots x 6 seconds
Aspect ratio: 9:16

Character consistency

DANIEL — 34-year-old Nigerian man, medium-dark skin, clean low fade haircut, neatly trimmed beard.
AMARA — 27-year-old Nigerian woman, warm brown skin, oval face, high cheekbones.
CHUKA (FRIEND) — 36-year-old Nigerian man, stocky, shaved head, thin moustache.

Location: Modern Lagos, Nigeria. Three recurring environments.

SHOT 1 - THE QUESTION IN THE DRIVEWAY

Scene: MANSION-A
Time: 00:00-00:06
Framing: Wide two-shot

Action:
Daniel stands beside the black SUV in his charcoal suit.

CHUKA:
"YOU HAVE MONEY. WHY HIDE IT?"

Sound: Distant generator hum.

Image prompt:
Vertical 9:16 cinematic frame of Daniel and Chuka beside a black Mercedes.

Video prompt:
Chuka steps toward Daniel while both preserve identity.

SHOT 2 - AMARA WAITS

Scene: MARKET
Time: 00:06-00:12
Framing: Medium

Action:
Amara waits near the stall.

AMARA:
"I believed in you."

Sound: Market chatter.

Image prompt:
Medium shot of Amara in a sky-blue ankara dress.

Video prompt:
Amara turns toward camera with a soft smile.
"""


def test_parse_character_consistency_block() -> None:
    characters = parse_character_consistency(CAST_SCRIPT)
    assert [item["name"] for item in characters] == ["DANIEL", "AMARA", "CHUKA"]
    assert "34-year-old Nigerian man" in characters[0]["notes"]
    assert characters[2]["notes"].startswith("(FRIEND)")


def test_structured_parser_includes_characters() -> None:
    result = parse_structured_screenplay(CAST_SCRIPT)
    assert result is not None
    assert len(result["characters"]) == 3
    assert result["characters"][0]["name"] == "DANIEL"


def test_sync_project_cast_tags_shots(db_session=None) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        project = Project(title="Cast sync", status="review")
        version = ScriptVersion(project=project, source_text=CAST_SCRIPT, status="review")
        scene = Scene(script_version=version, order_index=0, title="MANSION-A")
        shot_one = Shot(
            scene=scene,
            order_index=0,
            title="THE QUESTION IN THE DRIVEWAY",
            action="Daniel stands beside the black SUV.",
            dialogue=[{"character": "CHUKA", "text": "YOU HAVE MONEY. WHY HIDE IT?"}],
            image_prompt="Daniel and Chuka beside a black Mercedes.",
            video_prompt="preserve identity",
        )
        shot_two = Shot(
            scene=scene,
            order_index=1,
            title="AMARA WAITS",
            action="Amara waits near the stall.",
            dialogue=[{"character": "AMARA", "text": "I believed in you."}],
            image_prompt="Medium shot of Amara in a sky-blue ankara dress.",
            video_prompt="Amara turns toward camera",
        )
        session.add(project)
        session.commit()

        characters = sync_project_cast(project, session, source_text=CAST_SCRIPT, tag_shots=True)
        session.commit()
        session.refresh(shot_one)
        session.refresh(shot_two)

        names = sorted(character.name for character in characters)
        assert names == ["AMARA", "CHUKA", "DANIEL"]
        by_name = {character.name: character.id for character in characters}
        assert set(shot_one.character_ids) == {by_name["DANIEL"], by_name["CHUKA"]}
        assert shot_two.character_ids == [by_name["AMARA"]]
        assert "34-year-old Nigerian man" in session.get(Character, by_name["DANIEL"]).notes
    finally:
        session.close()


def test_generate_character_reference_queues_job(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.api import app
    from backend.database import Base, get_db
    from backend.settings import settings

    engine = create_engine(f"sqlite:///{tmp_path / 'cast-gen.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Cast gen", status="shot_plan")
    session.add(project)
    session.commit()
    character = Character(project_id=project.id, name="AMARA", notes="27-year-old Nigerian woman")
    session.add(character)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "backend.api.image_provider_statuses",
        lambda: [{"id": "chatgpt_web", "available": True, "label": "ChatGPT Web"}],
    )
    calls = {}

    def fake_enqueue(character_id, job_id, look=None):
        calls["character_id"] = character_id
        calls["job_id"] = job_id
        calls["look"] = look

        class Task:
            id = "cast-task-1"

        return Task()

    monkeypatch.setattr("backend.api.enqueue_cast_reference", fake_enqueue)
    client = TestClient(app)
    try:
        response = client.post(f"/api/characters/{character.id}/references/generate")
        assert response.status_code == 202
        payload = response.json()
        assert payload["kind"] == "cast_reference"
        assert calls["character_id"] == character.id
        assert calls["job_id"] == payload["id"]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_match_character_ids_uses_prompt_mentions() -> None:
    characters = [
        Character(id="1", project_id="p", name="DANIEL", notes=""),
        Character(id="2", project_id="p", name="AMARA", notes=""),
    ]
    matched = match_character_ids(characters, [], "A portrait of DANIEL in the driveway.")
    assert matched == ["1"]


def test_match_character_ids_recognizes_ensemble_and_troop_aliases() -> None:
    characters = [
        Character(id="1", project_id="p", name="WOMEN OF OLOKO", notes=""),
        Character(id="2", project_id="p", name="BRITISH COLONIAL TROOPS", notes=""),
    ]

    matched = match_character_ids(
        characters, [], "A CROWD STANDS FACING COLONIAL SOLDIERS."
    )

    assert matched == ["1", "2"]


def test_parse_explicit_cast_line() -> None:
    from backend.screenplay_parser import parse_explicit_cast_names

    assert parse_explicit_cast_names("Daniel, Chuka") == ["Daniel", "Chuka"]
    assert parse_explicit_cast_names("Maya and Gabe") == ["Maya", "Gabe"]
    assert parse_explicit_cast_names("Rosa & Mateo") == ["Rosa", "Mateo"]
    assert parse_explicit_cast_names("None") == []
    assert parse_explicit_cast_names("none.") == []
    assert parse_explicit_cast_names("N/A") == []


def test_sync_project_cast_respects_explicit_cast_with_fallback() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.database import Base

    script_with_cast = """TEST CAST SHOW

Genre: Drama
Duration: 18 seconds
Format: 3 shots x 6 seconds
Aspect ratio: 9:16

Character consistency

DANIEL — 34-year-old Nigerian man.
AMARA — 27-year-old Nigerian woman.
CHUKA — 36-year-old Nigerian man.

Location: Lagos, Nigeria.

SHOT 1 - EXPLICIT CAST
Scene: MANSION
Time: 00:00-00:06
Framing: Medium
Cast: Daniel

Action:
Daniel talks about Amara and Chuka.

DANIEL:
"Amara is not here."

Sound: Room tone.
Image prompt: Daniel alone.
Video prompt: Daniel speaks.

SHOT 2 - NO ONE PRESENT
Scene: EMPTY ROAD
Time: 00:06-00:12
Framing: Wide
Cast: None

Action:
The road Daniel and Amara usually walk along is deserted.

Sound: Wind.
Image prompt: Empty road.
Video prompt: Wind blowing.

SHOT 3 - FALLBACK AUTOMATIC
Scene: MARKET
Time: 00:12-00:18
Framing: Close-up

Action:
Amara looks around.

AMARA:
"Hello?"

Sound: Market buzz.
Image prompt: Amara looking.
Video prompt: Amara looks.
"""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        project = Project(title="Cast test", status="review")
        version = ScriptVersion(project=project, source_text=script_with_cast, status="review")
        scene = Scene(script_version=version, order_index=0, title="MANSION")
        shot1 = Shot(scene=scene, order_index=0, title="EXPLICIT CAST", action="Daniel talks about Amara and Chuka.")
        shot2 = Shot(scene=scene, order_index=1, title="NO ONE PRESENT", action="The road Daniel and Amara usually walk along is deserted.")
        shot3 = Shot(scene=scene, order_index=2, title="FALLBACK AUTOMATIC", action="Amara looks around.", dialogue=[{"character": "AMARA", "text": "Hello?"}])
        session.add(project)
        session.commit()

        characters = sync_project_cast(project, session, source_text=script_with_cast, tag_shots=True)
        session.commit()
        session.refresh(shot1)
        session.refresh(shot2)
        session.refresh(shot3)

        by_name = {c.name: c.id for c in characters}

        # Shot 1 has Cast: Daniel -> only Daniel is tagged (Amara and Chuka ignored despite mentions in action)
        assert shot1.character_ids == [by_name["DANIEL"]]

        # Shot 2 has Cast: None -> no characters tagged despite mentions in action
        assert shot2.character_ids == []

        # Shot 3 has no Cast line -> falls back to automatic detection (Amara tagged from action/dialogue)
        assert shot3.character_ids == [by_name["AMARA"]]
    finally:
        session.close()


def test_match_character_ids_ignores_title_and_location_tags() -> None:
    characters = [
        Character(id="1", project_id="p", name="GABE", notes=""),
        Character(id="2", project_id="p", name="EDUARDO", notes=""),
        Character(id="3", project_id="p", name="MAYA", notes=""),
        Character(id="4", project_id="p", name="OFFICE ASSISTANT", notes=""),
    ]
    shot = {
        "title": "NOT FOR MAYA",
        "action": "@EDUARDO remains screen-RIGHT. @GABE remains screen-LEFT.",
        "dialogue": [{"character": "EDUARDO", "text": "For Maya Santos?"}],
        "image_prompt": "Medium two-shot inside @DE_LEON_OFFICE. @GABE faces @EDUARDO.",
        "video_prompt": '@EDUARDO asks, "For Maya Santos?"',
    }
    names, haystack = shot_mentioned_names(shot)

    assert match_character_ids(characters, names, haystack) == ["1", "2"]


def test_sync_project_cast_reads_each_episodes_own_cast_lines() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.database import Base
    from backend.models import Episode

    def script(title: str, cast: str) -> str:
        return (
            "SHOW\n\nCharacter consistency\n\nGABE — 29-year-old man.\nMAYA — 27-year-old woman.\n\n"
            f"SHOT 1 - {title}\nScene: ROOM\nTime: 00:00-00:06\nFraming: Medium\nCast: {cast}\n\n"
            "Action:\nGabe and Maya talk.\n\nSound: Room tone.\nImage prompt: Gabe and Maya.\nVideo prompt: They talk.\n"
        )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        project = Project(title="Series", status="shot_plan")
        ep1 = Episode(project=project, episode_number=1, title="One")
        ep2 = Episode(project=project, episode_number=2, title="Two")
        v1 = ScriptVersion(project=project, episode=ep1, version_number=1, source_text=script("FIRST", "Gabe"), status="review")
        v2 = ScriptVersion(project=project, episode=ep2, version_number=2, source_text=script("SECOND", "Maya"), status="review")
        shot1 = Shot(scene=Scene(script_version=v1, order_index=0, title="ROOM"), order_index=0, title="FIRST", action="Gabe and Maya talk.")
        shot2 = Shot(scene=Scene(script_version=v2, order_index=0, title="ROOM"), order_index=0, title="SECOND", action="Gabe and Maya talk.")
        session.add(project)
        session.commit()

        characters = sync_project_cast(project, session, source_text=v2.source_text, tag_shots=True)
        session.commit()
        by_name = {c.name: c.id for c in characters}

        assert shot1.character_ids == [by_name["GABE"]]
        assert shot2.character_ids == [by_name["MAYA"]]
    finally:
        session.close()


def test_sync_project_cast_duplicate_shot_titles_across_different_scenes() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.database import Base

    script = (
        "SHOW\n\nCharacter consistency\n\nBAMIDELE — 50-year-old man.\nAMAKA — 28-year-old woman.\n\n"
        "SHOT 1 - The smile\nScene: SCENE A\nTime: 00:00-00:04\nFraming: Close-up\nCast: BAMIDELE\n\n"
        "Action:\nBamidele rehearses a smile.\n\nSound: Hum.\nImage prompt: Bamidele smiling.\nVideo prompt: Bamidele smiles.\n\n"
        "SHOT 2 - The smile\nScene: SCENE B\nTime: 00:04-00:08\nFraming: Close-up\nCast: AMAKA\n\n"
        "Action:\nAmaka forces a smile.\n\nSound: Hum.\nImage prompt: Amaka smiling.\nVideo prompt: Amaka smiles.\n"
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        project = Project(title="Series", status="shot_plan")
        v = ScriptVersion(project=project, version_number=1, source_text=script, status="review")
        scene1 = Scene(script_version=v, order_index=0, title="SCENE A")
        scene2 = Scene(script_version=v, order_index=1, title="SCENE B")
        shot1 = Shot(scene=scene1, order_index=0, title="The smile", action="Bamidele rehearses a smile.")
        shot2 = Shot(scene=scene2, order_index=0, title="The smile", action="Amaka forces a smile.")
        session.add(project)
        session.commit()

        characters = sync_project_cast(project, session, source_text=script, tag_shots=True)
        session.commit()
        by_name = {c.name: c.id for c in characters}

        assert shot1.character_ids == [by_name["BAMIDELE"]]
        assert shot2.character_ids == [by_name["AMAKA"]]
    finally:
        session.close()

