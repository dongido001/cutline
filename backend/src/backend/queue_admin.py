"""Job kinds owned by each Huey lane."""

QUEUE_JOB_KINDS: dict[str, list[str]] = {
    "image": ["shot_image", "cast_reference"],
    "video": ["shot_clip", "shot_lipsync", "final_cut", "create_draft", "publish"],
    "chatgpt": ["script_analysis", "script_continuation", "deep_review", "deep_review_draft"],
    "gemini": ["script_analysis", "script_continuation", "deep_review", "deep_review_draft"],
    "general": ["script_analysis", "script_continuation", "deep_review", "deep_review_draft", "analyze_script", "generate_shot_image"],
}


def job_matches_queue(kind: str, queue_name: str) -> bool:
    kinds = QUEUE_JOB_KINDS.get(queue_name, [])
    return any(kind == item or kind.startswith(f"{item}:") for item in kinds)
