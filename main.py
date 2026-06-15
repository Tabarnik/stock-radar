#!/usr/bin/env python3
"""
Reddit Stock Radar
------------------
Ranks the tickers Reddit is talking about by mention volume + 24h momentum,
keeps only names tradeable on Webull (US major exchanges), enriches them with
live price/volume, flags penny/pump risk, and sends a daily digest.

Data source (auto):
  * ApeWisdom public API  -> default, NO keys, works from any IP. Gives mentions
    + 24h-ago mentions + upvotes (aggregated from WSB/stocks/etc).
  * Reddit official API    -> used automatically if REDDIT_CLIENT_ID is set; adds
    per-post sentiment.

This is an INFORMATION tool. It is NOT financial advice and it does NOT trade.
These communities are full of pump-and-dump activity; treat every name as
high-risk and do your own research.
"""

import os
import re
import html
import json
import math
from datetime import datetime, timezone

import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import notify

# ---------------------------------------------------------------- config
SUBREDDITS = [s.strip() for s in os.getenv(
    "SUBREDDITS", "wallstreetbets,stockstobuytoday,Shortsqueeze").split(",") if s.strip()]
APEWISDOM_FILTER = os.getenv("APEWISDOM_FILTER", "Shortsqueeze,pennystocks")
POSTS_PER_SUB = int(os.getenv("POSTS_PER_SUB", "60"))
TOP_N = int(os.getenv("TOP_N", "5"))
MAX_VALIDATE = int(os.getenv("MAX_VALIDATE", "30"))
STATE_FILE = os.getenv("STATE_FILE", "state/history.json")
HIGH_RISK_SUBS = {"shortsqueeze", "stockstobuytoday", "pennystocks", "squeezeplays"}
USER_AGENT = os.getenv("REDDIT_USER_AGENT", "stock-radar/1.0 (personal daily digest)")

# Uppercase words that look like tickers but usually aren't (raw-text path only).
BLOCKLIST = {
    "A", "I", "AI", "AN", "ANY", "ARE", "ATH", "BE", "BIG", "BAN", "BUY", "CALL",
    "CAD", "CEO", "CFO", "COO", "CPI", "DD", "DCA", "DOW", "EOD", "EPS", "ER",
    "ETF", "EU", "EV", "FAQ", "FBI", "FDA", "FED", "FHSA", "FOMO", "FOMC", "FUD",
    "FY", "FYI", "GDP", "GO", "GUH", "HODL", "HOLD", "IMO", "IMHO", "IPO", "IRA",
    "IRS", "IT", "ITM", "LFG", "LMAO", "LOL", "LONG", "ME", "MOON", "NEW", "NO",
    "NOT", "NOW", "NSFW", "NYSE", "OK", "ON", "ONE", "OP", "OTM", "OUT", "PR",
    "PSA", "PT", "PUT", "RED", "RH", "RIP", "ROI", "RRSP", "RSI", "SAVE", "SEC",
    "SI", "SO", "TA", "TFSA", "TLDR", "TOP", "TOS", "UK", "US", "USA", "USD",
    "WIN", "WSB", "WTF", "YOLO", "YOY",
}

CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
BARE = re.compile(r"\b([A-Z]{2,5})\b")
analyzer = SentimentIntensityAnalyzer()


# ---------------------------------------------------------------- sources
def gather():
    """Return candidate dicts: sym, mentions, mentions_prev, engagement, sent, subs."""
    if os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET"):
        try:
            cands = _from_praw()
            if cands:
                return cands
            print("[warn] Reddit API returned nothing; using ApeWisdom")
        except Exception as e:
            print(f"[warn] Reddit API failed ({e}); using ApeWisdom")
    return _from_apewisdom()


