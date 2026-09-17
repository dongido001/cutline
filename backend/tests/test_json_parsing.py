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
