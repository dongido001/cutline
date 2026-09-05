from backend.providers import MockScriptProvider


def test_mock_provider_extracts_scene_and_suggests_timing() -> None:
    result = MockScriptProvider().analyze(
        """INT. KITCHEN - NIGHT
MARA
We should leave now.
She turns off the light.""",
        30,
    )

    assert result["scenes"][0]["location"] == "INT. KITCHEN"
    assert result["scenes"][0]["dialogue"][0]["character"] == "MARA"
    assert result["suggestions"][0]["category"] == "timing"
    assert result["scenes"][0]["shots"]

