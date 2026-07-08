from datetime import date, timedelta
from pathlib import Path

import pytest

from lettuceremind.receipt.scanner import ReceiptScanner, scan_receipt_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_walmart_receipt_end_to_end():
    result = ReceiptScanner().scan_file(FIXTURES / "walmart.txt",
                                        purchase_date=date(2026, 5, 14))
    names = {i.name for i in result.items}
    assert {"chicken breast", "milk", "eggs", "bread", "bananas", "spinach",
            "tomatoes", "shredded cheese", "ground beef", "peanut butter",
            "frozen pizza", "sparkling water", "avocado",
            "strawberries"} <= names
    assert len(result.items) == 14  # slogans/headers must not leak in
    assert result.match_rate == 1.0
    water = next(i for i in result.items if i.name == "sparkling water")
    assert water.quantity == 2
    beef = next(i for i in result.items if i.name == "ground beef")
    assert beef.expires_on == date(2026, 5, 14) + timedelta(days=2)


def test_kroger_receipt_end_to_end():
    result = ReceiptScanner().scan_file(FIXTURES / "kroger.txt",
                                        purchase_date=date(2026, 5, 2))
    names = {i.name for i in result.items}
    assert {"milk", "yogurt", "rotisserie chicken", "carrots", "potatoes",
            "onions", "provolone cheese", "heavy cream", "sourdough bread",
            "frozen vegetables", "canned soup", "orange juice", "tilapia",
            "apples", "kombucha"} <= names
    assert result.match_rate == 1.0
    apples = next(i for i in result.items if i.name == "apples")
    assert apples.quantity == 2


def test_scan_text_defaults_to_today():
    result = scan_receipt_text("MILK 3.29\n")
    assert result.items[0].expires_on > date.today()


def test_empty_receipt():
    result = scan_receipt_text("")
    assert result.items == []
    assert result.match_rate == 1.0


def test_unknown_item_still_gets_reminder():
    result = scan_receipt_text("ZBLORF QUUX 4.99\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert not item.matched
    assert item.shelf_life_days > 0
    assert item.expires_on > result.purchase_date


def test_unsupported_format_raises():
    with pytest.raises(ValueError):
        ReceiptScanner().scan_file("receipt.docx")
