"""Bundled planning demo; media is distributed separately."""
import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from .cast import sync_project_cast
from .models import Character, CharacterReference, Episode, Project, Scene, ScriptVersion, Shot, ShotImage, ShotClip
from .settings import settings


def _media(record: dict, copied: list[Path]) -> dict | None:
    source = settings.data_dir / "demo-media" / record["filename"]
    if not source.is_file():
        return None
    filename = f"demo-{uuid.uuid4()}{source.suffix}"
    destination = settings.media_dir / filename
    copied.append(destination)
    shutil.copyfile(source, destination)
    return {"filename": filename, "mime_type": record["mime_type"]}


def load_demo_project(db: Session) -> Project:
    copied: list[Path] = []
    try:
        demo = json.loads(Path(__file__).with_name("demo.json").read_text())
        project = Project(**{key: demo[key] for key in
                            ("title", "aspect_ratio", "target_duration_seconds", "episode_label")},
                          status="shot_plan", text_provider="mock")
        db.add(project)
        db.flush()
        episode = Episode(project_id=project.id, episode_number=1,
                          title=project.title, status="shot_plan")
        db.add(episode)
        db.flush()
        version = ScriptVersion(project_id=project.id, episode_id=episode.id,
                                source_text=demo["script"], status="approved")
        db.add(version)
        db.flush()
        for data in demo["scenes"]:
            scene = Scene(script_version_id=version.id,
                          **{key: value for key, value in data.items() if key != "shots"})
            db.add(scene)
            db.flush()
            for shot in data["shots"]:
                row = Shot(scene_id=scene.id, **{k: v for k, v in shot.items() if k not in ("image", "clip")})
                db.add(row)
                db.flush()
                for key, model in (("image", ShotImage), ("clip", ShotClip)):
                    media = _media(shot[key], copied) if key in shot else None
                    if media:
                        db.add(model(shot_id=row.id, source="uploaded", provider="demo", **media))
        for character in demo["characters"]:
            row = Character(project_id=project.id, name=character["name"], notes=character["notes"])
            db.add(row)
            db.flush()
            for reference in character.get("references", []):
                media = _media(reference, copied)
                if media:
                    db.add(CharacterReference(character_id=row.id, **media))
        db.flush()
        sync_project_cast(project, db, source_text=version.source_text, tag_shots=True)
        db.commit()
        db.refresh(project)
        return project
    except Exception:
        db.rollback()
        for path in copied:
            path.unlink(missing_ok=True)
        raise
