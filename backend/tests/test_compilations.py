import pytest
from fastapi.testclient import TestClient
from backend.api import app
from backend.database import Base, engine, SessionLocal
from backend.models import Project, Episode, ScriptVersion, Scene, Shot, ProjectTimeline, Compilation, ProjectVariantExport
from backend.timeline import ensure_timeline

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

def test_compilations_isolation_and_lifecycle():
    client = TestClient(app)
    db = SessionLocal()

    p = Project(title="TIDE COMPILATION TEST", status="approved", aspect_ratio="16:9")
    ep1 = Episode(project=p, episode_number=1, title="Episode 1")
    v1 = ScriptVersion(project=p, episode=ep1, source_text="s1", status="approved")
    sc1 = Scene(script_version=v1, title="Scene 1", order_index=0)
    sh1 = Shot(scene=sc1, title="Ep1 Shot 1", order_index=0, duration_ms=6000)
    sh2 = Shot(scene=sc1, title="Ep1 Shot 2", order_index=1, duration_ms=8000)

    ep2 = Episode(project=p, episode_number=2, title="Episode 2")
    v2 = ScriptVersion(project=p, episode=ep2, source_text="s2", status="approved")
    sc2 = Scene(script_version=v2, title="Scene 2", order_index=0)
    sh3 = Shot(scene=sc2, title="Ep2 Shot 1", order_index=0, duration_ms=7000)

    ep3 = Episode(project=p, episode_number=3, title="Episode 3")
    v3 = ScriptVersion(project=p, episode=ep3, source_text="s3", status="approved")
    sc3 = Scene(script_version=v3, title="Scene 3", order_index=0)
    sh4 = Shot(scene=sc3, title="Ep3 Shot 1", order_index=0, duration_ms=10000)

    db.add(p)
    db.commit()

    project_id = p.id
    ep1_id = ep1.id
    ep2_id = ep2.id
    ep3_id = ep3.id

    # Initialize each episode's timeline
    ensure_timeline(p, db, episode_id=ep1_id)
    ensure_timeline(p, db, episode_id=ep2_id)
    ensure_timeline(p, db, episode_id=ep3_id)
    db.close()

    try:
        # 1. Create Compilation A (Episodes 1 and 2)
        comp_a_res = client.post(
            f"/api/projects/{project_id}/compilations",
            json={
                "title": "Compilation A: Ep 1-2 Recap",
                "description": "Recap of early events",
                "episode_ids": [ep1_id, ep2_id],
            },
        )
        assert comp_a_res.status_code == 201
        comp_a = comp_a_res.json()
        comp_a_id = comp_a["id"]
        assert comp_a["title"] == "Compilation A: Ep 1-2 Recap"
        assert comp_a["episode_ids"] == [ep1_id, ep2_id]

        # 2. Create Compilation B (Episodes 1, 2, 3)
        comp_b_res = client.post(
            f"/api/projects/{project_id}/compilations",
            json={
                "title": "Compilation B: Full Director Cut",
                "description": "Episodes 1 to 3 full cut",
                "episode_ids": [ep1_id, ep2_id, ep3_id],
            },
        )
        assert comp_b_res.status_code == 201
        comp_b = comp_b_res.json()
        comp_b_id = comp_b["id"]
        assert comp_b["title"] == "Compilation B: Full Director Cut"
        assert comp_b["episode_ids"] == [ep1_id, ep2_id, ep3_id]

        # 3. Verify list of compilations
        list_res = client.get(f"/api/projects/{project_id}/compilations")
        assert list_res.status_code == 200
        comp_list = list_res.json()
        assert len(comp_list) == 2
        assert {c["id"] for c in comp_list} == {comp_a_id, comp_b_id}

        # 4. Verify project detail contains compilations
        detail_res = client.get(f"/api/projects/{project_id}")
        assert detail_res.status_code == 200
        assert len(detail_res.json()["compilations"]) == 2

        # 5. Fetch Timeline for Compilation A
        tl_a_res = client.get(f"/api/compilations/{comp_a_id}/timeline")
        assert tl_a_res.status_code == 200
        tl_a = tl_a_res.json()
        tl_a_doc = tl_a["document"]
        tl_a_count = len(tl_a_doc["primary"])
        # Compilation A has Ep1 (2 shots) + Ep2 (1 shot) = 3 shots
        assert tl_a_count == 3

        # Fetch Timeline for Compilation B
        tl_b_res = client.get(f"/api/compilations/{comp_b_id}/timeline")
        assert tl_b_res.status_code == 200
        tl_b = tl_b_res.json()
        tl_b_doc = tl_b["document"]
        tl_b_count = len(tl_b_doc["primary"])
        # Compilation B has Ep1 (2 shots) + Ep2 (1 shot) + Ep3 (1 shot) = 4 shots
        assert tl_b_count == 4

        # 6. Mutate Compilation A's timeline (reorder items)
        reversed_primary = list(reversed(tl_a_doc["primary"]))
        modified_a_doc = {**tl_a_doc, "primary": reversed_primary}
        update_a_res = client.put(
            f"/api/compilations/{comp_a_id}/timeline",
            json={
                "revision": tl_a["revision"],
                "document": modified_a_doc,
                "edit": "landscape",
            },
        )
        assert update_a_res.status_code == 200
        assert update_a_res.json()["revision"] == tl_a["revision"] + 1

        # 7. Verify Isolation: Compilation B timeline is COMPLETELY UNTOUCHED
        tl_b_check_res = client.get(f"/api/compilations/{comp_b_id}/timeline")
        assert tl_b_check_res.status_code == 200
        tl_b_check = tl_b_check_res.json()
        assert tl_b_check["revision"] == tl_b["revision"]
        assert len(tl_b_check["document"]["primary"]) == 4

        # Verify Isolation: Original Episode 1 timeline is COMPLETELY UNTOUCHED
        ep1_tl_res = client.get(f"/api/projects/{project_id}/timeline?episode_id={ep1_id}")
        assert ep1_tl_res.status_code == 200
        ep1_tl = ep1_tl_res.json()
        assert len(ep1_tl["document"]["primary"]) == 2

        # 8. Test Compilation Export queueing
        export_res = client.post(
            f"/api/compilations/{comp_a_id}/export?variant=landscape"
        )
        assert export_res.status_code == 202
        export_job = export_res.json()
        assert export_job["compilation_id"] == comp_a_id
        assert export_job["kind"] == "final_cut:landscape"

        # 9. Test Patch compilation (rename / settings)
        patch_res = client.patch(
            f"/api/compilations/{comp_a_id}",
            json={"title": "Compilation A: Updated Title"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["title"] == "Compilation A: Updated Title"

        # 10. Delete Compilation A
        del_a_res = client.delete(f"/api/compilations/{comp_a_id}")
        assert del_a_res.status_code == 204

        # Compilation B still exists intact
        get_b_res = client.get(f"/api/compilations/{comp_b_id}")
        assert get_b_res.status_code == 200
        assert get_b_res.json()["id"] == comp_b_id

        # List shows only Compilation B
        after_del_list = client.get(f"/api/projects/{project_id}/compilations").json()
        assert len(after_del_list) == 1
        assert after_del_list[0]["id"] == comp_b_id
    finally:
        del_p_db = SessionLocal()
        del_p = del_p_db.get(Project, project_id)
        if del_p:
            del_p_db.delete(del_p)
            del_p_db.commit()
        del_p_db.close()


def test_compilation_seeds_shorts_from_episode_shorts_and_rebuilds_on_membership_change():
    client = TestClient(app)
    db = SessionLocal()

    p = Project(title="COMP SHORTS SEED", status="approved", aspect_ratio="16:9")
    ep1 = Episode(project=p, episode_number=1, title="Episode 1")
    v1 = ScriptVersion(project=p, episode=ep1, source_text="s1", status="approved")
    sc1 = Scene(script_version=v1, title="Scene 1", order_index=0)
    sh1 = Shot(scene=sc1, title="Ep1 Shot", order_index=0, duration_ms=5000)
    ep2 = Episode(project=p, episode_number=2, title="Episode 2")
    v2 = ScriptVersion(project=p, episode=ep2, source_text="s2", status="approved")
    sc2 = Scene(script_version=v2, title="Scene 2", order_index=0)
    sh2 = Shot(scene=sc2, title="Ep2 Shot", order_index=0, duration_ms=5000)
    db.add(p)
    db.commit()
    project_id = p.id
    ep1_id = ep1.id
    ep2_id = ep2.id
    sh1_id = sh1.id
    sh2_id = sh2.id

    tl1 = ensure_timeline(p, db, episode_id=ep1_id)
    tl2 = ensure_timeline(p, db, episode_id=ep2_id)
    # Mark episode 1 shorts with a distinctive LUT so we can detect it in the compilation.
    shorts_doc = dict(tl1.document)
    shorts_primary = []
    for item in shorts_doc.get("primary", []):
        item = dict(item)
        variant = dict(item.get("variant") or {})
        variant["shorts"] = {
            "source_aspect": "auto",
            "fit": "cover",
            "focal_x": 0.5,
            "focal_y": 0.5,
            "zoom": 1.0,
            "position": "center",
            "scale": 1.0,
            "crop_top": 0.0,
            "crop_bottom": 0.0,
            "crop_left": 0.0,
            "crop_right": 0.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "lut": "vibrant_gaming",
            "lut_strength": 1.0,
        }
        item["variant"] = variant
        shorts_primary.append(item)
    shorts_doc["primary"] = shorts_primary
    tl1.shorts_document = shorts_doc
    db.commit()
    db.close()

    try:
        create_res = client.post(
            f"/api/projects/{project_id}/compilations",
            json={"title": "Seed Test", "episode_ids": [ep1_id, ep2_id]},
        )
        assert create_res.status_code == 201
        comp_id = create_res.json()["id"]

        landscape = client.get(f"/api/compilations/{comp_id}/timeline?edit=landscape").json()
        shorts = client.get(f"/api/compilations/{comp_id}/timeline?edit=shorts").json()
        assert len(landscape["document"]["primary"]) == 2
        assert len(shorts["document"]["primary"]) == 2

        shorts_item = next(
            item
            for item in shorts["document"]["primary"]
            if item.get("source", {}).get("ref_id") == sh1_id
        )
        assert shorts_item["variant"]["shorts"]["lut"] == "vibrant_gaming"

        # Shrinking membership rebuilds the timeline snapshot.
        patch_res = client.patch(
            f"/api/compilations/{comp_id}",
            json={"episode_ids": [ep1_id]},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["episode_ids"] == [ep1_id]
        rebuilt = client.get(f"/api/compilations/{comp_id}/timeline?edit=landscape").json()
        assert len(rebuilt["document"]["primary"]) == 1
        assert rebuilt["document"]["primary"][0]["source"]["ref_id"] == sh1_id

        # Deleting an episode also rebuilds affected compilations.
        create2 = client.post(
            f"/api/projects/{project_id}/compilations",
            json={"title": "Delete Ep Test", "episode_ids": [ep1_id, ep2_id]},
        )
        assert create2.status_code == 201
        comp2_id = create2.json()["id"]
        del_ep = client.delete(f"/api/projects/{project_id}/episodes/{ep2_id}")
        assert del_ep.status_code == 204
        after_del = client.get(f"/api/compilations/{comp2_id}").json()
        assert after_del["episode_ids"] == [ep1_id]
        after_tl = client.get(f"/api/compilations/{comp2_id}/timeline?edit=landscape").json()
        assert len(after_tl["document"]["primary"]) == 1
        assert after_tl["document"]["primary"][0]["source"]["ref_id"] == sh1_id
        assert all(item.get("source", {}).get("ref_id") != sh2_id for item in after_tl["document"]["primary"])
    finally:
        del_p_db = SessionLocal()
        del_p = del_p_db.get(Project, project_id)
        if del_p:
            del_p_db.delete(del_p)
            del_p_db.commit()
        del_p_db.close()


def test_episode_shot_compilation():
    client = TestClient(app)
    db = SessionLocal()

    p = Project(title="EPISODE SHOT COMPILATION TEST", status="approved", aspect_ratio="16:9")
    ep = Episode(project=p, episode_number=1, title="Episode 1")
    v = ScriptVersion(project=p, episode=ep, source_text="s1", status="approved")
    sc = Scene(script_version=v, title="Scene 1", order_index=0)
    sh1 = Shot(scene=sc, title="Shot 1", order_index=0, duration_ms=5000)
    sh2 = Shot(scene=sc, title="Shot 2", order_index=1, duration_ms=4000)
    sh3 = Shot(scene=sc, title="Shot 3", order_index=2, duration_ms=6000)

    db.add(p)
    db.commit()

    project_id = p.id
    ep_id = ep.id
    sh1_id = sh1.id
    sh3_id = sh3.id

    ensure_timeline(p, db, episode_id=ep_id)
    db.close()

    try:
        # Create shot-level compilation (Short Cut containing only Shot 1 and Shot 3)
        res = client.post(
            f"/api/projects/{project_id}/compilations",
            json={
                "title": "Ep 1 Teaser Short",
                "episode_id": ep_id,
                "shot_ids": [sh1_id, sh3_id],
            },
        )
        assert res.status_code == 201
        comp = res.json()
        assert comp["title"] == "Ep 1 Teaser Short"
        assert comp["episode_id"] == ep_id
        assert comp["shot_ids"] == [sh1_id, sh3_id]
        comp_id = comp["id"]

        # Verify timeline for shot-level compilation
        tl_res = client.get(f"/api/compilations/{comp_id}/timeline")
        assert tl_res.status_code == 200
        tl = tl_res.json()
        assert len(tl["document"]["primary"]) == 2
        assert tl["document"]["primary"][0]["source"]["ref_id"] == sh1_id
        assert tl["document"]["primary"][1]["source"]["ref_id"] == sh3_id

        # Verify get compilation shots
        shots_res = client.get(f"/api/compilations/{comp_id}/shots")
        assert shots_res.status_code == 200
        shots = shots_res.json()
        assert len(shots) == 2
        assert [s["id"] for s in shots] == [sh1_id, sh3_id]
    finally:
        del_p_db = SessionLocal()
        del_p = del_p_db.get(Project, project_id)
        if del_p:
            del_p_db.delete(del_p)
            del_p_db.commit()
        del_p_db.close()
        client.close()
