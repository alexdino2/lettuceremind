"""Tests for connecting accounts to Instacart (Developer Platform API)."""

from __future__ import annotations

import io
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from lettuceremind import auth, instacart
from lettuceremind.cli import main
from lettuceremind.models import PantryItem
from lettuceremind.store import PantryStore
from lettuceremind.web.server import ApiError, PantryScanApp, create_server

PW = "correct-horse-battery"
KEY = "idp-test-key-abcd1234"
LIST_URL = "https://customers.instacart.com/store/shopping_lists/123"

_REAL_URLOPEN = urllib.request.urlopen

RETAILERS = {"retailers": [
    {"retailer_key": "publix", "name": "Publix", "retailer_logo_url": "https://x/p.png"},
    {"retailer_key": "costco", "name": "Costco"},
    {"retailer_key": "sprouts", "name": "Sprouts Farmers Market"},
]}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_STORE", raising=False)
    monkeypatch.delenv("INSTACART_API_KEY", raising=False)
    return tmp_path


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeInstacart:
    """Stands in for urlopen; records every request it receives."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests = []

    def __call__(self, req, timeout=None):
        if not req.full_url.startswith(tuple(instacart.BASE_URLS.values())):
            return _REAL_URLOPEN(req, timeout=timeout)  # e.g. the local test server
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.requests.append({
            "method": req.get_method(),
            "url": req.full_url,
            "auth": req.get_header("Authorization"),
            "body": body,
        })
        if self.status != 200:
            raise urllib.error.HTTPError(
                req.full_url, self.status, "error", {}, io.BytesIO(b'{"error":"no"}'))
        if "/retailers" in req.full_url:
            return FakeResponse(json.dumps(RETAILERS).encode())
        if req.full_url.endswith("/products/products_link"):
            return FakeResponse(json.dumps({"products_link_url": LIST_URL}).encode())
        raise AssertionError(f"unexpected request {req.full_url}")


@pytest.fixture
def fake():
    fake = FakeInstacart()
    with patch("lettuceremind.instacart.urllib.request.urlopen", fake):
        yield fake


@pytest.fixture
def alice():
    auth.register("alice", PW)
    return "alice"


def _stock(*items: tuple[str, int]) -> None:
    today = date.today()
    PantryStore().add_all([
        PantryItem(name=name, category="test", quantity=1,
                   added_on=today, expires_on=today + timedelta(days=days))
        for name, days in items
    ])


# ---------------------------------------------------------------------------
# connecting accounts
# ---------------------------------------------------------------------------

def test_connect_requires_login(fake):
    with pytest.raises(instacart.InstacartError, match="log in first"):
        instacart.connect(KEY)
    assert fake.requests == []


def test_connect_verifies_key_and_stores_it_privately(fake, alice, isolated_home):
    conn = instacart.connect(KEY, postal_code="32081", retailer="publix")
    assert conn.retailer_key == "publix" and conn.retailer_name == "Publix"

    req = fake.requests[0]
    assert req["url"] == ("https://connect.instacart.com/idp/v1/retailers"
                          "?postal_code=32081&country_code=US")
    assert req["auth"] == f"Bearer {KEY}"

    path = isolated_home / "users" / "alice" / "instacart.json"
    assert path == instacart.connection_path("alice")
    assert json.loads(path.read_text())["api_key"] == KEY
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    assert instacart.get_connection() == conn


def test_connect_rejected_key_saves_nothing(alice):
    fake = FakeInstacart(status=401)
    with patch("lettuceremind.instacart.urllib.request.urlopen", fake):
        with pytest.raises(instacart.InstacartError, match="rejected the API key"):
            instacart.connect(KEY)
    assert instacart.get_connection() is None


def test_connect_development_key_uses_dev_host(fake, alice):
    instacart.connect(KEY, environment=instacart.DEVELOPMENT)
    assert fake.requests[0]["url"].startswith("https://connect.dev.instacart.tools/")
    assert instacart.get_connection().environment == instacart.DEVELOPMENT


def test_connect_retailer_by_partial_name(fake, alice):
    conn = instacart.connect(KEY, postal_code="32081", retailer="sprouts farmers")
    assert conn.retailer_key == "sprouts"


def test_connect_unknown_retailer_fails(fake, alice):
    with pytest.raises(instacart.InstacartError, match="no Instacart store"):
        instacart.connect(KEY, postal_code="32081", retailer="kroger")
    assert instacart.get_connection() is None


def test_connect_retailer_needs_zip(fake, alice):
    with pytest.raises(instacart.InstacartError, match="ZIP"):
        instacart.connect(KEY, retailer="publix")


def test_connect_no_verify_makes_no_request(fake, alice):
    instacart.connect(KEY, verify=False)
    assert fake.requests == []
    assert instacart.get_connection().api_key == KEY


def test_connections_are_per_account(fake, alice):
    instacart.connect(KEY)
    auth.logout()
    auth.register("bob", PW)
    assert instacart.get_connection() is None
    assert instacart.get_connection("alice").api_key == KEY


def test_disconnect_deletes_the_key(fake, alice):
    instacart.connect(KEY)
    assert instacart.disconnect() is True
    assert instacart.get_connection() is None
    assert not instacart.connection_path("alice").exists()
    assert instacart.disconnect() is False


def test_public_dict_never_exposes_the_key(fake, alice):
    conn = instacart.connect(KEY)
    public = conn.public_dict()
    assert KEY not in json.dumps(public)
    assert public["masked_key"] == "…1234"


# ---------------------------------------------------------------------------
# shopping lists
# ---------------------------------------------------------------------------

def test_restock_items_merges_and_limits_to_expiring():
    today = date.today()
    items = [
        PantryItem("milk", "dairy", 1, today, today + timedelta(days=1)),
        PantryItem("Milk", "dairy", 2, today, today + timedelta(days=2)),
        PantryItem("rice", "grains", 1, today, today + timedelta(days=90)),
        PantryItem("spinach", "greens", 1, today, today - timedelta(days=1)),
    ]
    lines = instacart.restock_items(items, within_days=3)
    assert [(li["name"], li["quantity"]) for li in lines] == [
        ("spinach", 1), ("milk", 3)]
    assert lines[1]["unit"] == "each"


def test_create_shopping_list_posts_line_items_and_prefers_store(fake, alice):
    conn = instacart.connect(KEY, postal_code="32081", retailer="costco")
    url = instacart.create_shopping_list(
        conn, instacart.line_items([("eggs", 2)]), title="Restock")
    assert url == LIST_URL + "?retailer_key=costco"
    req = fake.requests[-1]
    assert req["method"] == "POST"
    assert req["url"] == "https://connect.instacart.com/idp/v1/products/products_link"
    assert req["body"]["link_type"] == "shopping_list"
    assert req["body"]["title"] == "Restock"
    assert req["body"]["line_items"] == [
        {"name": "eggs", "display_text": "Eggs", "quantity": 2, "unit": "each"}]


def test_create_shopping_list_rejects_empty(fake, alice):
    conn = instacart.connect(KEY)
    with pytest.raises(instacart.InstacartError, match="nothing"):
        instacart.create_shopping_list(conn, [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_connect_status_disconnect(fake, capsys):
    main(["register", "alice", "--password", PW])
    assert main(["instacart", "connect", "--api-key", KEY, "--zip", "32081",
                 "--retailer", "Publix"]) == 0
    out = capsys.readouterr().out
    assert "Connected alice" in out and "Publix" in out
    assert KEY not in out

    assert main(["instacart", "status"]) == 0
    out = capsys.readouterr().out
    assert "connected" in out and "…1234" in out and KEY not in out

    assert main(["instacart", "disconnect"]) == 0
    main(["instacart", "status"])
    assert "isn't connected" in capsys.readouterr().out


def test_cli_connect_reads_key_from_env(fake, monkeypatch, capsys):
    main(["register", "alice", "--password", PW])
    monkeypatch.setenv("INSTACART_API_KEY", KEY)
    assert main(["instacart", "connect"]) == 0
    assert instacart.get_connection().api_key == KEY


def test_cli_connect_logged_out_fails(fake, capsys):
    assert main(["instacart", "connect", "--api-key", KEY]) == 1
    assert "log in first" in capsys.readouterr().err


def test_cli_shop_restocks_expiring_items(fake, capsys):
    main(["register", "alice", "--password", PW])
    main(["instacart", "connect", "--api-key", KEY])
    _stock(("milk", 1), ("rice", 90))
    capsys.readouterr()
    assert main(["instacart", "shop"]) == 0
    out = capsys.readouterr().out
    assert LIST_URL in out and "milk" in out and "rice" not in out
    names = [li["name"] for li in fake.requests[-1]["body"]["line_items"]]
    assert names == ["milk"]


def test_cli_shop_named_items(fake, capsys):
    main(["register", "alice", "--password", PW])
    main(["instacart", "connect", "--api-key", KEY])
    assert main(["instacart", "shop", "eggs", "bread", "eggs"]) == 0
    lines = fake.requests[-1]["body"]["line_items"]
    assert [(li["name"], li["quantity"]) for li in lines] == [("eggs", 2), ("bread", 1)]


def test_cli_shop_without_connection_fails(fake, capsys):
    main(["register", "alice", "--password", PW])
    assert main(["instacart", "shop", "eggs"]) == 1
    assert "instacart connect" in capsys.readouterr().err


def test_cli_stores_lists_retailers(fake, capsys):
    main(["register", "alice", "--password", PW])
    main(["instacart", "connect", "--api-key", KEY, "--zip", "32081",
          "--retailer", "costco"])
    capsys.readouterr()
    assert main(["instacart", "stores"]) == 0
    out = capsys.readouterr().out
    assert "Sprouts Farmers Market" in out
    assert "costco  ← preferred" in out


# ---------------------------------------------------------------------------
# web API
# ---------------------------------------------------------------------------

def test_web_status_hides_key(fake, alice):
    app = PantryScanApp()
    assert app.instacart_status()["connected"] is False
    instacart.connect(KEY)
    status = app.instacart_status()
    assert status["connected"] is True and status["user"] == "alice"
    assert KEY not in json.dumps(status)


def test_web_shopping_list_from_expiring(fake, alice):
    instacart.connect(KEY)
    _stock(("yogurt", 2), ("rice", 90))
    result = PantryScanApp().instacart_shopping_list({})
    assert result["url"] == LIST_URL
    assert [i["name"] for i in result["items"]] == ["yogurt"]


def test_web_shopping_list_requires_connection(fake, alice):
    with pytest.raises(ApiError) as exc:
        PantryScanApp().instacart_shopping_list({"names": ["eggs"]})
    assert exc.value.status == 409


def test_web_shopping_list_validates_names(fake, alice):
    instacart.connect(KEY)
    with pytest.raises(ApiError) as exc:
        PantryScanApp().instacart_shopping_list({"names": "eggs"})
    assert exc.value.status == 400


def test_web_shopping_list_nothing_expiring(fake, alice):
    instacart.connect(KEY)
    with pytest.raises(ApiError) as exc:
        PantryScanApp().instacart_shopping_list({})
    assert exc.value.status == 400


def test_web_routes_over_http(fake, alice, tmp_path):
    instacart.connect(KEY)
    _stock(("milk", 1))
    httpd, _app = create_server("127.0.0.1", 0, api_key="sesame")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    headers = {"X-LettuceRemind-Key": "sesame"}
    try:
        req = urllib.request.Request(f"{base}/api/instacart", headers=headers)
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["connected"] is True
        # An empty body means "restock what's expiring".
        req = urllib.request.Request(
            f"{base}/api/instacart/shopping-list", data=b"", headers=headers,
            method="POST")
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read())
        assert data["url"] == LIST_URL
        assert data["count"] == 1
        page = urllib.request.Request(f"{base}/?key=sesame")
        with urllib.request.urlopen(page) as res:
            assert b'id="instacartBtn"' in res.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
