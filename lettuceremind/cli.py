"""LettuceRemind command-line interface."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Optional

from lettuceremind import __version__
from lettuceremind.models import PantryItem
from lettuceremind.receipt.matcher import FoodMatcher
from lettuceremind.receipt.scanner import ReceiptScanner
from lettuceremind.reminders import expiring_soon, format_reminder
from lettuceremind.shelf_life import shelf_life_for
from lettuceremind.store import PantryStore


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from None


def cmd_scan(args: argparse.Namespace) -> int:
    scanner = ReceiptScanner()
    if args.receipt == "-":
        result = scanner.scan_text(sys.stdin.read(), args.date)
    else:
        try:
            result = scanner.scan_file(args.receipt, args.date)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if not result.items:
        print("No items found on this receipt.")
        return 0

    print(f"🥬 Found {len(result.items)} item(s) "
          f"(match rate {result.match_rate:.1%}):\n")
    width = max(len(i.name) for i in result.items)
    for item in result.items:
        qty = f" x{item.quantity}" if item.quantity > 1 else ""
        flag = "" if item.matched else "  (unrecognized — using safe defaults)"
        print(f"  {item.name:<{width}}{qty}  [{item.category}]  "
              f"expires {item.expires_on.isoformat()}{flag}")

    if args.dry_run:
        print("\nDry run — nothing saved.")
        return 0

    store = PantryStore(args.store)
    store.add_all([PantryItem.from_scanned(i, args.date) for i in result.items])
    print(f"\nSaved {len(result.items)} item(s) to your pantry. "
          f"Run `lettuceremind expiring` to see what's due.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    matcher = FoodMatcher()
    match = matcher.match(args.name)
    days = args.days if args.days is not None else shelf_life_for(match.food)
    added = args.date or date.today()
    item = PantryItem(
        name=match.food.name,
        category=match.food.category,
        quantity=args.quantity,
        added_on=added,
        expires_on=added + timedelta(days=days),
    )
    PantryStore(args.store).add(item)
    print(f"Added {item.name} [{item.category}], expires {item.expires_on.isoformat()}.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    items = PantryStore(args.store).all()
    if not items:
        print("Your pantry is empty. Scan a receipt with `lettuceremind scan <file>`.")
        return 0
    print(f"🥬 Pantry ({len(items)} item(s)):\n")
    for item in sorted(items, key=lambda i: i.expires_on):
        qty = f" x{item.quantity}" if item.quantity > 1 else ""
        days = item.days_left()
        status = "expired" if days < 0 else f"{days}d left"
        print(f"  {item.expires_on.isoformat()}  {item.name}{qty} "
              f"[{item.category}] — {status}")
    return 0


def cmd_expiring(args: argparse.Namespace) -> int:
    items = PantryStore(args.store).all()
    due = expiring_soon(items, within_days=args.days)
    if not due:
        print(f"Nothing expires in the next {args.days} day(s). 🎉")
        return 0
    print(f"🥬 {len(due)} item(s) need attention:\n")
    for item in due:
        print(f"  {format_reminder(item)}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    removed = PantryStore(args.store).remove(args.name)
    if removed:
        print(f"Removed {removed} item(s) named {args.name!r}.")
        return 0
    print(f"No pantry items named {args.name!r}.")
    return 1


def cmd_clear(args: argparse.Namespace) -> int:
    count = PantryStore(args.store).clear()
    print(f"Cleared {count} item(s) from your pantry.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lettuceremind",
        description="🥬 LettuceRemind — scan grocery receipts, get reminded "
                    "before food expires.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--store", metavar="PATH", default=None,
        help="pantry file (default: ~/.lettuceremind/pantry.json, "
             "or $LETTUCEREMIND_STORE)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="scan a receipt (.txt, image, or - for stdin)")
    p_scan.add_argument("receipt", help="receipt file, or - to read text from stdin")
    p_scan.add_argument("--date", type=_parse_date, default=None,
                        help="purchase date YYYY-MM-DD (default: today)")
    p_scan.add_argument("--dry-run", action="store_true",
                        help="show recognized items without saving")
    p_scan.set_defaults(func=cmd_scan)

    p_add = sub.add_parser("add", help="add a single item by name")
    p_add.add_argument("name", help='item name, e.g. "greek yogurt"')
    p_add.add_argument("--quantity", "-q", type=int, default=1)
    p_add.add_argument("--days", type=int, default=None,
                       help="override shelf life in days")
    p_add.add_argument("--date", type=_parse_date, default=None,
                       help="purchase date YYYY-MM-DD (default: today)")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list everything in your pantry")
    p_list.set_defaults(func=cmd_list)

    p_exp = sub.add_parser("expiring", help="show items expiring soon")
    p_exp.add_argument("--days", type=int, default=3,
                       help="look-ahead window in days (default: 3)")
    p_exp.set_defaults(func=cmd_expiring)

    p_rm = sub.add_parser("remove", help="remove items by name")
    p_rm.add_argument("name")
    p_rm.set_defaults(func=cmd_remove)

    p_clear = sub.add_parser("clear", help="remove all pantry items")
    p_clear.set_defaults(func=cmd_clear)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # Output piped to a closed reader (e.g. `lettuceremind list | head`).
        return 0


if __name__ == "__main__":
    sys.exit(main())
