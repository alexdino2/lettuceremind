"""The pantry-scanner web server behind ``lettuceremind serve``.

A small stdlib HTTP server hosts a phone-friendly single-page app: open
the printed URL on your iPhone, point the camera at your pantry, and
every product the scanner recognizes is added to your inventory as you
go. Frames are captured in the browser, sent here as JPEG, OCR'd
(``lettuceremind[ocr]``), and resolved through the same normalize/match
pipeline the receipt scanner uses.

Security model: the server is meant for your own Wi-Fi. Unless started
with ``--no-key`` it generates a random access key, embeds it in the URL
it prints, and rejects requests without it — so other devices on the
network can't read or edit your pantry. The pantry it serves is resolved
exactly like the CLI's (``--store``, ``$LETTUCEREMIND_STORE``, the
logged-in user's pantry, then the shared one).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import socket
import ssl
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Optional, Union

from lettuceremind import __version__
from lettuceremind.models import PantryItem
from lettuceremind.receipt.matcher import FoodMatcher
from lettuceremind.shelf_life import shelf_life_for
from lettuceremind.store import PantryStore
from lettuceremind.web import recognize

DEFAULT_PORT = 8043

#: Seconds during which re-recognizing the same product is treated as the
#: same physical item (consecutive frames of one jar), not a new one.
DUPLICATE_WINDOW_SECONDS = 45.0

#: Largest accepted request body — a downscaled JPEG frame is ~100-400 KB.
MAX_BODY_BYTES = 8 * 1024 * 1024


class ApiError(Exception):
    """An API failure with an HTTP status the client can act on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class PantryScanApp:
    """HTTP-independent request logic: JSON dicts in, JSON dicts out."""

    def __init__(
        self,
        store_path: Union[str, Path, None] = None,
        api_key: Optional[str] = None,
        dedupe_window: float = DUPLICATE_WINDOW_SECONDS,
        clock=time.monotonic,
    ):
        self.store_path = store_path
        self.api_key = api_key
        self._matcher = FoodMatcher()
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}  # food name -> last-added time
        self._dedupe_window = dedupe_window
        self._clock = clock

    def _store(self) -> PantryStore:
        # A fresh store per request picks up concurrent CLI edits.
        return PantryStore(self.store_path)

    @staticmethod
    def _item_dict(item: PantryItem) -> dict:
        d = item.to_dict()
        d["days_left"] = item.days_left()
        return d

    def pantry(self) -> dict:
        items = sorted(self._store().all(), key=lambda i: i.expires_on)
        return {"items": [self._item_dict(i) for i in items], "count": len(items)}

    def scan(self, payload: dict) -> dict:
        """Recognize foods in one camera frame and add the new ones."""
        text = payload.get("text")
        image_b64 = payload.get("image")
        if not text and not image_b64:
            raise ApiError(400, "send 'image' (base64 JPEG/PNG) or 'text'")
        if not text:
            try:
                data = base64.b64decode(image_b64, validate=True)
            except (binascii.Error, ValueError, TypeError):
                raise ApiError(400, "invalid base64 image data") from None
            try:
                text = recognize.ocr_image_bytes(data)
            except RuntimeError as exc:
                raise ApiError(501, str(exc)) from None

        matches = recognize.recognize_labels(text, matcher=self._matcher)
        today = date.today()
        recognized: list[dict] = []
        with self._lock:
            now = self._clock()
            self._recent = {
                n: t for n, t in self._recent.items()
                if now - t < self._dedupe_window
            }
            to_add: list[PantryItem] = []
            for m in matches:
                entry = {
                    "name": m.name,
                    "category": m.category,
                    "confidence": round(m.confidence, 2),
                    "source_text": m.source_text,
                }
                if m.name in self._recent:
                    entry["status"] = "duplicate"
                else:
                    days = shelf_life_for(m.food)
                    item = PantryItem(
                        name=m.name,
                        category=m.category,
                        quantity=1,
                        added_on=today,
                        expires_on=today + timedelta(days=days),
                    )
                    entry["status"] = "added"
                    entry["expires_on"] = item.expires_on.isoformat()
                    to_add.append(item)
                    self._recent[m.name] = now
                recognized.append(entry)
            store = self._store()
            if to_add:
                store.add_all(to_add)
        return {
            "recognized": recognized,
            "added": sum(1 for e in recognized if e["status"] == "added"),
            "pantry_count": len(store.all()),
        }

    def add(self, payload: dict) -> dict:
        """Manually add one item by name (same matching as `lettuceremind add`)."""
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ApiError(400, "missing 'name'")
        try:
            quantity = int(payload.get("quantity", 1))
        except (TypeError, ValueError):
            raise ApiError(400, "'quantity' must be an integer") from None
        match = self._matcher.match(name)
        today = date.today()
        item = PantryItem(
            name=match.food.name,
            category=match.food.category,
            quantity=max(1, quantity),
            added_on=today,
            expires_on=today + timedelta(days=shelf_life_for(match.food)),
        )
        with self._lock:
            store = self._store()
            store.add(item)
            count = len(store.all())
        return {"added": self._item_dict(item), "pantry_count": count}

    def remove(self, payload: dict) -> dict:
        """Remove items by name — also the scanner feed's undo."""
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ApiError(400, "missing 'name'")
        with self._lock:
            store = self._store()
            removed = store.remove(name)
            # Forget the dedupe entry so an undone item can be re-scanned.
            self._recent = {
                n: t for n, t in self._recent.items()
                if n.lower() != name.lower()
            }
            count = len(store.all())
        return {"removed": removed, "pantry_count": count}


