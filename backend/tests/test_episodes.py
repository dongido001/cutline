import pytest
from fastapi.testclient import TestClient
from backend.api import app
from backend.database import Base, SessionLocal, engine
from backend.models import Episode, Project, ScriptVersion, Scene, Shot

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

def test_episode_lifecycle():
    client = TestClient(app)

    # 1. Create a project
    create_res = client.post(
        "/api/projects",
        json={
            "title": "CHRONICLES OF DESTINY",
            "aspect_ratio": "16:9",
            "target_duration_seconds": 60,
            "text_provider": "mock",
            "episode_label": "Episode",
            "script": "INT. CASTLE - DAY\nELENA enters with haste.",
        },
    )
    assert create_res.status_code == 202
    job = create_res.json()
    project_id = job["project_id"]

    # 2. Get project details
    detail_res = client.get(f"/api/projects/{project_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["episode_label"] == "Episode"
    assert len(detail["episodes"]) == 1
    assert detail["episodes"][0]["episode_number"] == 1
    ep1_id = detail["episodes"][0]["id"]
    assert detail["current_episode_id"] == ep1_id

    # 3. Update project settings to use 'Part'
    patch_res = client.patch(
        f"/api/projects/{project_id}",
        json={"episode_label": "Part", "title": "CHRONICLES OF DESTINY: THE SAGA"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["episode_label"] == "Part"

    # 4. Create Episode 2 (Part 2)
    ep2_res = client.post(
        f"/api/projects/{project_id}/episodes",
        json={
            "title": "Part 2: The Hidden Realm",
            "script": "INT. CAVE - NIGHT\nELENA discovers the glowing artifact.",
            "target_duration_seconds": 45,
        },
    )
    assert ep2_res.status_code == 202

    # 5. Check list of episodes
    list_res = client.get(f"/api/projects/{project_id}/episodes")
    assert list_res.status_code == 200
    episodes = list_res.json()
    assert len(episodes) == 2
    assert episodes[0]["episode_number"] == 1
    assert episodes[1]["episode_number"] == 2
    assert episodes[1]["title"] == "Part 2: The Hidden Realm"
    ep2_id = episodes[1]["id"]

    # 6. Switch episode in get_project
    switched_res = client.get(f"/api/projects/{project_id}?episode_id={ep2_id}")
    assert switched_res.status_code == 200
    switched = switched_res.json()
    assert switched["current_episode_id"] == ep2_id
    assert switched["current_version"]["episode_id"] == ep2_id

    # 7. Check timeline scoping
    tl1_res = client.get(f"/api/projects/{project_id}/timeline?episode_id={ep1_id}")
    assert tl1_res.status_code == 200
    tl2_res = client.get(f"/api/projects/{project_id}/timeline?episode_id={ep2_id}")
    assert tl2_res.status_code == 200

    # 8. Clean up
    del_res = client.delete(f"/api/projects/{project_id}/episodes/{ep2_id}")
    assert del_res.status_code == 204
    after_del_res = client.get(f"/api/projects/{project_id}/episodes")
    assert len(after_del_res.json()) == 1

    # Clean up project
    client.delete(f"/api/projects/{project_id}")


def test_cross_episode_reference_sources():
    from io import BytesIO
    from PIL import Image
    from backend.models import ShotImage
    from backend.settings import settings

    client = TestClient(app)
    db = SessionLocal()

    p = Project(title='Cross Ep Ref Test', status='shot_plan', aspect_ratio='16:9')
    ep1 = Episode(project=p, episode_number=1, title='Episode 1')
    v1 = ScriptVersion(project=p, episode=ep1, source_text='s1')
    sc1 = Scene(script_version=v1, title='Scene 1', order_index=0)
    sh1 = Shot(scene=sc1, title='Ep1 Finale Shot', order_index=0, image_prompt='sunset cliff')

    buffer = BytesIO()
    Image.new('RGB', (32, 24), 'red').save(buffer, format='PNG')
    filename = 'test-ep1-frame.png'
    (settings.media_dir / filename).write_bytes(buffer.getvalue())
    sh1.image = ShotImage(filename=filename, mime_type='image/png', source='uploaded', provider='test')

    ep2 = Episode(project=p, episode_number=2, title='Episode 2')
    v2 = ScriptVersion(project=p, episode=ep2, source_text='s2')
    sc2 = Scene(script_version=v2, title='Scene 2', order_index=0)
    sh2 = Shot(scene=sc2, title='Ep2 Opening Shot', order_index=0, image_prompt='sunset cliff sunrise')

    db.add(p)
    db.commit()

    try:
        # Check that Episode 2 Shot 1 can fetch sources from Episode 1
        res = client.get(f'/api/shots/{sh2.id}/image-reference/sources')
        assert res.status_code == 200
        sources = res.json()
        assert len(sources) == 1
        assert sources[0]['id'] == sh1.id
        assert '[Episode 1] Ep1 Finale Shot' in sources[0]['title']

        # Save reference across episodes
        ref_res = client.post(
            f'/api/shots/{sh2.id}/image-reference',
            data={
                'source_shot_id': sh1.id,
                'source_media_id': sh1.image.id,
                'source_media_kind': 'image',
                'aspect_ratio': '16:9',
                'mode': 'visual_continuity',
            }
        )
        assert ref_res.status_code == 200
        saved_shot = ref_res.json()
        assert len(saved_shot['image_references']) == 1
        assert saved_shot['image_references'][0]['source_shot_id'] == sh1.id
    finally:
        db.delete(p)
        db.commit()
        db.close()
