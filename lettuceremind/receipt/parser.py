"""Parse raw receipt text into candidate item lines.

Handles the common layout of US grocery receipts:

    STORE NAME #1234
    123 MAIN ST ...
    BNLS CHKN BRST      7.42 F
    2 @ 1.99
    GV MILK 1GAL        3.29 F
    SUBTOTAL           10.71
    TAX                 0.55
    TOTAL              11.26
    VISA  ************1234

Item lines are kept; headers, totals, payment, and barcodes are skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Trailing price with optional currency symbol, negative sign (voids),
# and tax-flag suffix letters:  "3.29", "$3.29 F", "3.29-", "3.29 FT".
_PRICE_RE = re.compile(r"[-$]?\s*\d{1,4}\.\d{2}\s*-?\s*[A-Z]{0,2}\s*$")
# Leading quantity markers: "2 X ", "QTY 2", "2 @".
_QTY_PREFIX_RE = re.compile(
    r"^\s*(?:qty\s*(\d{1,2})|(\d{1,2})\s*[x@])\s+", re.IGNORECASE
)
# Trailing receipt tax/department flags left after stripping barcode+price,
# e.g. the "F" in "BANANAS 000000004011 F 1.24". Single letters only —
# two-letter runs collide with real words ("BF" beef, "OJ" orange juice).
_TAX_FLAG_RE = re.compile(r"(?:\s+[FTBNXOAEH])+\s*$")
# A quantity-only continuation line like "2 @ 3.99" or "1.34 lb @ 0.98/lb".
_QTY_LINE_RE = re.compile(
    r"^\s*\d+(\.\d+)?\s*(lb|lbs|kg|ea)?\s*@\s*[\d.]+(\s*/\s*(lb|kg|ea))?.*$",
    re.IGNORECASE,
)
# UPC / PLU barcode digit runs.
_BARCODE_RE = re.compile(r"\b\d{9,14}\b")
_ONLY_SYMBOLS_RE = re.compile(r"^[\W\d]+$")

# Any line containing one of these words (as its own word) is not an item.
_SKIP_WORDS: frozenset[str] = frozenset({
    "subtotal", "total", "tax", "change", "cash", "credit", "debit", "visa",
    "mastercard", "amex", "discover", "tend", "tender", "payment", "balance",
    "due", "approved", "auth", "account", "card", "refund", "void",
    "coupon", "savings", "saved", "discount", "loyalty", "rewards", "points",
    "receipt", "cashier", "register", "lane", "transaction", "invoice",
    "thank", "thanks", "welcome", "store", "phone", "tel", "manager",
    "items", "item", "sold", "count", "st#", "op#", "te#", "tr#",
    "survey", "www", "http", "com", "feedback", "return", "policy", "exchange",
    "open", "hours", "member", "membership", "price", "prices", "guarantee",
    # store names commonly printed as receipt headers
    "walmart", "supercenter", "kroger", "costco", "safeway", "albertsons",
    "publix", "aldi", "target", "meijer", "wegmans", "walgreens", "cvs",
})

# Lines that are store addresses: contain street/city keywords with numbers.
_ADDRESS_RE = re.compile(
    r"\b(st|ave|blvd|rd|dr|ln|hwy|suite|ste|street|avenue|road|drive)\b[\s.,]*$"
    r"|^\d+\s+\w+|\b[a-z]{2}\s+\d{5}\b",
    re.IGNORECASE,
)
_DATE_TIME_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}:\d{2}\b"
)


@dataclass
class ParsedLine:
    """A receipt line identified as a purchasable item."""

    raw: str
    name_text: str
    quantity: int = 1


def _looks_like_item(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _ONLY_SYMBOLS_RE.match(stripped):
        return False
    if _QTY_LINE_RE.match(stripped):
        return False
    if _DATE_TIME_RE.search(stripped):
        return False
    lowered = stripped.lower()
    words = set(re.split(r"[^a-z#]+", lowered))
    if words & _SKIP_WORDS:
        return False
    # Remove price and barcode, then require some alphabetic content.
    # Very short names ("OJ 3.89") only count when a price anchors them.
    has_price = _PRICE_RE.search(stripped) is not None
    remainder = _PRICE_RE.sub("", stripped)
    remainder = _BARCODE_RE.sub("", remainder)
    letters = re.sub(r"[^a-zA-Z]", "", remainder)
    if len(letters) < (2 if has_price else 3):
        return False
    if _ADDRESS_RE.search(stripped) and _PRICE_RE.search(stripped) is None:
        return False
    return True


def parse_receipt_text(text: str) -> tuple[list[ParsedLine], list[str]]:
    """Split receipt text into item lines and skipped lines.

    Returns ``(items, skipped)``. Quantity continuation lines ("2 @ 1.99")
    apply to the item line directly above them.
    """
    items: list[ParsedLine] = []
    skipped: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue
        # Quantity continuation for the previous item.
        qty_line = _QTY_LINE_RE.match(stripped)
        if qty_line and items:
            qty = float(qty_line.group(0).split("@")[0].strip().split()[0])
            if qty.is_integer() and qty > 1:
                items[-1].quantity = int(qty)
            skipped.append(stripped)
            continue
        if not _looks_like_item(stripped):
            skipped.append(stripped)
            continue
        name_text = _PRICE_RE.sub("", stripped)
        name_text = _BARCODE_RE.sub("", name_text)
        name_text = _TAX_FLAG_RE.sub("", name_text).strip()
        quantity = 1
        qty_match = _QTY_PREFIX_RE.match(name_text)
        if qty_match:
            quantity = max(1, int(qty_match.group(1) or qty_match.group(2)))
            name_text = name_text[qty_match.end():].strip()
        if not name_text:
            skipped.append(stripped)
            continue
        items.append(ParsedLine(raw=stripped, name_text=name_text, quantity=quantity))
    return items, skipped
