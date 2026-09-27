"""Tests for structured vision inventory extraction."""

from lettuceremind.web import vision


def test_analyze_inventory_image_builds_vision_request(monkeypatch):
    captured = {}

    def fake_request(url, payload, api_key):
        captured.update(url=url, payload=payload, api_key=api_key)
        return {
            "choices": [{
                "message": {
                    "content": '{"items":[{"name":"milk","quantity":1,'
                               '"confidence":0.98}]}'
                }
            }]
        }

    monkeypatch.setenv("LETTUCEREMIND_VISION_API_KEY", "test-key")
    monkeypatch.setenv("LETTUCEREMIND_VISION_MODEL", "test-vision-model")
    monkeypatch.setattr(vision, "_request_json", fake_request)

    result = vision.analyze_inventory_image(
        b"image", "image/jpeg", [{"name": "eggs", "quantity": 1}]
    )

    assert result == [{"name": "milk", "quantity": 1, "confidence": 0.98}]
    assert captured["api_key"] == "test-key"
    assert captured["payload"]["model"] == "test-vision-model"
    content = captured["payload"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert '"name":"eggs"' in content[0]["text"]


def test_analyze_inventory_image_requires_key(monkeypatch):
    monkeypatch.delenv("LETTUCEREMIND_VISION_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    try:
        vision.analyze_inventory_image(b"image", "image/jpeg", [])
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("missing API key should fail")


def test_json_object_accepts_fenced_json():
    assert vision._json_object('```json\n{"items":[]}\n```') == {"items": []}
