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


def test_inventory_photo_is_reviewed_before_items_are_added(tmp_path):
    seen = {}

    def analyze(image, media_type, current):
        seen.update(image=image, media_type=media_type, current=current)
        return [
            {"name": "whole milk", "quantity": 1, "confidence": 0.96},
            {"name": "greek yogurt", "quantity": 2, "confidence": 0.91},
        ]

    photo_app = PantryScanApp(
        store_path=tmp_path / "photo-pantry.json",
        vision_analyzer=analyze,
    )
    photo_app.add({"name": "milk", "quantity": 1})
    encoded = base64.b64encode(b"fake-jpeg").decode()
    result = photo_app.analyze_inventory_photo({
        "image": encoded,
        "media_type": "image/jpeg",
    })

    assert seen["image"] == b"fake-jpeg"
    assert seen["current"] == [{"name": "milk", "quantity": 1}]
    assert photo_app.pantry()["count"] == 1  # analysis never writes
    milk = next(item for item in result["items"] if item["name"] == "milk")
    yogurt = next(item for item in result["items"] if item["name"] == "yogurt")
    assert milk["already_in_inventory"] is True
    assert milk["existing_quantity"] == 1
    assert milk["selected"] is False
    assert yogurt["selected"] is True
    assert yogurt["quantity"] == 2


def test_confirm_inventory_photo_uses_manual_expiration_defaults(app):
    from datetime import date, timedelta

    result = app.confirm_inventory_photo({"items": [
        {"name": "greek yogurt", "quantity": 2, "selected": True},
        {"name": "milk", "quantity": 1, "selected": False},
    ]})

    assert result["added_count"] == 1
    assert result["added"][0]["name"] == "yogurt"
    assert result["added"][0]["quantity"] == 2
    assert result["added"][0]["expires_on"] == (
        date.today() + timedelta(days=14)
    ).isoformat()
    assert app.pantry()["count"] == 1


def test_inventory_photo_validates_input(app):
    with pytest.raises(ApiError) as exc:
        app.analyze_inventory_photo({"image": "bad!!"})
    assert exc.value.status == 400
    with pytest.raises(ApiError) as exc:
        app.confirm_inventory_photo({"items": [
            {"name": "milk", "quantity": 0, "selected": True},
        ]})
    assert exc.value.status == 400


def test_pantry_includes_expiring_soon(app):
    from datetime import date, timedelta
    from lettuceremind.models import PantryItem
    from lettuceremind.store import PantryStore

    today = date.today()
    store = PantryStore(app.store_path)
    store.add_all([
        PantryItem("milk", "milk & cream", 1, today, today + timedelta(days=1)),
        PantryItem("rice", "grains", 1, today, today + timedelta(days=30)),
        PantryItem("spinach", "leafy greens", 1, today, today - timedelta(days=1)),
    ])

    result = app.pantry()
    assert result["count"] == 3
    assert result["expiring_soon_days"] == 3
    assert [i["name"] for i in result["expiring_soon"]] == ["spinach", "milk"]


def test_remove_expiring_and_update_expiration(app):
    from datetime import date, timedelta
    from lettuceremind.models import PantryItem
    from lettuceremind.store import PantryStore

    today = date.today()
    store = PantryStore(app.store_path)
    store.add_all([
        PantryItem("milk", "milk & cream", 1, today, today + timedelta(days=1)),
        PantryItem("rice", "grains", 1, today, today + timedelta(days=30)),
    ])
    milk_exp = (today + timedelta(days=1)).isoformat()
    updated = app.update_expiration({
        "name": "milk",
        "expires_on": milk_exp,
        "new_expires_on": (today + timedelta(days=14)).isoformat(),
    })
    assert updated["updated"] == 1
    assert updated["expiring_soon"] == []
    assert updated["count"] == 2

    # Put milk back in the expiring window, then clear it.
    app.update_expiration({
        "name": "milk",
        "expires_on": (today + timedelta(days=14)).isoformat(),
        "new_expires_on": milk_exp,
    })
    cleared = app.remove_expiring({})
    assert cleared["removed"] == 1
    assert cleared["pantry_count"] == 1
    assert app.pantry()["items"][0]["name"] == "rice"


