from backend.video import find_video_output, frame_count, render_workflow


def test_frame_count_matches_video_model_constraint() -> None:
    count = frame_count(6000, 16)

    assert count <= 96
    assert count % 4 == 1


def test_render_workflow_replaces_nested_values_and_keeps_numeric_types() -> None:
    workflow = {
        "1": {
            "inputs": {
                "image": "{{INPUT_IMAGE}}",
                "prompt": "Move: {{PROMPT}}",
                "width": "{{WIDTH}}",
                "frames": "{{FRAME_COUNT}}",
            }
        }
    }

    rendered = render_workflow(
        workflow,
        {
            "{{INPUT_IMAGE}}": "input/frame.png",
            "{{PROMPT}}": "slow push in",
            "{{WIDTH}}": 832,
            "{{FRAME_COUNT}}": 93,
        },
    )

    assert rendered["1"]["inputs"] == {
        "image": "input/frame.png",
        "prompt": "Move: slow push in",
        "width": 832,
        "frames": 93,
    }


def test_find_video_output_ignores_preview_images() -> None:
    history = {
        "outputs": {
            "preview": {"images": [{"filename": "preview.png"}]},
            "save": {
                "gifs": [
                    {
                        "filename": "shot-01.mp4",
                        "subfolder": "cutline",
                        "type": "output",
                    }
                ]
            },
        }
    }

    assert find_video_output(history) == {
        "filename": "shot-01.mp4",
        "subfolder": "cutline",
        "type": "output",
    }
