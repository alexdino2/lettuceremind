from lettuceremind.receipt.parser import parse_receipt_text


def test_keeps_item_lines_and_strips_prices():
    items, _ = parse_receipt_text("BANANAS 000000004011 F  1.24\nMILK 3.29 F\n")
    assert [i.name_text for i in items] == ["BANANAS", "MILK"]


def test_skips_totals_payment_and_headers():
    text = """WALMART SUPERCENTER
SUBTOTAL 47.63
TAX 1 6.250 % 2.98
TOTAL 50.61
VISA TEND 50.61
CHANGE DUE 0.00
THANK YOU
"""
    items, skipped = parse_receipt_text(text)
    assert items == []
    assert len(skipped) == 7


def test_quantity_prefix():
    items, _ = parse_receipt_text("2 X SPARKLING WATER 1.76\nQTY 3 GALA APPLES 2.37\n")
    assert items[0].quantity == 2
    assert items[0].name_text == "SPARKLING WATER"
    assert items[1].quantity == 3
    assert items[1].name_text == "GALA APPLES"


def test_weight_continuation_line_is_not_an_item():
    text = "BANANAS 1.24\n1.94 lb @ 0.64/lb\n"
    items, skipped = parse_receipt_text(text)
    assert len(items) == 1
    assert "1.94 lb @ 0.64/lb" in skipped


def test_integer_quantity_continuation_applies_to_previous_item():
    text = "SPARKLING WATER 3.52\n2 @ 1.76\n"
    items, _ = parse_receipt_text(text)
    assert items[0].quantity == 2


def test_dates_and_barcodes_are_skipped():
    items, _ = parse_receipt_text("05/14/26 18:22:41\nTC# 8823441290345567\n")
    assert items == []


def test_never_crashes_on_garbage():
    garbage = "\x00\x01??\n***\n----\n$$$$ 9.99\n\n"
    items, skipped = parse_receipt_text(garbage)
    assert isinstance(items, list) and isinstance(skipped, list)
