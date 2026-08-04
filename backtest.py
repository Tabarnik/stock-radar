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
from datetime import date as _date

import yfinance as yf

# Per position, for the per-ticker tables.
STAKE = float(os.getenv("STAKE", "1000"))
# One account for the compounding curve.
START_CASH = float(os.getenv("START_CASH", "10000"))
# How much of the account goes into each individual pick. Sized per position,
# not per day: a fixed daily budget split across that day's names meant a
# one-pick day put the entire budget into a single ticker while an eight-pick
# day put an eighth into each, so identical rules got wildly different risk.
# One slice per name keeps every position the same size.
POSITION_FRAC = float(os.getenv("POSITION_FRAC", "0.05"))
# The curve exits on the same rule the sell panel measures, so the two halves of
# the dashboard describe one strategy rather than contradicting each other.
# A time stop, not a bracket: it beat every price-based exit over the pick record
# and it is the only rule that guarantees the capital comes back on a schedule,
# which is what lets a near-daily pick cadence stay funded.
CURVE_DAYS = int(os.getenv("CURVE_DAYS", "5"))
OUT = os.path.join(os.getenv("DASHBOARD_DIR", "docs"), "backtest.json")

# One position per ticker. A name flagged on five different days is still one
# thing you would have bought, on the first day you saw it — counting it five
# times inflates both the position count and the P/L.
TIER_RANK = {"pick": 0, "buzz": 1, "mover": 2}

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


def daily_closes(sym):
    """date string -> close, for the whole flag window."""
    try:
        h = yf.Ticker(sym).history(period="1y", interval="1d")
        if h.empty:
            return {}
        return {idx.strftime("%Y-%m-%d"): float(c)
                for idx, c in h["Close"].items() if c == c}
    except Exception as e:
        print(f"[warn] closes {sym}: {e}")
        return {}


def close_on_or_before(closes, date):
    """Last close at or before date — markets are shut on the day itself sometimes."""
    if not closes or not date:
        return None
    keys = [k for k in closes if k <= date]
    return closes[max(keys)] if keys else None


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


# The benchmark. Every return in this file was measured against zero, which
# cannot distinguish a good pick from a rising market: SPY moved +2.5% over a
# recent five-session window while the picks averaged far more, and none of the
# tables said so. Fetched once and reused.
SPY_CLOSES = {}


def spy_over(start_date, sessions):
    """SPY's move over the same window: `sessions` closes after start_date."""
    if not SPY_CLOSES or not start_date or not sessions:
        return None
    base = close_on_or_before(SPY_CLOSES, start_date)
    ks = sorted(k for k in SPY_CLOSES if k > start_date)[:sessions]
    if not base or not ks:
        return None
    return round((SPY_CLOSES[ks[-1]] - base) / base * 100, 1)


# ---------------------------------------------------------------- exit rules
# "When do I sell?" is the one question here that can be answered with evidence
# rather than opinion: every flag has a real price path after it, so competing
# sell rules can be replayed over the same 69 positions and compared.
#
# Each rule sees the daily closes from the day after the flag and returns
# (exit_index, exit_price) or None to keep holding to the last close.

def rule_hold(path, entry):
    return None


def _stop(pct):
    def f(path, entry):
        trigger = entry * (1 + pct / 100)
        for i, c in enumerate(path):
            if c <= trigger:
                return i, c
        return None
    return f


def _trail(pct):
    def f(path, entry):
        peak = entry
        for i, c in enumerate(path):
            peak = max(peak, c)
            if c <= peak * (1 - pct / 100):
                return i, c
        return None
    return f


def _target(pct):
    def f(path, entry):
        trigger = entry * (1 + pct / 100)
        for i, c in enumerate(path):
            if c >= trigger:
                return i, c
        return None
    return f


def _bracket(stop_pct, target_pct):
    def f(path, entry):
        lo, hi = entry * (1 + stop_pct / 100), entry * (1 + target_pct / 100)
        for i, c in enumerate(path):
            if c <= lo or c >= hi:
                return i, c
        return None
    return f


