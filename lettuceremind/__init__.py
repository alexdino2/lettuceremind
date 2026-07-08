"""LettuceRemind — never let your groceries go bad again.

Scan a grocery receipt, and LettuceRemind figures out what you bought,
how long each item keeps, and reminds you before anything expires.
"""

__version__ = "1.0.0"

from lettuceremind.models import FoodInfo, PantryItem, ScannedItem, ScanResult
from lettuceremind.receipt.scanner import ReceiptScanner, scan_receipt_text

__all__ = [
    "FoodInfo",
    "PantryItem",
    "ScannedItem",
    "ScanResult",
    "ReceiptScanner",
    "scan_receipt_text",
    "__version__",
]
