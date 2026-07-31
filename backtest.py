#!/usr/bin/env python3
"""
Backtest the radar against its own history.

Every digest the radar ever sent is preserved in its GitHub Actions log,
including the price of each ticker at the moment it was sent. This script
replays those flags against today's prices and answers one question:

    if you had acted on the notifications, where would you be?

FLAGS below were transcribed from the Actions logs of the runs that actually
sent (many scheduled runs were silently skipped by the old ET gate, so the
digest history is far shorter than the run history).

Tiers, because the radar does not treat all sections alike:
  pick  — "Worth a Closer Look" / "Worth Watching". The only sections ever
          framed as something to research. This is the real track record.
  buzz  — "Reddit Buzz". What the digest put in front of you, descriptive.
  mover — "Biggest Market Gainers". Explicitly labelled NOT a prediction;
          included only to show what chasing them would have cost.

Writes docs/backtest.json for the dashboard and prints a report.
"""
import json
import os
from collections import defaultdict

import yfinance as yf

STAKE = float(os.getenv("STAKE", "1000"))   # hypothetical $ per flagged name
OUT = os.path.join(os.getenv("DASHBOARD_DIR", "docs"), "backtest.json")

# date, tier, symbol, price at the moment the notification was sent
FLAGS = [
    # ---- 2026-06-15 15:20 ET (first digest, v0 ranked list — no pick section)
    ("2026-06-15", "buzz", "LFVN", 6.34), ("2026-06-15", "buzz", "CTM", 0.68),
    ("2026-06-15", "buzz", "CXAI", 0.20), ("2026-06-15", "buzz", "SPCE", 3.66),
    ("2026-06-15", "buzz", "RS", 408.28),

    # ---- 2026-06-16 09:02 ET  (buzz list partially truncated in the log)
    ("2026-06-16", "buzz", "SPCE", 3.56), ("2026-06-16", "buzz", "IMCC", 0.26),
    ("2026-06-16", "mover", "XNDU", 13.94), ("2026-06-16", "mover", "WDC", 653.53),
    ("2026-06-16", "mover", "AXTI", 110.74), ("2026-06-16", "mover", "GPGI", 14.57),
    ("2026-06-16", "mover", "WOLF", 49.09),

    # ---- 2026-06-17 12:32 ET  (pick section: "nothing qualifies")
    ("2026-06-17", "buzz", "LFVN", 6.11), ("2026-06-17", "buzz", "OTLK", 1.67),
    ("2026-06-17", "buzz", "CXAI", 0.21), ("2026-06-17", "buzz", "CRVO", 3.82),
    ("2026-06-17", "buzz", "RS", 408.79),
    ("2026-06-17", "mover", "QURE", 47.34), ("2026-06-17", "mover", "BRAI", 10.00),
    ("2026-06-17", "mover", "EOSE", 7.95), ("2026-06-17", "mover", "WOLF", 52.35),
    ("2026-06-17", "mover", "QNT", 63.97),

    # ---- 2026-06-18 12:20 ET  (buzz list partially truncated in the log)
    ("2026-06-18", "buzz", "OTLK", 1.49), ("2026-06-18", "buzz", "CTNT", 1.70),
    ("2026-06-18", "mover", "CHRN", 23.36), ("2026-06-18", "mover", "QS", 7.82),
    ("2026-06-18", "mover", "MRVL", 327.40), ("2026-06-18", "mover", "FRMI", 9.76),
    ("2026-06-18", "mover", "DIOD", 122.71),

    # ---- 2026-06-22 12:55 ET  (the only explicit pick in the whole June era)
    ("2026-06-22", "pick", "SLS", 8.55),
    ("2026-06-22", "buzz", "GETY", 1.25), ("2026-06-22", "buzz", "SUNE", 2.76),
    ("2026-06-22", "buzz", "SLS", 8.55), ("2026-06-22", "buzz", "PR", 18.94),
    ("2026-06-22", "buzz", "CXAI", 0.23),
    ("2026-06-22", "mover", "DFTX", 37.30), ("2026-06-22", "mover", "APGE", 132.65),
    ("2026-06-22", "mover", "BWIN", 24.46), ("2026-06-22", "mover", "ORKA", 87.42),
    ("2026-06-22", "mover", "SMCI", 35.54),

    # ---- 2026-06-29 12:30 ET  (pick section: "sit it out — no trade is a position")
    ("2026-06-29", "buzz", "SRFM", 0.96), ("2026-06-29", "buzz", "SLS", 13.84),
    ("2026-06-29", "buzz", "NNBR", 3.84), ("2026-06-29", "buzz", "VIVS", 1.02),
    ("2026-06-29", "buzz", "ALL", 240.38),
    ("2026-06-29", "mover", "FCEL", 29.62), ("2026-06-29", "mover", "OUST", 51.64),
    ("2026-06-29", "mover", "IRDM", 53.37), ("2026-06-29", "mover", "VSAT", 75.31),
    ("2026-06-29", "mover", "ASTS", 83.17),

    # ---- 2026-07-06 12:11 ET
    ("2026-07-06", "buzz", "ELAB", 1.36), ("2026-07-06", "buzz", "MU", 1012.00),
    ("2026-07-06", "buzz", "LUCY", 1.35), ("2026-07-06", "buzz", "CXAI", 0.16),
    ("2026-07-06", "buzz", "ALL", 246.70),
    ("2026-07-06", "mover", "AXTI", 67.95), ("2026-07-06", "mover", "CHRN", 19.53),
    ("2026-07-06", "mover", "BFLY", 8.87), ("2026-07-06", "mover", "IREN", 44.74),
    ("2026-07-06", "mover", "IMOS", 73.60),

    # ---- 2026-07-29 18:15 ET  (first digest with the Worth Watching section)
    ("2026-07-29", "pick", "MU", 739.00), ("2026-07-29", "pick", "MSFT", 390.54),
    ("2026-07-29", "pick", "SNDK", 1015.89), ("2026-07-29", "pick", "SPY", 729.46),
    ("2026-07-29", "pick", "NVDA", 190.01), ("2026-07-29", "pick", "NOK", 8.41),
    ("2026-07-29", "pick", "T", 23.94), ("2026-07-29", "pick", "SKHY", 126.79),
    ("2026-07-29", "buzz", "MU", 739.00), ("2026-07-29", "buzz", "MSFT", 390.54),
    ("2026-07-29", "buzz", "SNDK", 1015.89), ("2026-07-29", "buzz", "SPY", 729.46),
    ("2026-07-29", "buzz", "META", 585.61), ("2026-07-29", "buzz", "QQQ", 661.73),
    ("2026-07-29", "buzz", "NVDA", 190.01), ("2026-07-29", "buzz", "AAPL", 338.19),
    ("2026-07-29", "buzz", "NBIS", 148.22), ("2026-07-29", "buzz", "SPCX", 112.55),
    ("2026-07-29", "mover", "HURN", 170.37), ("2026-07-29", "mover", "MANH", 204.02),
    ("2026-07-29", "mover", "LAD", 427.48), ("2026-07-29", "mover", "EXLS", 35.99),
    ("2026-07-29", "mover", "CBZ", 54.90),

    # ---- 2026-07-30 10:58 ET  (buzz list partially truncated in the log)
    ("2026-07-30", "buzz", "TSLA", 304.11), ("2026-07-30", "buzz", "META", 534.22),
    ("2026-07-30", "buzz", "AAPL", 331.58),
    ("2026-07-30", "mover", "CORT", 121.56), ("2026-07-30", "mover", "MKTX", 163.12),
    ("2026-07-30", "mover", "BHC", 5.99), ("2026-07-30", "mover", "NBIS", 188.92),
    ("2026-07-30", "mover", "BE", 206.43),
]


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


