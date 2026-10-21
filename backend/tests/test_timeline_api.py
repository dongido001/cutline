from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.api import app
from backend.database import Base, get_db
from backend.models import Project, Scene, ScriptVersion, Shot, ShotMediaVariant
from backend.settings import settings
from backend.timeline import promote_available_clips


def test_timeline_persists_split_and_aspect_media_without_replacing_legacy(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Timeline test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Test shot",
        duration_ms=2000,
        image_prompt="A room",
        video_prompt="Push in",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        landscape = client.post(
            f"/api/shots/{shot.id}/image/upload?aspect_ratio=16%3A9",
            files={"file": ("wide.png", b"wide", "image/png")},
        )
        vertical = client.post(
            f"/api/shots/{shot.id}/image/upload?aspect_ratio=9%3A16",
            files={"file": ("vertical.png", b"vertical", "image/png")},
        )
        assert landscape.status_code == 200
        assert vertical.status_code == 200
        assert vertical.json()["image"]["url"] == landscape.json()["image"]["url"]
        variants = vertical.json()["media_variants"]
        assert [(item["media_type"], item["aspect_ratio"]) for item in variants] == [("image", "9:16")]

        response = client.get(f"/api/projects/{project.id}/timeline")
        assert response.status_code == 200
        timeline = response.json()
        original = timeline["document"]["primary"][0]
        first = {**original, "duration_ms": 800, "source_out_ms": 800}
        second = {
            **original,
            "id": "split-b",
            "label": "Test shot B",
            "source_in_ms": 800,
            "duration_ms": 1200,
            "source_out_ms": 2000,
        }
        timeline["document"]["primary"] = [first, second]
        timeline["document"]["text_overlays"] = [
            {
                "id": "text-1",
                "text": "Lower third",
                "start_ms": 900,
                "duration_ms": 500,
                "presentation": "overlay",
                "size": "medium",
                "color": "white",
                "background": "shadow",
                "variant": {},
            }
        ]
        saved = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"]},
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == timeline["revision"] + 1
        assert len(saved.json()["document"]["primary"]) == 2

        conflict = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"]},
        )
        assert conflict.status_code == 409
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_available_clip_promotes_existing_storyboard_timeline_block(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'promotion.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Promotion test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Test shot", duration_ms=2000, image_prompt="A room", video_prompt="Push in")
    session.add(project)
    session.commit()

    from backend.timeline import ensure_timeline

    timeline = ensure_timeline(project, session)
    session.add(ShotMediaVariant(shot=shot, media_type="clip", aspect_ratio="9:16", filename="vertical.mp4", mime_type="video/mp4", source="uploaded", provider="manual-upload"))
    session.flush()

    assert promote_available_clips(project, session) == 1
    document = timeline.document
    item = document["primary"][0]
    assert item["kind"] == "video"
    assert item["source"]["kind"] == "shot_clip"
    assert item["source_out_ms"] == 2000
    session.close()


def test_timeline_accepts_float_millisecond_values_and_coerces_to_int(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline_floats.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Timeline float test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Test shot",
        duration_ms=2000,
        image_prompt="A room",
        video_prompt="Push in",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get(f"/api/projects/{project.id}/timeline")
        assert response.status_code == 200
        timeline = response.json()
        original = timeline["document"]["primary"][0]
        # Simulate fractional split milliseconds from floating point playhead
        first = {**original, "duration_ms": 800.4, "source_out_ms": 800.4}
        second = {
            **original,
            "id": "split-b",
            "label": "Test shot B",
            "source_in_ms": 800.4,
            "duration_ms": 1199.6,
            "source_out_ms": 2000.0,
        }
        timeline["document"]["primary"] = [first, second]
        timeline["document"]["text_overlays"] = [
            {
                "id": "text-1",
                "text": "Lower third",
                "start_ms": 900.2,
                "duration_ms": 500.8,
                "presentation": "overlay",
                "size": "medium",
                "color": "white",
                "background": "shadow",
                "variant": {},
            }
        ]
        saved = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"]},
        )
        assert saved.status_code == 200, saved.text
        doc = saved.json()["document"]
        assert doc["primary"][0]["duration_ms"] == 800
        assert doc["primary"][0]["source_out_ms"] == 800
        assert doc["primary"][1]["source_in_ms"] == 800
        assert doc["primary"][1]["duration_ms"] == 1200
        assert doc["text_overlays"][0]["start_ms"] == 900
        assert doc["text_overlays"][0]["duration_ms"] == 501
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_timeline_persists_mute_audio_across_episode_1_adoption(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline_mute.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Mute persistence test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. STUDIO - DAY")
    Shot(
        scene=scene,
        order_index=0,
        title="Opening shot",
        duration_ms=3000,
        image_prompt="A studio",
        video_prompt="Pan across",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        # 1. Create and edit legacy timeline before Episode 1 is resolved
        initial_res = client.get(f"/api/projects/{project.id}/timeline")
        assert initial_res.status_code == 200
        timeline = initial_res.json()
        timeline["document"]["primary"][0]["mute_audio"] = True

        saved_res = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": timeline["revision"], "document": timeline["document"], "edit": "landscape"},
        )
        assert saved_res.status_code == 200
        assert saved_res.json()["document"]["primary"][0]["mute_audio"] is True

        # 2. Fetch project detail (which auto-creates Episode 1) and then fetch timeline with episode_id
        proj_res = client.get(f"/api/projects/{project.id}")
        assert proj_res.status_code == 200
        ep1_id = proj_res.json()["current_episode_id"]
        assert ep1_id

        ep_timeline_res = client.get(f"/api/projects/{project.id}/timeline?episode_id={ep1_id}&edit=landscape")
        assert ep_timeline_res.status_code == 200
        ep_timeline = ep_timeline_res.json()
        assert ep_timeline["document"]["primary"][0]["mute_audio"] is True
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_timeline_audio_asset_upload_and_audio_overlays_persistence(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline_audio.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Audio timeline test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Opening shot",
        duration_ms=3000,
        image_prompt="A room",
        video_prompt="Pan across",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        upload_res = client.post(
            f"/api/projects/{project.id}/timeline/assets",
            files={"file": ("theme.mp3", b"ID3-fake-mp3-bytes", "audio/mpeg")},
            data={"label": "Theme Music"},
        )
        assert upload_res.status_code == 200, upload_res.text
        tl = upload_res.json()
        assert len(tl["assets"]) == 1
        asset = tl["assets"][0]
        assert asset["mime_type"] == "audio/mpeg"
        assert asset["label"] == "Theme Music"

        tl["document"]["audio_overlays"] = [
            {
                "id": "aud-1",
                "label": "Theme Music",
                "source": {"kind": "asset", "ref_id": asset["id"]},
                "start_ms": 200.4,
                "duration_ms": 1500.6,
                "source_in_ms": 100.2,
                "volume": 0.85,
                "mute_audio": False,
            },
            {
                "id": "aud-2",
                "label": "Opening shot audio",
                "source": {"kind": "shot_audio", "ref_id": shot.id},
                "start_ms": 0,
                "duration_ms": 1200,
                "source_in_ms": 0,
                "volume": 1.0,
                "mute_audio": True,
            },
        ]
        saved_res = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": tl["revision"], "document": tl["document"]},
        )
        assert saved_res.status_code == 200, saved_res.text
        saved_doc = saved_res.json()["document"]
        assert len(saved_doc["audio_overlays"]) == 2
        assert saved_doc["audio_overlays"][0]["start_ms"] == 200
        assert saved_doc["audio_overlays"][0]["duration_ms"] == 1501
        assert saved_doc["audio_overlays"][0]["source_in_ms"] == 100
        assert saved_doc["audio_overlays"][0]["volume"] == 0.85
        assert saved_doc["audio_overlays"][1]["mute_audio"] is True

        # Deleting an in-use audio asset is rejected with 409
        del_conflict = client.delete(f"/api/projects/{project.id}/timeline/assets/{asset['id']}")
        assert del_conflict.status_code == 409

        # Remove the asset audio overlay and delete the asset cleanly
        saved_doc["audio_overlays"] = [saved_doc["audio_overlays"][1]]
        saved_res2 = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": saved_res.json()["revision"], "document": saved_doc},
        )
        assert saved_res2.status_code == 200
        del_ok = client.delete(f"/api/projects/{project.id}/timeline/assets/{asset['id']}")
        assert del_ok.status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_timeline_video_asset_upload_and_multiple_video_overlays_persistence(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline_video.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Video layers test", status="shot_plan", aspect_ratio="9:16")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="Primary shot",
        duration_ms=5000,
        image_prompt="A room",
        video_prompt="Pan across",
    )
    session.add(project)
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        upload_res = client.post(
            f"/api/projects/{project.id}/timeline/assets",
            files={"file": ("overlay_layer.webm", b"\x1a\x45\xdf\xa3-fake-webm", "video/webm")},
            data={"label": "Secondary Overlay 1", "kind": "video"},
        )
        assert upload_res.status_code == 200, upload_res.text
        tl = upload_res.json()
        assert len(tl["assets"]) == 1
        asset = tl["assets"][0]
        assert asset["mime_type"] == "video/webm"
        assert asset["label"] == "Secondary Overlay 1"

        tl["document"]["video_overlays"] = [
            {
                "id": "vid-1",
                "label": "Secondary Overlay 1",
                "source": {"kind": "asset", "ref_id": asset["id"]},
                "start_ms": 0,
                "duration_ms": 4000,
                "source_in_ms": 0,
                "opacity": 1.0,
                "volume": 1.0,
                "mute_audio": False,
                "variant": {
                    "shorts": {"position": "full-frame", "fit": "cover", "scale": 1.0, "x": 0.0, "y": 0.0},
                },
            },
            {
                "id": "vid-2",
                "label": "Secondary Overlay 2",
                "source": {"kind": "shot_clip", "ref_id": shot.id},
                "start_ms": 500.4,
                "duration_ms": 3000.6,
                "source_in_ms": 200.2,
                "opacity": 0.85,
                "volume": 0.5,
                "mute_audio": True,
                "variant": {
                    "shorts": {"position": "full-frame", "fit": "cover", "scale": 1.0, "x": 0.0, "y": 0.0},
                },
            },
        ]
        saved_res = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": tl["revision"], "document": tl["document"]},
        )
        assert saved_res.status_code == 200, saved_res.text
        saved_doc = saved_res.json()["document"]
        assert len(saved_doc["video_overlays"]) == 2
        assert saved_doc["video_overlays"][0]["variant"]["shorts"]["position"] == "full-frame"
        assert saved_doc["video_overlays"][0]["variant"]["shorts"]["fit"] == "cover"
        assert saved_doc["video_overlays"][1]["start_ms"] == 500
        assert saved_doc["video_overlays"][1]["duration_ms"] == 3001
        assert saved_doc["video_overlays"][1]["source_in_ms"] == 200
        assert saved_doc["video_overlays"][1]["opacity"] == 0.85
        assert saved_doc["video_overlays"][1]["mute_audio"] is True

        # Deleting an in-use video asset is rejected with 409
        del_conflict = client.delete(f"/api/projects/{project.id}/timeline/assets/{asset['id']}")
        assert del_conflict.status_code == 409

        # Remove the asset video overlay and delete the asset cleanly
        saved_doc["video_overlays"] = [saved_doc["video_overlays"][1]]
        saved_res2 = client.put(
            f"/api/projects/{project.id}/timeline",
            json={"revision": saved_res.json()["revision"], "document": saved_doc},
        )
        assert saved_res2.status_code == 200
        del_ok = client.delete(f"/api/projects/{project.id}/timeline/assets/{asset['id']}")
        assert del_ok.status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_shot_clip_color_boost_step_undo_redo_reset(tmp_path, monkeypatch) -> None:
    from backend.models import ShotClip

    engine = create_engine(f"sqlite:///{tmp_path / 'clip_boost.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Clip boost test", status="shot_plan", aspect_ratio="9:16")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(
        scene=scene,
        order_index=0,
        title="DMZ clip",
        duration_ms=5000,
        image_prompt="A room",
        video_prompt="Pan across",
    )
    session.add(project)
    session.flush()
    clip_fn = "clip-dmz.webm"
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)
    (tmp_path / "media" / clip_fn).write_bytes(b"\x1a\x45\xdf\xa3-fake-webm")
    session.add(
        ShotClip(
            shot=shot,
            filename=clip_fn,
            mime_type="video/webm",
            source="uploaded",
            provider="manual-upload",
        )
    )
    session.add(
        ShotMediaVariant(
            shot=shot,
            media_type="clip",
            aspect_ratio="9:16",
            filename=clip_fn,
            mime_type="video/webm",
            source="uploaded",
            provider="manual-upload",
        )
    )
    session.commit()

    def override_db():
        yield session

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        res1 = client.post(f"/api/shots/{shot.id}/clip/retouch-color?aspect_ratio=9%3A16&action=boost")
        assert res1.status_code == 200, res1.text
        data1 = res1.json()
        assert data1["clip"]["color_boost_strength"] == 1.0
        assert data1["clip"]["can_undo_color"] is True
        assert data1["clip"]["can_redo_color"] is False

        res2 = client.post(f"/api/shots/{shot.id}/clip/retouch-color?aspect_ratio=9%3A16&action=increase")
        assert res2.status_code == 200
        assert res2.json()["clip"]["color_boost_strength"] == 1.25

        res_undo = client.post(f"/api/shots/{shot.id}/clip/retouch-color?aspect_ratio=9%3A16&action=undo")
        assert res_undo.status_code == 200
        assert res_undo.json()["clip"]["color_boost_strength"] == 1.0
        assert res_undo.json()["clip"]["can_redo_color"] is True

        res_redo = client.post(f"/api/shots/{shot.id}/clip/retouch-color?aspect_ratio=9%3A16&action=redo")
        assert res_redo.status_code == 200
        assert res_redo.json()["clip"]["color_boost_strength"] == 1.25

        res_reset = client.post(f"/api/shots/{shot.id}/clip/retouch-color?aspect_ratio=9%3A16&action=reset")
        assert res_reset.status_code == 200
        assert res_reset.json()["clip"]["color_boost_strength"] == 0.0
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_get_timeline_luts_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    client = TestClient(app)
    res = client.get("/api/timeline/luts")
    assert res.status_code == 200
    presets = res.json()
    assert isinstance(presets, list)
    assert len(presets) >= 10
    ids = [p["id"] for p in presets]
    assert "vibrant_gaming" in ids
    assert "warzone_crisp" in ids
    for p in presets:
        assert "label" in p
        assert "description" in p
        assert "category" in p
        assert "css_filter" in p


