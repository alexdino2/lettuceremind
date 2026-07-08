"""Local grocery deals for Publix, Kroger, Whole Foods, and Costco.

The app is offline and dependency-free, and none of these chains publish a
free deals API — so this module ships a built-in catalog of representative
circular deals per store that rotates deterministically: a Wednesday-to-
Tuesday window for the grocery chains (circulars flip on Wednesday) and a
calendar-month window for Costco's savings book. The same date always shows
the same deals.

Real circular data can be plugged in without touching code: drop a JSON
feed at ``~/.lettuceremind/deals.json`` (or point ``$LETTUCEREMIND_DEALS``
at one) and its entries are merged in on top of the built-ins::

    {"deals": [{"store": "kroger", "item": "milk", "price": "$1.99",
                "regular_price": "$3.49", "description": "gallon",
                "valid_from": "2026-07-01", "valid_to": "2026-07-08"}]}

Every built-in deal names a product from the food database, so deals can be
cross-referenced against the pantry — "strawberries are on sale and yours
expire tomorrow" is the whole point.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from lettuceremind.models import PantryItem
from lettuceremind.paths import base_dir
from lettuceremind.receipt.matcher import FoodMatcher

# Canonical store key -> display name, in display order.
STORES: dict[str, str] = {
    "publix": "Publix",
    "kroger": "Kroger",
    "whole-foods": "Whole Foods",
    "costco": "Costco",
}

_STORE_ALIASES: dict[str, str] = {
    "publix": "publix",
    "kroger": "kroger",
    "whole foods": "whole-foods",
    "wholefoods": "whole-foods",
    "whole foods market": "whole-foods",
    "wfm": "whole-foods",
    "costco": "costco",
    "costco wholesale": "costco",
}


def resolve_store(name: str) -> str:
    """Turn user input ('Whole Foods', 'wholefoods', …) into a store key."""
    key = re.sub(r"[\s_-]+", " ", name.strip().lower())
    try:
        return _STORE_ALIASES[key]
    except KeyError:
        valid = ", ".join(STORES.values())
        raise ValueError(f"unknown store {name!r} — supported: {valid}") from None


@dataclass(frozen=True)
class Deal:
    """One advertised deal at one store."""

    store: str  # canonical key from STORES
    item: str  # product name resolvable in the food database
    description: str  # size/pack blurb, e.g. "16 oz" or "family pack"
    price: str  # display price: "$2.99/lb", "BOGO $4.99", "2 for $6"
    regular_price: Optional[str]
    valid_from: date
    valid_to: date
    source: str = "builtin"  # "builtin" or "custom"

    @property
    def store_name(self) -> str:
        return STORES.get(self.store, self.store)


# (item, description, price, regular_price) — items must resolve exactly in
# FOOD_DB (enforced by tests) so pantry cross-referencing always works.
BUILTIN_CATALOG: dict[str, tuple[tuple[str, str, str, Optional[str]], ...]] = {
    "publix": (
        ("strawberries", "16 oz", "BOGO $4.99", None),
        ("chicken breast", "boneless skinless, family pack", "$2.99/lb", "$5.49/lb"),
        ("greek yogurt", "32 oz tub", "BOGO $5.99", None),
        ("avocado", "each", "$1.25", "$2.00"),
        ("ground beef", "80/20", "$3.99/lb", "$5.29/lb"),
        ("orange juice", "52 oz", "BOGO $4.79", None),
        ("bread", "bakery white or wheat", "$1.99", "$3.29"),
        ("salsa", "16 oz jar", "BOGO $3.99", None),
        ("ice cream", "48 oz", "BOGO $6.49", None),
        ("deli turkey", "sliced fresh", "$7.99/lb", "$9.99/lb"),
        ("blueberries", "pint", "2 for $6", "$4.29 ea"),
        ("bell peppers", "tri-color 3 pack", "$2.99", "$4.49"),
    ),
    "kroger": (
        ("milk", "gallon, with card", "$2.49", "$3.79"),
        ("eggs", "dozen large, with card", "$1.99", "$3.19"),
        ("bananas", "per lb", "$0.49/lb", "$0.59/lb"),
        ("cheddar cheese", "8 oz block", "$1.88", "$2.99"),
        ("pasta", "16 oz box", "$0.99", "$1.79"),
        ("pasta sauce", "24 oz jar", "$1.79", "$2.69"),
        ("chicken thighs", "boneless", "$1.99/lb", "$3.49/lb"),
        ("apples", "gala, 3 lb bag", "$2.99", "$4.99"),
        ("cereal", "family size, with card", "$2.49", "$4.29"),
        ("frozen pizza", "rising crust", "$3.99", "$6.49"),
        ("bacon", "16 oz", "$3.99", "$6.49"),
        ("spinach", "baby spinach, 10 oz", "$1.99", "$3.49"),
    ),
    "whole-foods": (
        ("salmon", "wild-caught sockeye", "$9.99/lb", "$14.99/lb"),
        ("avocado", "organic hass, each", "$1.49", "$2.29"),
        ("kale", "organic bunch", "$1.79", "$2.49"),
        ("sourdough bread", "bakery loaf", "$3.49", "$4.99"),
        ("almond milk", "64 oz", "$2.79", "$3.99"),
        ("blueberries", "organic pint", "$3.49", "$5.99"),
        ("ground turkey", "organic", "$4.99/lb", "$6.99/lb"),
        ("hummus", "10 oz", "$2.99", "$4.49"),
        ("kombucha", "16 oz bottle", "2 for $5", "$3.49 ea"),
        ("rotisserie chicken", "whole, classic", "$7.99", "$9.99"),
        ("feta cheese", "6 oz block", "$3.49", "$4.99"),
        ("honey", "raw, 12 oz", "$5.99", "$7.99"),
    ),
    "costco": (
        ("rotisserie chicken", "whole, hot", "$4.99", None),
        ("eggs", "24-count cage free", "$5.49", "$6.99"),
        ("paper towels", "12 rolls", "$18.99", "$23.99"),
        ("ground beef", "88/12, 5 lb pack", "$4.49/lb", "$5.49/lb"),
        ("salmon", "atlantic fillet", "$8.99/lb", "$10.99/lb"),
        ("spring mix", "1 lb clamshell", "$4.49", "$5.49"),
        ("bagels", "2 x 6 pack", "$5.99", "$7.49"),
        ("shredded cheese", "2.5 lb bag", "$8.99", "$10.99"),
        ("maple syrup", "1 L organic", "$11.49", "$13.99"),
        ("coffee", "2.5 lb whole bean", "$14.99", "$19.99"),
        ("olive oil", "2 L extra virgin", "$19.99", "$24.99"),
        ("butter", "4 x 1 lb", "$10.99", "$12.99"),
    ),
}

_DEALS_PER_WEEK = 7
_DEALS_PER_MONTH = 6  # Costco's savings book


def custom_feed_path() -> Path:
    env = os.environ.get("LETTUCEREMIND_DEALS")
    return Path(env) if env else base_dir() / "deals.json"


def _circular_window(today: date) -> tuple[date, date]:
    """The Wed-Tue circular week containing ``today``."""
    start = today - timedelta(days=(today.weekday() - 2) % 7)
    return start, start + timedelta(days=6)


def _monthly_window(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month + 1) - timedelta(days=1)
    return start, end


def _rotate(entries: tuple, index: int, count: int) -> list:
    """Pick ``count`` consecutive entries starting at a window-derived
    offset, wrapping around — deterministic, and cycles the whole catalog."""
    n = len(entries)
    count = min(count, n)
    start = (index * 5) % n
    return [entries[(start + i) % n] for i in range(count)]


def builtin_deals(today: date, stores: Optional[list[str]] = None) -> list[Deal]:
    out: list[Deal] = []
    for key in stores or STORES:
        entries = BUILTIN_CATALOG[key]
        if key == "costco":
            start, end = _monthly_window(today)
            index = today.year * 12 + today.month
            count = _DEALS_PER_MONTH
        else:
            start, end = _circular_window(today)
            index = start.toordinal() // 7
            count = _DEALS_PER_WEEK
        for item, desc, price, reg in _rotate(entries, index, count):
            out.append(Deal(store=key, item=item, description=desc, price=price,
                            regular_price=reg, valid_from=start, valid_to=end))
    return out


def custom_deals(today: date, stores: Optional[list[str]] = None) -> list[Deal]:
    """Deals from the user's JSON feed that are valid on ``today``.

    Malformed entries are skipped rather than failing the whole command.
    """
    path = custom_feed_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Deal] = []
    for entry in data.get("deals", []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            store = resolve_store(str(entry["store"]))
            item = str(entry["item"])
            price = str(entry["price"])
            valid_from = (date.fromisoformat(entry["valid_from"])
                          if "valid_from" in entry else today)
            valid_to = (date.fromisoformat(entry["valid_to"])
                        if "valid_to" in entry else today)
        except (KeyError, ValueError):
            continue
        if stores is not None and store not in stores:
            continue
        if not valid_from <= today <= valid_to:
            continue
        reg = entry.get("regular_price")
        out.append(Deal(store=store, item=item,
                        description=str(entry.get("description", "")),
                        price=price, regular_price=str(reg) if reg else None,
                        valid_from=valid_from, valid_to=valid_to, source="custom"))
    return out


def current_deals(today: Optional[date] = None,
                  stores: Optional[list[str]] = None) -> list[Deal]:
    """All deals valid on ``today``: the built-in rotation plus any custom
    feed entries, for the requested stores (default: all four)."""
    today = today or date.today()
    return builtin_deals(today, stores) + custom_deals(today, stores)


def match_pantry(deals: list[Deal], pantry_items: list[PantryItem],
                 matcher: Optional[FoodMatcher] = None) -> dict[Deal, PantryItem]:
    """Cross-reference deals with the pantry.

    Returns, for each deal on a product the user currently has, the
    earliest-expiring pantry item of that product — so the caller can say
    "on sale, and yours expires in 2 days".
    """
    matcher = matcher or FoodMatcher()
    earliest: dict[str, PantryItem] = {}
    for item in sorted(pantry_items, key=lambda i: i.expires_on):
        earliest.setdefault(item.name.lower(), item)
    matched: dict[Deal, PantryItem] = {}
    for deal in deals:
        result = matcher.match(deal.item)
        if result.matched:
            hit = earliest.get(result.food.name.lower())
            if hit is not None:
                matched[deal] = hit
    return matched
