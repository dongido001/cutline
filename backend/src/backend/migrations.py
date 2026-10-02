import json

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


SHOT_COLUMNS = {
    "title": "VARCHAR(200) NOT NULL DEFAULT ''",
    "start_ms": "INTEGER",
    "end_ms": "INTEGER",
    "action": "TEXT NOT NULL DEFAULT ''",
    "dialogue": "JSON NOT NULL DEFAULT '[]'",
    "sound": "TEXT NOT NULL DEFAULT ''",
    "title_card_text": "TEXT NOT NULL DEFAULT ''",
    "character_ids": "JSON NOT NULL DEFAULT '[]'",
    "continuity_snapshot": "JSON",
    "selected_image_take_id": "VARCHAR",
    "selected_clip_take_id": "VARCHAR",
    "selected_audio_take_id": "VARCHAR",
    "dialogue_audio_filename": "VARCHAR(240)",
    "dialogue_audio_mime": "VARCHAR(80)",
}
SCENE_COLUMNS = {
    "set_notes": "TEXT NOT NULL DEFAULT ''",
    "lighting_notes": "TEXT NOT NULL DEFAULT ''",
    "wardrobe_notes": "TEXT NOT NULL DEFAULT ''",
    "props_notes": "TEXT NOT NULL DEFAULT ''",
}
TIMELINE_COLUMNS = {
    "shorts_document": "JSON",
}
LEGACY_SHOT_COLUMNS = {
    "title",
    "start_ms",
    "end_ms",
    "action",
    "dialogue",
    "sound",
}
JOB_COLUMNS = {
    "provider": "VARCHAR(40)",
    "provider_phase": "VARCHAR(24)",
    "conversation_url": "TEXT",
    "attempts": "JSON NOT NULL DEFAULT '[]'",
}
CHARACTER_COLUMNS = {
    "voice_provider": "VARCHAR(40) NOT NULL DEFAULT 'vibevoice'",
    "voice_id": "VARCHAR(120)",
    "voice_settings": "JSON NOT NULL DEFAULT '{}'",
}
PROJECT_COLUMNS = {
    "image_conversation_url": "TEXT",
}


def _ensure_columns(engine: Engine, table: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table)}
    missing = [name for name in columns if name not in existing]
    if not missing:
        return
    with engine.begin() as connection:
        for name in missing:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {columns[name]}"))



def _ensure_oauth_states(engine: Engine) -> None:
    if "oauth_states" in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE oauth_states (
                state VARCHAR(120) PRIMARY KEY,
                platform VARCHAR(32) NOT NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME
            )
        """))
        connection.execute(text("CREATE INDEX ix_oauth_states_platform ON oauth_states (platform)"))
        connection.execute(text("CREATE INDEX ix_oauth_states_expires_at ON oauth_states (expires_at)"))


def _ensure_publish_records(engine: Engine) -> None:
    if "publish_records" in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE publish_records (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                job_id VARCHAR,
                scope VARCHAR(16) NOT NULL,
                variant VARCHAR(16) NOT NULL DEFAULT 'landscape',
                platforms JSON NOT NULL DEFAULT '[]',
                title VARCHAR(180) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags JSON NOT NULL DEFAULT '[]',
                privacy JSON NOT NULL DEFAULT '{}',
                selected_asset_ids JSON NOT NULL DEFAULT '[]',
                scheduled_at VARCHAR(40),
                made_for_kids BOOLEAN NOT NULL DEFAULT 0,
                options JSON NOT NULL DEFAULT '{}',
                status VARCHAR(24) NOT NULL DEFAULT 'queued',
                results JSON NOT NULL DEFAULT '[]',
                created_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
            )
        """))
        connection.execute(text("CREATE INDEX ix_publish_records_project_id ON publish_records (project_id)"))
        connection.execute(text("CREATE INDEX ix_publish_records_job_id ON publish_records (job_id)"))


