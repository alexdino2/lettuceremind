"""Tests for the mobile pantry-scanner web app (`lettuceremind serve`)."""

import base64
import json
import threading
import urllib.error
import urllib.request

import pytest

from lettuceremind.receipt.matcher import FoodMatcher
from lettuceremind.web import recognize
from lettuceremind.web.recognize import recognize_labels
from lettuceremind.web.server import PantryScanApp, ApiError, create_server

MATCHER = FoodMatcher()

LABEL_TEXT = """\
Tillamook
CHEDDAR CHEESE
Sharp
NET WT 8 OZ (226g)
Nutrition Facts
Serving Size 1 oz
Calories 110
INGREDIENTS: MILK, SALT, CULTURES
KEEP REFRIGERATED
"""


# ---------------------------------------------------------------------------
# recognition
# ---------------------------------------------------------------------------

def test_recognizes_product_and_ignores_label_noise():
    matches = recognize_labels(LABEL_TEXT, matcher=MATCHER)
    names = [m.name for m in matches]
    assert "cheddar cheese" in names
    # The ingredient list must not leak "milk" into the pantry.
    assert "milk" not in names
    for m in matches:
        assert m.confidence >= recognize.MIN_CONFIDENCE


def test_pure_noise_yields_nothing():
    text = "NET WT 12 OZ\nNutrition Facts\nCalories 140\nBest if used by\n08/2026"
    assert recognize_labels(text, matcher=MATCHER) == []


def test_name_split_across_lines_is_joined():
    matches = recognize_labels("PEANUT\nBUTTER\ncreamy", matcher=MATCHER)
    assert any(m.name == "peanut butter" for m in matches)


def test_one_match_per_product_and_ranked_by_confidence():
    matches = recognize_labels("olive oil\nextra virgin olive oil", matcher=MATCHER)
    assert [m.name for m in matches] == ["olive oil"]
    assert matches[0].confidence == 1.0


# ---------------------------------------------------------------------------
# app logic (no HTTP)
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    return PantryScanApp(store_path=tmp_path / "pantry.json", dedupe_window=45.0)


def test_scan_text_adds_items(app):
    result = app.scan({"text": LABEL_TEXT})
    assert result["added"] == 1
    entry = next(e for e in result["recognized"] if e["name"] == "cheddar cheese")
    assert entry["status"] == "added"
    assert "expires_on" in entry
    assert result["pantry_count"] == 1
    assert app.pantry()["items"][0]["name"] == "cheddar cheese"


def test_scan_suppresses_duplicates_within_window(app):
    app.scan({"text": "CHEDDAR CHEESE"})
    result = app.scan({"text": "CHEDDAR CHEESE"})
    assert result["added"] == 0
    assert result["recognized"][0]["status"] == "duplicate"
    assert result["pantry_count"] == 1


def test_same_product_is_new_item_after_window(tmp_path):
    now = [0.0]
    app = PantryScanApp(store_path=tmp_path / "p.json", dedupe_window=45.0,
                        clock=lambda: now[0])
    app.scan({"text": "CHEDDAR CHEESE"})
    now[0] = 60.0
    result = app.scan({"text": "CHEDDAR CHEESE"})
    assert result["added"] == 1
    assert result["pantry_count"] == 2


def test_undo_forgets_dedupe_so_rescan_works(app):
    app.scan({"text": "CHEDDAR CHEESE"})
    assert app.remove({"name": "cheddar cheese"})["removed"] == 1
    result = app.scan({"text": "CHEDDAR CHEESE"})
    assert result["added"] == 1


def test_scan_requires_image_or_text(app):
    with pytest.raises(ApiError) as exc:
        app.scan({})
    assert exc.value.status == 400


def test_scan_rejects_bad_base64(app):
    with pytest.raises(ApiError) as exc:
        app.scan({"image": "not!!valid@@base64"})
    assert exc.value.status == 400


def test_scan_image_reports_missing_ocr(app, monkeypatch):
    def boom(data):
        raise RuntimeError("Image OCR needs the optional dependencies.")
    monkeypatch.setattr(recognize, "ocr_image_bytes", boom)
    with pytest.raises(ApiError) as exc:
        app.scan({"image": base64.b64encode(b"\xff\xd8fake").decode()})
    assert exc.value.status == 501


def test_scan_image_runs_ocr_pipeline(app, monkeypatch):
    monkeypatch.setattr(recognize, "ocr_image_bytes",
                        lambda data: "PEANUT BUTTER\nNET WT 16 OZ")
    result = app.scan({"image": base64.b64encode(b"\xff\xd8fake").decode()})
    assert result["added"] == 1
    assert result["recognized"][0]["name"] == "peanut butter"


def test_manual_add_and_remove(app):
    result = app.add({"name": "greek yogurt", "quantity": 2})
    assert result["added"]["name"] == "yogurt"
    assert result["added"]["quantity"] == 2
    assert app.remove({"name": "yogurt"})["removed"] == 1
    assert app.pantry()["count"] == 0


def test_manual_add_validates_input(app):
    with pytest.raises(ApiError):
        app.add({"name": "   "})
    with pytest.raises(ApiError):
        app.add({"name": "milk", "quantity": "lots"})


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    httpd, app = create_server("127.0.0.1", 0, store_path=tmp_path / "pantry.json",
                               api_key="sesame")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _request(url, body=None, key="sesame"):
    headers = {}
    if key is not None:
        headers["X-LettuceRemind-Key"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req) as res:
        return res.status, res.read(), res.headers.get("Content-Type", "")


def test_page_requires_key_and_serves_app(server):
    status, body, ctype = _request(f"{server}/?key=sesame", key=None)
    assert status == 200
    assert ctype.startswith("text/html")
    assert b"Pantry Scanner" in body

    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(f"{server}/", key=None)
    assert exc.value.code == 401


def test_api_end_to_end_over_http(server):
    status, body, _ = _request(f"{server}/api/scan", {"text": "CHEDDAR CHEESE"})
    assert status == 200
    assert json.loads(body)["added"] == 1

    status, body, _ = _request(f"{server}/api/add", {"name": "milk"})
    assert json.loads(body)["pantry_count"] == 2

    status, body, _ = _request(f"{server}/api/pantry")
    names = {i["name"] for i in json.loads(body)["items"]}
    assert names == {"cheddar cheese", "milk"}

    status, body, _ = _request(f"{server}/api/remove", {"name": "milk"})
    assert json.loads(body)["removed"] == 1


def test_api_rejects_wrong_key_and_bad_routes(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(f"{server}/api/pantry", key="wrong")
    assert exc.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(f"{server}/api/nope", {"x": 1})
    assert exc.value.code == 404

    with pytest.raises(urllib.error.HTTPError) as exc:
        req = urllib.request.Request(
            f"{server}/api/scan", data=b"not json",
            headers={"X-LettuceRemind-Key": "sesame"})
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_open_server_without_key(tmp_path):
    httpd, app = create_server("127.0.0.1", 0, store_path=tmp_path / "p.json",
                               api_key=None)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, body, _ = _request(f"{url}/api/pantry", key=None)
        assert json.loads(body)["count"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
