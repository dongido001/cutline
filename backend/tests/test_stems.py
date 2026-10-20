import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend import stems, tasks
from backend.api import app
from backend.database import Base, get_db
from backend.models import Job, Project, Scene, ScriptVersion, Shot, StemSeparation, TimelineAsset
from backend.settings import settings


class QueuedTask:
    id = "task-1"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'stems.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _audio_asset(db, project: Project, seconds: float = 2.0) -> TimelineAsset:
    filename = "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(settings.media_dir / filename)],
        check=True, capture_output=True,
    )
    asset = TimelineAsset(project_id=project.id, filename=filename, mime_type="audio/wav", label="Tone")
    db.add(asset)
    db.commit()
    return asset


def test_selected_separator_defaults_to_demucs_and_persists(client) -> None:
    assert settings.stem_separator == "demucs"
    response = client.post("/api/stem-separators/selected", json={"provider": "uvr"})
    assert response.status_code == 200
    assert settings.stem_separator == "uvr"
    assert json.loads(settings.stem_separator_path.read_text()) == {"provider": "uvr"}
    assert [item["id"] for item in response.json() if item["selected"]] == ["uvr"]
    assert client.post("/api/stem-separators/selected", json={"provider": "spleeter"}).status_code == 422


def test_unknown_saved_separator_falls_back_to_demucs() -> None:
    settings.stem_separator_path.write_text(json.dumps({"provider": "nope"}))
    assert settings.stem_separator == "demucs"


def test_statuses_report_missing_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "lalal_api_key", "")
    monkeypatch.setattr(settings, "stems_venv", tmp_path / "missing-venv")
    by_id = {item["id"]: item for item in stems.separator_statuses()}
    assert set(by_id) == {"lalal", "demucs", "uvr"}
    assert not any(item["available"] for item in by_id.values())
    assert "MOVIE_LALAL_API_KEY" in by_id["lalal"]["detail"]
    assert "setup-stems" in by_id["demucs"]["detail"]


def test_start_separation_validates_source(client, db, monkeypatch) -> None:
    monkeypatch.setattr("backend.api.enqueue_stem_separation", lambda *args: QueuedTask())
    project = Project(title="Stems", status="shot_plan", aspect_ratio="16:9")
    other = Project(title="Other", status="shot_plan", aspect_ratio="16:9")
    db.add_all([project, other])
    db.commit()
    image = TimelineAsset(project_id=project.id, filename="still.png", mime_type="image/png", label="Still")
    foreign = TimelineAsset(project_id=other.id, filename="x.wav", mime_type="audio/wav", label="X")
    db.add_all([image, foreign])
    db.commit()
    url = f"/api/projects/{project.id}/stem-separations"

    def post(source, **extra):
        return client.post(url, json={"source": source, "duration_ms": 2000, **extra})

    assert post({"kind": "asset", "ref_id": image.id}).status_code == 422
    assert post({"kind": "asset", "ref_id": foreign.id}).status_code == 404
    assert post({"kind": "shot_clip", "ref_id": "missing"}).status_code == 404
    assert post({"kind": "shot_image", "ref_id": "x"}).status_code == 422
    assert client.post(url, json={"source": {"kind": "asset", "ref_id": image.id}, "duration_ms": 10}).status_code == 422

    audio = _audio_asset(db, project)
    response = post({"kind": "asset", "ref_id": audio.id}, label="Song", provider="lalal")
    assert response.status_code == 201
    body = response.json()
    assert body["job"]["kind"] == "stem_separation"
    assert body["job"]["provider"] == "lalal"
    assert body["separation"]["status"] == "queued"
    assert body["separation"]["label"] == "Song"
    assert db.get(Job, body["job"]["id"]).task_id == "task-1"

    settings.stem_separator_path.write_text(json.dumps({"provider": "uvr"}))
    assert post({"kind": "asset", "ref_id": audio.id}).json()["separation"]["provider"] == "uvr"

    def make_shot(owner: Project) -> Shot:
        version = ScriptVersion(project=owner, source_text="x")
        scene = Scene(script_version=version, order_index=0, title="INT. ROOM")
        shot = Shot(scene=scene, order_index=0, title="Shot", duration_ms=2000, image_prompt="", video_prompt="")
        db.add(shot)
        db.commit()
        return shot

    assert post({"kind": "shot_clip", "ref_id": make_shot(project).id}).status_code == 201
    assert post({"kind": "shot_audio", "ref_id": make_shot(other).id}).status_code == 404


