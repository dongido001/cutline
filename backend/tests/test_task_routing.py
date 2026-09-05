from types import SimpleNamespace

from backend import tasks


def _fake_task(name: str, calls: list[tuple[str, tuple[str, ...]]]):
    def enqueue(*args: str):
        calls.append((name, args))
        return SimpleNamespace(id=f"{name}-task")

    return enqueue


def test_routes_text_analysis_by_provider(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(tasks, "analyze_script_chatgpt", _fake_task("chatgpt", calls))
    monkeypatch.setattr(tasks, "analyze_script_gemini", _fake_task("gemini", calls))
    monkeypatch.setattr(tasks, "analyze_script_general", _fake_task("general", calls))

    tasks.enqueue_script_analysis("chatgpt_web", "p1", "v1", "j1")
    tasks.enqueue_script_analysis("gemini_web", "p2", "v2", "j2")
    tasks.enqueue_script_analysis("ollama", "p3", "v3", "j3")
    tasks.enqueue_script_analysis("mock", "p4", "v4", "j4")

    assert [name for name, _ in calls] == ["chatgpt", "gemini", "general", "general"]


def test_routes_images_to_the_dedicated_provider_lane(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(tasks, "generate_shot_image_worker", _fake_task("image", calls))

    tasks.enqueue_shot_image("s1", "j1")

    assert [name for name, _ in calls] == ["image"]


def test_video_prompt_includes_separate_sound_direction() -> None:
    assert tasks.compose_video_prompt(
        "Animate the frame.", "Clock ticking and quiet piano."
    ) == (
        "Animate the frame.\n\nAudio direction: Clock ticking and quiet piano."
    )
