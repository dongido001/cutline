import subprocess
import sys
import time

from .settings import settings


def run_api() -> None:
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", "8000"],
        check=True,
    )


def run_worker() -> None:
    queues = (
        "backend.tasks.chatgpt_huey",
        "backend.tasks.gemini_huey",
        "backend.tasks.general_huey",
        "backend.tasks.video_huey",
        "backend.tasks.image_huey",
    )
    consumers = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "huey.bin.huey_consumer",
                queue,
                "-k",
                "thread",
                "-w",
                str(settings.video_worker_concurrency) if queue == "backend.tasks.video_huey" else "1",
            ]
        )
        for queue in queues
    ]
    try:
        while True:
            stopped = next(
                (consumer for consumer in consumers if consumer.poll() is not None),
                None,
            )
            if stopped:
                if stopped.returncode:
                    raise subprocess.CalledProcessError(
                        stopped.returncode, stopped.args
                    )
                return
            time.sleep(0.25)
    finally:
        for consumer in consumers:
            if consumer.poll() is None:
                consumer.terminate()
        for consumer in consumers:
            consumer.wait()
