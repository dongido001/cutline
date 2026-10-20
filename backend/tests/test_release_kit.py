import io
import json
import subprocess
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend import api, publishing, release_kit
from backend.database import Base, get_db
from backend.models import (
    Compilation,
    Episode,
    Job,
    Project,
    ProjectVariantExport,
    ReleaseKit,
    Scene,
    ScriptVersion,
    Shot,
    ShotClip,
    ShotImage,
)
from backend.providers import CompatibleScriptProvider
from backend.settings import settings


def _png(width=320, height=180, color=(200, 40, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "publish_mode", "mock")
    engine = create_engine(f"sqlite:///{tmp_path / 'kit.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    media = settings.media_dir
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=2",
         "-pix_fmt", "yuv420p", str(media / "clip.mp4")],
        check=True,
    )
    (media / "still.png").write_bytes(_png(1600, 900))
    (media / "final.mp4").write_bytes(b"final")
    project = Project(title="Night Market", status="shot_plan", aspect_ratio="16:9")
    episode = Episode(project=project, episode_number=1, title="The Stall", synopsis="Ada risks the family stall.")
    session.add(project)
    session.flush()
    version = ScriptVersion(project=project, episode_id=episode.id, source_text="x")
    scene = Scene(script_version=version, order_index=0, title="Market", action="Ada argues with her brother.",
                  dialogue=[{"character": "ADA", "text": "We are not selling."}])
    clip_shot = Shot(scene=scene, order_index=0, title="Argument", duration_ms=2000, image_prompt="x", video_prompt="y")
    clip_shot.clip = ShotClip(filename="clip.mp4", mime_type="video/mp4", source="generated", provider="test")
    still_shot = Shot(scene=scene, order_index=1, title="Still", duration_ms=2000, image_prompt="x", video_prompt="y")
    still_shot.image = ShotImage(filename="still.png", mime_type="image/png", source="generated", provider="test", prompt="x")
    session.add(version)
    project.variant_exports.append(ProjectVariantExport(
        variant="landscape", episode_id=episode.id, filename="final.mp4", duration_ms=2000,
        width=1920, height=1080, clip_count=1, fallback_count=0,
    ))
    session.add(version)
    session.commit()
    api.app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(release_kit, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    return engine, session, TestClient(api.app), project, episode, clip_shot, still_shot


def teardown(engine, session):
    api.app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def test_kits_are_scoped_by_episode_variant_and_compilation(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        base = f"/api/projects/{project.id}/release-kit"
        landscape = client.get(base, params={"variant": "landscape", "episode_id": episode.id}).json()
        again = client.get(base, params={"variant": "landscape", "episode_id": episode.id}).json()
        shorts = client.get(base, params={"variant": "shorts", "episode_id": episode.id}).json()
        compilation = Compilation(project_id=project.id, title="Season reel", episode_ids=[episode.id])
        session.add(compilation)
        session.commit()
        reel = client.get(base, params={"variant": "landscape", "compilation_id": compilation.id}).json()
        assert landscape["id"] == again["id"]
        assert len({landscape["id"], shorts["id"], reel["id"]}) == 3
        assert landscape["scope_label"] == "Episode 1: The Stall"
        assert reel["scope_label"] == "Season reel"
        assert client.get(base, params={"variant": "landscape", "episode_id": "nope"}).status_code == 404
        assert client.get(base, params={"variant": "square"}).status_code == 422
        assert len(client.get(f"/api/projects/{project.id}/release-kits").json()) == 3
    finally:
        teardown(engine, session)


def test_frame_capture_from_clip_and_still_then_delete(tmp_path, monkeypatch):
    engine, session, client, project, episode, clip_shot, still_shot = setup(tmp_path, monkeypatch)
    try:
        kit = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()
        response = client.post(f"/api/release-kits/{kit['id']}/frames", json={
            "source": {"kind": "shot_clip", "ref_id": clip_shot.id}, "source_ms": 1200, "timeline_ms": 1200, "label": "Argument",
        })
        assert response.status_code == 201, response.text
        response = client.post(f"/api/release-kits/{kit['id']}/frames", json={
            "source": {"kind": "shot_image", "ref_id": still_shot.id}, "source_ms": 0, "timeline_ms": 2500,
        })
        assert response.status_code == 201, response.text
        frames = response.json()["frames"]
        assert [frame["timeline_ms"] for frame in frames] == [1200, 2500]
        with Image.open(settings.media_dir / frames[0]["filename"]) as image:
            assert image.size == (640, 360)
        with Image.open(settings.media_dir / frames[1]["filename"]) as image:
            assert image.size == (1280, 720)
        assert frames[0]["url"].startswith("/media/release-frame-")

        other = Project(title="Other", status="shot_plan")
        other_version = ScriptVersion(project=other, source_text="x")
        other_scene = Scene(script_version=other_version, order_index=0, title="X")
        other_shot = Shot(scene=other_scene, order_index=0, title="X", image_prompt="x", video_prompt="y")
        session.add(other)
        session.commit()
        foreign = client.post(f"/api/release-kits/{kit['id']}/frames", json={
            "source": {"kind": "shot_clip", "ref_id": other_shot.id}, "source_ms": 0,
        })
        assert foreign.status_code == 404

        removed = client.delete(f"/api/release-kits/{kit['id']}/frames/{frames[0]['id']}")
        assert removed.status_code == 200
        assert [frame["id"] for frame in removed.json()["frames"]] == [frames[1]["id"]]
        assert not (settings.media_dir / frames[0]["filename"]).exists()
    finally:
        teardown(engine, session)


def test_suggest_sends_frames_to_bridge_and_keeps_custom_titles(tmp_path, monkeypatch):
    engine, session, client, project, episode, clip_shot, _ = setup(tmp_path, monkeypatch)
    try:
        kit = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()
        client.post(f"/api/release-kits/{kit['id']}/frames", json={
            "source": {"kind": "shot_clip", "ref_id": clip_shot.id}, "source_ms": 500,
        })
        client.patch(f"/api/release-kits/{kit['id']}", json={
            "brief": "Ada refuses to sell",
            "title_options": [{"text": "My own title", "custom": True}],
        })
        queued = []
        monkeypatch.setattr(api, "enqueue_release_copy", lambda provider, kit_id, job_id: queued.append((provider, kit_id, job_id)) or SimpleNamespace(id="t1"))
        response = client.post(f"/api/release-kits/{kit['id']}/suggest", json={"provider": "chatgpt_web"})
        assert response.status_code == 202, response.text
        provider, kit_id, job_id = queued[0]
        assert provider == "chatgpt_web"

        sent = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent["url"], sent["payload"] = url, json
            reply = {
                "titles": [{"text": "She Won't Sell", "angle": "stakes"}, {"text": "She Won't Sell", "angle": "direct"},
                           {"text": "What would you risk?", "angle": "question"}],
                "description": "Ada draws a line.\n\n#NightMarket",
                "tags": ["#Night Market", "drama", "DRAMA", "family"],
                "cover_ideas": [{"concept": "Ada guarding the stall", "headline": "NOT FOR SALE", "prompt": "Low angle"}],
            }
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "```json\n" + __import__("json").dumps(reply) + "\n```"}}]})

        monkeypatch.setattr(release_kit, "get_script_provider", lambda name: CompatibleScriptProvider("http://bridge/v1", "gpt", "key", True))
        monkeypatch.setattr(release_kit.httpx, "post", fake_post)
        monkeypatch.setattr(release_kit.httpx, "get", lambda url, headers=None, timeout=None: httpx.Response(
            200, request=httpx.Request("GET", url), json={"chat_attachments": {"images": True}}))
        release_kit.run_copy_job(kit_id, job_id)

        session.expire_all()
        job = session.get(Job, job_id)
        assert job.status == "completed", job.error
        content = sent["payload"]["messages"][1]["content"]
        assert content[0]["type"] == "text" and "Ada refuses to sell" in content[0]["text"]
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert sent["payload"]["new_session"] is True

        data = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()
        assert [item["text"] for item in data["title_options"]] == ["She Won't Sell", "What would you risk?", "My own title"]
        assert data["selected_title_id"] == data["title_options"][2]["id"]
        assert data["tags"] == ["Night Market", "drama", "family"]
        assert data["cover_ideas"][0]["headline"] == "NOT FOR SALE"
        assert data["copy_provider"] == "chatgpt_web"
    finally:
        teardown(engine, session)


