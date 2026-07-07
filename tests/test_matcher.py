from lettuceremind.receipt.matcher import FoodMatcher


def matcher() -> FoodMatcher:
    return FoodMatcher()


def test_exact_match():
    m = matcher().match("bananas")
    assert m.food.name == "bananas"
    assert m.method == "exact"
    assert m.confidence == 1.0


def test_alias_match():
    m = matcher().match("honeycrisp apples")
    assert m.food.name == "apples"


def test_abbreviated_receipt_text():
    m = matcher().match("BNLS SKLS CHKN BRST")
    assert m.food.name == "chicken breast"
    assert m.matched


def test_subset_prefers_most_specific_product():
    m = matcher().match("organic sweet potatoes 3lb")
    assert m.food.name == "sweet potatoes"


def test_fuzzy_handles_typos():
    m = matcher().match("bananna")
    assert m.food.name == "bananas"
    assert m.method in {"fuzzy", "partial", "subset"}


def test_fallback_never_fails():
    m = matcher().match("XQZWKJ FLURBOBLAT 9000")
    assert m.food.category == "other"
    assert m.method == "fallback"
    assert not m.matched
    assert m.food.name  # still has a usable name


def test_empty_input_falls_back():
    m = matcher().match("   ")
    assert m.method == "fallback"


def test_household_items_recognized():
    m = matcher().match("PPR TWLS 6 ROLLS")
    assert m.food.category == "household"
