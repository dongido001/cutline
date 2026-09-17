from backend.draft import project_readiness
from backend.models import Project
from backend.settings import settings
from backend.vibevoice_tts import synthesize_dialogue


def test_synthesize_dialogue_mock_writes_wav(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "vibevoice_mode", "mock")
    filename, mime, provider = synthesize_dialogue(
        [{"character": "A", "text": "Hello there."}],
        duration_hint_ms=1200,
    )
    assert provider == "vibevoice-mock"
    assert mime == "audio/wav"
    assert (tmp_path / "media" / filename).is_file()


def test_project_readiness_reports_script_gate(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.database import Base

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "video_provider", "runway")
    monkeypatch.setattr(settings, "runway_api_key", "")
    monkeypatch.setattr(settings, "vibevoice_mode", "mock")
    engine = create_engine(f"sqlite:///{tmp_path / 'ready.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(title="Draft", status="review", aspect_ratio="16:9")
    session.add(project)
    session.commit()
    readiness = project_readiness(project, session)
    assert readiness["can_start"] is False
    assert any(item["code"] == "script" for item in readiness["attention"])
    assert any(item["code"] == "video_provider" for item in readiness["attention"])
    session.close()
