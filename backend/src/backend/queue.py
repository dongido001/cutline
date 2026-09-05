from huey import SqliteHuey

from .settings import settings


general_huey = SqliteHuey(
    "movie-creator-general",
    filename=str(settings.queue_path),
    results=True,
    store_none=False,
    immediate=False,
)

chatgpt_huey = SqliteHuey(
    "movie-creator-chatgpt",
    filename=str(settings.chatgpt_queue_path),
    results=True,
    store_none=False,
    immediate=False,
)

gemini_huey = SqliteHuey(
    "movie-creator-gemini",
    filename=str(settings.gemini_queue_path),
    results=True,
    store_none=False,
    immediate=False,
)

video_huey = SqliteHuey(
    "movie-creator-video",
    filename=str(settings.video_queue_path),
    results=True,
    store_none=False,
    immediate=False,
)

image_huey = SqliteHuey(
    "movie-creator-image",
    filename=str(settings.image_queue_path),
    results=True,
    store_none=False,
    immediate=False,
)