def _from_apewisdom():
    """Fetch one or more comma-separated ApeWisdom feeds and merge by ticker."""
    agg = {}
    for filt in [f.strip() for f in APEWISDOM_FILTER.split(",") if f.strip()]:
        url = f"https://apewisdom.io/api/v1.0/filter/{filt}/page/1"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            print(f"[warn] apewisdom {filt}: {e}")
            continue
        for x in results:
            sym = (x.get("ticker") or "").upper()
            if not sym:
                continue
            a = agg.setdefault(sym, {"name": "", "mentions": 0, "mentions_prev": 0,
                                     "engagement": 0, "subs": set()})
            a["name"] = a["name"] or html.unescape(x.get("name") or "")
            a["mentions"] += int(x.get("mentions") or 0)
            a["mentions_prev"] += int(x.get("mentions_24h_ago") or 0)
            a["engagement"] += int(x.get("upvotes") or 0)
            a["subs"].add(filt.lower())
    return [{
        "sym": s, "name": a["name"], "mentions": a["mentions"],
        "mentions_prev": a["mentions_prev"], "engagement": a["engagement"],
        "sent": None,                     # ApeWisdom doesn't expose per-post sentiment
        "subs": a["subs"],
    } for s, a in agg.items()]


def _from_praw():
    import praw
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=USER_AGENT, check_for_async=False,
    )
    reddit.read_only = True
    agg = {}
    for name in SUBREDDITS:
        sub = reddit.subreddit(name)
        for stream in (sub.hot(limit=POSTS_PER_SUB), sub.rising(limit=25)):
            for p in stream:
                if getattr(p, "stickied", False):
                    continue
                text = f"{p.title or ''} {(p.selftext or '')[:600]}"
                comp = analyzer.polarity_scores(text)["compound"]
                for sym in extract(text):
                    a = agg.setdefault(sym, {"mentions": 0, "engagement": 0,
                                             "_s": 0.0, "subs": set()})
                    a["mentions"] += 1
                    a["engagement"] += int(p.score or 0) + int(p.num_comments or 0)
                    a["_s"] += comp
                    a["subs"].add(name.lower())

    prev = load_state()
    save_state({s: a["mentions"] for s, a in agg.items()})
    return [{
        "sym": s, "mentions": a["mentions"], "mentions_prev": prev.get(s, 0),
        "engagement": a["engagement"], "sent": a["_s"] / max(a["mentions"], 1),
        "subs": a["subs"],
    } for s, a in agg.items()]


def extract(text):
    found = set()
    for m in CASHTAG.findall(text):
        found.add(m.upper())
    for m in BARE.findall(text):
        if m not in BLOCKLIST:
            found.add(m)
    return found


# ---------------------------------------------------------------- ticker validation
_valid, _exch = {}, {}
def is_real_ticker(sym):
    if sym in _valid:
        return _valid[sym]
    ok = False
    try:
        t = yf.Ticker(sym)
        ok = not t.history(period="5d").empty
        if ok:
            md = getattr(t, "history_metadata", {}) or {}
            _exch[sym] = (md.get("exchangeName") or md.get("fullExchangeName") or "")
    except Exception:
        ok = False
    _valid[sym] = ok
    return ok


def tradeable(sym):
    """Keep US major-exchange names (what Webull trades); drop OTC/pink/foreign."""
    if os.getenv("EXCHANGE_FILTER", "1") != "1":
        return True
    code = (_exch.get(sym) or "").upper()
    if not code:
        return True  # unknown -> don't over-filter
    if any(x in code for x in ("PNK", "OTC", "PINK", "EXPM")):
        return False
    if any(x in code for x in ("NMS", "NGM", "NCM", "NYQ", "NYE", "ASE", "PCX",
                               "BATS", "BTS", "BZX", "NASDAQ", "NYSE", "ARCA",
                               "AMERICAN", "CBOE")):
        return True
    if os.getenv("INCLUDE_CANADIAN", "0") == "1" and any(
            x in code for x in ("TOR", "VAN", "CVE", "NEO", "TSX", "TORONTO", "VENTURE")):
        return True
    return False


