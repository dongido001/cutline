from sqlalchemy import create_engine, text

from backend.migrations import migrate_schema


def test_adds_and_backfills_shot_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE scenes (id TEXT PRIMARY KEY, title TEXT, start_ms INTEGER, action TEXT, dialogue JSON)"))
        connection.execute(text("CREATE TABLE shots (id TEXT PRIMARY KEY, scene_id TEXT, order_index INTEGER, duration_ms INTEGER)"))
        connection.execute(text("INSERT INTO scenes VALUES ('scene-1', 'The Door', 1000, 'She reaches.', '[]')"))
        connection.execute(text("INSERT INTO shots VALUES ('shot-1', 'scene-1', 0, 4000)"))
        connection.execute(text("INSERT INTO shots VALUES ('shot-2', 'scene-1', 1, 5000)"))

    migrate_schema(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT title, start_ms, end_ms, action, sound FROM shots ORDER BY order_index")
        ).all()
    assert rows == [
        ("The Door - Shot 1", 1000, 5000, "She reaches.", ""),
        ("The Door - Shot 2", 5000, 10000, "She reaches.", ""),
    ]


def test_additive_shot_column_does_not_rewrite_existing_metadata(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'additive.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE shots (id TEXT PRIMARY KEY, scene_id TEXT, order_index INTEGER, "
                "duration_ms INTEGER, title TEXT, start_ms INTEGER, end_ms INTEGER, action TEXT, "
                "dialogue JSON, sound TEXT, framing TEXT, image_prompt TEXT, video_prompt TEXT, "
                "status TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO shots VALUES ('shot-1', 'scene-1', 0, 4000, 'Original title', "
                "2000, 6000, 'Original action', '[]', 'Rain', 'Wide', '', '', 'planned')"
            )
        )

    migrate_schema(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT title, start_ms, end_ms, action, sound, title_card_text FROM shots"
            )
        ).one()
    assert row == ("Original title", 2000, 6000, "Original action", "Rain", "")
