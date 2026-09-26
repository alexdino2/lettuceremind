"""LettuceRemind command-line interface."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from datetime import date, timedelta
from typing import Optional

from lettuceremind import __version__, auth, instacart
from lettuceremind.deals import STORES, current_deals, match_pantry, resolve_store
from lettuceremind.models import PantryItem
from lettuceremind.publix import DEFAULT_ZIP, refresh_feed
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


def cmd_deals(args: argparse.Namespace) -> int:
    today = args.date or date.today()
    stores: Optional[list[str]] = None
    if args.store_name:
        try:
            stores = [resolve_store(args.store_name)]
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    live_note = ""
    if args.refresh or args.zip:
        zip_code = args.zip or DEFAULT_ZIP
        if stores is not None and stores != ["publix"]:
            print("error: --zip/--refresh only applies to Publix deals",
                  file=sys.stderr)
            return 1
        stores = ["publix"]
        try:
            # Always persist — the web Deals page reads deals.json, so a
            # print-only --zip left the UI stuck on the built-in snapshot.
            store, live_deals, path = refresh_feed(zip_code)
            live_note = (
                f"  refreshed Publix from ZIP {zip_code} → "
                f"{store.name} #{store.number}\n"
                f"  wrote {len(live_deals)} deal(s) to {path}\n"
            )
        except (ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    deals = current_deals(today=today, stores=stores)
    matches = match_pantry(deals, PantryStore(args.store).all())
    if args.pantry:
        deals = [d for d in deals if d in matches]
        if not deals:
            print("No current deals match what's in your pantry.")
            return 0

    names = ", ".join(STORES[key] for key in (stores or STORES))
    print(f"💸 Local deals for {today.isoformat()} — {names}\n")
    if live_note:
        print(live_note)
    for key in stores or list(STORES):
        group = [d for d in deals if d.store == key]
        if not group:
            continue
        print(f"  {STORES[key]}")
        labels = {d: d.item + (f" — {d.description}" if d.description else "")
                  for d in group}
        width = max(len(label) for label in labels.values())
        price_width = max(len(d.price) for d in group)
        for deal in group:
            reg = f"  (reg {deal.regular_price})" if deal.regular_price else ""
            note = ""
            pantry_item = matches.get(deal)
            if pantry_item is not None:
                days = pantry_item.days_left(today)
                if days < 0:
                    note = "  ← in your pantry (expired — replace it)"
                elif days == 0:
                    note = "  ← in your pantry, expires TODAY — restock"
                elif days <= 3:
                    note = f"  ← in your pantry, expires in {days}d — restock"
                else:
                    note = "  ← in your pantry"
            tag = ""
            if deal.source == "custom":
                tag = "  [custom feed]"
            elif deal.source == "publix":
                tag = "  [publix weekly ad]"
            print(f"    {labels[deal]:<{width}}  {deal.price:<{price_width}}{reg}"
                  f"  thru {deal.valid_to.strftime('%b %d')}{note}{tag}")
        print()
    if any(d.source == "builtin" and d.store == "publix" for d in deals):
        print("  (built-in Publix catalog is a ZIP 32081 snapshot — refresh "
              "with `lettuceremind deals --zip 32081 --refresh`)")
    elif any(d.source == "builtin" for d in deals):
        print("  (built-in sample circulars; plug in a live feed via "
              "~/.lettuceremind/deals.json — see README)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from lettuceremind.web.server import create_server, lan_ip

    api_key = None if args.no_key else secrets.token_urlsafe(6)
    try:
        httpd, _app = create_server(
            args.host, args.port, store_path=args.store,
            api_key=api_key, certfile=args.certfile, keyfile=args.keyfile,
        )
    except OSError as exc:
        print(f"error: could not start server: {exc}", file=sys.stderr)
        return 1

    scheme = "https" if args.certfile else "http"
    host = lan_ip() if args.host in ("", "0.0.0.0", "::") else args.host
    url = f"{scheme}://{host}:{httpd.server_address[1]}/"
    if api_key:
        url += f"?key={api_key}"
    username = auth.current_user()
    pantry = PantryStore(args.store).path

    print("🥬 LettuceRemind Pantry Scanner")
    print(f"   Open this on your iPhone (same Wi-Fi):\n\n   {url}\n")
    print(f"   pantry: {pantry}" + (f"  (user: {username})" if username else ""))
    if not args.certfile:
        print("   Live viewfinder mode needs HTTPS (--certfile/--keyfile); "
              "over plain HTTP the app\n   uses tap-to-snap photos via the "
              "native camera, which works everywhere.")
    print("   Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


def _read_password(args: argparse.Namespace, confirm: bool) -> str:
    if args.password:
        return args.password
    password = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Confirm password: ") != password:
        raise auth.AuthError("passwords do not match")
    return password


def cmd_register(args: argparse.Namespace) -> int:
    username = auth.register(args.username, _read_password(args, confirm=True))
    print(f"👤 Registered and logged in as {username}.")
    print(f"Your pantry now lives at {auth.user_pantry_path(username)} — "
          f"scan, add, list, expiring and deals all use it while you're logged in.")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    username = auth.login(args.username, _read_password(args, confirm=False))
    print(f"👤 Logged in as {username}. All commands now use your pantry "
          f"({auth.user_pantry_path(username)}).")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    username = auth.logout()
    if username:
        print(f"Logged out {username}. Back to the shared pantry.")
    else:
        print("Not logged in.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    username = auth.current_user()
    path = PantryStore(args.store).path
    if username:
        print(f"👤 Logged in as {username}\n   pantry: {path}")
    else:
        print(f"Not logged in — using the shared pantry.\n   pantry: {path}")
    return 0


def cmd_instacart_connect(args: argparse.Namespace) -> int:
    username = instacart.require_user()  # fail before prompting for a key
    api_key = args.api_key or os.environ.get("INSTACART_API_KEY") or ""
    if not api_key:
        api_key = getpass.getpass("Instacart API key: ")
    conn = instacart.connect(
        api_key,
        username=username,
        environment=instacart.DEVELOPMENT if args.dev else instacart.PRODUCTION,
        postal_code=args.zip,
        retailer=args.retailer,
        verify=not args.no_verify,
    )
    dev = " (development)" if conn.environment == instacart.DEVELOPMENT else ""
    print(f"🛒 Connected {username} to Instacart{dev} — key {conn.masked_key}.")
    if conn.retailer_name:
        print(f"   Preferred store: {conn.retailer_name}")
    if conn.postal_code:
        print(f"   ZIP: {conn.postal_code}")
    print("   Run `lettuceremind instacart shop` to restock what's expiring.")
    return 0


def cmd_instacart_disconnect(args: argparse.Namespace) -> int:
    username = instacart.require_user()
    if instacart.disconnect(username):
        print(f"Disconnected {username} from Instacart; the API key was deleted.")
    else:
        print(f"{username} isn't connected to Instacart.")
    return 0


def cmd_instacart_status(args: argparse.Namespace) -> int:
    username = auth.current_user()
    if username is None:
        print("Not logged in — log in to connect your account to Instacart.")
        return 0
    conn = instacart.get_connection(username)
    if conn is None:
        print(f"{username} isn't connected to Instacart. "
              f"Run `lettuceremind instacart connect`.")
        return 0
    print(f"🛒 {username} is connected to Instacart")
    print(f"   key: {conn.masked_key}  ({conn.environment})")
    print(f"   store: {conn.retailer_name or 'choose at checkout'}")
    if conn.postal_code:
        print(f"   ZIP: {conn.postal_code}")
    if conn.connected_on:
        print(f"   since: {conn.connected_on}")
    return 0


def cmd_instacart_stores(args: argparse.Namespace) -> int:
    conn = instacart.require_connection()
    zip_code = args.zip or conn.postal_code or DEFAULT_ZIP
    client = instacart.InstacartClient(conn.api_key, conn.environment)
    retailers = client.list_retailers(zip_code)
    if not retailers:
        print(f"No Instacart stores deliver to ZIP {zip_code}.")
        return 0
    print(f"🛒 Instacart stores near {zip_code}:\n")
    width = max(len(r.name) for r in retailers)
    for r in retailers:
        mark = "  ← preferred" if r.key == conn.retailer_key else ""
        print(f"  {r.name:<{width}}  {r.key}{mark}")
    print("\nSet a preferred store with "
          "`lettuceremind instacart connect --zip ZIP --retailer NAME`.")
    return 0


def cmd_instacart_shop(args: argparse.Namespace) -> int:
    conn = instacart.require_connection()
    if args.items:
        items = instacart.line_items((name, 1) for name in args.items)
    else:
        items = instacart.restock_items(PantryStore(args.store).all(),
                                        within_days=args.days)
        if not items:
            print(f"Nothing expires in the next {args.days} day(s) — "
                  f"name items to shop for, e.g. "
                  f"`lettuceremind instacart shop milk eggs`.")
            return 0
    url = instacart.create_shopping_list(conn, items, title=args.title)
    print(f"🛒 Instacart shopping list ({len(items)} item(s)):\n")
    for item in items:
        qty = f" x{item['quantity']}" if item["quantity"] > 1 else ""
        print(f"  {item['name']}{qty}")
    print(f"\nOpen it to pick a store and check out:\n  {url}")
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

    p_deals = sub.add_parser(
        "deals",
        help="this week's grocery deals (Publix, Kroger, Whole Foods, Aldi, Costco)")
    p_deals.add_argument("store_name", nargs="?", metavar="STORE",
                         help="only one store, e.g. publix, kroger, "
                              '"whole foods", aldi, costco')
    p_deals.add_argument("--pantry", action="store_true",
                         help="only deals on items currently in your pantry")
    p_deals.add_argument("--date", type=_parse_date, default=None,
                         help="show deals as of DATE (default: today)")
    p_deals.add_argument(
        "--zip", metavar="ZIP", default=None,
        help="pull live Publix weekly ad near ZIP into ~/.lettuceremind/deals.json "
             f"(default with --refresh: {DEFAULT_ZIP})")
    p_deals.add_argument(
        "--refresh", action="store_true",
        help="same as --zip (uses ZIP 32081 when --zip is omitted)")
    p_deals.set_defaults(func=cmd_deals)

    p_serve = sub.add_parser(
        "serve",
        help="host the mobile pantry-scanner web app — scan your pantry "
             "with your iPhone camera")
    p_serve.add_argument("--host", default="0.0.0.0",
                         help="address to bind (default: all interfaces)")
    p_serve.add_argument("--port", type=int, default=8043,
                         help="port to listen on (default: 8043)")
    p_serve.add_argument("--no-key", action="store_true",
                         help="disable the access key (anyone on the network "
                              "can then edit your pantry)")
    p_serve.add_argument("--certfile", default=None,
                         help="TLS certificate (PEM) — enables HTTPS, which "
                              "unlocks the live viewfinder on iPhone")
    p_serve.add_argument("--keyfile", default=None,
                         help="TLS private key (PEM), if separate from "
                              "--certfile")
    p_serve.set_defaults(func=cmd_serve)

    p_reg = sub.add_parser("register", help="create an account (and log in)")
    p_reg.add_argument("username")
    p_reg.add_argument("--password", default=None,
                       help="password (omit to be prompted securely)")
    p_reg.set_defaults(func=cmd_register)

    p_login = sub.add_parser("login", help="log in — every command then uses "
                                           "your own pantry")
    p_login.add_argument("username")
    p_login.add_argument("--password", default=None,
                         help="password (omit to be prompted securely)")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="log out (back to the shared pantry)")
    p_logout.set_defaults(func=cmd_logout)

    p_who = sub.add_parser("whoami", help="show the active account and pantry")
    p_who.set_defaults(func=cmd_whoami)

    p_ic = sub.add_parser(
        "instacart",
        help="connect your account to Instacart and restock from your pantry")
    ic_sub = p_ic.add_subparsers(dest="instacart_command", required=True)

    p_ic_conn = ic_sub.add_parser(
        "connect", help="link an Instacart Developer Platform API key to your account")
    p_ic_conn.add_argument("--api-key", default=None,
                           help="API key (default: $INSTACART_API_KEY, else "
                                "prompted securely)")
    p_ic_conn.add_argument("--zip", default=None, help="your delivery ZIP code")
    p_ic_conn.add_argument("--retailer", metavar="STORE", default=None,
                           help="preferred store name or retailer key "
                                "(needs --zip)")
    p_ic_conn.add_argument("--dev", action="store_true",
                           help="the key is an Instacart development key")
    p_ic_conn.add_argument("--no-verify", action="store_true",
                           help="save the key without checking it with Instacart")
    p_ic_conn.set_defaults(func=cmd_instacart_connect)

    p_ic_disc = ic_sub.add_parser("disconnect",
                                  help="unlink Instacart and delete the stored key")
    p_ic_disc.set_defaults(func=cmd_instacart_disconnect)

    p_ic_stat = ic_sub.add_parser("status", help="show your Instacart connection")
    p_ic_stat.set_defaults(func=cmd_instacart_status)

    p_ic_stores = ic_sub.add_parser("stores",
                                    help="Instacart stores that deliver to a ZIP")
    p_ic_stores.add_argument("--zip", default=None,
                             help="ZIP code (default: your connected ZIP)")
    p_ic_stores.set_defaults(func=cmd_instacart_stores)

    p_ic_shop = ic_sub.add_parser(
        "shop", help="create an Instacart shopping list — by default, "
                     "everything expiring soon")
    p_ic_shop.add_argument("items", nargs="*", metavar="ITEM",
                           help="items to shop for instead of expiring ones")
    p_ic_shop.add_argument("--days", type=int, default=3,
                           help="restock items expiring within DAYS (default: 3)")
    p_ic_shop.add_argument("--title", default="LettuceRemind restock",
                           help="shopping-list title")
    p_ic_shop.set_defaults(func=cmd_instacart_shop)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (auth.AuthError, instacart.InstacartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Output piped to a closed reader (e.g. `lettuceremind list | head`).
        return 0


if __name__ == "__main__":
    sys.exit(main())
