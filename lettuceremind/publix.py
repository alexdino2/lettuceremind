"""Fetch Publix weekly-ad deals by ZIP code.

Uses the same store-scoped weekly-ad source as
https://github.com/jhustln/Publix-Discount-Detector
(``/savings/weekly-ad/view-all?storeNumber=…``), calling Publix's public
services API directly so LettuceRemind stays dependency-free — no Selenium,
no Apify token.

Default focus is ZIP ``32081`` (Nocatee / Ponte Vedra, FL).
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional
from urllib.request import Request

from lettuceremind.deals import Deal, custom_feed_path, resolve_store
from lettuceremind.receipt.matcher import FoodMatcher

DEFAULT_ZIP = "32081"
_UA = "LettuceRemind/1.0 (+https://github.com/alexdino2/lettuceremind)"
_STORE_URL = "https://services.publix.com/api/v1/storelocation"
_SAVINGS_URL = "https://services.publix.com/api/v4/savings"

# Prefer pantry-relevant foods when mapping the circular into deals.json.
_PREFERRED_ITEMS: tuple[str, ...] = (
    "chicken breast",
    "strawberries",
    "yogurt",
    "avocado",
    "ground beef",
    "apples",
    "bread",
    "bacon",
    "spinach",
    "eggs",
    "grapes",
    "deli turkey",
    "broccoli",
    "ice cream",
    "hummus",
    "salmon",
    "blueberries",
    "chicken thighs",
    "tomatoes",
    "mushrooms",
    "blackberries",
    "cherries",
    "peaches",
    "lettuce",
    "green beans",
    "onions",
    "coffee",
    "butter",
    "cereal",
    "pasta",
    "salsa",
    "rotisserie chicken",
)


@dataclass(frozen=True)
class PublixStore:
    """A Publix location resolved from a ZIP code."""

    number: str  # e.g. "01243"
    name: str
    address: str
    city: str
    state: str
    zip: str
    distance: Optional[str] = None


@dataclass(frozen=True)
class RawPublixDeal:
    """One weekly-ad line as returned by Publix."""

    title: str
    savings: str
    description: str
    department: str
    valid_from: date
    valid_to: date


def _get_json(url: str, headers: Optional[dict[str, str]] = None) -> Any:
    req_headers = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"Publix API HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Publix API unreachable: {exc.reason}") from exc


def _parse_day(value: Optional[str], fallback: date) -> date:
    if not value:
        return fallback
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return fallback


def find_stores(zip_code: str = DEFAULT_ZIP, count: int = 5) -> list[PublixStore]:
    """Nearest Publix stores for ``zip_code`` (closest first)."""
    zip_code = zip_code.strip()
    if not re.fullmatch(r"\d{5}", zip_code):
        raise ValueError(f"invalid ZIP code {zip_code!r} — expected 5 digits")
    qs = urllib.parse.urlencode({"zipCode": zip_code, "count": str(count)})
    data = _get_json(f"{_STORE_URL}?{qs}")
    out: list[PublixStore] = []
    for row in data.get("Stores") or []:
        number = str(row.get("KEY") or "").strip()
        if not number:
            continue
        out.append(PublixStore(
            number=number.zfill(5) if number.isdigit() and len(number) <= 5 else number,
            name=str(row.get("NAME") or row.get("SHORTNAME") or "Publix"),
            address=str(row.get("ADDR") or ""),
            city=str(row.get("CITY") or ""),
            state=str(row.get("STATE") or ""),
            zip=str(row.get("ZIP") or zip_code)[:5],
            distance=str(row["DISTANCE"]) if row.get("DISTANCE") is not None else None,
        ))
    return out


def find_store(zip_code: str = DEFAULT_ZIP) -> PublixStore:
    """Closest Publix for ``zip_code``."""
    stores = find_stores(zip_code, count=1)
    if not stores:
        raise RuntimeError(f"no Publix store found near ZIP {zip_code}")
    return stores[0]


def fetch_weekly_ad(store_number: str) -> list[RawPublixDeal]:
    """Pull the current weekly ad for a Publix store number.

    Same circular the Discount-Detector scraper loads via
    ``view-all?storeNumber=…``; we hit the JSON endpoint the page uses.
    """
    number = str(store_number).strip()
    if number.isdigit():
        number = number.zfill(5)
    qs = urllib.parse.urlencode({
        "storeNumber": number,
        "getSavingType": "WeeklyAd",
        "isWeb": "true",
        "pageSize": "0",
    })
    data = _get_json(
        f"{_SAVINGS_URL}?{qs}",
        headers={"PublixStore": number},
    )
    today = date.today()
    out: list[RawPublixDeal] = []
    for row in data.get("Savings") or []:
        if row.get("savingType") not in ("WeeklyAd", "Stacked"):
            continue
        title = html.unescape(str(row.get("title") or "")).strip()
        if not title:
            continue
        savings = html.unescape(str(row.get("savings") or "")).strip()
        savings = _normalize_price(savings)
        desc = html.unescape(str(row.get("description") or "")).strip()
        desc = re.sub(r"\s+", " ", desc.replace("\r\n", " "))
        dept = html.unescape(str(row.get("department") or "")).replace("&amp;", "&")
        out.append(RawPublixDeal(
            title=title,
            savings=savings,
            description=desc,
            department=dept,
            valid_from=_parse_day(row.get("wa_startDate"), today),
            valid_to=_parse_day(row.get("wa_endDate"), today),
        ))
    return out


def _normalize_price(savings: str) -> str:
    text = savings.strip()
    text = re.sub(r"Buy\s*1\s*Get\s*1\s*FREE", "BOGO", text, flags=re.I)
    text = re.sub(r"(\d)\s*/\s*\$(\d+\.\d{2})", r"\1 for $\2", text)
    return text


def _size_blurb(raw: RawPublixDeal) -> str:
    for part in re.split(r"[.\n]", raw.description):
        part = part.strip()
        if (
            part
            and re.search(r"\d", part)
            and len(part) < 70
            and "free item" not in part.lower()
        ):
            return part
    return raw.department or raw.title


_FRESH_DEPTS = frozenset({
    "produce", "meat", "seafood", "dairy", "bakery", "deli", "frozen",
    "frozen food", "frozen meat",
})
_STALE_DEPT_MARKERS = ("canned", "snack", "international", "household", "beverage")


def _department_penalty(department: str) -> int:
    dept = department.lower().strip()
    if not dept:
        return 1
    if any(marker in dept for marker in _STALE_DEPT_MARKERS):
        return 2
    if dept in _FRESH_DEPTS or any(
        dept == token or dept.startswith(token + " ") or dept.endswith(" " + token)
        for token in _FRESH_DEPTS
    ):
        return 0
    return 1


def map_to_deals(
    raw_deals: list[RawPublixDeal],
    *,
    matcher: Optional[FoodMatcher] = None,
    limit: int = 24,
) -> list[Deal]:
    """Map weekly-ad lines onto food-DB products (pantry-matchable)."""
    matcher = matcher or FoodMatcher()
    preferred = {name: i for i, name in enumerate(_PREFERRED_ITEMS)}
    method_rank = {
        "exact": 0,
        "subset": 1,
        "word_overlap": 2,
        "fuzzy": 3,
        "partial": 4,
    }
    # name -> (sort_key, deal)
    best: dict[str, tuple[tuple, Deal]] = {}

    for raw in raw_deals:
        result = matcher.match(raw.title)
        if not result.matched:
            continue
        # Accept exact / subset / strong fuzzy / solid partial matches.
        # Branded titles ("Just Bare Chicken Breast Fillets", "Florida
        # Tropical Avocados") usually land on fuzzy or subset rather than
        # exact.
        ok = result.method in ("exact", "subset", "word_overlap") or (
            result.method in ("fuzzy", "partial") and result.confidence >= 0.8
        )
        if not ok:
            continue
        name = result.food.name
        if result.food.category in ("household", "baby & health"):
            continue
        if not raw.savings:
            continue
        title_l = raw.title.lower()
        # Prefer titles that actually contain the food name / a key word.
        name_hit = 0 if name.split()[0] in title_l else 1
        sort_key = (
            preferred.get(name, 1000),
            _department_penalty(raw.department),
            method_rank.get(result.method, 9),
            name_hit,
            -result.confidence,
        )
        deal = Deal(
            store="publix",
            item=name,
            description=_size_blurb(raw),
            price=raw.savings,
            regular_price=None,
            valid_from=raw.valid_from,
            valid_to=raw.valid_to,
            source="publix",
        )
        prev = best.get(name)
        if prev is None or sort_key < prev[0]:
            best[name] = (sort_key, deal)

    ordered = sorted(best.values(), key=lambda pair: pair[0])
    return [deal for _, deal in ordered[:limit]]


def deals_for_zip(
    zip_code: str = DEFAULT_ZIP,
    *,
    limit: int = 24,
) -> tuple[PublixStore, list[Deal]]:
    """Resolve ZIP → nearest store → pantry-mapped weekly-ad deals."""
    store = find_store(zip_code)
    raw = fetch_weekly_ad(store.number)
    return store, map_to_deals(raw, limit=limit)


def refresh_feed(
    zip_code: str = DEFAULT_ZIP,
    *,
    path=None,
    limit: int = 24,
) -> tuple[PublixStore, list[Deal], Any]:
    """Fetch Publix deals for ``zip_code`` and merge them into the JSON feed.

    Existing non-Publix entries are preserved; prior Publix entries are
    replaced so the feed stays current.
    """
    path = path or custom_feed_path()
    store, deals = deals_for_zip(zip_code, limit=limit)

    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for entry in data.get("deals") or []:
                    if not isinstance(entry, dict):
                        continue
                    try:
                        if resolve_store(str(entry.get("store", ""))) == "publix":
                            continue
                    except ValueError:
                        pass
                    existing.append(entry)
        except (OSError, json.JSONDecodeError):
            existing = []

    publix_entries = [
        {
            "store": "publix",
            "item": d.item,
            "description": d.description,
            "price": d.price,
            "regular_price": d.regular_price,
            "valid_from": d.valid_from.isoformat(),
            "valid_to": d.valid_to.isoformat(),
            "zip": zip_code,
            "store_number": store.number,
            "store_name": store.name,
        }
        for d in deals
    ]
    payload = {
        "zip": zip_code,
        "store_number": store.number,
        "store_name": store.name,
        "updated": date.today().isoformat(),
        "deals": existing + publix_entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return store, deals, path
