"""JSON-backed pantry storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Union

from lettuceremind.models import PantryItem

DEFAULT_STORE_PATH = Path.home() / ".lettuceremind" / "pantry.json"


class PantryStore:
    """Persists pantry items to a JSON file."""

    def __init__(self, path: Union[str, Path, None] = None):
        env_path = os.environ.get("LETTUCEREMIND_STORE")
        self.path = Path(path or env_path or DEFAULT_STORE_PATH)
        self._items: list[PantryItem] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._items = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._items = []
            return
        self._items = [PantryItem.from_dict(d) for d in data.get("items", [])]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [i.to_dict() for i in self._items]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, item: PantryItem) -> None:
        self._items.append(item)
        self._save()

    def add_all(self, items: list[PantryItem]) -> None:
        self._items.extend(items)
        self._save()

    def all(self) -> list[PantryItem]:
        return list(self._items)

    def remove(self, name: str) -> int:
        """Remove all items matching ``name`` (case-insensitive). Returns count."""
        lowered = name.lower()
        before = len(self._items)
        self._items = [i for i in self._items if i.name.lower() != lowered]
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    def clear(self) -> int:
        count = len(self._items)
        self._items = []
        self._save()
        return count
