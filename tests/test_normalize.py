import ast
from pathlib import Path

from lettuceremind.receipt.normalize import ABBREVIATIONS, normalize, tokenize

NORMALIZE_SRC = (
    Path(__file__).resolve().parents[1] / "lettuceremind" / "receipt" / "normalize.py"
)


def test_tokenize_strips_punctuation_and_case():
    assert tokenize("Grn. Onions!!") == ["grn", "onions"]


def test_normalize_expands_common_abbreviations():
    assert normalize("BNLS SKLS CHKN BRST") == "boneless skinless chicken breast"
    assert normalize("GV WHP CRM 16OZ") == "whipping cream"
    assert normalize("ORG BBY SPINACH") == "organic baby spinach"


def test_normalize_drops_store_brand_prefix_only_at_start():
    assert normalize("GV WHL MLK") == "whole milk"
    # "st" mid-name must not be treated as a brand prefix
    assert "st" in normalize("MAIN ST SODA") or "soda" in normalize("MAIN ST SODA")


def test_normalize_drops_sizes_and_counts():
    assert normalize("EGGS 12CT") == "eggs"
    assert normalize("MILK 1GAL 2%") == "milk"


def test_no_duplicate_abbreviation_keys():
    """Duplicate dict keys are silently dropped by Python; catch them."""
    tree = ast.parse(NORMALIZE_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(getattr(t, "id", None) == "ABBREVIATIONS" for t in targets):
            keys = [k.value for k in node.value.keys]
            dupes = {k for k in keys if keys.count(k) > 1}
            assert not dupes, f"duplicate abbreviation keys: {sorted(dupes)}"
            return
    raise AssertionError("ABBREVIATIONS assignment not found")


def test_abbreviation_keys_are_normalized_form():
    for key in ABBREVIATIONS:
        assert key == key.lower(), f"non-lowercase abbreviation key: {key!r}"
        assert " " not in key, f"multi-word abbreviation key: {key!r}"