def _time_stop(days):
    def f(path, entry):
        if len(path) > days:
            return days - 1, path[days - 1]
        return None
    return f


def _trail_after_target(target_pct, trail_pct):
    """Let it run, but once it is up target_pct, protect with a trailing stop."""
    def f(path, entry):
        armed, peak = False, entry
        for i, c in enumerate(path):
            peak = max(peak, c)
            if not armed and c >= entry * (1 + target_pct / 100):
                armed = True
            if armed and c <= peak * (1 - trail_pct / 100):
                return i, c
        return None
    return f


RULES = [
    ("Hold, never sell",            rule_hold),
    ("Stop loss -10%",              _stop(-10)),
    ("Stop loss -15%",              _stop(-15)),
    ("Stop loss -20%",              _stop(-20)),
    ("Trailing stop -15%",          _trail(15)),
    ("Trailing stop -20%",          _trail(20)),
    ("Trailing stop -25%",          _trail(25)),
    ("Take profit +20%",            _target(20)),
    ("Take profit +30%",            _target(30)),
    # Symmetric ±15%: cut and take profit at the same distance. This is the rule
    # the compounded account below actually trades, so it is measured here on the
    # same pick record rather than asserted.
    ("Stop -15% / target +15%",     _bracket(-15, 15)),
    ("Stop -15% / target +25%",     _bracket(-15, 25)),
    ("Stop -20% / target +40%",     _bracket(-20, 40)),
    ("Run then trail -15% (+20%)",  _trail_after_target(20, 15)),
    ("Sell after 5 days",           _time_stop(5)),
    ("Sell after 10 days",          _time_stop(10)),
    ("Sell after 20 days",          _time_stop(20)),
]


def daily_path(sym, start):
    """Closes from the day after the flag onward."""
    try:
        h = yf.Ticker(sym).history(start=start, interval="1d")
        if h.empty:
            return []
        return [float(c) for c in h["Close"].tolist() if c == c][1:]
    except Exception as e:
        print(f"[warn] path {sym}: {e}")
        return []


def test_exit_rules(pos_list, detail=False):
    """Replay every rule over every position; return per-rule aggregates.

    With detail=True each rule also carries the individual positions behind its
    average, so "which pick drove this number" is answerable from the JSON
    instead of being taken on trust.
    """
    paths = {}
    for p in pos_list:
        path = daily_path(p["sym"], p["date"])
        if path:
            paths[p["sym"]] = path
    print(f"price paths for {len(paths)}/{len(pos_list)} positions")

    results = []
    for name, fn in RULES:
        rets, held, exited, rows_ = [], [], 0, []
        for p in pos_list:
            path = paths.get(p["sym"])
            if not path:
                continue
            entry = p["entry"]
            hit = fn(path, entry)
            if hit is None:
                ret, days, out = (path[-1] - entry) / entry * 100, len(path), path[-1]
            else:
                i, out = hit
                ret, days = (out - entry) / entry * 100, i + 1
                exited += 1
            rets.append(ret)
            held.append(days)
            if detail:
                sp = spy_over(p["date"], days)
                rows_.append({"sym": p["sym"], "date": p["date"],
                              "entry": round(entry, 4), "exit": round(out, 4),
                              "ret": round(ret, 1), "days": days,
                              "spy_ret": sp,
                              "excess": None if sp is None else round(ret - sp, 1),
                              "held_to_end": hit is None,
                              "simulated": bool(p.get("simulated"))})
        if not rets:
            continue
        wins = [r for r in rets if r > 0]
        avg = sum(rets) / len(rets)
        # the same average over only the picks that were really sent — the
        # reconstructed ones never reached the user, so they cannot be credited
        # to the record without saying so
        real = [r["ret"] for r in rows_ if not r["simulated"]] if detail else []
        exc = [r["excess"] for r in rows_ if r.get("excess") is not None] if detail else []
        results.append({
            "rule": name,
            "positions": len(rets),
            "exited": exited,
            "avg_return": round(avg, 1),
            "win_rate": round(len(wins) / len(rets) * 100, 1),
            "pnl": round(START_CASH * avg / 100, 2),
            "invested": round(START_CASH, 2),
            "per_ticker": round(START_CASH / len(rets), 2),
            "final": round(START_CASH * (1 + avg / 100), 2),
            "avg_days_held": round(sum(held) / len(held), 1),
            "worst": round(min(rets), 1),
            "best": round(max(rets), 1),
            **({"avg_spy": round(sum(r["spy_ret"] for r in rows_
                                     if r.get("spy_ret") is not None) / len(exc), 1),
                "avg_excess": round(sum(exc) / len(exc), 1)} if exc else {}),
            **({"positions_real": len(real),
                "avg_return_real": round(sum(real) / len(real), 1),
                "win_rate_real": round(
                    len([r for r in real if r > 0]) / len(real) * 100, 1),
                "detail": sorted(rows_, key=lambda r: r["ret"])} if real else {}),
        })
    results.sort(key=lambda r: -r["avg_return"])
    return results


