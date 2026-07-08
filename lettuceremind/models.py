"""Core data models for LettuceRemind."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


@dataclass(frozen=True)
class FoodInfo:
    """Static knowledge about a food product.

    ``shelf_life_days`` overrides the category default when set.
    """

    name: str
    category: str
    aliases: tuple[str, ...] = ()
    shelf_life_days: Optional[int] = None


@dataclass
class ScannedItem:
    """A single item recognized on a receipt."""

    raw_text: str
    name: str
    category: str
    quantity: int
    shelf_life_days: int
    expires_on: date
    confidence: float
    matched: bool

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["expires_on"] = self.expires_on.isoformat()
        return d


@dataclass
class ScanResult:
    """The outcome of scanning one receipt."""

    items: list[ScannedItem] = field(default_factory=list)
    skipped_lines: list[str] = field(default_factory=list)
    purchase_date: date = field(default_factory=date.today)

    @property
    def match_rate(self) -> float:
        """Fraction of detected items resolved to a known product."""
        if not self.items:
            return 1.0
        return sum(1 for i in self.items if i.matched) / len(self.items)


@dataclass
class PantryItem:
    """An item tracked in the user's pantry."""

    name: str
    category: str
    quantity: int
    added_on: date
    expires_on: date

    def days_left(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        return (self.expires_on - today).days

    def is_expired(self, today: Optional[date] = None) -> bool:
        return self.days_left(today) < 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "quantity": self.quantity,
            "added_on": self.added_on.isoformat(),
            "expires_on": self.expires_on.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PantryItem":
        return cls(
            name=d["name"],
            category=d["category"],
            quantity=int(d.get("quantity", 1)),
            added_on=date.fromisoformat(d["added_on"]),
            expires_on=date.fromisoformat(d["expires_on"]),
        )

    @classmethod
    def from_scanned(cls, item: "ScannedItem", added_on: Optional[date] = None) -> "PantryItem":
        added = added_on or date.today()
        return cls(
            name=item.name,
            category=item.category,
            quantity=item.quantity,
            added_on=added,
            expires_on=added + timedelta(days=item.shelf_life_days),
        )
