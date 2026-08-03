#!/usr/bin/env python3
"""
Record what was actually bought and sold.

The dashboard's "Model portfolio" assumes every pick was bought the moment it
appeared. That is the rule's account, not a real one — miss a run and the two
diverge. docs/trades.json is the real record, and this is how it gets written.

    ./trade.py buy  MU  --price 739.00 --amount 540
    ./trade.py buy  MU  --price 739.00 --shares 0.73 --date 2026-07-29
    ./trade.py sell MU  --price 823.03
    ./trade.py list
    ./trade.py rm   3                       # undo a mistyped entry by index

Nothing here fetches a price: the price you paid is the one that matters, not
the close. Pass it. Prices and P/L on the board are marked from live data at
run time, so this file only ever holds what actually happened.

Commit and push after editing — the board reads the committed file:

    git add docs/trades.json && git commit -m "trade: bought MU" && git push
"""
import argparse
import json
import os
import sys
from datetime import date

TRADES = os.path.join(os.getenv("DASHBOARD_DIR", "docs"), "trades.json")


def load():
    try:
        with open(TRADES) as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        sys.exit(f"{TRADES} is not valid JSON ({e}). Fix it by hand before writing.")


def save(rows):
    os.makedirs(os.path.dirname(TRADES) or ".", exist_ok=True)
    with open(TRADES, "w") as f:
        json.dump(rows, f, indent=1)
        f.write("\n")


def _open_rows(rows, sym):
    return [r for r in rows if r["sym"] == sym and not r.get("sell_price")]


def cmd_buy(a):
    rows = load()
    sym = a.sym.upper().lstrip("$")
    if not a.shares and not a.amount:
        sys.exit("give --shares or --amount")
    shares = a.shares if a.shares else a.amount / a.price
    if _open_rows(rows, sym) and not a.again:
        sys.exit(f"{sym} is already open. Sell it first, or pass --again to "
                 f"deliberately hold two lots.")
    rows.append({"sym": sym, "buy_date": a.date, "buy_price": a.price,
                 "shares": round(shares, 6), "sell_date": None,
                 "sell_price": None, "note": a.note or ""})
    save(rows)
    print(f"bought {shares:.4f} {sym} @ ${a.price:,.2f} "
          f"= ${shares * a.price:,.2f} on {a.date}")


def cmd_sell(a):
    rows = load()
    sym = a.sym.upper().lstrip("$")
    live = _open_rows(rows, sym)
    if not live:
        sys.exit(f"no open position in {sym}")
    if len(live) > 1 and a.index is None:
        for i, r in enumerate(rows):
            if r in live:
                print(f"  [{i}] bought {r['buy_date']} @ ${r['buy_price']}")
        sys.exit(f"{len(live)} open lots in {sym} — pass --index to pick one")
    r = rows[a.index] if a.index is not None else live[0]
    r["sell_date"], r["sell_price"] = a.date, a.price
    save(rows)
    pnl = (a.price - r["buy_price"]) * r["shares"]
    pct = (a.price - r["buy_price"]) / r["buy_price"] * 100
    print(f"sold {r['shares']:.4f} {sym} @ ${a.price:,.2f}  "
          f"P/L ${pnl:+,.2f} ({pct:+.1f}%)")


def cmd_list(a):
    rows = load()
    if not rows:
        print("no trades recorded yet")
        return
    print(f"{'#':>3}  {'sym':<6}{'bought':<12}{'at':>10}{'shares':>10}"
          f"{'cost':>10}  status")
    for i, r in enumerate(rows):
        cost = r["buy_price"] * r["shares"]
        status = (f"sold {r['sell_date']} @ ${r['sell_price']:,.2f}"
                  if r.get("sell_price") else "OPEN")
        print(f"{i:>3}  {r['sym']:<6}{r['buy_date']:<12}{r['buy_price']:>10,.2f}"
              f"{r['shares']:>10.4f}{cost:>10,.2f}  {status}")


def cmd_rm(a):
    rows = load()
    if not 0 <= a.index < len(rows):
        sys.exit(f"index {a.index} out of range (0..{len(rows)-1})")
    gone = rows.pop(a.index)
    save(rows)
    print(f"removed {gone['sym']} bought {gone['buy_date']}")


def main():
    today = date.today().isoformat()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buy")
    b.add_argument("sym")
    b.add_argument("--price", type=float, required=True, help="what you paid per share")
    b.add_argument("--shares", type=float)
    b.add_argument("--amount", type=float, help="dollars in; shares derived from price")
    b.add_argument("--date", default=today)
    b.add_argument("--note", default="")
    b.add_argument("--again", action="store_true", help="allow a second open lot")
    b.set_defaults(fn=cmd_buy)

    s = sub.add_parser("sell")
    s.add_argument("sym")
    s.add_argument("--price", type=float, required=True)
    s.add_argument("--date", default=today)
    s.add_argument("--index", type=int, help="which lot, when more than one is open")
    s.set_defaults(fn=cmd_sell)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    r = sub.add_parser("rm")
    r.add_argument("index", type=int)
    r.set_defaults(fn=cmd_rm)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