def main():
    global SPY_CLOSES
    SPY_CLOSES = daily_closes("SPY")
    print(f"benchmark: {len(SPY_CLOSES)} SPY closes")
    syms = sorted({f[2] for f in FLAGS})
    print(f"pricing {len(syms)} distinct tickers…")
    now = {s: last_price(s) for s in syms}
    missing = [s for s, p in now.items() if not p]
    if missing:
        print(f"[warn] no current price for: {', '.join(missing)}")

    # collapse to one position per ticker: bought the first time it appeared,
    # filed under the strongest tier it was ever flagged under
    pos = {}
    for date, tier, sym, entry in FLAGS:
        p = pos.get(sym)
        if p is None:
            pos[sym] = {"date": date, "tier": tier, "sym": sym, "entry": entry,
                        "flags": 1, "days": {date}}
            continue
        p["flags"] += 1
        p["days"].add(date)
        if date < p["date"]:                    # an earlier sighting wins the entry
            p["date"], p["entry"] = date, entry
        if TIER_RANK[tier] < TIER_RANK[p["tier"]]:
            p["tier"] = tier

    rows, by_tier = [], defaultdict(list)
    for sym, p in pos.items():
        cur = now.get(sym)
        if not cur or not p["entry"]:
            continue
        ret = (cur - p["entry"]) / p["entry"] * 100
        row = {"date": p["date"], "tier": p["tier"], "sym": sym,
               "entry": p["entry"], "now": round(cur, 2), "ret": round(ret, 1),
               "flags": p["flags"], "days": len(p["days"])}
        rows.append(row)
        by_tier[p["tier"]].append(row)

    # STAKE goes into each ticker, so a basket's cost scales with how many
    # names it held and its return is still the mean of those names.
    for r in rows:
        r["alloc"] = round(STAKE, 2)
        r["pnl"] = round(STAKE * r["ret"] / 100, 2)

    def basket(rs):
        # An empty basket means every price lookup failed. Return a zeroed shape
        # rather than dying on max() — a run that crashes here leaves the last
        # good backtest.json in place and the dashboard silently goes stale.
        if not rs:
            return {"positions": 0, "winners": 0, "win_rate": 0, "invested": 0,
                    "per_ticker": round(STAKE, 2), "pnl": 0, "return_pct": 0,
                    "best": None, "worst": None}
        wins = [r for r in rs if r["ret"] > 0]
        avg = sum(r["ret"] for r in rs) / len(rs) if rs else 0
        return {
            "positions": len(rs),
            "winners": len(wins),
            "win_rate": round(len(wins) / len(rs) * 100, 1) if rs else 0,
            "invested": round(STAKE * len(rs), 2),
            "per_ticker": round(STAKE, 2),
            "pnl": round(STAKE * len(rs) * avg / 100, 2),
            "return_pct": round(avg, 1),
            "best": max(rs, key=lambda r: r["ret"]),
            "worst": min(rs, key=lambda r: r["ret"]),
        }

    summary = {tier: basket(rs) for tier, rs in by_tier.items()}

    # The canonical pick record, built before anything reads it. docs/history.json
    # carries the days reconstructed from the logs as well as the ones FLAGS
    # transcribes; the exit-rule replay and the compounded curve below both run
    # off this one structure, so it has to exist before either of them.
    pick_days = defaultdict(dict)
    # 15 of the history entries are reconstructed by replaying the pick rule over
    # digests that predate the pick section — they were never actually sent, so
    # the flag rides along and the record can be quoted with or without them
    pick_sim = {}
    try:
        with open(os.path.join(os.path.dirname(OUT) or ".", "history.json")) as f:
            for e in json.load(f):
                if e.get("flag_price"):
                    pick_days[e["date"]].setdefault(e["sym"], e["flag_price"])
                    pick_sim.setdefault((e["date"], e["sym"]),
                                        bool(e.get("simulated")))
    except Exception as e:
        print(f"[warn] history.json: {e}")
    for date, tier, sym, entry in FLAGS:        # fall back to the transcription
        if tier == "pick":
            pick_days[date].setdefault(sym, entry)
            pick_sim.setdefault((date, sym), False)

    # Every pick ever made, first flag per ticker. This is the personal record
    # and it grows with each run, rather than being fixed at what FLAGS lists.
    seen_pick = {}
    for d in sorted(pick_days):
        for sym, entry in pick_days[d].items():
            seen_pick.setdefault(sym, {"sym": sym, "date": d, "entry": entry,
                                       "simulated": pick_sim.get((d, sym), False)})
    picks_only = list(seen_pick.values())
    n_sim = sum(1 for p in picks_only if p["simulated"])
    print(f"\nreplaying exit rules — {len(picks_only)} distinct picks "
          f"({len(picks_only) - n_sim} really sent, {n_sim} reconstructed), "
          f"{len(rows)} positions overall…")
    exits = test_exit_rules(rows)
    exits_picks = (test_exit_rules(picks_only, detail=True)
                   if len(picks_only) >= 5 else [])

    overall = basket(rows)
    overall["losers"] = overall["positions"] - overall["winners"]


    # One account, compounded, on a capital model that agrees with the exit rule.
    # Each pick day deploys DEPLOY_FRAC of the account across that day's picks;
    # each position then runs until the ±15% bracket closes it, and the freed cash
    # funds later days. Baskets overlap, which is the honest picture of buying on
    # most days and letting every position finish at its own pace.
    days = sorted(pick_days)
    curve_syms = {s for d in days for s in pick_days[d]}
    closes = {s: daily_closes(s) for s in curve_syms}
    for s_ in curve_syms:                       # price anything rows never covered
        if s_ not in now:
            now[s_] = last_price(s_)

    # every trading day the positions live on, plus the pick days themselves —
    # a pick day that fell on a holiday still has to be able to buy
    cal = sorted({d for s in curve_syms for d in closes.get(s, {})
                  if days and d >= days[0]} | set(days))
    cash, openpos, cohorts, starved = START_CASH, [], {}, []

    def _exit_date(sym, buy_date):
        """The CURVE_DAYS-th close after the buy — the same bar _time_stop takes."""
        ks = sorted(k for k in closes.get(sym, {}) if k > buy_date)
        return ks[CURVE_DAYS - 1] if len(ks) >= CURVE_DAYS else None

    def _sessions(sym, after, upto):
        """Trading sessions in (after, upto]."""
        return sum(1 for k in closes.get(sym, {}) if after < k <= upto)

    def _days_left(p, ref):
        """Sessions still to run on an open position as of ref."""
        if p["exit_on"]:
            return max(0, _sessions(p["sym"], ref, p["exit_on"]))
        # exit has not happened yet, so count down from the full holding period
        return max(0, CURVE_DAYS - _sessions(p["sym"], p["day"], ref))

    # (pick day, symbol) -> sessions left on the position already open that day
    held_at = {}

    def _mv(on_date):
        """Open positions marked to the last close at or before on_date."""
        return sum(p["shares"] * (close_on_or_before(closes.get(p["sym"]), on_date)
                                  or p["entry"]) for p in openpos)

    for date in cal:
        # exits first — cash freed today can fund today's picks
        still_open = []
        for p in openpos:
            px = closes.get(p["sym"], {}).get(date)
            if px is not None and p["exit_on"] and date >= p["exit_on"]:
                cash += p["shares"] * px
                c = cohorts[p["day"]]
                c["rets"].append((px - p["entry"]) / p["entry"] * 100)
                c["held"].append((_date.fromisoformat(date)
                                  - _date.fromisoformat(p["day"])).days)
            else:
                still_open.append(p)
        openpos = still_open

        if date not in pick_days:
            continue
        live = {p["sym"] for p in openpos}
        flagged = [(s, e) for s, e in pick_days[date].items() if e]
        names = [(s, e) for s, e in flagged
                 if s not in live]              # never stack a second buy on a name
        # A name the radar flagged again while the account still holds it is not
        # bought twice. That is deliberate, but it makes the day's row cover
        # fewer names than the digest listed, so both counts are reported rather
        # than letting "picks" quietly mean two different things on two panels.
        held_already = sorted(s for s, _ in flagged if s in live)
        for s in held_already:                  # how much longer it was tied up,
            p = next(x for x in openpos if x["sym"] == s)   # and what it cost
            held_at[(date, s)] = {"days_left": _days_left(p, date),
                                  "bought": p["day"], "buy_price": p["entry"]}
        if not names:
            continue
        acct = cash + _mv(date)
        target = acct * POSITION_FRAC           # same dollar risk on every name
        allocs = []
        for s, e in names:
            size = min(target, cash)
            if size < 1:                        # fully invested — the rest is missed
                starved.append(date)
                break
            openpos.append({"sym": s, "day": date, "entry": e,
                            "shares": size / e, "alloc": size,
                            "exit_on": _exit_date(s, date)})
            cash -= size
            allocs.append(size)
        if not allocs:
            continue
        cohorts[date] = {"picks": len(allocs),
                         "flagged": len(flagged),
                         "held_already": held_already,
                         "bought": [s for s, _ in names[:len(allocs)]],
                         "per_ticker": sum(allocs) / len(allocs),
                         "balance": acct, "rets": [], "held": []}

    # whatever is still open is marked to the latest price
    today = _date.today().isoformat()

    def _mark(sym):
        return (now.get(sym) or close_on_or_before(closes.get(sym), today))

    for p in openpos:
        px = _mark(p["sym"]) or p["entry"]
        c = cohorts[p["day"]]
        c["rets"].append((px - p["entry"]) / p["entry"] * 100)
        c["held"].append((_date.today() - _date.fromisoformat(p["day"])).days)
        c["open"] = True

    equity = []
    for date in sorted(cohorts):
        c = cohorts[date]
        if not c["rets"]:
            continue
        r = sum(c["rets"]) / len(c["rets"])
        invested = c["per_ticker"] * c["picks"]
        equity.append({
            "date": date,
            "picks": c["picks"],
            "flagged": c.get("flagged", c["picks"]),
            "held_already": c.get("held_already", []),
            "bought": c.get("bought", []),
            "per_ticker": round(c["per_ticker"], 2),
            "held_days": round(sum(c["held"]) / len(c["held"])),
            "return_pct": round(r, 1),
            "invested": round(invested, 2),
            # the account on the day that basket was bought
            "balance": round(c["balance"], 2),
            "pnl": round(invested * r / 100, 2),
            "open": bool(c.get("open")),
        })

    final_balance = cash + sum(p["shares"] * (_mark(p["sym"]) or p["entry"])
                               for p in openpos)

    # Every pick occurrence, not collapsed to one position per ticker: the
    # history screen lists a name once per day it was flagged, and each of those
    # days has its own entry price and therefore its own 5-day outcome. Marking
    # them all to today answers "where is it now" but not "what would the rule
    # have paid", which is the number the rule is actually judged on.
    pick_detail = []
    for date in sorted(pick_days):
        for sym, entry in sorted(pick_days[date].items()):
            if not entry:
                continue
            cur = _mark(sym)
            xd = _exit_date(sym, date)
            xpx = closes.get(sym, {}).get(xd) if xd else None
            pick_detail.append({
                "date": date, "sym": sym, "entry": entry,
                "now": round(cur, 4) if cur else None,
                "ret_total": round((cur - entry) / entry * 100, 1) if cur else None,
                "exit_date": xd,
                "exit_price": round(xpx, 4) if xpx else None,
                "ret_hold": round((xpx - entry) / entry * 100, 1) if xpx else None,
                # fewer than CURVE_DAYS closes since the flag — the rule has not
                # come due yet, so there is no exit to report rather than a zero
                "still_open": xpx is None,
                "simulated": pick_sim.get((date, sym), False),
            })
    for p in pick_detail:
        # flagged again while the account still held it: not a buy, and the day's
        # return should not average it in. The position's own gain is measured
        # from what it was actually bought at on the earlier day, which is a
        # different number from this day's flag price.
        h = held_at.get((p["date"], p["sym"]))
        p["already_held"] = h is not None
        p["days_left"] = h["days_left"] if h else None
        p["held_since"] = h["bought"] if h else None
        p["buy_price"] = round(h["buy_price"], 4) if h else None
        p["ret_since_buy"] = (round((p["now"] - h["buy_price"]) / h["buy_price"] * 100, 1)
                              if h and p["now"] and h["buy_price"] else None)

    # What the account is still holding as of this run, so today's list can say
    # "already in it, N days left" instead of telling you to buy it twice.
    def _day_by_day(sym, buy_date, entry):
        """Each session since the buy: that day's move and the running total.

        Both are needed and they are different questions — a position can be up
        overall on a day it fell. Capped at CURVE_DAYS because the rule sells
        there; a sixth close would describe a position that no longer exists.
        """
        ks = sorted(k for k in closes.get(sym, {}) if k > buy_date)[:CURVE_DAYS]
        out, prev = [], entry
        for i, k in enumerate(ks, 1):
            c = closes[sym][k]
            out.append({"day": i, "date": k, "close": round(c, 4),
                        "day_pct": round((c - prev) / prev * 100, 1),
                        "cum_pct": round((c - entry) / entry * 100, 1)})
            prev = c
        return out

    open_positions = []
    for p in openpos:
        px = _mark(p["sym"]) or p["entry"]
        val = p["shares"] * px
        open_positions.append({
            "sym": p["sym"], "bought": p["day"],
            "days_left": _days_left(p, today),
            "exit_on": p["exit_on"],
            "entry": round(p["entry"], 4), "price": round(px, 4),
            "shares": round(p["shares"], 4),
            "alloc": round(p["alloc"], 2), "value": round(val, 2),
            "pnl": round(val - p["alloc"], 2),
            "ret": round((px - p["entry"]) / p["entry"] * 100, 1),
            "spy_ret": spy_over(p["day"], CURVE_DAYS - _days_left(p, today) or 1),
            "path": _day_by_day(p["sym"], p["day"], p["entry"]),
        })
    open_positions.sort(key=lambda o: (o["days_left"], o["sym"]))

    # ---- the real book -------------------------------------------------
    # docs/trades.json is what was actually bought, written by trade.py. The
    # model account above assumes every pick was taken the moment it appeared;
    # this is the one with missed runs and skipped names in it. Same shape as
    # open_positions so the board renders both through the same code.
    my_open, my_closed = [], []
    try:
        with open(os.path.join(os.path.dirname(OUT) or ".", "trades.json")) as f:
            trades = json.load(f)
    except Exception:
        trades = []
    for t in trades:
        sym, bp, sh = t.get("sym"), t.get("buy_price"), t.get("shares")
        if not sym or not bp or not sh:
            continue
        cost = bp * sh
        if t.get("sell_price"):                 # closed: marked at what it sold for
            val = t["sell_price"] * sh
            my_closed.append({
                "sym": sym, "bought": t.get("buy_date"), "sold": t.get("sell_date"),
                "entry": round(bp, 4), "exit": round(t["sell_price"], 4),
                "shares": round(sh, 6), "cost": round(cost, 2), "value": round(val, 2),
                "pnl": round(val - cost, 2), "ret": round((val - cost) / cost * 100, 1),
                "note": t.get("note") or "",
            })
            continue
        if sym not in closes:                   # a name the picks never covered
            closes[sym] = daily_closes(sym)
        if sym not in now:
            now[sym] = last_price(sym)
        px = _mark(sym) or bp
        val = px * sh
        held = sum(1 for k in closes.get(sym, {}) if t["buy_date"] < k <= today)
        my_open.append({
            "sym": sym, "bought": t.get("buy_date"),
            "days_left": max(0, CURVE_DAYS - held),
            "entry": round(bp, 4), "price": round(px, 4), "shares": round(sh, 6),
            "alloc": round(cost, 2), "value": round(val, 2),
            "pnl": round(val - cost, 2), "ret": round((px - bp) / bp * 100, 1),
            "path": _day_by_day(sym, t["buy_date"], bp),
            "note": t.get("note") or "",
        })
    # Every symbol the board might need to price a browser-recorded trade: this
    # run's picks, plus anything already open. Trimmed to the sessions a position
    # can still be in, which keeps the payload small.
    recent_closes = {}
    live_syms = set()
    for d in sorted(pick_days)[-4:]:
        live_syms |= set(pick_days[d])
    live_syms |= {o["sym"] for o in my_open} | {o["sym"] for o in open_positions}
    # Anything currently on the board, because a trade recorded in the browser can
    # be any name you saw there — and once it drops off the picks the page would
    # otherwise lose the closes it needs to chart and price your position.
    try:
        with open(os.path.join(os.path.dirname(OUT) or ".", "data.json")) as f:
            dash = json.load(f)
        for key in ("watch", "buzz", "gainers"):
            live_syms |= {r.get("sym") for r in (dash.get(key) or []) if r.get("sym")}
    except Exception as e:
        print(f"[warn] data.json for closes: {e}")
    for sym in sorted(live_syms):
        c = closes.get(sym) or daily_closes(sym)
        if not c:
            continue
        ks = sorted(c)[-(CURVE_DAYS + 8):]
        recent_closes[sym] = {k: round(c[k], 4) for k in ks}

    my_open.sort(key=lambda o: (o["days_left"], o["sym"]))
    my_closed.sort(key=lambda o: o["sold"] or "", reverse=True)
    print(f"real book: {len(trades)} trade(s) in trades.json -> "
          f"{len(my_open)} open, {len(my_closed)} closed")

    payload = {
        "stake": STAKE,
        # best first — the shape of the outcome should be visible without sorting
        "rows": sorted(rows, key=lambda r: -r["ret"]),
        "summary": summary,
        "missing": missing,
        "overall": overall,
        "equity": equity,
        "start_cash": START_CASH,
        # the capital model behind the curve, so the dashboard can describe it
        # instead of hard-coding a description that drifts out of date
        "final_balance": round(final_balance, 2),
        "position_frac": POSITION_FRAC,
        # exactly as RULES labels it, so the dashboard can find the curve's own
        # rule in the exit table and show where it ranks
        "curve_rule": f"Sell after {CURVE_DAYS} days",
        "curve_days": CURVE_DAYS,
        "pick_detail": pick_detail,
        "my_positions": my_open,
        "my_closed": my_closed,
        # Recent sessions for anything currently on the board, so the page can
        # work out a position you record in the browser — its day-by-day path,
        # how many sessions it has run — without a price feed of its own.
        "recent_closes": recent_closes,
        "open_positions": open_positions,
        "starved_days": starved,
        "exit_rules": exits,
        "exit_rules_picks": exits_picks,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)

    print(f"\n{'='*74}")
    print(f"IF YOU HAD PUT ${STAKE:,.0f} INTO EACH LIST  (split equally, held to now)")
    print(f"{'='*74}")
    for tier in ("pick", "buzz", "mover"):
        s = summary.get(tier)
        if not s:
            continue
        label = {"pick": "PICKS  (Worth Watching — act on these first)",
                 "buzz": "BUZZ   (Reddit Buzz — watch, don't act)",
                 "mover": "MOVERS (Biggest movers — don't chase)"}[tier]
        print(f"\n{label}")
        print(f"  {s['positions']:>2} tickers @ ${s['per_ticker']:,.0f} each"
              f"   {s['winners']} up / {s['positions']-s['winners']} down"
              f"   ({s['win_rate']}% up)")
        print(f"  ${STAKE:,.0f} -> ${STAKE + s['pnl']:,.0f}"
              f"   P/L ${s['pnl']:>+10,.0f}   ({s['return_pct']:+.1f}%)")
        print(f"  best  {s['best']['sym']:<6} {s['best']['ret']:+7.1f}%"
              f"   worst {s['worst']['sym']:<6} {s['worst']['ret']:+7.1f}%")

    o = payload["overall"]
    print(f"\n{'='*74}")
    print(f"EVERYTHING  {o['positions']} tickers @ ${o['per_ticker']:,.0f} each  "
          f"{o['winners']} up / {o['losers']} down  ({o['win_rate']}% up)")
    print(f"            ${STAKE:,.0f} -> ${STAKE + o['pnl']:,.0f}"
          f"   P/L ${o['pnl']:+,.0f}   ({o['return_pct']:+.1f}%)")

    if equity:
        tot = (final_balance - START_CASH) / START_CASH * 100
        print(f"\n{'='*74}")
        print(f"COMPOUNDED — ${START_CASH:,.0f}, {POSITION_FRAC:.0%} of the account "
              f"per pick, sold after {CURVE_DAYS} trading days")
        print(f"{'='*74}")
        print(f"{'pick day':<12}{'picks':>6}{'each':>10}{'held':>7}"
              f"{'return':>9}{'account':>12}")
        for e in equity:
            tag = "  (open)" if e["open"] else ""
            print(f"{e['date']:<12}{e['picks']:>6}{e['per_ticker']:>10,.0f}"
                  f"{e['held_days']:>6}d{e['return_pct']:>8.1f}%"
                  f"{e['balance']:>12,.0f}{tag}")
        print(f"\n${START_CASH:,.0f} -> ${final_balance:,.0f}  ({tot:+.1f}%) "
              f"over {len(equity)} pick days")
        if starved:
            print(f"{len(starved)} pick day(s) missed for lack of cash: "
                  f"{', '.join(starved)}")

    print(f"\n{'-'*74}\nONE ROW PER TICKER, BEST FIRST\n{'-'*74}")
    print(f"{'sym':<7}{'tier':<7}{'first seen':<12}{'bought':>10}{'now':>10}"
          f"{'change':>9}{'P/L':>12}{'seen':>6}")
    for r in payload["rows"]:
        print(f"{r['sym']:<7}{r['tier']:<7}{r['date']:<12}"
              f"{r['entry']:>10.2f}{r['now']:>10.2f}{r['ret']:>8.1f}%"
              f"{r['pnl']:>+12,.0f}{r['days']:>6}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
