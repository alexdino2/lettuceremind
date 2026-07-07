from datetime import date

from lettuceremind.models import PantryItem
from lettuceremind.reminders import expiring_soon, format_reminder
from lettuceremind.store import PantryStore


def make_item(name="milk", days_out=5, today=date(2026, 7, 1)):
    from datetime import timedelta
    return PantryItem(
        name=name, category="milk & cream", quantity=1,
        added_on=today, expires_on=today + timedelta(days=days_out),
    )


def test_store_roundtrip(tmp_path):
    path = tmp_path / "pantry.json"
    store = PantryStore(path)
    store.add(make_item())
    reloaded = PantryStore(path)
    items = reloaded.all()
    assert len(items) == 1
    assert items[0].name == "milk"


def test_store_remove_and_clear(tmp_path):
    store = PantryStore(tmp_path / "p.json")
    store.add_all([make_item("milk"), make_item("Milk"), make_item("eggs")])
    assert store.remove("MILK") == 2
    assert store.clear() == 1


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "pantry.json"
    path.write_text("{not json!!", encoding="utf-8")
    store = PantryStore(path)
    assert store.all() == []


def test_expiring_soon_sorted_and_includes_expired():
    today = date(2026, 7, 1)
    items = [
        make_item("fresh", days_out=10, today=today),
        make_item("due tomorrow", days_out=1, today=today),
        make_item("expired", days_out=-2, today=today),
        make_item("today", days_out=0, today=today),
    ]
    due = expiring_soon(items, within_days=3, today=today)
    assert [i.name for i in due] == ["expired", "today", "due tomorrow"]


def test_format_reminder():
    today = date(2026, 7, 1)
    assert "TODAY" in format_reminder(make_item(days_out=0, today=today), today)
    assert "expired" in format_reminder(make_item(days_out=-3, today=today), today)
    assert "in 2 days" in format_reminder(make_item(days_out=2, today=today), today)
