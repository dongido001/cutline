from backend.providers import loads_model_json


def test_loads_model_json_strips_fences_and_prose() -> None:
    content = """Here you go:
```json
{"scenes": [], "suggestions": []}
```
Thanks!
"""
    assert loads_model_json(content) == {"scenes": [], "suggestions": []}


def test_loads_model_json_direct_object() -> None:
    assert loads_model_json('{"suggestions": []}') == {"suggestions": []}


def test_loads_model_json_repairs_markdown_stripped_quotes_and_keeps_root_report() -> None:
    content = """ChatGPT said:
Thought for 1m 24s
{
"assessment": "The episode is structurally strong.",
"strengths": [
"Clear setup."
],
"priorities": [
"Fix dialogue."
],
"findings": [
{
"kind": "confirmed_inconsistency",
"category": "dialogue",
"title": "Missing spoken answers",
"reason": "Shot 4 video prompt includes "RED BALL!" spoken by Skylar.",
"recommendation": "Add Skylar line to dialogue.",
"evidence": [
{
"version_id": "v1",
"shot_id": "s4",
"field": "video_prompt",
"quote": "Skylar points clearly and says, "RED BALL!" The ball gently rolls."
},
{
"version_id": "v1",
"shot_id": "s1",
"field": "source_text",
"quote": "SKYLAR:\n"Jasmine... where did the colors go?""
}
],
"patches": []
}
]
}"""
    parsed = loads_model_json(content)
    assert parsed["assessment"] == "The episode is structurally strong."
    assert len(parsed["findings"]) == 1
    assert (
        parsed["findings"][0]["evidence"][0]["quote"]
        == 'Skylar points clearly and says, "RED BALL!" The ball gently rolls.'
    )
    assert (
        parsed["findings"][0]["evidence"][1]["quote"]
        == 'SKYLAR:\n"Jasmine... where did the colors go?"'
    )