def migrate_schema(engine: Engine) -> None:
    """Apply the small additive migrations needed by the local prototype."""
    _ensure_columns(engine, "projects", PROJECT_COLUMNS)
    existing = {column["name"] for column in inspect(engine).get_columns("shots")}
    missing = [name for name in SHOT_COLUMNS if name not in existing]
    needs_legacy_backfill = any(name in LEGACY_SHOT_COLUMNS for name in missing)
    if missing:
        with engine.begin() as connection:
            for name in missing:
                connection.execute(
                    text(f"ALTER TABLE shots ADD COLUMN {name} {SHOT_COLUMNS[name]}")
                )

            if not needs_legacy_backfill:
                rows = []
            else:
                rows = connection.execute(
                    text(
                        """
                        SELECT shots.id, shots.scene_id, shots.order_index, shots.duration_ms,
                               scenes.title AS scene_title, scenes.start_ms AS scene_start,
                               scenes.action AS scene_action, scenes.dialogue AS scene_dialogue
                        FROM shots
                        JOIN scenes ON scenes.id = shots.scene_id
                        ORDER BY shots.scene_id, shots.order_index
                        """
                    )
                ).mappings()
            scene_offsets: dict[str, int] = {}
            for row in rows:
                scene_key = row["scene_id"]
                offset = scene_offsets.get(scene_key, 0)
                start_ms = (
                    row["scene_start"] + offset
                    if row["scene_start"] is not None
                    else None
                )
                end_ms = start_ms + row["duration_ms"] if start_ms is not None else None
                connection.execute(
                    text(
                        """
                        UPDATE shots
                        SET title = :title, start_ms = :start_ms, end_ms = :end_ms,
                            action = :action, dialogue = :dialogue
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": row["id"],
                        "title": f"{row['scene_title']} - Shot {row['order_index'] + 1}",
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "action": row["scene_action"] or "",
                        "dialogue": row["scene_dialogue"] or json.dumps([]),
                    },
                )
                scene_offsets[scene_key] = offset + row["duration_ms"]

    if "jobs" in inspect(engine).get_table_names():
        job_existing = {column["name"] for column in inspect(engine).get_columns("jobs")}
        with engine.begin() as connection:
            for name, definition in JOB_COLUMNS.items():
                if name not in job_existing:
                    connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {definition}"))

    if "scenes" in inspect(engine).get_table_names():
        scene_existing = {column["name"] for column in inspect(engine).get_columns("scenes")}
        with engine.begin() as connection:
            for name, definition in SCENE_COLUMNS.items():
                if name not in scene_existing:
                    connection.execute(text(f"ALTER TABLE scenes ADD COLUMN {name} {definition}"))

    if "project_timelines" in inspect(engine).get_table_names():
        timeline_existing = {
            column["name"] for column in inspect(engine).get_columns("project_timelines")
        }
        with engine.begin() as connection:
            for name, definition in TIMELINE_COLUMNS.items():
                if name not in timeline_existing:
                    connection.execute(
                        text(f"ALTER TABLE project_timelines ADD COLUMN {name} {definition}")
                    )

    if "characters" in inspect(engine).get_table_names():
        character_existing = {
            column["name"] for column in inspect(engine).get_columns("characters")
        }
        with engine.begin() as connection:
            for name, definition in CHARACTER_COLUMNS.items():
                if name not in character_existing:
                    connection.execute(
                        text(f"ALTER TABLE characters ADD COLUMN {name} {definition}")
                    )

    _ensure_publish_records(engine)
    _ensure_oauth_states(engine)
    _group_legacy_structured_shots(engine)


def _group_legacy_structured_shots(engine: Engine) -> None:
    if "script_versions" not in inspect(engine).get_table_names():
        return
    with engine.begin() as connection:
        versions = connection.execute(
            text(
                """
                SELECT id, source_text, structured_data
                FROM script_versions
                WHERE json_extract(structured_data, '$.parser') = 'structured-shot-v1'
                """
            )
        ).mappings()
        for version in versions:
            data = json.loads(version["structured_data"])
            old_scene_data = data.get("scenes", [])
            if not old_scene_data:
                data["parser"] = "structured-shot-v2"
                connection.execute(
                    text("UPDATE script_versions SET structured_data = :data WHERE id = :id"),
                    {"id": version["id"], "data": json.dumps(data)},
                )
                continue

            scene_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT id FROM scenes
                        WHERE script_version_id = :version_id
                        ORDER BY order_index
                        """
                    ),
                    {"version_id": version["id"]},
                ).mappings()
            )
            if not scene_rows:
                continue

            film_title = next(
                (
                    line.strip()
                    for line in version["source_text"].splitlines()
                    if line.strip()
                ),
                "MAIN SEQUENCE",
            )
            flat_shots = [
                shot
                for scene in old_scene_data
                for shot in scene.get("shots", [])
            ]
            for index, shot in enumerate(flat_shots):
                shot["order_index"] = index
            first_scene_data = old_scene_data[0]
            grouped_scene = {
                "title": film_title,
                "location": first_scene_data.get("location", "Unspecified"),
                "time_of_day": next(
                    (
                        scene.get("time_of_day")
                        for scene in old_scene_data
                        if scene.get("time_of_day") not in (None, "", "Unspecified")
                    ),
                    "Unspecified",
                ),
                "start_ms": first_scene_data.get("start_ms"),
                "end_ms": old_scene_data[-1].get("end_ms"),
                "action": "\n\n".join(
                    scene.get("action", "")
                    for scene in old_scene_data
                    if scene.get("action")
                ),
                "dialogue": [
                    line
                    for scene in old_scene_data
                    for line in scene.get("dialogue", [])
                ],
                "shots": flat_shots,
            }

            def remap_target(target: object) -> None:
                if not isinstance(target, dict):
                    return
                old_index = target.get("scene_index")
                if not isinstance(old_index, int):
                    return
                target["scene_index"] = 0
                target["shot_index"] = old_index

            for suggestion in data.get("suggestions", []):
                if isinstance(suggestion, dict):
                    remap_target(suggestion.get("target"))
            data["scenes"] = [grouped_scene]
            data["parser"] = "structured-shot-v2"

            retained_scene_id = scene_rows[0]["id"]
            shot_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT shots.id
                        FROM shots JOIN scenes ON scenes.id = shots.scene_id
                        WHERE scenes.script_version_id = :version_id
                        ORDER BY scenes.order_index, shots.order_index
                        """
                    ),
                    {"version_id": version["id"]},
                ).mappings()
            )
            for index, shot_row in enumerate(shot_rows):
                connection.execute(
                    text(
                        "UPDATE shots SET scene_id = :scene_id, order_index = :order_index WHERE id = :id"
                    ),
                    {
                        "scene_id": retained_scene_id,
                        "order_index": index,
                        "id": shot_row["id"],
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE scenes
                    SET title = :title, location = :location, time_of_day = :time_of_day,
                        start_ms = :start_ms, end_ms = :end_ms, action = :action,
                        dialogue = :dialogue, order_index = 0
                    WHERE id = :id
                    """
                ),
                {
                    "id": retained_scene_id,
                    "title": grouped_scene["title"],
                    "location": grouped_scene["location"],
                    "time_of_day": grouped_scene["time_of_day"],
                    "start_ms": grouped_scene["start_ms"],
                    "end_ms": grouped_scene["end_ms"],
                    "action": grouped_scene["action"],
                    "dialogue": json.dumps(grouped_scene["dialogue"]),
                },
            )
            for scene_row in scene_rows[1:]:
                connection.execute(
                    text("DELETE FROM scenes WHERE id = :id"),
                    {"id": scene_row["id"]},
                )

            suggestion_rows = connection.execute(
                text(
                    "SELECT id, target FROM suggestions WHERE script_version_id = :version_id"
                ),
                {"version_id": version["id"]},
            ).mappings()
            for suggestion_row in suggestion_rows:
                target = json.loads(suggestion_row["target"])
                remap_target(target)
                connection.execute(
                    text("UPDATE suggestions SET target = :target WHERE id = :id"),
                    {"id": suggestion_row["id"], "target": json.dumps(target)},
                )
            connection.execute(
                text("UPDATE script_versions SET structured_data = :data WHERE id = :id"),
                {"id": version["id"], "data": json.dumps(data)},
            )
