"""Receipt scanning: turn a receipt (text or image) into recognized items."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Union

from lettuceremind.models import ScannedItem, ScanResult
from lettuceremind.receipt.matcher import FoodMatcher
from lettuceremind.receipt.parser import parse_receipt_text
from lettuceremind.shelf_life import shelf_life_for


class ReceiptScanner:
    """Scans receipts and resolves each line to a food with a shelf life."""

    def __init__(self, matcher: Optional[FoodMatcher] = None):
        self._matcher = matcher or FoodMatcher()

    def scan_text(self, text: str, purchase_date: Optional[date] = None) -> ScanResult:
        """Scan plain receipt text (e.g. OCR output or an emailed receipt)."""
        purchased = purchase_date or date.today()
        parsed, skipped = parse_receipt_text(text)
        result = ScanResult(purchase_date=purchased, skipped_lines=skipped)
        for line in parsed:
            match = self._matcher.match(line.name_text)
            days = shelf_life_for(match.food)
            result.items.append(
                ScannedItem(
                    raw_text=line.raw,
                    name=match.food.name,
                    category=match.food.category,
                    quantity=line.quantity,
                    shelf_life_days=days,
                    expires_on=purchased + timedelta(days=days),
                    confidence=match.confidence,
                    matched=match.matched,
                )
            )
        return result

    def scan_file(self, path: Union[str, Path], purchase_date: Optional[date] = None) -> ScanResult:
        """Scan a receipt file: .txt directly, images via OCR if available."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in {".txt", ".text", ""}:
            return self.scan_text(path.read_text(encoding="utf-8"), purchase_date)
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}:
            return self.scan_text(self._ocr_image(path), purchase_date)
        raise ValueError(
            f"Unsupported receipt format {suffix!r}. "
            "Use a .txt file or an image (.png/.jpg)."
        )

    @staticmethod
    def _ocr_image(path: Path) -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Image OCR needs the optional dependencies. Install them with:\n"
                "    pip install lettuceremind[ocr]\n"
                "(requires the tesseract binary on your system), or pass the "
                "receipt as a .txt file instead."
            ) from exc
        return pytesseract.image_to_string(Image.open(path))


def scan_receipt_text(text: str, purchase_date: Optional[date] = None) -> ScanResult:
    """Convenience one-shot scan of receipt text."""
    return ReceiptScanner().scan_text(text, purchase_date)
