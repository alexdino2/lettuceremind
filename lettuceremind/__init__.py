"""LettuceRemind — never let your groceries go bad again.

Scan a grocery receipt, and LettuceRemind figures out what you bought,
how long each item keeps, and reminds you before anything expires.
It also tracks local grocery deals (Publix, Kroger, Whole Foods, Costco)
and supports per-user accounts with their own pantries.
"""

__version__ = "1.1.0"

from lettuceremind.deals import Deal, current_deals
from lettuceremind.models import FoodInfo, PantryItem, ScannedItem, ScanResult
from lettuceremind.receipt.scanner import ReceiptScanner, scan_receipt_text

__all__ = [
    "Deal",
    "FoodInfo",
    "PantryItem",
    "ScannedItem",
    "ScanResult",
    "ReceiptScanner",
    "current_deals",
    "scan_receipt_text",
    "__version__",
]