# ---------------------------------------------------------------- pricing
def _g(fi, *keys):
    for k in keys:
        try:
            v = fi[k]
        except Exception:
            v = getattr(fi, k, None)
        if v is not None:
            return v
    return None


def enrich(sym):
    out = {"price": None, "pct": None, "vol_ratio": None, "mcap": None}
    try:
        fi = yf.Ticker(sym).fast_info
        price = _g(fi, "last_price", "lastPrice")
        prev = _g(fi, "previous_close", "previousClose")
        vol = _g(fi, "last_volume", "lastVolume")
        avg = _g(fi, "three_month_average_volume", "threeMonthAverageVolume",
                 "ten_day_average_volume", "tenDayAverageVolume")
        mcap = _g(fi, "market_cap", "marketCap")
        out["price"] = float(price) if price else None
        out["mcap"] = float(mcap) if mcap else None
        if price and prev:
            out["pct"] = (float(price) - float(prev)) / float(prev) * 100.0
        if vol and avg:
            out["vol_ratio"] = float(vol) / float(avg)
    except Exception as e:
        print(f"[warn] enrich {sym}: {e}")
    return out


def risk_flags(data, subs):
    flags = []
    p, pct, vr, mc = data["price"], data["pct"], data["vol_ratio"], data["mcap"]
    if p is not None and p < 5:
        flags.append("penny <$5")
    if mc is not None and mc < 300_000_000:
        flags.append("micro-cap")
    if pct is not None and pct >= 20:
        flags.append(f"already +{pct:.0f}% (chasing risk)")
    if vr is not None and vr >= 3:
        flags.append(f"vol {vr:.0f}x normal")
    if subs & HIGH_RISK_SUBS:
        flags.append("squeeze/penny sub")
    return flags


# ---------------------------------------------------------------- state (PRAW path)
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(counts):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(counts, f)
    except Exception as e:
        print(f"[warn] save state: {e}")


# ---------------------------------------------------------------- digest
def arrow(now, prev):
    if not prev:
        return "✦"          # new on the board
    if now > prev * 1.15:
        return "↑"
    if now < prev * 0.85:
        return "↓"
    return "→"


def format_digest(results):
    today = datetime.now(timezone.utc).astimezone().strftime("%b %d")
    lines = [f"Reddit Radar — {today}", "Trends only · not advice · high risk", ""]
    if not results:
        lines.append("No clear ticker signal today.")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        price = f"${r['price']:.2f}" if r.get("price") else "n/a"
        pct = f" ({r['pct']:+.0f}%)" if r.get("pct") is not None else ""
        dot = ""
        if r.get("sent") is not None:
            dot = " " + ("🟢" if r["sent"] > 0.15 else "🔴" if r["sent"] < -0.15 else "⚪")
        nm = r.get("name")
        tag = f" ({nm[:22]})" if nm else ""
        lines.append(f"{i}. ${r['sym']}{tag} {price}{pct} {r['mom']}")
        sub = f"   {r['mentions']} mentions"
        if r.get("mentions_prev"):
            sub += f" (was {r['mentions_prev']})"
        lines.append(sub + dot)
        if r["flags"]:
            lines.append(f"   ⚠️ {', '.join(r['flags'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def main():
    cands = gather()
    print(f"gathered {len(cands)} candidate tickers")
    cands.sort(key=lambda c: c["mentions"] + 0.25 * math.log1p(max(c["engagement"], 0)),
               reverse=True)

    results = []
    for c in cands:
        if len(results) >= TOP_N:
            break
        if len(_valid) >= MAX_VALIDATE and c["sym"] not in _valid:
            continue
        if not is_real_ticker(c["sym"]):
            continue
        if not tradeable(c["sym"]):
            continue
        data = enrich(c["sym"])
        results.append({
            **c, **data,
            "mom": arrow(c["mentions"], c["mentions_prev"]),
            "flags": risk_flags(data, c["subs"]),
        })

    msg = format_digest(results)
    print("\n" + msg)
    notify.send(msg)


if __name__ == "__main__":
    main()