def test_worker_creates_music_and_voice_assets(db, monkeypatch) -> None:
    project = Project(title="Stems", status="shot_plan", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    audio = _audio_asset(db, project, seconds=3)
    job = Job(project_id=project.id, kind="stem_separation", provider="demucs")
    db.add(job)
    db.flush()
    separation = StemSeparation(
        project_id=project.id, job_id=job.id, provider="demucs", source_kind="asset",
        source_ref_id=audio.id, label="Song", source_in_ms=500, duration_ms=2000,
    )
    db.add(separation)
    db.commit()
    seen = {}

    def fake_separate(provider, source, out_dir, progress):
        seen["provider"] = provider
        seen["duration"] = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
            capture_output=True, text=True, check=True,
        ).stdout)
        progress(0.5, "halfway")
        music, voice = out_dir / "m.wav", out_dir / "v.wav"
        music.write_bytes(source.read_bytes())
        voice.write_bytes(source.read_bytes())
        return music, voice

    monkeypatch.setattr(stems, "separate", fake_separate)
    tasks._separate_stems(separation.id)

    db.expire_all()
    separation = db.get(StemSeparation, separation.id)
    job = db.get(Job, job.id)
    assert separation.status == "completed", separation.error
    assert job.status == "completed" and job.progress == 1
    assert seen["provider"] == "demucs"
    assert abs(seen["duration"] - 2.0) < 0.05
    music = db.get(TimelineAsset, separation.music_asset_id)
    voice = db.get(TimelineAsset, separation.voice_asset_id)
    assert music.label == "Song music (Demucs)"
    assert voice.label == "Song voice (Demucs)"
    assert music.mime_type == "audio/mp4"
    assert (settings.media_dir / music.filename).stat().st_size > 0


def test_worker_reports_provider_errors(db, monkeypatch) -> None:
    project = Project(title="Stems", status="shot_plan", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    audio = _audio_asset(db, project)
    job = Job(project_id=project.id, kind="stem_separation", provider="lalal")
    db.add(job)
    db.flush()
    separation = StemSeparation(
        project_id=project.id, job_id=job.id, provider="lalal", source_kind="asset",
        source_ref_id=audio.id, label="Song", duration_ms=1000,
    )
    db.add(separation)
    db.commit()

    def failing(*_args):
        raise stems.StemSeparationError("LALAL.AI upload failed: not enough minutes")

    monkeypatch.setattr(stems, "separate", failing)
    tasks._separate_stems(separation.id)
    db.expire_all()
    assert db.get(StemSeparation, separation.id).status == "failed"
    job = db.get(Job, job.id)
    assert job.status == "failed"
    assert job.message == "LALAL.AI separation failed"
    assert "not enough minutes" in job.error


def test_lalal_client_uploads_splits_polls_and_downloads(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "lalal_api_key", "key-123")
    monkeypatch.setattr(stems.time, "sleep", lambda _s: None)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF-audio")
    calls = []
    checks = iter([
        {"result": {"task-9": {"status": "progress", "progress": 40}}},
        {"result": {"task-9": {"status": "success", "result": {"tracks": [
            {"type": "stem", "label": "vocals", "url": "https://cdn/vocals.wav"},
            {"type": "back", "label": "no_vocals", "url": "https://cdn/back.wav"},
        ]}}}},
    ])

    def fake_post(url, headers=None, json=None, content=None, timeout=None):
        calls.append((url.rsplit("/api/v1/", 1)[1], headers, json))
        assert headers["X-License-Key"] == "key-123"
        if url.endswith("upload/"):
            assert content == b"RIFF-audio"
            assert "filename=source.wav" in headers["Content-Disposition"]
            return httpx.Response(200, json={"id": "src-1", "name": "source.wav", "size": 10, "duration": 2, "expires": 0})
        if url.endswith("split/stem_separator/"):
            return httpx.Response(200, json={"task_id": "task-9"})
        if url.endswith("check/"):
            return httpx.Response(200, json=next(checks))
        if url.endswith("delete/"):
            return httpx.Response(200, json={})
        raise AssertionError(url)

    def fake_get(url, timeout=None, follow_redirects=None):
        return httpx.Response(200, content=b"music" if "back" in url else b"voice", request=httpx.Request("GET", url))

    monkeypatch.setattr(stems.httpx, "post", fake_post)
    monkeypatch.setattr(stems.httpx, "get", fake_get)
    progress = []
    music, voice = stems.separate_with_lalal(source, tmp_path, lambda f, m: progress.append(m))

    assert music.read_bytes() == b"music"
    assert voice.read_bytes() == b"voice"
    assert [call[0] for call in calls] == ["upload/", "split/stem_separator/", "check/", "check/", "delete/"]
    assert calls[1][2] == {"source_id": "src-1", "presets": {"stem": "vocals", "splitter": "auto", "encoder_format": "wav"}}
    assert calls[-1][2] == {"source_id": "src-1"}
    assert "LALAL.AI separating (40%)" in progress


def test_lalal_errors_are_readable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "lalal_api_key", "")
    with pytest.raises(stems.StemSeparationError, match="MOVIE_LALAL_API_KEY"):
        stems.separate_with_lalal(tmp_path / "x.wav", tmp_path)

    monkeypatch.setattr(settings, "lalal_api_key", "key")
    source = tmp_path / "x.wav"
    source.write_bytes(b"x")
    monkeypatch.setattr(stems.httpx, "post", lambda *a, **k: httpx.Response(403, json={"detail": "Not enough minutes", "code": "limit"}))
    with pytest.raises(stems.StemSeparationError, match="Not enough minutes.*Demucs or UVR"):
        stems.separate_with_lalal(source, tmp_path)


