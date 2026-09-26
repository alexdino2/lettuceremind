"""Local grocery deals for Publix, Kroger, Whole Foods, Aldi, and Costco.

The app is offline and dependency-free. Kroger / Whole Foods / Aldi / Costco ship
a built-in catalog of representative circular deals that rotates
deterministically: a Wednesday-to-Tuesday window for the grocery chains
(circulars flip on Wednesday) and a calendar-month window for Costco's
savings book. The same date always shows the same deals.

Publix is different: live weekly-ad data is available by ZIP via Publix's
public services API (the same store-scoped circular
https://github.com/jhustln/Publix-Discount-Detector scrapes). Run
``lettuceremind deals --zip 32081 --refresh`` to pull the nearest store's
ad into ``~/.lettuceremind/deals.json``. The built-in Publix catalog is a
snapshot of ZIP 32081 (Nocatee Town Center) used when no live feed is
present.

You can also drop any JSON feed at ``~/.lettuceremind/deals.json`` (or
point ``$LETTUCEREMIND_DEALS`` at one); its entries are merged in, and
built-ins for a store are skipped when the feed already has that store::

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
    "aldi": "Aldi",
    "costco": "Costco",
}

_STORE_ALIASES: dict[str, str] = {
    "publix": "publix",
    "kroger": "kroger",
    "whole foods": "whole-foods",
    "wholefoods": "whole-foods",
    "whole foods market": "whole-foods",
    "wfm": "whole-foods",
    "aldi": "aldi",
    "aldi us": "aldi",
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
    source: str = "builtin"  # "builtin", "custom", or "publix"

    @property
    def store_name(self) -> str:
        return STORES.get(self.store, self.store)


# (item, description, price, regular_price) — items must resolve exactly in
# FOOD_DB (enforced by tests) so pantry cross-referencing always works.
#
# Publix entries are a snapshot of the ZIP 32081 (Nocatee Town Center,
# store 01243) weekly ad for the week of 2026-08-06. Refresh with
# ``lettuceremind deals --zip 32081 --refresh`` for the live circular.
BUILTIN_CATALOG: dict[str, tuple[tuple[str, str, str, Optional[str]], ...]] = {
    "publix": (
        ("chicken breast", "18–20 oz boneless skinless", "BOGO", None),
        ("strawberries", "6 or 16 oz", "BOGO", None),
        ("yogurt", "32 oz tub", "BOGO", None),
        ("avocado", "Florida tropical, each", "2 for $5.00", None),
        ("ground beef", "chuck, 3 lb+", "$7.99/lb", None),
        ("apples", "Honeycrisp, 2 lb pouch", "BOGO", None),
        ("bread", "Nature's Own Hawaiian", "BOGO", None),
        ("bacon", "12 or 16 oz", "2 for $10.00", None),
        ("spinach", "9 oz", "2 for $4.00", None),
        ("eggs", "dozen free-range large", "BOGO", None),
        ("grapes", "red or white seedless", "$2.99/lb", None),
        ("deli turkey", "oven-roasted, sliced", "$9.99/lb", None),
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
    "aldi": (
        ("milk", "gallon, Friendly Farms", "$2.29", "$2.89"),
        ("eggs", "dozen large, Goldhen", "$1.79", "$2.49"),
        ("avocado", "hass, each", "$0.59", "$0.89"),
        ("strawberries", "16 oz", "$1.99", "$2.99"),
        ("chicken breast", "boneless skinless, Kirkwood", "$2.19/lb", "$2.99/lb"),
        ("ground beef", "80/20, 1 lb", "$3.79", "$4.69"),
        ("bread", "Specially Selected brioche loaf", "$2.99", "$3.69"),
        ("shredded cheese", "8 oz, Happy Farms", "$1.89", "$2.39"),
        ("greek yogurt", "32 oz, Friendly Farms", "$3.29", "$4.19"),
        ("potatoes", "russet, 5 lb bag", "$2.49", "$3.49"),
        ("tortillas", "flour, 10 ct, Casa Mamita", "$1.49", "$1.99"),
        ("orange juice", "52 oz, Nature's Nectar", "$2.99", "$3.79"),
    ),
    "costco": (
        ("rotisserie chicken", "whole, hot", "$4.99", None),
        ("eggs", "24-count cage free", "$5.49", "$6.99"),
        ("strawberries", "2 lb clamshell", "$5.99", "$7.49"),
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

# The deals tab is for groceries, so alcohol, pharmacy / health & beauty,
# and household or other non-food lines are dropped no matter where they
# came from (built-in catalog, custom feed, or the live Publix circular).
NON_FOOD_CATEGORIES = frozenset({"household", "baby & health"})

_NON_FOOD_DEPARTMENTS = re.compile(
    r"\b(?:beer|wine|liquor|spirits|alcohol|pharmacy|health (?:&|and) beauty|"
    r"health care|beauty|personal care|wellness|vitamins?|supplements?|"
    r"household|cleaning|laundry|paper|pet|floral|flowers|baby care|"
    r"diapers?|tobacco|general merchandise|gift cards?|greeting cards?)\b",
    re.I,
)

_NON_FOOD_TERMS = re.compile(
    r"\b(?:"
    # alcohol and related items
    r"beers?|ales?|lagers?|ipas?|stouts?|porters?|pilsners?|wines?|"
    r"champagne|prosecco|ros[eé]|merlot|cabernet|chardonnay|pinot|"
    r"sauvignon|riesling|moscato|malbec|zinfandel|sangria|sake|vodka|"
    r"whiske?y|bourbon|scotch|rum|tequila|mezcal|gin|brandy|cognac|"
    r"liqueurs?|spirits|hard seltzers?|hard cider|hard lemonade|"
    r"spiked|cocktails?|margaritas?|mixers?|bloody mary|daiquiri|mojito|"
    r"white claw|twisted tea|mike'?s hard|high noon|"
    r"budweiser|bud light|coors|miller lite|michelob|corona|modelo|heineken|"
    r"stella artois|yuengling|blue moon|sam(?:uel)? adams|"
    # pharmacy / health & beauty
    r"pharmacy|medicines?|medications?|vitamins|multivitamins?|"
    r"gummy vitamins|supplements?|pain relievers?|"
    r"ibuprofen|acetaminophen|aspirin|naproxen|tylenol|advil|motrin|aleve|"
    r"allergy|antihistamine|claritin|zyrtec|allegra|benadryl|cough|"
    r"cold (?:and|&) flu|decongestant|antacids?|tums|laxative|nyquil|dayquil|"
    r"mucinex|bandages?|band-aid|first aid|thermometer|sunscreen|lotion|"
    r"shampoo|conditioner|body wash|deodorant|toothpaste|toothbrush|"
    r"mouthwash|floss|razors?|shaving|cosmetics?|makeup|contact lens|"
    r"diapers?|baby wipes|tampons?|incontinence|"
    # household and other non-food
    r"paper towels?|toilet paper|bath tissue|facial tissues?|napkins|"
    r"paper plates|plastic cups|cutlery|detergent|fabric softener|"
    r"dryer sheets|bleach|cleaners?|disinfecting|dish soap|dishwasher|"
    r"sponges?|trash bags?|garbage bags?|aluminum foil|plastic wrap|"
    r"storage bags|zip(?:loc|lock) bags|batteries|light bulbs?|charcoal|"
    r"lighter fluid|propane|air fresheners?|candles?|"
    r"dog food|cat food|pet food|dog treats|cat treats|cat litter|"
    r"flowers?|bouquets?|roses|plants?|gift cards?|greeting cards?|"
    r"cigarettes?|cigars?|tobacco|vapes?|lottery"
    r")\b",
    re.I,
)

# Grocery phrases that contain one of the words above.
_FOOD_EXCEPTIONS = re.compile(
    r"\b(?:(?:red |white |rice )?wine vinegar|cider vinegar|cooking wine|"
    r"root beer|ginger beer|ginger ale|birch beer|beer[- ]battered|"
    r"beer brats?|rum raisin|rum cake|bourbon chicken|vodka sauce|"
    r"wine sauce|non[- ]?alcoholic|alcohol[- ]free|mocktails?|"
    r"shrimp cocktail|cocktail sauce|cocktail shrimp|fruit cocktail|"
    r"cocktail peanuts|cocktail franks|cocktail (?:sausages|smokies)|"
    r"plant[- ]based|flower sprinkles)\b",
    re.I,
)


def is_non_food(*texts: str, department: str = "") -> bool:
    """True for alcohol, pharmacy / health & beauty, household, and other
    non-food lines — judged from the item text and store department."""
    if department and _NON_FOOD_DEPARTMENTS.search(department):
        return True
    text = " ".join(t for t in texts if t)
    if not text:
        return False
    return bool(_NON_FOOD_TERMS.search(_FOOD_EXCEPTIONS.sub(" ", text)))


def is_food_deal(deal: "Deal", matcher: Optional[FoodMatcher] = None) -> bool:
    """Whether a deal belongs in the grocery deals list."""
    if is_non_food(deal.item, deal.description):
        return False
    result = (matcher or _default_matcher()).match(deal.item)
    return not (result.matched and result.food.category in NON_FOOD_CATEGORIES)


_MATCHER: Optional[FoodMatcher] = None


def _default_matcher() -> FoodMatcher:
    global _MATCHER
    if _MATCHER is None:
        _MATCHER = FoodMatcher()
    return _MATCHER


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
        source = str(entry.get("source") or "custom").strip().lower()
        if source not in ("custom", "publix", "builtin"):
            source = "custom"
        out.append(Deal(store=store, item=item,
                        description=str(entry.get("description", "")),
                        price=price, regular_price=str(reg) if reg else None,
                        valid_from=valid_from, valid_to=valid_to, source=source))
    return out


def current_deals(today: Optional[date] = None,
                  stores: Optional[list[str]] = None) -> list[Deal]:
    """All deals valid on ``today``: the built-in rotation plus any custom
    feed entries, for the requested stores (default: all five). Alcohol,
    pharmacy, and other non-food deals are filtered out.

    When the custom feed already has entries for a store, that store's
    built-in sample catalog is skipped so live Publix (or other) data
    replaces the snapshot instead of duplicating it.
    """
    today = today or date.today()
    custom = custom_deals(today, stores)
    custom_stores = {d.store for d in custom}
    builtin_stores = [
        key for key in (stores or list(STORES))
        if key not in custom_stores
    ]
    builtin = builtin_deals(today, builtin_stores) if builtin_stores else []
    return [d for d in builtin + custom if is_food_deal(d)]


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