def main():
    syms = sorted({f[2] for f in FLAGS})
    print(f"pricing {len(syms)} distinct tickers…")
    now = {s: last_price(s) for s in syms}
    missing = [s for s, p in now.items() if not p]
    if missing:
        print(f"[warn] no current price for: {', '.join(missing)}")

    rows, by_tier = [], defaultdict(list)
    for date, tier, sym, entry in FLAGS:
        cur = now.get(sym)
        if not cur or not entry:
            continue
        ret = (cur - entry) / entry * 100
        row = {"date": date, "tier": tier, "sym": sym, "entry": entry,
               "now": round(cur, 2), "ret": round(ret, 1),
               "pnl": round(STAKE * ret / 100, 2)}
        rows.append(row)
        by_tier[tier].append(row)

    summary = {}
    for tier, rs in by_tier.items():
        wins = [r for r in rs if r["ret"] > 0]
        pnl = sum(r["pnl"] for r in rs)
        summary[tier] = {
            "positions": len(rs),
            "winners": len(wins),
            "win_rate": round(len(wins) / len(rs) * 100, 1) if rs else 0,
            "invested": round(STAKE * len(rs), 2),
            "pnl": round(pnl, 2),
            "return_pct": round(pnl / (STAKE * len(rs)) * 100, 1) if rs else 0,
            "best": max(rs, key=lambda r: r["ret"]),
            "worst": min(rs, key=lambda r: r["ret"]),
        }

    payload = {"stake": STAKE, "rows": sorted(rows, key=lambda r: (r["date"], r["sym"])),
               "summary": summary, "missing": missing}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)

    print(f"\n{'='*74}\nIF YOU HAD ACTED ON EVERY NOTIFICATION  (${STAKE:,.0f} per name)\n{'='*74}")
    for tier in ("pick", "buzz", "mover"):
        s = summary.get(tier)
        if not s:
            continue
        label = {"pick": "PICKS  (Worth Watching / Worth a Closer Look)",
                 "buzz": "BUZZ   (Reddit Buzz list)",
                 "mover": "MOVERS (labelled 'not a prediction')"}[tier]
        print(f"\n{label}")
        print(f"  positions {s['positions']:>3}   winners {s['winners']:>3}"
              f"   win rate {s['win_rate']:>5.1f}%")
        print(f"  invested  ${s['invested']:>10,.0f}   P/L ${s['pnl']:>+11,.0f}"
              f"   ({s['return_pct']:+.1f}%)")
        print(f"  best  {s['best']['sym']:<6} {s['best']['ret']:+7.1f}%"
              f"   worst {s['worst']['sym']:<6} {s['worst']['ret']:+7.1f}%")

    print(f"\n{'-'*74}\nEVERY FLAG, OLDEST FIRST\n{'-'*74}")
    print(f"{'date':<11}{'tier':<7}{'sym':<7}{'entry':>10}{'now':>10}{'return':>9}{'P/L':>10}")
    for r in payload["rows"]:
        print(f"{r['date']:<11}{r['tier']:<7}{r['sym']:<7}"
              f"{r['entry']:>10.2f}{r['now']:>10.2f}{r['ret']:>8.1f}%{r['pnl']:>+10.0f}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