def test_old_chatgpt_bridge_gets_text_only(monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["payload"] = json
        reply = {"titles": [{"text": "A title", "angle": "direct"}], "description": "", "tags": [], "cover_ideas": []}
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": __import__("json").dumps(reply)}}]})

    monkeypatch.setattr(release_kit, "get_script_provider", lambda name: CompatibleScriptProvider("http://bridge/v1", "gpt", "key"))
    monkeypatch.setattr(release_kit.httpx, "post", fake_post)
    monkeypatch.setattr(release_kit.httpx, "get", lambda url, headers=None, timeout=None: httpx.Response(
        200, request=httpx.Request("GET", url), json={"image_generation": {}}))
    release_kit.request_copy("chatgpt_web", {"series_title": "S", "frames_attached": 2}, "landscape", [("image/jpeg", b"x")])
    content = sent["payload"]["messages"][1]["content"]
    assert isinstance(content, str)
    assert "attached images" not in content


def test_mock_provider_suggestions_need_no_network(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        kit = release_kit.get_or_create_kit(session, project, "shorts", episode.id)
        copy = release_kit.request_copy("mock", release_kit.build_copy_context(session, kit), "shorts", [])
        assert len(copy["titles"]) == 5
        assert "#Shorts" in copy["description"]
        assert len(copy["cover_ideas"]) == 3
    finally:
        teardown(engine, session)


def test_cover_job_asks_bridge_to_paint_headline_and_fits_canvas(tmp_path, monkeypatch):
    engine, session, client, project, episode, clip_shot, _ = setup(tmp_path, monkeypatch)
    try:
        kit = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()
        client.post(f"/api/release-kits/{kit['id']}/frames", json={"source": {"kind": "shot_clip", "ref_id": clip_shot.id}, "source_ms": 500})
        client.patch(f"/api/release-kits/{kit['id']}", json={"cover_ideas": [{"id": "idea1", "concept": "Ada at the stall", "headline": "NOT FOR SALE", "prompt": "Low angle hero shot"}]})
        assert client.post(f"/api/release-kits/{kit['id']}/covers", json={"provider": "openai"}).status_code == 422
        assert client.post(f"/api/release-kits/{kit['id']}/covers", json={}).status_code == 422

        queued = []
        monkeypatch.setattr(api, "release_cover_worker", lambda *args: queued.append(args) or SimpleNamespace(id="t2"))
        response = client.post(f"/api/release-kits/{kit['id']}/covers", json={"idea_id": "idea1", "provider": "gemini_web"})
        assert response.status_code == 202, response.text
        kit_id, job_id, cover_id = queued[0]
        pending = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()["covers"][0]
        assert pending["status"] == "generating" and pending["job_id"] == job_id

        calls = {}

        def fake_image(provider, prompt, aspect_ratio, progress=None, references=None, **_):
            calls.update(provider=provider, prompt=prompt, aspect=aspect_ratio, refs=references)
            return _png(1344, 768, (20, 120, 220)), "model", {}

        monkeypatch.setattr(release_kit, "image_provider_statuses", lambda: [{"id": "gemini_web", "label": "Gemini Web", "available": True}])
        monkeypatch.setattr(release_kit, "_request_provider_image", fake_image)
        release_kit.run_cover_job(kit_id, job_id, cover_id)

        assert calls["provider"] == "gemini_web" and calls["aspect"] == "16:9"
        assert 'spelled exactly "NOT FOR SALE"' in calls["prompt"]
        assert len(calls["refs"]) == 1 and calls["refs"][0].mime_type == "image/jpeg"
        session.expire_all()
        data = client.get(f"/api/projects/{project.id}/release-kit", params={"episode_id": episode.id}).json()
        cover = data["covers"][0]
        assert cover["status"] == "ready", cover
        assert data["selected_cover_id"] == cover_id
        with Image.open(settings.media_dir / cover["filename"]) as image:
            assert image.size == (1280, 720)
    finally:
        teardown(engine, session)


def test_cover_job_records_bridge_failure(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        kit = release_kit.get_or_create_kit(session, project, "shorts", episode.id)
        cover = release_kit.start_cover(kit, None, "BIG NEWS", "A poster", "chatgpt_web")
        job = Job(project_id=project.id, kind="release_cover")
        session.add(job)
        session.commit()
        monkeypatch.setattr(release_kit, "image_provider_statuses", lambda: [{"id": "chatgpt_web", "label": "ChatGPT Web", "available": False}])
        release_kit.run_cover_job(kit.id, job.id, cover["id"])
        session.expire_all()
        stored = session.get(ReleaseKit, kit.id).covers[0]
        assert stored["status"] == "failed" and "offline" in stored["error"]
        assert session.get(Job, job.id).status == "failed"
        assert session.get(ReleaseKit, kit.id).selected_cover_id is None
    finally:
        teardown(engine, session)


def test_publish_with_cover_passes_thumbnail_through_dry_run(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        kit = release_kit.get_or_create_kit(session, project, "landscape", episode.id)
        cover = release_kit.import_cover(kit, _png(1280, 720), "My cover")
        session.commit()
        monkeypatch.setattr(api, "enqueue_publish_project", lambda *args: SimpleNamespace(id="publish-task"))
        body = {
            "platforms": ["youtube"], "scope": "final", "variant": "landscape", "title": "She Won't Sell",
            "privacy": {"youtube": "private"}, "release_kit_id": kit.id, "cover_id": cover["id"],
        }
        response = client.post(f"/api/projects/{project.id}/publish?episode_id={episode.id}", json=body)
        assert response.status_code == 202, response.text
        payload = session.get(Job, response.json()["id"]).attempts[0]
        assert payload["cover_filename"] == cover["filename"]
        assets = publishing.collect_publish_assets(project, payload, session)
        result = publishing.publish_assets(assets, payload, db=session)[0]
        assert result["thumbnail"] == cover["filename"]
        assert result["message"].endswith("with cover")

        missing = client.post(f"/api/projects/{project.id}/publish?episode_id={episode.id}", json={**body, "cover_id": "nope"})
        assert missing.status_code == 409
    finally:
        teardown(engine, session)


def test_youtube_thumbnail_set_reports_warnings_without_failing(tmp_path):
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (1280, 720)).save(cover, "JPEG")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["videoId"] == "ok":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(403, json={"error": {"message": "No permission", "errors": [{"reason": "forbidden"}]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert publishing._youtube_set_thumbnail(client, "token", "ok", cover) is None
        warning = publishing._youtube_set_thumbnail(client, "token", "bad", cover)
    assert "verification" in warning
    assert seen[0].url.path == "/upload/youtube/v3/thumbnails/set"
    assert seen[0].headers["content-type"] == "image/jpeg"


def test_facebook_upload_attaches_thumb(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "facebook_page_access_token", "tok")
    monkeypatch.setattr(settings, "facebook_page_id", "page")
    video = settings.media_dir / "v.mp4"
    video.write_bytes(b"v")
    thumb = settings.media_dir / "t.jpg"
    Image.new("RGB", (64, 36)).save(thumb, "JPEG")
    captured = {}

    def fake_post(url, data=None, files=None, timeout=None):
        captured["files"] = {key: value[0] for key, value in files.items()}
        return httpx.Response(200, request=httpx.Request("POST", url), json={"id": "fb1"})

    monkeypatch.setattr(publishing.httpx, "post", fake_post)
    asset = publishing.PublishAsset(id="final:landscape", scope="final", label="x", filename="v.mp4", path=video,
                                    duration_ms=1, aspect_ratio="16:9", variant="landscape", source_ref="r")
    result = publishing._facebook_upload(asset, {"title": "T", "thumbnail_path": str(thumb)})
    assert result["external_id"] == "fb1"
    assert captured["files"] == {"source": "v.mp4", "thumb": "t.jpg"}


def test_compilation_publish_plan_uses_compilation_export_only(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        compilation = Compilation(project_id=project.id, title="Reel", episode_ids=[episode.id])
        session.add(compilation)
        session.flush()
        (settings.media_dir / "reel.mp4").write_bytes(b"reel")
        session.add(ProjectVariantExport(project_id=project.id, compilation_id=compilation.id, variant="landscape",
                                         filename="reel.mp4", duration_ms=9000, width=1920, height=1080, clip_count=2, fallback_count=0))
        session.commit()
        session.refresh(project)
        plan = client.get(f"/api/projects/{project.id}/publish/plan", params={"variant": "landscape", "compilation_id": compilation.id}).json()
        assert plan["final"][0]["duration_ms"] == 9000
        assert plan["final"][0]["label"].endswith("compilation")
        assert plan["scenes"] == [] and plan["shots"] == []
        monkeypatch.setattr(api, "enqueue_publish_project", lambda *args: SimpleNamespace(id="publish-task"))
        response = client.post(f"/api/projects/{project.id}/publish", params={"compilation_id": compilation.id}, json={
            "platforms": ["youtube"], "scope": "final", "variant": "landscape", "title": "Reel", "privacy": {"youtube": "private"},
        })
        assert response.status_code == 202, response.text
        job = session.get(Job, response.json()["id"])
        assert job.compilation_id == compilation.id
        assets = publishing.collect_publish_assets(project, job.attempts[0], session)
        assert assets[0].filename == "reel.mp4"
    finally:
        teardown(engine, session)


def test_suggest_thumbnail_hooks_and_auto_cover_headline(tmp_path, monkeypatch):
    engine, session, client, project, episode, *_ = setup(tmp_path, monkeypatch)
    try:
        # 1. Suggest hooks helper
        hooks = release_kit.suggest_thumbnail_hooks("they pushed too early")
        assert len(hooks) >= 1
        assert any("PUSHED TOO EARLY" in h["full"] for h in hooks)
        assert any("I CLUTCHED" in h["full"] for h in hooks)

        # 2. Auto-suggest headline in start_cover when headline is None or empty
        kit = release_kit.get_or_create_kit(session, project, "landscape", episode.id)
        cover1 = release_kit.start_cover(kit, None, None, "they pushed too early", "chatgpt_web")
        assert cover1["headline"] != ""
        assert "PUSHED TOO EARLY" in cover1["headline"]

        # 3. Explicit NO_TEXT honors empty headline
        cover2 = release_kit.start_cover(kit, None, "NO_TEXT", "they pushed too early", "chatgpt_web")
        assert cover2["headline"] == ""

        # 4. Prompt builder with dual hook
        prompt = release_kit.build_cover_prompt(
            {"series_title": "Game"}, "landscape", {"concept": "A battle"}, "PUSHED TOO EARLY // I CLUTCHED", 0
        )
        assert "Top text" in prompt and "PUSHED TOO EARLY" in prompt
        assert "Bottom text" in prompt and "I CLUTCHED" in prompt

        # 5. API endpoint /api/release-kits/{kit_id}/suggest-hooks
        resp = client.post(f"/api/release-kits/{kit.id}/suggest-hooks", json={"prompt": "they pushed too early"})
        assert resp.status_code == 200
        data = resp.json()
        assert "hooks" in data and len(data["hooks"]) >= 1
        assert any("PUSHED TOO EARLY" in h["headline"] for h in data["hooks"])
    finally:
        teardown(engine, session)