def test_get_timeline_lut_cube_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    client = TestClient(app)
    missing = client.get("/api/timeline/luts/not_a_real_lut/cube")
    assert missing.status_code == 404
    res = client.get("/api/timeline/luts/noir_bw/cube", params={"strength": 0.75})
    assert res.status_code == 200
    body = res.text
    assert "LUT_3D_SIZE 17" in body
    assert "DOMAIN_MIN" in body




def test_delete_shot_cleans_all_episode_timeline_variants(tmp_path):
    from backend.api import delete_shot
    from backend.models import Episode, ProjectTimeline
    engine = create_engine(f"sqlite:///{tmp_path / 'delete-all.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        project = Project(title='Series')
        episode1 = Episode(project=project, episode_number=1, title="First")
        episode2 = Episode(project=project, episode_number=2, title="Second")
        version = ScriptVersion(project=project, episode=episode2, source_text='script')
        scene = Scene(script_version=version, order_index=0, title='Room')
        deleted = Shot(scene=scene, order_index=0, title='Deleted')
        kept = Shot(scene=scene, order_index=1, title='Kept')
        db.add(project)
        db.flush()
        def document():
            return {'version': 1, 'primary': [
                {'id': shot.id, 'kind': 'image', 'label': shot.title, 'source': {'kind': 'shot_image', 'ref_id': shot.id}, 'duration_ms': 5000}
                for shot in [deleted, kept]]}
        timelines = [ProjectTimeline(project=project, episode=ep, document=document(), shorts_document=document(), revision=1) for ep in [episode1, episode2]]
        db.add_all(timelines)
        db.commit()
        delete_shot(deleted.id, db)
        for timeline in timelines:
            for data in [timeline.document, timeline.shorts_document]:
                assert [item['source']['ref_id'] for item in data['primary']] == [kept.id]
            assert timeline.revision > 1