def _fake_venv(tmp_path, monkeypatch, binary: str):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / binary).write_text("#!/bin/sh\n")
    monkeypatch.setattr(settings, "stems_venv", venv)
    return venv


def test_demucs_command_and_outputs(tmp_path, monkeypatch) -> None:
    venv = _fake_venv(tmp_path, monkeypatch, "demucs")
    source = tmp_path / "source.wav"
    source.write_bytes(b"x")
    out = tmp_path / "out"
    seen = {}

    def fake_run(command, capture_output, text, timeout):
        seen["command"] = command
        stem_dir = out / "htdemucs" / "source"
        stem_dir.mkdir(parents=True)
        (stem_dir / "no_vocals.wav").write_bytes(b"m")
        (stem_dir / "vocals.wav").write_bytes(b"v")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stems.subprocess, "run", fake_run)
    music, voice = stems.separate_with_demucs(source, out)
    assert seen["command"] == [str(venv / "bin" / "demucs"), "--two-stems=vocals", "-n", "htdemucs", "-o", str(out), str(source)]
    assert music.name == "no_vocals.wav" and voice.name == "vocals.wav"


def test_uvr_command_outputs_and_failures(tmp_path, monkeypatch) -> None:
    venv = _fake_venv(tmp_path, monkeypatch, "audio-separator")
    source = tmp_path / "source.wav"
    source.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    seen = {}

    def fake_run(command, capture_output, text, timeout):
        seen["command"] = command
        (out / "source_(Instrumental)_model.wav").write_bytes(b"m")
        (out / "source_(Vocals)_model.wav").write_bytes(b"v")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stems.subprocess, "run", fake_run)
    music, voice = stems.separate_with_uvr(source, out)
    command = seen["command"]
    assert command[:2] == [str(venv / "bin" / "audio-separator"), str(source)]
    assert command[command.index("--model_filename") + 1] == settings.uvr_model
    assert command[command.index("--output_dir") + 1] == str(out)
    assert "(Instrumental)" in music.name and "(Vocals)" in voice.name

    monkeypatch.setattr(stems.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="loading\nModel file not found\n"))
    with pytest.raises(stems.StemSeparationError, match="UVR failed: Model file not found"):
        stems.separate_with_uvr(source, tmp_path / "empty")


def test_local_separators_explain_missing_install(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "stems_venv", tmp_path / "none")
    with pytest.raises(stems.StemSeparationError, match="setup-stems"):
        stems.separate("demucs", tmp_path / "x.wav", tmp_path)
    with pytest.raises(stems.StemSeparationError, match="setup-stems"):
        stems.separate("uvr", tmp_path / "x.wav", tmp_path)
