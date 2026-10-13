from huey import RedisHuey, SqliteHuey

from backend import queue as queue_module
from backend.settings import Settings


def test_default_database_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIE_DATABASE_DSN", "")
    monkeypatch.setenv("MOVIE_QUEUE_BACKEND", "sqlite")
    cfg = Settings(_env_file=None, data_dir=tmp_path, database_dsn="", queue_backend="sqlite")
    assert cfg.uses_sqlite
    assert cfg.database_url.startswith("sqlite:")
    assert not cfg.uses_redis_queue


def test_postgres_dsn_override(tmp_path):
    dsn = "postgresql+psycopg://cutline:cutline@127.0.0.1:5432/cutline"
    cfg = Settings(_env_file=None, data_dir=tmp_path, database_dsn=dsn, queue_backend="sqlite")
    assert cfg.database_url == dsn
    assert not cfg.uses_sqlite


def test_queue_factory_selects_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(queue_module.settings, "queue_backend", "sqlite")
    monkeypatch.setattr(queue_module.settings, "redis_url", "redis://127.0.0.1:6379/0")
    sqlite_huey = queue_module._make_huey("test-sqlite", str(tmp_path / "q.db"))
    assert isinstance(sqlite_huey, SqliteHuey)

    monkeypatch.setattr(queue_module.settings, "queue_backend", "redis")
    redis_huey = queue_module._make_huey("test-redis", str(tmp_path / "q.db"))
    assert isinstance(redis_huey, RedisHuey)
