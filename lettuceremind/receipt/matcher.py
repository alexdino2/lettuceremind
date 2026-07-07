"""Match normalized receipt text to a known food product.

The matcher is a staged pipeline with a guaranteed result — there is
always a final fallback, so matching never fails outright:

1. exact    — normalized text equals a product name or alias
2. overlap  — best word overlap between text and product aliases, ranked
              by words in common and how completely the alias is covered
              (handles "organic boneless skinless chicken breast fillets")
3. fuzzy    — whole-string difflib similarity for typos and OCR errors;
              beats stage 2 when it explains more of the text
4. per-word fuzzy — one garbled word can still hit a product name
5. fallback — generic "other" item using the raw text as the name
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Optional

from lettuceremind.models import FoodInfo
from lettuceremind.receipt.normalize import normalize
from lettuceremind.shelf_life import DEFAULT_CATEGORY, FOOD_DB

# Descriptor words that don't distinguish products; ignored when scoring
# word overlap (an "organic" prefix shouldn't block a match).
_DESCRIPTORS: frozenset[str] = frozenset({
    "organic", "fresh", "natural", "boneless", "skinless", "large", "small",
    "medium", "extra", "whole", "sliced", "diced", "shredded", "chopped",
    "baby", "sweet", "seedless", "ripe", "raw", "lean", "thick", "thin",
    "cut", "free", "range", "cage", "grade", "fat", "reduced", "low",
    "light", "unsalted", "salted", "sharp", "mild", "red", "green", "yellow",
    "white", "brown", "black", "golden",
})


@dataclass
class MatchResult:
    """The product a piece of receipt text resolved to."""

    food: FoodInfo
    confidence: float
    method: str
    normalized_text: str

    @property
    def matched(self) -> bool:
        return self.method != "fallback"


class FoodMatcher:
    """Resolves normalized receipt text to entries in the food database."""

    def __init__(self, db: tuple[FoodInfo, ...] = FOOD_DB):
        self._db = db
        # alias string -> food
        self._exact: dict[str, FoodInfo] = {}
        # alias word tuple -> food, for subset/overlap matching
        self._word_index: list[tuple[frozenset[str], str, FoodInfo]] = []
        for food in db:
            for alias in (food.name, *food.aliases):
                key = normalize(alias) or alias.lower()
                if key not in self._exact:
                    self._exact[key] = food
                words = frozenset(key.split())
                if words:
                    self._word_index.append((words, key, food))
        self._all_keys = list(self._exact.keys())

    def match(self, text: str) -> MatchResult:
        """Match receipt text to a food. Never raises; always returns."""
        norm = normalize(text)
        if not norm:
            return self._fallback(text, norm)

        # Stage 1: exact match on name or alias.
        food = self._exact.get(norm)
        if food is not None:
            return MatchResult(food, 1.0, "exact", norm)

        word_list = norm.split()
        words = set(word_list)
        core_words = words - _DESCRIPTORS or words
        # Position of each word in the text; receipts lead with the product
        # words, so earlier coverage breaks ties between candidate aliases.
        position = {w: i for i, w in reversed(list(enumerate(word_list)))}

        def _tiebreak(alias_words: frozenset[str], key: str) -> tuple:
            first = min((position[w] for w in alias_words if w in position),
                        default=len(word_list))
            return (-first, len(key))

        # Stage 2: word overlap. Candidates are ranked by specificity —
        # words in common, then how completely the alias is covered —
        # ignoring pure descriptors. How much of the *query* the winner
        # explains decides the contest against fuzzy matching below.
        best_overlap: Optional[tuple[tuple, float, float, FoodInfo]] = None
        for alias_words, key, cand in self._word_index:
            common = alias_words & words
            if not common:
                continue
            alias_core = alias_words - _DESCRIPTORS or alias_words
            alias_cov = len(common) / len(alias_words)
            query_cov = len(core_words & alias_core) / len(core_words)
            score = (len(common), alias_cov, *_tiebreak(alias_words, key))
            if best_overlap is None or score > best_overlap[0]:
                best_overlap = (score, alias_cov, query_cov, cand)

        # Stage 3: whole-string fuzzy match for typos / OCR noise. Wins when
        # it explains more of the text than the word overlap does, so
        # "CHDDAR CHEESE" resolves to cheddar cheese, not some other cheese.
        fuzzy: Optional[tuple[float, FoodInfo]] = None
        close = difflib.get_close_matches(norm, self._all_keys, n=1, cutoff=0.75)
        if close:
            ratio = difflib.SequenceMatcher(None, norm, close[0]).ratio()
            fuzzy = (ratio, self._exact[close[0]])

        query_explained = best_overlap[2] if best_overlap else 0.0
        if fuzzy is not None and fuzzy[0] >= 0.8 and fuzzy[0] > query_explained:
            return MatchResult(fuzzy[1], 0.5 + 0.4 * fuzzy[0], "fuzzy", norm)
        if best_overlap is not None and best_overlap[1] >= 1.0:
            return MatchResult(best_overlap[3], 0.9, "subset", norm)
        if best_overlap is not None and best_overlap[1] >= 0.5:
            return MatchResult(best_overlap[3], 0.6 + 0.3 * best_overlap[1], "partial", norm)
        if fuzzy is not None:
            return MatchResult(fuzzy[1], 0.5 + 0.4 * fuzzy[0], "fuzzy", norm)

        # Stage 4b: per-word fuzzy, so one garbled word can still hit a
        # single-word product name.
        fuzzy_best: Optional[tuple[float, FoodInfo]] = None
        for word in core_words:
            if len(word) < 4:
                continue
            hits = difflib.get_close_matches(word, self._all_keys, n=1, cutoff=0.8)
            if hits:
                ratio = difflib.SequenceMatcher(None, word, hits[0]).ratio()
                if fuzzy_best is None or ratio > fuzzy_best[0]:
                    fuzzy_best = (ratio, self._exact[hits[0]])
        if fuzzy_best is not None:
            return MatchResult(fuzzy_best[1], 0.4 + 0.4 * fuzzy_best[0], "fuzzy", norm)

        # Stage 5: never fail — keep the item with safe defaults so the
        # user still gets a reminder for it.
        return self._fallback(text, norm)

    def _fallback(self, raw: str, norm: str) -> MatchResult:
        name = norm or raw.strip().lower() or "unknown item"
        food = FoodInfo(name=name, category=DEFAULT_CATEGORY)
        return MatchResult(food, 0.0, "fallback", norm)
