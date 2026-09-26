"""Connect a LettuceRemind account to Instacart.

Uses the public Instacart Developer Platform (IDP) API
(https://docs.instacart.com/developer_platform_api/): a LettuceRemind user
links an IDP API key to their account, optionally with a home ZIP and a
preferred store, and can then turn their pantry — by default whatever is
about to expire — into an Instacart shopping-list page to restock from.

Instacart does not hand third-party apps a customer's Instacart login;
the shopping-list link is opened in the browser or Instacart app, where the
shopper signs in, picks a store, and checks out. So "connected" here means
the account holds an API key LettuceRemind can create those pages with.

Connections are per user and live next to that user's pantry, in
``~/.lettuceremind/users/<name>/instacart.json`` (mode 0600, like the
account files). Connecting requires a login so each account's key and
preferences stay its own.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.request import Request

from lettuceremind import __version__, auth
from lettuceremind.models import PantryItem
from lettuceremind.publix import DEFAULT_ZIP
from lettuceremind.reminders import expiring_soon

PRODUCTION = "production"
DEVELOPMENT = "development"
BASE_URLS = {
    PRODUCTION: "https://connect.instacart.com",
    DEVELOPMENT: "https://connect.dev.instacart.tools",
}
_UA = f"LettuceRemind/{__version__} (+https://github.com/alexdino2/lettuceremind)"
_ZIP_RE = re.compile(r"^\d{5}$")

#: How long a generated shopping-list page stays valid, in days.
LINK_EXPIRES_IN_DAYS = 30


class InstacartError(Exception):
    """An Instacart connection or API problem the user can act on."""


@dataclass(frozen=True)
class Retailer:
    """A store that delivers through Instacart near a postal code."""

    key: str
    name: str
    logo_url: str = ""


@dataclass
class InstacartConnection:
    """One account's link to the Instacart Developer Platform."""

    api_key: str
    environment: str = PRODUCTION
    postal_code: Optional[str] = None
    retailer_key: Optional[str] = None
    retailer_name: Optional[str] = None
    connected_on: str = ""

    @property
    def masked_key(self) -> str:
        return "…" + self.api_key[-4:] if len(self.api_key) > 8 else "…"

    def public_dict(self) -> dict:
        """Everything except the API key itself — safe to show or serve."""
        d = asdict(self)
        del d["api_key"]
        d["masked_key"] = self.masked_key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InstacartConnection":
        env = d.get("environment")
        return cls(
            api_key=str(d["api_key"]),
            environment=env if env in BASE_URLS else PRODUCTION,
            postal_code=d.get("postal_code") or None,
            retailer_key=d.get("retailer_key") or None,
            retailer_name=d.get("retailer_name") or None,
            connected_on=str(d.get("connected_on") or ""),
        )


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class InstacartClient:
    """Minimal stdlib client for the Instacart Developer Platform API."""

    def __init__(self, api_key: str, environment: str = PRODUCTION):
        if environment not in BASE_URLS:
            raise InstacartError(f"unknown Instacart environment {environment!r}")
        self.api_key = api_key
        self.base_url = BASE_URLS[environment]

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": _UA,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise InstacartError(
                    "Instacart rejected the API key — check it, and pass --dev "
                    "for a development key"
                ) from exc
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise InstacartError(f"Instacart API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise InstacartError(f"Instacart API unreachable: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise InstacartError("Instacart API returned invalid JSON") from exc

    def list_retailers(self, postal_code: str, country_code: str = "US") -> list[Retailer]:
        """Stores that deliver to ``postal_code`` through Instacart."""
        postal_code = _check_zip(postal_code)
        qs = urllib.parse.urlencode({
            "postal_code": postal_code, "country_code": country_code,
        })
        data = self._request("GET", f"/idp/v1/retailers?{qs}")
        out: list[Retailer] = []
        for row in (data or {}).get("retailers") or []:
            key = str(row.get("retailer_key") or "").strip()
            if key:
                out.append(Retailer(
                    key=key,
                    name=str(row.get("name") or key),
                    logo_url=str(row.get("retailer_logo_url") or ""),
                ))
        return out

    def create_shopping_list(
        self,
        title: str,
        line_items: list[dict],
        *,
        linkback_url: Optional[str] = None,
        expires_in: int = LINK_EXPIRES_IN_DAYS,
    ) -> str:
        """Create an Instacart shopping-list page. Returns its URL."""
        if not line_items:
            raise InstacartError("nothing to put on the shopping list")
        body: dict = {
            "title": title,
            "link_type": "shopping_list",
            "expires_in": expires_in,
            "line_items": line_items,
        }
        if linkback_url:
            body["landing_page_configuration"] = {"partner_linkback_url": linkback_url}
        data = self._request("POST", "/idp/v1/products/products_link", body)
        url = (data or {}).get("products_link_url")
        if not isinstance(url, str) or not url:
            raise InstacartError("Instacart did not return a shopping-list link")
        return url


# ---------------------------------------------------------------------------
# Per-account connections
# ---------------------------------------------------------------------------

def connection_path(username: str) -> Path:
    return auth.user_pantry_path(username).with_name("instacart.json")


def _check_zip(postal_code: str) -> str:
    postal_code = postal_code.strip()
    if not _ZIP_RE.match(postal_code):
        raise InstacartError(f"invalid ZIP code {postal_code!r} — expected 5 digits")
    return postal_code


def require_user(username: Optional[str] = None) -> str:
    name = username or auth.current_user()
    if name is None:
        raise InstacartError(
            "log in first — Instacart connects to a LettuceRemind account "
            "(`lettuceremind login <name>` or `lettuceremind register <name>`)"
        )
    return name


def get_connection(username: Optional[str] = None) -> Optional[InstacartConnection]:
    """The Instacart connection for ``username`` (default: logged-in user)."""
    name = username or auth.current_user()
    if name is None:
        return None
    data = auth._load_json(connection_path(name))
    if not isinstance(data.get("api_key"), str) or not data["api_key"]:
        return None
    return InstacartConnection.from_dict(data)


def _pick_retailer(retailers: list[Retailer], wanted: str) -> Retailer:
    wanted_l = wanted.strip().lower()
    for r in retailers:
        if r.key.lower() == wanted_l or r.name.lower() == wanted_l:
            return r
    partial = [r for r in retailers if wanted_l in r.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        names = ", ".join(r.name for r in partial[:6])
        raise InstacartError(f"store {wanted!r} is ambiguous: {names}")
    raise InstacartError(
        f"no Instacart store matching {wanted!r} near that ZIP — "
        "see `lettuceremind instacart stores`"
    )


def connect(
    api_key: str,
    *,
    username: Optional[str] = None,
    environment: str = PRODUCTION,
    postal_code: Optional[str] = None,
    retailer: Optional[str] = None,
    verify: bool = True,
) -> InstacartConnection:
    """Link an Instacart API key (and store preferences) to an account.

    With ``verify`` the key is checked against Instacart's retailer lookup,
    which is also how ``retailer`` (a store name or retailer key) is
    resolved. Returns the saved connection.
    """
    name = require_user(username)
    api_key = api_key.strip()
    if not api_key:
        raise InstacartError("missing Instacart API key")
    if environment not in BASE_URLS:
        raise InstacartError(f"unknown Instacart environment {environment!r}")
    if postal_code:
        postal_code = _check_zip(postal_code)
    if retailer and not postal_code:
        raise InstacartError("choosing a store needs a ZIP code (--zip)")
    if retailer and not verify:
        raise InstacartError("choosing a store needs Instacart's store lookup "
                             "(drop --no-verify)")

    retailer_key = retailer_name = None
    if verify:
        client = InstacartClient(api_key, environment)
        retailers = client.list_retailers(postal_code or DEFAULT_ZIP)
        if retailer:
            chosen = _pick_retailer(retailers, retailer)
            retailer_key, retailer_name = chosen.key, chosen.name

    conn = InstacartConnection(
        api_key=api_key,
        environment=environment,
        postal_code=postal_code,
        retailer_key=retailer_key,
        retailer_name=retailer_name,
        connected_on=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    auth._write_json(connection_path(name), asdict(conn))
    return conn


def disconnect(username: Optional[str] = None) -> bool:
    """Forget the account's Instacart key. Returns True if one was stored."""
    name = require_user(username)
    try:
        connection_path(name).unlink()
    except FileNotFoundError:
        return False
    return True


def require_connection(username: Optional[str] = None) -> InstacartConnection:
    name = require_user(username)
    conn = get_connection(name)
    if conn is None:
        raise InstacartError(
            f"{name} isn't connected to Instacart — run "
            "`lettuceremind instacart connect`"
        )
    return conn


# ---------------------------------------------------------------------------
# Pantry → shopping list
# ---------------------------------------------------------------------------

def line_items(names_and_quantities: Iterable[tuple[str, int]]) -> list[dict]:
    """IDP line items, merging repeated names (case-insensitive)."""
    merged: dict[str, dict] = {}
    for name, quantity in names_and_quantities:
        name = name.strip()
        if not name:
            continue
        key = name.lower()
        if key in merged:
            merged[key]["quantity"] += max(1, quantity)
        else:
            merged[key] = {
                "name": name,
                "display_text": name[:1].upper() + name[1:],
                "quantity": max(1, quantity),
                "unit": "each",
            }
    return list(merged.values())


def restock_items(items: list[PantryItem], within_days: int = 3) -> list[dict]:
    """Line items for everything expiring within ``within_days`` (incl. expired)."""
    due = expiring_soon(items, within_days=within_days)
    return line_items((i.name, i.quantity) for i in due)


def shop_url(url: str, conn: InstacartConnection) -> str:
    """Point the shopping-list page at the account's preferred store."""
    if not conn.retailer_key:
        return url
    sep = "&" if urllib.parse.urlsplit(url).query else "?"
    return f"{url}{sep}{urllib.parse.urlencode({'retailer_key': conn.retailer_key})}"


def create_shopping_list(
    conn: InstacartConnection,
    items: list[dict],
    *,
    title: str = "LettuceRemind restock",
    linkback_url: Optional[str] = None,
) -> str:
    """Create a shopping-list page for ``items`` on the account's connection."""
    client = InstacartClient(conn.api_key, conn.environment)
    url = client.create_shopping_list(title, items, linkback_url=linkback_url)
    return shop_url(url, conn)