def test_manual_add_validates_input(app):
    with pytest.raises(ApiError):
        app.add({"name": "   "})
    with pytest.raises(ApiError):
        app.add({"name": "milk", "quantity": "lots"})


def test_remove_expiring_validates_days(app):
    with pytest.raises(ApiError) as exc:
        app.remove_expiring({"days": "soon"})
    assert exc.value.status == 400


def test_update_expiration_not_found(app):
    with pytest.raises(ApiError) as exc:
        app.update_expiration({
            "name": "ghost",
            "expires_on": "2026-01-01",
            "new_expires_on": "2026-02-01",
        })
    assert exc.value.status == 404


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
    assert b"id=\"dealsBtn\"" in body
    assert b"id=\"dealsPanel\"" in body
    assert b"id=\"inventoryPhotoBtn\"" in body
    assert b"id=\"photoReviewOverlay\"" in body

    # Deep links into the SPA must serve the same app (not JSON 404).
    for path in ("/deals", "/pantry"):
        status, body, ctype = _request(f"{server}{path}?key=sesame", key=None)
        assert status == 200, path
        assert ctype.startswith("text/html"), path
        assert b"id=\"dealsPanel\"" in body, path

    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(f"{server}/", key=None)
    assert exc.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(f"{server}/deals", key=None)
    assert exc.value.code == 401


def test_page_includes_expiring_soon_controls(server):
    status, body, ctype = _request(f"{server}/?key=sesame", key=None)
    assert status == 200
    assert b"Expiring soon" in body
    assert b"deleteExpiringBtn" in body
    assert b"changeDatesLink" in body
    assert b"This will delete" in body


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


def test_api_deals_returns_builtin_catalog(server, monkeypatch, tmp_path):
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_DEALS", raising=False)
    status, body, _ = _request(f"{server}/api/deals")
    assert status == 200
    data = json.loads(body)
    assert data["count"] > 0
    assert {d["store"] for d in data["deals"]} >= {"publix", "kroger", "aldi"}
    assert {"key": "aldi", "name": "Aldi"} in data["stores"]
    assert data["default_zip"] == "32081"
    assert data["publix_is_sample"] is True
    assert data["publix_live"] is False
    assert all("price" in d and "valid_to" in d and "source" in d for d in data["deals"])


def test_api_deals_refresh_publix(server, monkeypatch, tmp_path):
    from unittest.mock import patch

    from tests.test_publix import _fake_get_json

    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_DEALS", raising=False)
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        status, body, _ = _request(
            f"{server}/api/deals/refresh", {"zip": "32081"}
        )
    assert status == 200
    data = json.loads(body)
    assert data["feed"]["zip"] == "32081"
    assert data["feed"]["store_number"] == "01243"
    assert data["publix_live"] is True
    assert data["publix_is_sample"] is False
    chicken = next(d for d in data["deals"] if d["item"] == "chicken breast")
    assert chicken["source"] == "publix"
    assert "Fillets" in chicken["description"]
    assert "Save up to $8.50" in chicken["price"]
    assert (tmp_path / "deals.json").exists()

    # After refresh, GET serves the live feed (not just builtins).
    status, body, _ = _request(f"{server}/api/deals?store=publix")
    live = json.loads(body)
    assert live["publix_live"] is True
    assert any(d["item"] == "chicken breast" and d["source"] == "publix"
               for d in live["deals"])

    # ?live=1 refreshes again via GET.
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        status, body, _ = _request(f"{server}/api/deals?live=1&zip=32081")
    assert status == 200
    assert json.loads(body)["publix_live"] is True


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