def _page_html() -> str:
    return (
        resources.files("lettuceremind.web")
        .joinpath("static/app.html")
        .read_text(encoding="utf-8")
    )


class _Handler(BaseHTTPRequestHandler):
    app: PantryScanApp  # bound by create_server()

    server_version = f"LettuceRemind/{__version__}"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # keep the CLI output clean

    # -- helpers ---------------------------------------------------------

    def _split_path(self) -> tuple[str, dict[str, str]]:
        path, _, query_string = self.path.partition("?")
        query = {}
        for pair in query_string.split("&"):
            key, _, value = pair.partition("=")
            if key:
                query[key] = value
        return path, query

    def _authorized(self, query: dict[str, str]) -> bool:
        if self.app.api_key is None:
            return True
        supplied = (
            self.headers.get("X-LettuceRemind-Key")
            or query.get("key")
            or ""
        )
        return hmac.compare_digest(supplied, self.app.api_key)

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._respond(status, body, "application/json; charset=utf-8")

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ApiError(400, "bad Content-Length") from None
        if length <= 0:
            raise ApiError(400, "empty request body")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "request body too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "request body must be JSON") from None
        if not isinstance(payload, dict):
            raise ApiError(400, "request body must be a JSON object")
        return payload

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._split_path()
        if path in ("/", "/index.html"):
            if not self._authorized(query):
                self._respond(
                    401,
                    b"<h1>401</h1><p>Access key missing or wrong. Open the "
                    b"exact URL printed by <code>lettuceremind serve</code>.</p>",
                    "text/html; charset=utf-8",
                )
                return
            self._respond(200, _page_html().encode("utf-8"),
                          "text/html; charset=utf-8")
        elif path == "/api/pantry":
            if not self._authorized(query):
                self._json(401, {"error": "missing or wrong access key"})
                return
            self._json(200, self.app.pantry())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._split_path()
        routes = {
            "/api/scan": self.app.scan,
            "/api/add": self.app.add,
            "/api/remove": self.app.remove,
        }
        handler = routes.get(path)
        try:
            if handler is None:
                raise ApiError(404, "not found")
            if not self._authorized(query):
                raise ApiError(401, "missing or wrong access key")
            self._json(200, handler(self._read_json_body()))
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})


def create_server(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    store_path: Union[str, Path, None] = None,
    api_key: Optional[str] = None,
    certfile: Optional[str] = None,
    keyfile: Optional[str] = None,
) -> tuple[ThreadingHTTPServer, PantryScanApp]:
    """Build the server (without starting it). Returns (httpd, app)."""
    app = PantryScanApp(store_path=store_path, api_key=api_key)
    handler = type("BoundHandler", (_Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd, app


def lan_ip() -> str:
    """This machine's LAN address — the one to type into the phone."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP connect: routes locally, sends nothing
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
