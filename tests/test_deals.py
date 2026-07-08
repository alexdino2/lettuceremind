import json
from datetime import date, timedelta

import pytest

from lettuceremind.cli import main
from lettuceremind.deals import (
    BUILTIN_CATALOG,
    STORES,
    builtin_deals,
    current_deals,
    match_pantry,
    resolve_store,
)
from lettuceremind.models import PantryItem
from lettuceremind.receipt.matcher import FoodMatcher

WED = date(2026, 7, 8)  # a Wednesday — the start of a circular week


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep deals/auth/pantry files away from the real ~/.lettuceremind."""
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_STORE", raising=False)
    monkeypatch.delenv("LETTUCEREMIND_DEALS", raising=False)
    return tmp_path


def test_all_four_stores_are_supported():
    assert set(STORES) == {"publix", "kroger", "whole-foods", "costco"}
    assert set(BUILTIN_CATALOG) == set(STORES)


def test_every_builtin_deal_resolves_in_food_db():
    matcher = FoodMatcher()
    for store, entries in BUILTIN_CATALOG.items():
        for item, *_ in entries:
            result = matcher.match(item)
            assert result.method == "exact", (store, item, result.method)


def test_current_deals_covers_all_stores_and_is_valid_today():
    deals = current_deals(today=WED)
    assert {d.store for d in deals} == set(STORES)
    for deal in deals:
        assert deal.valid_from <= WED <= deal.valid_to


def test_deals_are_stable_within_a_week_and_rotate_across_weeks():
    this_week = [d.item for d in current_deals(today=WED)]
    same_week = [d.item for d in current_deals(today=WED + timedelta(days=6))]
    next_week = [d.item for d in current_deals(today=WED + timedelta(days=7))]
    assert this_week == same_week
    assert this_week != next_week


def test_costco_deals_run_for_the_calendar_month():
    deals = [d for d in builtin_deals(WED) if d.store == "costco"]
    assert deals
    for deal in deals:
        assert deal.valid_from == date(2026, 7, 1)
        assert deal.valid_to == date(2026, 7, 31)


@pytest.mark.parametrize("name,key", [
    ("publix", "publix"),
    ("Kroger", "kroger"),
    ("Whole Foods", "whole-foods"),
    ("wholefoods", "whole-foods"),
    ("WHOLE_FOODS", "whole-foods"),
    ("Costco Wholesale", "costco"),
])
def test_resolve_store_aliases(name, key):
    assert resolve_store(name) == key


def test_resolve_store_rejects_unknown():
    with pytest.raises(ValueError, match="Publix"):
        resolve_store("trader joes")


def test_custom_feed_merges_and_expired_entries_are_dropped(isolated_home, monkeypatch):
    feed = isolated_home / "feed.json"
    feed.write_text(json.dumps({"deals": [
        {"store": "kroger", "item": "milk", "price": "$1.49",
         "valid_from": "2026-07-01", "valid_to": "2026-07-31"},
        {"store": "publix", "item": "bananas", "price": "$0.10",
         "valid_from": "2026-01-01", "valid_to": "2026-01-07"},
        {"store": "nope", "item": "milk", "price": "$1"},
        "garbage",
    ]}))
    monkeypatch.setenv("LETTUCEREMIND_DEALS", str(feed))
    deals = current_deals(today=WED)
    custom = [d for d in deals if d.source == "custom"]
    assert [(d.store, d.item, d.price) for d in custom] == [("kroger", "milk", "$1.49")]


def test_match_pantry_flags_deals_on_owned_items():
    deals = current_deals(today=WED)
    strawberry_deals = [d for d in deals if d.item == "strawberries"]
    assert strawberry_deals, "expected a strawberries deal in the 2026-07-08 rotation"
    pantry = [PantryItem(name="strawberries", category="fresh fruit", quantity=1,
                         added_on=WED, expires_on=WED + timedelta(days=2))]
    matches = match_pantry(deals, pantry)
    assert strawberry_deals[0] in matches
    assert matches[strawberry_deals[0]].name == "strawberries"


def test_cli_deals_lists_all_four_stores(capsys):
    rc = main(["deals", "--date", "2026-07-08"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("Publix", "Kroger", "Whole Foods", "Costco"):
        assert name in out


def test_cli_deals_single_store_filter(capsys):
    rc = main(["deals", "costco", "--date", "2026-07-08"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Costco" in out
    assert "Publix" not in out


def test_cli_deals_unknown_store_errors(capsys):
    rc = main(["deals", "walmart"])
    assert rc == 1
    assert "unknown store" in capsys.readouterr().err


def test_cli_deals_pantry_cross_reference(isolated_home, monkeypatch, capsys):
    feed = isolated_home / "feed.json"
    feed.write_text(json.dumps({"deals": [
        {"store": "kroger", "item": "strawberries", "price": "$1.99",
         "description": "1 lb", "valid_from": "2026-01-01",
         "valid_to": "2026-12-31"},
    ]}))
    monkeypatch.setenv("LETTUCEREMIND_DEALS", str(feed))
    assert main(["add", "strawberries"]) == 0
    capsys.readouterr()
    rc = main(["deals", "--pantry"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "strawberries" in out
    assert "in your pantry" in out


def test_cli_deals_pantry_with_empty_pantry(capsys):
    rc = main(["deals", "--pantry", "--date", "2026-07-08"])
    assert rc == 0
    assert "No current deals match" in capsys.readouterr().out
