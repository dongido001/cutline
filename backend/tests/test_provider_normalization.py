from backend.providers import normalize_analysis_result


def test_normalizes_loose_model_output() -> None:
    result = normalize_analysis_result(
        {
            "scenes": [
                {
                    "title": "Kitchen",
                    "start_ms": "0",
                    "end_ms": "10000",
                    "action": "Mara listens.",
                    "dialogue": "MARA: Someone is outside.",
                    "shots": [{"duration_ms": 12000}],
                }
            ],
            "suggestions": [
                {
                    "severity": "medium",
                    "target": "Kitchen scene",
                    "title": "Add sound",
                    "reason": "The source is unclear.",
                }
            ],
        },
        10,
    )

    assert result["scenes"][0]["dialogue"] == [
        {"character": "MARA", "text": "Someone is outside."}
    ]
    assert result["scenes"][0]["shots"][0]["duration_ms"] == 10000
    assert result["suggestions"][0]["severity"] == "warning"
    assert result["suggestions"][0]["target"] == {"description": "Kitchen scene"}
