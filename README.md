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
or `$LETTUCEREMIND_STORE`). When you're logged in (see
[Accounts](#-accounts-login--registration)), it lives in your own
per-user file instead.

## 📱 Pantry Scanner (mobile web app)

Already-bought groceries with no receipt? Walk your pantry with your
iPhone instead. `lettuceremind serve` hosts a phone-friendly web app on
your own Wi-Fi — point the camera at shelves and labels, and every
product it recognizes is **added to your inventory as you scan**, with
an undo button and a live pantry view:

```bash
pip install lettuceremind[ocr]     # camera frames are OCR'd server-side
lettuceremind serve

🥬 LettuceRemind Pantry Scanner
   Open this on your iPhone (same Wi-Fi):

   http://192.168.1.23:8043/?key=kPz3vQx9
```

Open that URL in Safari and scan away. How it works:

- Each camera frame is sent to the server, OCR'd, and pushed through the
  same normalize → match pipeline as receipts. Unlike receipt mode
  (which never drops a line), pantry mode is **conservative**: nutrition
  panels, ingredient lists, net-weight lines, and date stamps are
  filtered out, and only high-confidence product matches are added — a
  blurry frame adds nothing rather than junk.
- Re-seeing the same product within ~45 seconds counts as the *same*
  jar, so consecutive frames don't create duplicates — but scanning two
  actual boxes of pasta a minute apart adds two.
- Over plain HTTP, the shutter opens the native iPhone camera
  (tap-to-snap), which works everywhere. With HTTPS
  (`--certfile`/`--keyfile`) you get a **live viewfinder with
  auto-scan** — pan slowly along the shelf and items roll in.
- There's also a type-to-add box, a session feed with undo, and a
  pantry view with per-item days-left badges and remove buttons.

The server protects your pantry with a random access key baked into the
printed URL (disable with `--no-key`), and uses the same pantry
resolution as the CLI — so if you're logged in, scans land in your own
per-user pantry. `--host` and `--port` (default 8043) work as expected.

## 💸 Local deals

LettuceRemind tracks weekly grocery deals at **Publix**, **Kroger**,
**Whole Foods**, and **Costco**, and cross-references them with your pantry —
so you know when something you're about to run out of is on sale:

```bash
lettuceremind deals                  # this week's deals at all four stores
lettuceremind deals publix           # one store (kroger, "whole foods", costco)
lettuceremind deals --pantry         # only deals on items you currently have
```

```
💸 Local deals for 2026-07-08 — Publix, Kroger, Whole Foods, Costco

  Publix
    strawberries — 16 oz   BOGO $4.99   thru Jul 14  ← in your pantry, expires in 2d — restock
    avocado — each         $1.25        (reg $2.00)  thru Jul 14
    ...
```

Grocery circulars rotate Wednesday→Tuesday; Costco's savings run per
calendar month. Because the app is offline and none of these chains offer a
free public deals API, the built-in catalog is **representative sample
circular data** that rotates deterministically each week — the plumbing for
real data is there: drop a JSON feed at `~/.lettuceremind/deals.json` (or
point `$LETTUCEREMIND_DEALS` at one) and it's merged in on top:

```json
{"deals": [{"store": "kroger", "item": "milk", "price": "$1.99",
            "regular_price": "$3.49", "description": "gallon",
            "valid_from": "2026-07-01", "valid_to": "2026-07-08"}]}
```

Expired or malformed entries are skipped; `item` is matched against the
product database so pantry cross-referencing keeps working.

## 👤 Accounts (login & registration)

Accounts are optional, local to your machine, and give **every feature its
own per-user pantry** — scan, add, list, expiring, remove, clear, and deals
all follow the logged-in user:

```bash
lettuceremind register alice         # prompts for a password, logs you in
lettuceremind login alice
lettuceremind whoami                 # who's logged in + which pantry is active
lettuceremind logout                 # back to the shared pantry
```

While logged in, your pantry lives at
`~/.lettuceremind/users/<name>/pantry.json`; without a login the shared
`pantry.json` is used, so existing setups keep working. Passwords are never
stored — only a salted PBKDF2-HMAC-SHA256 hash (200k iterations) — and
logging out revokes the session token, so a stale session file can't
authenticate. `--password` is accepted for scripting; omit it to be
prompted securely.

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
| `lettuceremind/store.py` | JSON pantry persistence (per-user aware) |
| `lettuceremind/reminders.py` | expiration reminders |
| `lettuceremind/deals.py` | local deals: Publix, Kroger, Whole Foods, Costco |
| `lettuceremind/web/recognize.py` | pantry-photo OCR text → confident food matches |
| `lettuceremind/web/server.py` | pantry-scanner web server (`lettuceremind serve`) |
| `lettuceremind/web/static/app.html` | the mobile single-page app |
| `lettuceremind/auth.py` | local accounts: register, login, sessions |
| `lettuceremind/paths.py` | data directory resolution (`$LETTUCEREMIND_HOME`) |
| `lettuceremind/cli.py` | command-line interface |

`tests/test_accuracy.py` holds the recognition-accuracy guarantee; if you
add products or abbreviations, it automatically folds them into the corpus.

## License

See [LICENSE](LICENSE).
