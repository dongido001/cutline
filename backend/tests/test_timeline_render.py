import shutil

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import Project, Scene, ScriptVersion, Shot, ShotImage, TimelineAsset
from backend.schemas import TimelineDocument
from backend.settings import settings
from backend.timeline_render import _has_audio, assemble_timeline


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_timeline_renders_both_variants_with_text_overlay(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'render.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    frame = tmp_path / "media" / "frame.ppm"
    frame.parent.mkdir()
    frame.write_text("P3\n2 2\n255\n120 20 20  120 20 20\n120 20 20  120 20 20\n")
    project = Project(title="Render test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Still", duration_ms=500)
    shot.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.commit()
    document = TimelineDocument.model_validate(
        {
            "version": 1,
            "primary": [
                {
                    "id": "still-1",
                    "kind": "image",
                    "label": "Still",
                    "source": {"kind": "shot_image", "ref_id": shot.id},
                    "duration_ms": 500,
                    "source_in_ms": 0,
                    "source_out_ms": None,
                    "text": "",
                    "variant": {},
                }
            ],
            "image_overlays": [],
            "text_overlays": [
                {
                    "id": "text-1",
                    "text": "HELLO",
                    "start_ms": 100,
                    "duration_ms": 300,
                    "presentation": "overlay",
                    "size": "medium",
                    "color": "white",
                    "background": "shadow",
                    "variant": {"shorts": {"position": "bottom"}},
                }
            ],
        }
    )
    landscape = assemble_timeline(project, document, "landscape", session)
    shorts = assemble_timeline(project, document, "shorts", session)
    assert (tmp_path / "media" / landscape.filename).is_file()
    assert (tmp_path / "media" / shorts.filename).is_file()
    assert (landscape.width, landscape.height) == (1920, 1080)
    assert (shorts.width, shorts.height) == (1080, 1920)
    assert 450 <= landscape.duration_ms <= 600
    assert 450 <= shorts.duration_ms <= 600
    session.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_timeline_renders_with_audio_overlay(tmp_path, monkeypatch) -> None:
    import wave

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'render_audio.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    frame = media_dir / "frame.ppm"
    frame.write_text("P3\n2 2\n255\n30 90 150  30 90 150\n30 90 150  30 90 150\n")

    wav_path = media_dir / "tone.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x10\x00" * 8000)  # 0.5s mono PCM

    project = Project(title="Audio render test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Still", duration_ms=500)
    shot.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.flush()
    audio_asset = TimelineAsset(project_id=project.id, filename="tone.wav", mime_type="audio/wav", label="Tone")
    session.add(audio_asset)
    session.commit()

    document = TimelineDocument.model_validate(
        {
            "version": 1,
            "primary": [
                {
                    "id": "still-1",
                    "kind": "image",
                    "label": "Still",
                    "source": {"kind": "shot_image", "ref_id": shot.id},
                    "duration_ms": 500,
                    "source_in_ms": 0,
                    "source_out_ms": None,
                    "text": "",
                    "variant": {},
                }
            ],
            "image_overlays": [],
            "text_overlays": [],
            "audio_overlays": [
                {
                    "id": "aud-1",
                    "label": "Tone",
                    "source": {"kind": "asset", "ref_id": audio_asset.id},
                    "start_ms": 100,
                    "duration_ms": 300,
                    "source_in_ms": 50,
                    "volume": 0.9,
                    "mute_audio": False,
                }
            ],
        }
    )
    rendered = assemble_timeline(project, document, "landscape", session)
    out_file = media_dir / rendered.filename
    assert out_file.is_file()
    assert _has_audio(out_file)
    assert 450 <= rendered.duration_ms <= 600
    session.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_timeline_renders_with_video_overlay_pip_and_crop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'render_video_overlay.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    frame = media_dir / "frame.ppm"
    frame.write_text("P3\n2 2\n255\n30 90 150  30 90 150\n30 90 150  30 90 150\n")

    overlay_frame = media_dir / "overlay_frame.ppm"
    overlay_frame.write_text("P3\n2 2\n255\n200 50 50  200 50 50\n200 50 50  200 50 50\n")

    project = Project(title="Video overlay render test", status="shot_plan", aspect_ratio="9:16")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot1 = Shot(scene=scene, order_index=0, title="Shot 1", duration_ms=600)
    shot1.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    shot2 = Shot(scene=scene, order_index=1, title="Shot 2", duration_ms=600)
    shot2.image = ShotImage(filename="overlay_frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.commit()

    document = TimelineDocument.model_validate(
        {
            "version": 1,
            "primary": [
                {
                    "id": "p-1",
                    "kind": "image",
                    "label": "Shot 1",
                    "source": {"kind": "shot_image", "ref_id": shot1.id},
                    "duration_ms": 600,
                    "source_in_ms": 0,
                    "source_out_ms": None,
                    "text": "",
                    "variant": {},
                }
            ],
            "video_overlays": [
                {
                    "id": "vo-1",
                    "label": "Shot 2 Overlay",
                    "source": {"kind": "shot_image", "ref_id": shot2.id},
                    "start_ms": 100,
                    "duration_ms": 400,
                    "source_in_ms": 0,
                    "opacity": 0.95,
                    "volume": 1.0,
                    "mute_audio": True,
                    "variant": {
                        "shorts": {
                            "position": "top",
                            "scale": 1.5,
                            "crop_top": 0.1,
                            "crop_bottom": 0.1,
                            "crop_left": 0.1,
                            "crop_right": 0.1,
                            "offset_x": 0.05,
                            "offset_y": -0.05,
                        }
                    },
                }
            ],
            "image_overlays": [],
            "text_overlays": [],
            "audio_overlays": [],
        }
    )
    rendered = assemble_timeline(project, document, "shorts", session)
    out_file = media_dir / rendered.filename
    assert out_file.is_file()
    assert (rendered.width, rendered.height) == (1080, 1920)
    assert 550 <= rendered.duration_ms <= 700
    session.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_timeline_renders_with_lut(tmp_path, monkeypatch) -> None:
    from backend.luts import list_lut_presets, build_lut_ffmpeg_filter

    presets = list_lut_presets()
    assert len(presets) >= 10
    preset_ids = [p["id"] for p in presets]
    assert "vibrant_gaming" in preset_ids
    assert "warzone_crisp" in preset_ids
    assert "teal_orange" in preset_ids
    assert "cyberpunk_neon" in preset_ids

    filter_str = build_lut_ffmpeg_filter("vibrant_gaming", 0.75)
    assert filter_str.startswith(",lut3d=file=")
    assert "vibrant_gaming_75.cube" in filter_str

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'render.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    frame = tmp_path / "media" / "frame.ppm"
    frame.parent.mkdir()
    frame.write_text("P3\n2 2\n255\n120 20 20  120 20 20\n120 20 20  120 20 20\n")
    project = Project(title="LUT render test", status="shot_plan", aspect_ratio="16:9")
    version = ScriptVersion(project=project, source_text="test")
    scene = Scene(script_version=version, order_index=0, title="INT. ROOM - DAY")
    shot = Shot(scene=scene, order_index=0, title="Still", duration_ms=500)
    shot.image = ShotImage(filename="frame.ppm", mime_type="image/x-portable-pixmap", source="uploaded", provider="test")
    session.add(project)
    session.commit()

    document = TimelineDocument.model_validate(
        {
            "version": 1,
            "primary": [
                {
                    "id": "still-1",
                    "kind": "image",
                    "label": "Still",
                    "source": {"kind": "shot_image", "ref_id": shot.id},
                    "duration_ms": 500,
                    "source_in_ms": 0,
                    "source_out_ms": None,
                    "text": "",
                    "variant": {
                        "landscape": {
                            "lut": "vibrant_gaming",
                            "lut_strength": 0.8,
                        }
                    },
                }
            ],
            "video_overlays": [],
            "image_overlays": [],
            "text_overlays": [],
            "audio_overlays": [],
        }
    )
    rendered = assemble_timeline(project, document, "landscape", session)
    out_file = tmp_path / "media" / rendered.filename
    assert out_file.is_file()
    session.close()

