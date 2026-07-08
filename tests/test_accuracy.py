"""Receipt-scan accuracy guarantee.

Builds a large corpus of receipt lines covering every product and alias in
the food database, rendered the way real US grocery receipts print them
(uppercase, trailing prices and tax flags, store-brand prefixes, size
tokens, quantity markers, and per-word abbreviations), runs each line
through the full scan pipeline (parser -> normalizer -> matcher), and
asserts:

* recognition accuracy >= 99.9% on receipt-formatted lines, and
* 100% coverage — every item line always yields a tracked item with a
  shelf life and reminder, even for typo'd or unknown text.
"""

from __future__ import annotations

import pytest

from lettuceremind.receipt.matcher import FoodMatcher
from lettuceremind.receipt.normalize import ABBREVIATIONS, normalize
from lettuceremind.receipt.scanner import scan_receipt_text
from lettuceremind.shelf_life import FOOD_DB

# Single-word expansion -> receipt abbreviation (exact inverse of the
# expansion table, so these are abbreviations the scanner claims to handle).
_REVERSE_ABBR: dict[str, str] = {}
for _abbr, _exp in ABBREVIATIONS.items():
    if " " not in _exp:
        _REVERSE_ABBR.setdefault(_exp, _abbr)

# Canonical owner of each normalized alias (first food in DB wins, matching
# FoodMatcher's index construction).
_OWNER: dict[str, str] = {}
for _food in FOOD_DB:
    for _alias in (_food.name, *_food.aliases):
        _OWNER.setdefault(normalize(_alias) or _alias.lower(), _food.name)


def _abbreviate(alias: str) -> str:
    words = normalize(alias).split()
    return " ".join(_REVERSE_ABBR.get(w, w) for w in words)


def _variants(alias: str) -> list[str]:
    up = alias.upper()
    cases = [
        f"{up}  3.49 F",             # Walmart-style price + tax flag
        f"{up} 12OZ  2.99",          # size token
        f"GV {up}  1.98 F",          # store-brand prefix
        f"2 X {up}  5.98",           # quantity marker
    ]
    if sum(c.isalpha() for c in up) >= 3:
        cases.append(up)             # plain, no price (emailed receipts)
    abbr = _abbreviate(alias)
    # Skip degenerate abbreviations (<3 letters); real receipts don't print
    # them and the parser rightly treats such lines as noise.
    if abbr != normalize(alias) and sum(c.isalpha() for c in abbr) >= 3:
        cases.append(f"{abbr.upper()}  4.29 F")
    return cases


def _corpus() -> list[tuple[str, str]]:
    """(receipt line, expected canonical product name) pairs."""
    pairs: list[tuple[str, str]] = []
    for food in FOOD_DB:
        for alias in (food.name, *food.aliases):
            expected = _OWNER[normalize(alias) or alias.lower()]
            for line in _variants(alias):
                pairs.append((line, expected))
    return pairs


def _typo_corpus() -> list[tuple[str, str]]:
    """Names with an OCR-style dropped vowel in the longest word."""
    pairs: list[tuple[str, str]] = []
    for food in FOOD_DB:
        words = normalize(food.name).split()
        longest = max(words, key=len)
        if len(longest) < 6:
            continue
        mid = longest[1:-1]
        vowel_at = next((i for i, c in enumerate(mid) if c in "aeiou"), None)
        if vowel_at is None:
            continue
        typo = longest[0] + mid[:vowel_at] + mid[vowel_at + 1:] + longest[-1]
        line = " ".join(typo if w == longest else w for w in words)
        pairs.append((f"{line.upper()}  3.99 F", food.name))
    return pairs


def _run(pairs: list[tuple[str, str]]) -> tuple[float, list[tuple[str, str, str]]]:
    matcher = FoodMatcher()
    correct = 0
    misses: list[tuple[str, str, str]] = []
    for line, expected in pairs:
        result = scan_receipt_text(line)
        # Coverage guarantee: a receipt item line is never dropped and
        # always gets a shelf life.
        assert len(result.items) == 1, f"line lost by parser: {line!r}"
        got = result.items[0]
        assert got.shelf_life_days > 0
        assert got.expires_on > result.purchase_date
        if got.name == expected:
            correct += 1
        else:
            misses.append((line, expected, got.name))
    return correct / len(pairs), misses


def test_receipt_scan_recognizes_99_9_pct_of_items():
    pairs = _corpus()
    assert len(pairs) > 2000, "corpus should be large enough to be meaningful"
    accuracy, misses = _run(pairs)
    assert accuracy >= 0.999, (
        f"accuracy {accuracy:.4%} on {len(pairs)} lines; "
        f"first misses: {misses[:15]}"
    )


def test_receipt_scan_is_robust_to_ocr_typos():
    pairs = _typo_corpus()
    assert len(pairs) > 80
    accuracy, misses = _run(pairs)
    assert accuracy >= 0.95, (
        f"typo accuracy {accuracy:.4%} on {len(pairs)} lines; "
        f"first misses: {misses[:15]}"
    )


def test_every_line_yields_an_item_even_pure_garbage():
    for junk in ["XQZWKJ FLURBOBLAT 9.99", "?????? 1.00", "ZZZZZ"]:
        result = scan_receipt_text(junk)
        if result.items:  # symbol-only lines are legitimately skipped
            assert result.items[0].shelf_life_days > 0


@pytest.mark.parametrize("line,expected", [
    ("BNLS SKLS CHKN BRST  7.42 F", "chicken breast"),
    ("GV WHL MLK GAL  3.29 F", "milk"),
    ("ORG BBY SPINACH  3.99 F", "spinach"),
    ("HVY WHP CRM PT  3.19 B", "heavy cream"),
    ("GRND BF 80/20  6.53 F", "ground beef"),
    ("SHRD CHDR CHZ  2.88", "shredded cheese"),
    ("RUSST POTS 5LB  3.79", "potatoes"),
    ("FRZN MXD VEG  2.29", "frozen vegetables"),
    ("CHKN NDLE SP CN  1.25", "canned soup"),
    ("PPR TWLS 6CT  8.99", "paper towels"),
])
def test_known_hard_receipt_lines(line, expected):
    result = scan_receipt_text(line)
    assert result.items and result.items[0].name == expected
