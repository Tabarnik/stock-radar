#!/usr/bin/env python3
"""
Backfill docs/history.json with the picks the radar made before it kept a
history file of its own.

Sources, in order of confidence:
  * git history of docs/data.json  — exact pick lists, from 2026-07-31 onward
  * Actions run logs               — the digests sent on 2026-06-22 and 07-29,
                                     transcribed in backtest.py's FLAGS
Where a day's pick list could not be recovered it is left out rather than
reconstructed: the radar's filter is deterministic, but re-deriving a list from
a partially-visible log would put entries in the record that were never sent.

Idempotent — merges on (date, sym) and never overwrites a live entry.
"""
import json
import os
import sys

import yfinance as yf

OUT = os.path.join(os.getenv("DASHBOARD_DIR", "docs"), "history.json")

# Runs skipped by the old ET gate scanned nothing — the gate returned before
# gather(), so those logs contain only "[skip] ET hour ...". There is no hidden
# data to recover from them.
#
# SIMULATED covers the other gap: on the days a digest DID send before the pick
# section existed, the buzz list was recorded with price, % move and momentum
# arrow. The names below were judged BY HAND against the pick rule (momentum
# rising, not already +20%, not already -15%) and hardcoded — the rule is not
# re-run here, and the per-ticker momentum arrows it needs are no longer
# recoverable, so this list cannot be regenerated or checked. Treat it as an
# assertion, not a derivation. Marked simulated everywhere it appears: these
# were never sent, and treating them as a track record would be a lie.
#
# Jun 16, Jun 18 and Jun 29 produced no qualifying name from the recoverable
# part of their lists, so they have no entry rather than a guessed one.
SIMULATED = {
    "2026-06-15": [("CTM", 0.68), ("CXAI", 0.20), ("RS", 408.28)],
    "2026-06-17": [("OTLK", 1.67), ("CXAI", 0.21), ("CRVO", 3.82), ("RS", 408.79)],
    "2026-06-22": [("CXAI", 0.23)],
    "2026-07-06": [("CXAI", 0.16)],
    "2026-07-30": [("SPY", 736.05), ("QQQ", 679.17), ("NVDA", 194.89),
                   ("TSLA", 304.11), ("META", 534.22), ("AAPL", 331.58)],
}

# date -> [(symbol, price at the first flag of that day)]
BACKFILL = {
    # "Worth a Closer Look" — the only explicit pick of the June era
    "2026-06-22": [("SLS", 8.55)],
    # first digest carrying a Worth Watching section
    "2026-07-29": [("MU", 739.00), ("MSFT", 390.54), ("SNDK", 1015.89),
                   ("SPY", 729.46), ("NVDA", 190.01), ("NOK", 8.41),
                   ("T", 23.94), ("SKHY", 126.79)],
    # 2026-07-30: the pick list sits above the recoverable part of the log —
    # deliberately omitted rather than inferred.
    "2026-07-31": [("AAPL", 303.33), ("RDDT", 137.17), ("AMZN", 269.81),
                   ("NOK", 9.095), ("NVDA", 196.22)],
    "2026-08-01": [("RDDT", 140.67), ("AAPL", 308.91), ("NVDA", 200.75),
                   ("NOK", 9.14), ("IREN", 36.80)],
}


def last_price(sym):
    try:
        fi = yf.Ticker(sym).fast_info
        for k in ("last_price", "lastPrice"):
            try:
                v = fi[k]
            except Exception:
                v = getattr(fi, k, None)
            if v:
                return float(v)
    except Exception as e:
        print(f"[warn] price {sym}: {e}")
    return None


def outcome(pct):
    return "worked" if pct >= 10 else "faded" if pct <= -10 else "flat"


def main():
    try:
        with open(OUT) as f:
            hist = json.load(f)
    except Exception:
        hist = []

    have = {(e.get("date"), e.get("sym")) for e in hist}
    sources = [(BACKFILL, False), (SIMULATED, True)]
    syms = sorted({s for src, _ in sources for day in src.values() for s, _ in day})
    print(f"pricing {len(syms)} tickers…")
    now = {s: last_price(s) for s in syms}

    added = 0
    for src, simulated in sources:
        for date, picks in sorted(src.items()):
            for sym, entry in picks:
                if (date, sym) in have:
                    continue
                have.add((date, sym))
                cur = now.get(sym) or entry
                pct = (cur - entry) / entry * 100 if entry else 0.0
                hist.append({
                    "date": date, "sym": sym, "name": "",
                    "flag_price": entry, "last_price": round(cur, 2),
                    "pct": round(pct, 2), "outcome": outcome(pct),
                    "simulated": simulated,
                    "note": ("judged by hand to fit the rule — never sent, not re-derived"
                             if simulated else "from the sent digest"),
                })
                added += 1

    hist.sort(key=lambda e: (e["date"], e["sym"]), reverse=True)
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(hist, f, indent=1)

    days = sorted({e["date"] for e in hist})
    print(f"added {added} entries; history now covers {len(days)} days: "
          f"{days[0]} … {days[-1]} ({len(hist)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
