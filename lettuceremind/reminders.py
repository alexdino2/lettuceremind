"""Expiration reminders over the pantry."""

from __future__ import annotations

from datetime import date
from typing import Optional

from lettuceremind.models import PantryItem


def expiring_soon(
    items: list[PantryItem],
    within_days: int = 3,
    today: Optional[date] = None,
) -> list[PantryItem]:
    """Items that expire within ``within_days`` (including already expired),
    soonest first."""
    today = today or date.today()
    due = [i for i in items if i.days_left(today) <= within_days]
    return sorted(due, key=lambda i: i.expires_on)


def format_reminder(item: PantryItem, today: Optional[date] = None) -> str:
    days = item.days_left(today)
    qty = f" x{item.quantity}" if item.quantity > 1 else ""
    if days < 0:
        return f"🥀 {item.name}{qty} expired {-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return f"⚠️  {item.name}{qty} expires TODAY"
    return f"⏰ {item.name}{qty} expires in {days} day{'s' if days != 1 else ''}"
