# 🥬 LettuceRemind

**Lettuce remind you before your food goes bad.**

LettuceRemind is a grocery expiration tracker with a receipt scanner at its
heart: feed it a grocery receipt and it figures out **what you bought**, **how
long each item keeps**, and **reminds you before anything expires** — so the
spinach gets eaten instead of composted.

```
$ lettuceremind scan receipt.txt
🥬 Found 14 item(s) (match rate 100.0%):

  chicken breast   [fresh poultry]     expires 2026-07-07
  milk             [milk & cream]      expires 2026-07-12
  bananas          [fresh fruit]       expires 2026-07-12
  spinach          [leafy greens]      expires 2026-07-10
  ground beef      [fresh meat]        expires 2026-07-07
  ...

Saved 14 item(s) to your pantry. Run `lettuceremind expiring` to see what's due.

$ lettuceremind expiring
🥬 5 item(s) need attention:

  ⚠️  chicken breast expires TODAY
  ⚠️  ground beef expires TODAY
  ⏰ avocado expires in 2 days
  ⏰ spinach expires in 3 days
  ⏰ strawberries expires in 3 days
```

## Why receipt scanning is hard (and how this works)

Receipts don't say "boneless skinless chicken breast" — they say
`BNLS SKLS CHKN BRST 007874201234 F 7.42`. LettuceRemind decodes that with a
layered pipeline, where every layer is a safety net for the one above:

1. **Parse** — item lines are separated from store headers, addresses,
   subtotals, tax lines, payment info, barcodes, and loyalty-card noise.
   Quantity markers (`2 X`, `QTY 3`, `2 @ 1.76`) and weighed-produce
   continuation lines (`1.94 lb @ 0.64/lb`) are understood.
2. **Normalize** — 400+ receipt abbreviations used by major US chains are
   expanded (`CHKN → chicken`, `WHP CRM → whipping cream`), and store-brand
   prefixes (`GV`, `KRO`, `KS`, …), sizes (`12OZ`, `1GAL`), and marketing
   filler are stripped.
3. **Match** — the cleaned text is resolved against a database of 200+
   products and 500+ aliases with USDA-FoodKeeper-based shelf lives:
   exact match → word-overlap match → fuzzy match (typos and OCR errors) →
   a guaranteed fallback.
4. **Never lose an item** — text that matches nothing still becomes a
   tracked pantry item with a conservative shelf life, flagged as
   unrecognized, so you always get a reminder.

**Accuracy is enforced by tests**: the suite generates a 3,700+ line corpus
covering every product and alias in receipt formats (prices, tax flags,
store-brand prefixes, size tokens, quantity markers, abbreviations) and
asserts **≥ 99.9% recognition** — currently at 100%, plus 100% on an
OCR-typo corpus and a 100% no-item-lost coverage guarantee.

## Install

```bash
pip install .            # core (no dependencies, pure stdlib)
pip install .[ocr]       # + image OCR via tesseract (optional)
pip install .[dev]       # + pytest
```

## Usage

```bash
# Scan a receipt — .txt, an image (with the ocr extra), or stdin
lettuceremind scan receipt.txt
lettuceremind scan receipt.png
pbpaste | lettuceremind scan -

# Preview without saving; backdate a purchase
lettuceremind scan receipt.txt --dry-run
lettuceremind scan receipt.txt --date 2026-07-01

# Manage the pantry
lettuceremind add "greek yogurt"
lettuceremind add leftovers --days 4
lettuceremind list
lettuceremind expiring            # next 3 days (default)
lettuceremind expiring --days 7
lettuceremind remove "greek yogurt"
lettuceremind clear
```

The pantry lives in `~/.lettuceremind/pantry.json` (override with `--store`
or `$LETTUCEREMIND_STORE`).

### As a library

```python
from lettuceremind import scan_receipt_text

result = scan_receipt_text(open("receipt.txt").read())
for item in result.items:
    print(item.name, item.category, item.expires_on, item.confidence)
```

## Development

```bash
pip install -e .[dev]
pytest
```

Key modules:

| Module | Role |
|---|---|
| `lettuceremind/receipt/parser.py` | receipt text → item lines |
| `lettuceremind/receipt/normalize.py` | abbreviation expansion & cleanup |
| `lettuceremind/receipt/matcher.py` | staged product matching |
| `lettuceremind/shelf_life.py` | product & shelf-life database |
| `lettuceremind/store.py` | JSON pantry persistence |
| `lettuceremind/reminders.py` | expiration reminders |
| `lettuceremind/cli.py` | command-line interface |

`tests/test_accuracy.py` holds the recognition-accuracy guarantee; if you
add products or abbreviations, it automatically folds them into the corpus.

## License

See [LICENSE](LICENSE).
