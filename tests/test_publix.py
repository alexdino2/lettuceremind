"""Tests for Publix weekly-ad fetch (ZIP → store → deals)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from lettuceremind.cli import main
from lettuceremind.deals import current_deals
from lettuceremind.publix import (
    DEFAULT_ZIP,
    RawPublixDeal,
    deals_for_zip,
    fetch_weekly_ad,
    find_store,
    map_to_deals,
    refresh_feed,
)


STORE_PAYLOAD = {
    "Stores": [{
        "KEY": "01243",
        "NAME": "Nocatee Town Center",
        "ADDR": "120 Marketside Ave",
        "CITY": "Ponte Vedra",
        "STATE": "FL",
        "ZIP": "32081-0574",
        "DISTANCE": "2",
    }],
}

SAVINGS_PAYLOAD = {
    "Savings": [
        {
            "savingType": "WeeklyAd",
            "title": "Strawberries",
            "savings": "Buy 1 Get 1 FREE",
            "description": "Or Red Raspberries, 6 or 16-oz pkg.",
            "department": "Produce",
            "wa_startDate": "2026-08-06T00:00:00Z",
            "wa_endDate": "2026-08-12T23:59:59Z",
        },
        {
            "savingType": "WeeklyAd",
            "title": "Just Bare Chicken Breast Fillets",
            "savings": "Buy 1 Get 1 FREE",
            "additionalDealInfo": "SAVE UP TO $8.50",
            "description": "Boneless, Skinless, 18 or 20-oz pkg.",
            "department": "Meat",
            "recommendedRank": 1,
            "wa_startDate": "2026-08-06T00:00:00Z",
            "wa_endDate": "2026-08-12T23:59:59Z",
        },
        {
            "savingType": "WeeklyAd",
            "title": "Just Bare Lightly Breaded Chicken Breast",
            "savings": "Buy 1 Get 1 FREE",
            "description": "Bites, Strips, or Fillets; Sold Frozen, 19.2 or 24-oz pkg.",
            "department": "Frozen Meat",
            "recommendedRank": 40,
            "wa_startDate": "2026-08-06T00:00:00Z",
            "wa_endDate": "2026-08-12T23:59:59Z",
        },
        {
            "savingType": "DigitalCoupon",
            "title": "Save $1.00 on detergent",
            "savings": "Save $1.00",
            "description": "Laundry",
            "department": "Household",
            "wa_startDate": None,
            "wa_endDate": None,
        },
        {
            "savingType": "WeeklyAd",
            "title": "Ground Chuck",
            "savings": "$7.99 lb",
            "description": "Publix Beef, USDA-Inspected, 3-lbs or More Package.",
            "department": "Meat",
            "wa_startDate": "2026-08-06T00:00:00Z",
            "wa_endDate": "2026-08-12T23:59:59Z",
        },
    ],
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_STORE", raising=False)
    monkeypatch.delenv("LETTUCEREMIND_DEALS", raising=False)
    return tmp_path


def _fake_get_json(url: str, headers=None):
    if "storelocation" in url:
        assert "zipCode=32081" in url
        return STORE_PAYLOAD
    if "savings" in url:
        assert headers and headers.get("PublixStore") == "01243"
        assert "getSavingType=WeeklyAd" in url
        return SAVINGS_PAYLOAD
    raise AssertionError(f"unexpected URL {url}")


def test_default_zip_is_32081():
    assert DEFAULT_ZIP == "32081"


def test_find_store_resolves_nocatee():
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        store = find_store("32081")
    assert store.number == "01243"
    assert store.name == "Nocatee Town Center"
    assert store.city == "Ponte Vedra"


def test_find_store_rejects_bad_zip():
    with pytest.raises(ValueError, match="invalid ZIP"):
        find_store("3208")


def test_fetch_weekly_ad_filters_digital_coupons():
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        raw = fetch_weekly_ad("01243")
    assert len(raw) == 4
    assert all(isinstance(r, RawPublixDeal) for r in raw)
    fillets = next(r for r in raw if "Fillets" in r.title)
    assert fillets.savings == "BOGO · Save up to $8.50"
    assert fillets.recommended_rank == 1


def test_map_to_deals_matches_food_db():
    raw = [
        RawPublixDeal(
            title="Strawberries",
            savings="BOGO",
            description="6 or 16-oz pkg.",
            department="Produce",
            valid_from=date(2026, 8, 6),
            valid_to=date(2026, 8, 12),
        ),
        RawPublixDeal(
            title="Just Bare Chicken Breast Fillets",
            savings="BOGO · Save up to $8.50",
            description="18 or 20-oz pkg.",
            department="Meat",
            valid_from=date(2026, 8, 6),
            valid_to=date(2026, 8, 12),
            recommended_rank=1,
        ),
    ]
    deals = map_to_deals(raw)
    items = {d.item for d in deals}
    assert "strawberries" in items
    assert "chicken breast" in items
    chicken = next(d for d in deals if d.item == "chicken breast")
    assert "Just Bare Chicken Breast Fillets" in chicken.description
    assert "Save up to $8.50" in chicken.price
    assert all(d.store == "publix" and d.source == "publix" for d in deals)


def test_map_prefers_featured_fillets_over_breaded_variant():
    raw = [
        RawPublixDeal(
            title="Just Bare Lightly Breaded Chicken Breast",
            savings="BOGO",
            description="Bites, Strips, or Fillets; Sold Frozen, 19.2 or 24-oz pkg.",
            department="Frozen Meat",
            valid_from=date(2026, 8, 6),
            valid_to=date(2026, 8, 12),
            recommended_rank=40,
        ),
        RawPublixDeal(
            title="Just Bare Chicken Breast Fillets",
            savings="BOGO · Save up to $8.50",
            description="Boneless, Skinless, 18 or 20-oz pkg.",
            department="Meat",
            valid_from=date(2026, 8, 6),
            valid_to=date(2026, 8, 12),
            recommended_rank=1,
        ),
    ]
    deals = map_to_deals(raw)
    chicken = next(d for d in deals if d.item == "chicken breast")
    assert "Fillets" in chicken.description
    assert "19.2 or 24-oz" not in chicken.description
    assert "Save up to $8.50" in chicken.price


def test_deals_for_zip_end_to_end():
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        store, deals = deals_for_zip("32081")
    assert store.number == "01243"
    assert {d.item for d in deals} >= {"strawberries", "chicken breast", "ground beef"}
    chicken = next(d for d in deals if d.item == "chicken breast")
    assert "Fillets" in chicken.description
    assert "Save up to $8.50" in chicken.price


def test_refresh_feed_writes_publix_and_preserves_other_stores(isolated_home, monkeypatch):
    feed = isolated_home / "deals.json"
    feed.write_text(json.dumps({"deals": [
        {"store": "kroger", "item": "milk", "price": "$1.49",
         "valid_from": "2026-08-01", "valid_to": "2026-08-31"},
        {"store": "publix", "item": "bananas", "price": "$0.10",
         "valid_from": "2026-01-01", "valid_to": "2026-01-07"},
    ]}))
    monkeypatch.setenv("LETTUCEREMIND_DEALS", str(feed))
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        store, deals, path = refresh_feed("32081")
    assert path == feed
    assert store.number == "01243"
    data = json.loads(feed.read_text(encoding="utf-8"))
    stores = [d["store"] for d in data["deals"]]
    assert "kroger" in stores
    assert stores.count("publix") == len(deals)
    assert "bananas" not in [d["item"] for d in data["deals"] if d["store"] == "publix"]

    # Live Publix feed replaces the built-in Publix snapshot.
    shown = current_deals(today=date(2026, 8, 9), stores=["publix", "kroger"])
    assert {d.store for d in shown} == {"publix", "kroger"}
    assert all(d.source == "publix" for d in shown if d.store == "publix")
    assert any(d.item == "milk" and d.source == "custom" for d in shown)


def test_cli_zip_lists_live_publix(capsys):
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        rc = main(["deals", "--zip", "32081"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nocatee Town Center" in out
    assert "strawberries" in out
    assert "chicken breast" in out


def test_cli_refresh_saves_feed(isolated_home, monkeypatch, capsys):
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(isolated_home))
    with patch("lettuceremind.publix._get_json", side_effect=_fake_get_json):
        rc = main(["deals", "--refresh"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "refreshed Publix from ZIP 32081" in out
    feed = isolated_home / "deals.json"
    assert feed.exists()
    data = json.loads(feed.read_text(encoding="utf-8"))
    assert data["store_number"] == "01243"
    assert any(d["item"] == "strawberries" for d in data["deals"])
