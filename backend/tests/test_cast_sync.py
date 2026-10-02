from backend.cast import cast_portrait_prompt, match_character_ids, sync_project_cast
from backend.models import Character, Project, Scene, ScriptVersion, Shot
from backend.screenplay_parser import parse_character_consistency, parse_structured_screenplay


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
