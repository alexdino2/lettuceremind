"""Recognize foods in OCR text from a pantry photo.

Receipts and pantry photos are opposite problems. A receipt line always
names a product, so the receipt pipeline guarantees an item for every
line. A camera frame of a pantry shelf is mostly noise — nutrition
panels, net-weight declarations, date stamps, ingredient lists,
marketing copy — with a product name buried somewhere in it. So this
module is deliberately conservative: a candidate line only becomes an
item when it resolves to a known product with high confidence, and
everything else is dropped rather than kept as junk.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Optional

from lettuceremind.models import FoodInfo
from lettuceremind.receipt.matcher import FoodMatcher

#: Below this match confidence a candidate is treated as noise.
MIN_CONFIDENCE = 0.7

#: Cap on items recognized in a single frame; a shelf shot rarely has a
#: readable label count higher than this, so anything past it is noise.
MAX_MATCHES_PER_FRAME = 4

# A line containing any of these words is packaging boilerplate, never a
# product name. ("INGREDIENTS: MILK, SALT, CULTURES" on a cheese label
# would otherwise confidently match milk.)
_SKIP_LINE_WORDS = frozenset({
    "ingredients", "ingredient", "allergen", "allergens", "contains",
    "nutrition", "calories", "calorie", "serving", "servings",
    "distributed", "manufactured", "www", "http", "https", "com",
})

# Words that never distinguish a product; a line made only of these is
# discarded before matching.
_NOISE_WORDS = frozenset({
    "net", "wt", "weight", "fl", "oz", "lb", "lbs", "g", "mg", "kg",
    "ml", "l", "ct", "keep", "refrigerated", "frozen", "best", "if",
    "used", "use", "by", "before", "sell", "date", "exp", "expires",
    "size", "per", "cup", "container", "daily", "value", "values",
    "total", "fat", "saturated", "trans", "cholesterol", "sodium",
    "carbohydrate", "carbohydrates", "protein", "fiber", "vitamin",
    "iron", "calcium", "potassium", "facts", "amount", "the", "a", "of",
    "and", "with", "new", "look", "same", "great", "taste",
})

_WORD_RE = re.compile(r"[a-z]+")


@dataclass
class LabelMatch:
    """A food confidently recognized somewhere in one camera frame."""

    food: FoodInfo
    confidence: float
    source_text: str

    @property
    def name(self) -> str:
        return self.food.name

    @property
    def category(self) -> str:
        return self.food.category


def _usable_lines(text: str) -> list[str]:
    """OCR lines that could plausibly contain a product name."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        words = _WORD_RE.findall(line.lower())
        if not words:
            continue
        if any(w in _SKIP_LINE_WORDS for w in words):
            continue
        if all(w in _NOISE_WORDS for w in words):
            continue
        lines.append(line)
    return lines


def candidates(text: str) -> list[str]:
    """Candidate product strings: each usable line, plus adjacent pairs.

    Pairs catch names split across label lines ("GREEK" / "YOGURT").
    """
    lines = _usable_lines(text)
    return lines + [f"{a} {b}" for a, b in zip(lines, lines[1:])]


def recognize_labels(
    text: str,
    matcher: Optional[FoodMatcher] = None,
    min_confidence: float = MIN_CONFIDENCE,
    limit: int = MAX_MATCHES_PER_FRAME,
) -> list[LabelMatch]:
    """Foods confidently recognized in one frame's OCR text.

    Returns the best match per distinct product, highest confidence
    first. Unlike receipt scanning there is no fallback: an unreadable
    frame yields an empty list, never a junk item.
    """
    matcher = matcher or FoodMatcher()
    best: dict[str, LabelMatch] = {}
    for cand in candidates(text):
        result = matcher.match(cand)
        if not result.matched or result.confidence < min_confidence:
            continue
        prev = best.get(result.food.name)
        if prev is None or result.confidence > prev.confidence:
            best[result.food.name] = LabelMatch(result.food, result.confidence, cand)
    ranked = sorted(best.values(), key=lambda m: -m.confidence)
    return ranked[:limit]


def ocr_image_bytes(data: bytes) -> str:
    """OCR a camera frame (JPEG/PNG bytes) into text."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Image OCR needs the optional dependencies. Install them with:\n"
            "    pip install lettuceremind[ocr]\n"
            "(requires the tesseract binary on your system)."
        ) from exc
    return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
