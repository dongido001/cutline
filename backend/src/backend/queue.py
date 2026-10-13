from __future__ import annotations

from huey import RedisHuey, SqliteHuey

from .settings import settings


def _make_huey(name: str, sqlite_filename: str):
    if settings.uses_redis_queue:
        return RedisHuey(
            name,
            url=settings.redis_url,
            results=True,
            store_none=False,
            immediate=False,
        )
    return SqliteHuey(
        name,
        filename=sqlite_filename,
        results=True,
        store_none=False,
        immediate=False,
    )


general_huey = _make_huey("movie-creator-general", str(settings.queue_path))
chatgpt_huey = _make_huey("movie-creator-chatgpt", str(settings.chatgpt_queue_path))
gemini_huey = _make_huey("movie-creator-gemini", str(settings.gemini_queue_path))
video_huey = _make_huey("movie-creator-video", str(settings.video_queue_path))
image_huey = _make_huey("movie-creator-image", str(settings.image_queue_path))
