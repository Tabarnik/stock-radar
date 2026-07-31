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
TOP_N = int(os.getenv("TOP_N", "10"))
MAX_VALIDATE = int(os.getenv("MAX_VALIDATE", "50"))
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


def news_headlines(sym, n=2):
    """Return up to n Yahoo headlines for a ticker."""
    out = []
    try:
        for it in (yf.Ticker(sym).news or []):
            title = it.get("title") or (it.get("content") or {}).get("title")
            if title:
                out.append(title.strip())
            if len(out) >= n:
                break
    except Exception as e:
        print(f"[warn] news {sym}: {e}")
    return out


def earnings_soon(sym, days=7):
    """Return 'Mon DD (Nd)' if earnings fall within `days` days, else None."""
    try:
        cal = yf.Ticker(sym).calendar
        dates = []
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            dates = raw if isinstance(raw, list) else [raw]
        elif cal is not None and hasattr(cal, "columns"):
            dates = list(cal.columns)
        today = datetime.now().date()
        for d in dates:
            if d is None:
                continue
            if hasattr(d, "date"):
                d = d.date()
            try:
                delta = (d - today).days
            except Exception:
                continue
            if 0 <= delta <= days:
                return f"{d.strftime('%b %d')} ({delta}d)"
    except Exception as e:
        print(f"[warn] earnings {sym}: {e}")
    return None


def analyst_info(sym):
    """Return (consensus: str|None, target_price: float|None) from Yahoo."""
    try:
        info = yf.Ticker(sym).info
        rec = (info.get("recommendationKey") or "").upper()
        target = info.get("targetMeanPrice")
        return rec or None, float(target) if target else None
    except Exception:
        return None, None


def yahoo_watchlist(n=5):
    """
    Yahoo most-actives screen filtered to analyst-buy stocks that haven't
    run yet. Second data source alongside Reddit for the watch section.
    """
    out = []
    try:
        quotes = (yf.screen("most_actives") or {}).get("quotes", [])
    except Exception as e:
        print(f"[warn] yahoo_watchlist: {e}")
        return []
    for q in quotes:
        sym = q.get("symbol")
        if not sym:
            continue
        exch = (q.get("fullExchangeName") or "").upper()
        if any(x in exch for x in ("OTC", "PINK")):
            continue
        pct = q.get("regularMarketChangePercent")
        if pct is None:
            continue
        pct = float(pct)
        if pct >= 15 or pct <= -10:   # already ran or already dumped
            continue
        rec, target = analyst_info(sym)
        if not rec or "BUY" not in rec:  # BUY or STRONG_BUY
            continue
        price = q.get("regularMarketPrice")
        out.append({
            "sym": sym,
            "name": q.get("shortName") or q.get("longName") or "",
            "price": float(price) if price else None,
            "pct": pct,
            "analyst_rec": rec,
            "analyst_target": target,
            "headlines": news_headlines(sym),
            "earnings": earnings_soon(sym),
            "source": "yahoo",
        })
        if len(out) >= n:
            break
    return out


def momentum_label(pct, rising):
    """Honest, descriptive tag — never a prediction."""
    if pct is None:
        return ""
    if pct <= -10:
        return "🔻 already dumped today" + (" (crowd still piling in)" if rising else "")
    if pct >= 10:
        return "📈 running today" + (" + chatter rising" if rising else "")
    return "chatter rising" if rising else ""


def market_gainers(n):
    """Today's biggest US-exchange % gainers (factual — NOT a prediction)."""
    try:
        quotes = (yf.screen("day_gainers") or {}).get("quotes") or []
    except Exception as e:
        print(f"[warn] gainers screen: {e}")
        return []
    out = []
    for q in quotes:
        sym, pct = q.get("symbol"), q.get("regularMarketChangePercent")
        exch = (q.get("fullExchangeName") or "").upper()
        if not sym or pct is None or any(x in exch for x in ("OTC", "PINK")):
            continue
        out.append({"sym": sym, "pct": float(pct),
                    "price": q.get("regularMarketPrice"),
                    "name": q.get("shortName") or q.get("longName") or ""})
        if len(out) >= n:
            break
    return out


def _fmt_price(p):
    return f"${p:.2f}" if isinstance(p, (int, float)) else "n/a"


def _analyst_line(r):
    rec, target, price = r.get("analyst_rec"), r.get("analyst_target"), r.get("price")
    parts = []
    if rec:
        parts.append(f"Analysts: **{rec}**")
    if target and price:
        upside = (target - price) / price * 100
        parts.append(f"Target ${target:.2f} ({upside:+.0f}%)")
    return f"→ {' | '.join(parts)}" if parts else ""


def _buzz_md(r):
    pct = f" · {r['pct']:+.0f}% today" if r.get("pct") is not None else ""
    nm = f" ({r['name'][:20]})" if r.get("name") else ""
    out = [f"**${r['sym']}**{nm} · {_fmt_price(r.get('price'))}{pct} · {r['mentions']} mentions {r['mom']}"]
    if r.get("label"):
        out.append(r["label"])
    if r.get("flags"):
        out.append(f"⚠️ {', '.join(r['flags'])}")
    al = _analyst_line(r)
    if al:
        out.append(al)
    if r.get("earnings"):
        out.append(f"📅 Earnings: {r['earnings']}")
    for h in (r.get("headlines") or [])[:2]:
        out.append(f"📰 {h[:80]}")
    out.append("")
    return out


SECTION_SEP = "_" * 30
MIN_STANDOUT_PCT = 2.0   # a standout must actually be up, not flat noise


def _worth_watching(results):
    """
    Rising Reddit attention, price hasn't moved yet — the pattern that sometimes
    precedes a run. Research signal only, not a prediction.
    """
    watches = []
    for r in results:
        if r.get("mom") != "↑":
            continue
        pct = r.get("pct") or 0.0
        if pct >= 20 or pct <= -15:   # already spiked, or already dumped
            continue
        watches.append(r)
    return watches


def _standouts(results, mkt):
    """Names that are BOTH up today AND gaining attention. Descriptive, not advice."""
    mkt_syms = {g["sym"] for g in mkt}
    picks = []
    for r in results:
        pct, rising = r.get("pct"), r.get("mom") == "↑"
        if pct is None or pct < MIN_STANDOUT_PCT or not rising:
            continue
        why = [f"up {pct:+.0f}% today"]
        prev = r.get("mentions_prev") or 0
        if prev:
            mult = r["mentions"] / max(prev, 1)
            why.append(f"mentions {mult:.0f}x vs yesterday" if mult >= 2 else "mentions rising")
        else:
            why.append("new + rising chatter")
        if r["sym"] in mkt_syms:
            why.append("also a top market gainer")
        picks.append((r["sym"], r.get("name", ""), why))
    return picks


def format_message(results, mkt, yahoo_watches=None):
    today = datetime.now(timezone.utc).astimezone().strftime("%b %d")
    L = [f"*Trends only · not advice · high risk · {today}*", ""]

    # ── 🔭 Worth Watching ──────────────────────────────────────────
    L += ["## 🔭 Worth Watching",
          "*Rising chatter or analyst buy, room to move — research only*", ""]

    def _watch_md(r, source_tag):
        nm = f" ({r['name'][:18]})" if r.get("name") else ""
        pct_s = f" · {r['pct']:+.0f}% today" if r.get("pct") is not None else ""
        block = [f"**${r['sym']}**{nm} · {_fmt_price(r.get('price'))}{pct_s} · {source_tag}"]
        al = _analyst_line(r)
        if al:
            block.append(al)
        if r.get("earnings"):
            block.append(f"📅 Earnings: {r['earnings']}")
        for h in (r.get("headlines") or [])[:2]:
            block.append(f"📰 {h[:80]}")
        block.append("")
        return block

    reddit_watches = _worth_watching(results)
    for r in reddit_watches[:4]:
        prev = r.get("mentions_prev") or 0
        tag = (f"{r['mentions']/max(prev,1):.0f}x Reddit mentions"
               if prev else "new to Reddit radar")
        L += _watch_md(r, tag)
    for r in (yahoo_watches or [])[:4]:
        L += _watch_md(r, "Yahoo most-active")
    if not reddit_watches and not yahoo_watches:
        L += ["*Nothing stands out today.*", ""]

    # ── ⭐ Worth a Closer Look ─────────────────────────────────────
    L += ["---", "## ⭐ Worth a Closer Look",
          "*Research starting point — not a buy signal*", ""]
    picks = _standouts(results, mkt)
    if picks:
        for sym, name, why in picks:
            nm = f" ({name[:18]})" if name else ""
            L.append(f"**${sym}**{nm}: {', '.join(why)}")
        L += ["", "*→ Find out WHY it's moving before risking a cent.*", ""]
    else:
        L += ["*Nothing is both up AND gaining attention today.*", ""]

    # ── 🔥 Reddit Buzz ────────────────────────────────────────────
    L += ["---", "## 🔥 Reddit Buzz", ""]
    if not results:
        L += ["*No clear signal today.*", ""]
    else:
        gain = [r for r in results if (r.get("pct") or 0) >= 0]
        decl = [r for r in results if (r.get("pct") or 0) < 0]
        if gain:
            L += ["🟢 **Gainers**", ""]
            for r in gain:
                L += _buzz_md(r)
        if decl:
            L += ["🔻 **Decliners**", ""]
            for r in decl:
                L += _buzz_md(r)

    # ── 📈 Market Gainers ─────────────────────────────────────────
    L += ["---", "## 📈 Market Gainers Today",
          "*Already up most — not a prediction*", ""]
    if not mkt:
        L.append("*Unavailable.*")
    else:
        for g in mkt:
            nm = f" ({g['name'][:18]})" if g.get("name") else ""
            L.append(f"**${g['sym']}**{nm} +{g['pct']:.0f}% · {_fmt_price(g.get('price'))}")
            for h in (g.get("headlines") or [])[:1]:
                L.append(f"📰 {h[:80]}")
            L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------- dashboard
DASHBOARD_DIR = os.getenv("DASHBOARD_DIR", "docs")


def _dash_ticker(r, extra=None):
    """Trim a result dict down to the fields the dashboard renders."""
    out = {
        "sym": r.get("sym"),
        "name": r.get("name") or "",
        "price": r.get("price"),
        "pct": r.get("pct"),
        "mentions": r.get("mentions"),
        "mentions_prev": r.get("mentions_prev"),
        "mom": r.get("mom"),
        "label": r.get("label") or "",
        "flags": r.get("flags") or [],
        "analyst_rec": r.get("analyst_rec"),
        "analyst_target": r.get("analyst_target"),
        "earnings": r.get("earnings"),
        "headlines": (r.get("headlines") or [])[:2],
    }
    if extra:
        out.update(extra)
    return out


def write_dashboard(results, gainers, yahoo_watches):
    """Dump the run's data as JSON for the HTML dashboard to render."""
    now = datetime.now(timezone.utc)
    watch = []
    for r in _worth_watching(results)[:4]:
        prev = r.get("mentions_prev") or 0
        tag = (f"{r['mentions']/max(prev,1):.0f}x Reddit mentions"
               if prev else "new to Reddit radar")
        watch.append(_dash_ticker(r, {"tag": tag, "source": "reddit"}))
    for r in (yahoo_watches or [])[:4]:
        watch.append(_dash_ticker(r, {"tag": "Yahoo most-active", "source": "yahoo"}))

    payload = {
        "generated_at": now.isoformat(),
        "watch": watch,
        "buzz": [_dash_ticker(r) for r in results],
        "gainers": [_dash_ticker(g) for g in gainers],
        "standouts": [{"sym": s, "name": n, "why": w}
                      for s, n, w in _standouts(results, gainers)],
    }
    try:
        os.makedirs(DASHBOARD_DIR, exist_ok=True)
        path = os.path.join(DASHBOARD_DIR, "data.json")
        with open(path, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"[dashboard] wrote {path}")
    except Exception as e:
        print(f"[warn] dashboard write: {e}")


# ---------------------------------------------------------------- main
def _et_gate():
    """For scheduled cloud runs, only proceed at an intended ET hour (DST-proof)."""
    targets_str = os.getenv("RUN_HOURS_ET") or os.getenv("RUN_HOUR_ET")
    if targets_str and os.getenv("GITHUB_EVENT_NAME") == "schedule":
        try:
            from zoneinfo import ZoneInfo
            hour = datetime.now(ZoneInfo("America/New_York")).hour
            targets = {int(h.strip()) for h in targets_str.split(",")}
            if hour not in targets:
                print(f"[skip] ET hour {hour} not in {sorted(targets)}; not sending")
                return False
        except Exception as e:
            print(f"[warn] et_gate: {e}")
    return True


def main():
    if not _et_gate():
        return
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

    # enrich top names with headlines, analyst data, earnings, and momentum label
    for r in results:
        r["headlines"] = news_headlines(r["sym"])
        r["why"] = r["headlines"][0] if r["headlines"] else ""
        r["label"] = momentum_label(r.get("pct"), r.get("mom") == "↑")
        r["analyst_rec"], r["analyst_target"] = analyst_info(r["sym"])
        r["earnings"] = earnings_soon(r["sym"])

    gainers = market_gainers(int(os.getenv("GAINERS_N", "5")))
    for g in gainers:
        g["headlines"] = news_headlines(g["sym"])
        g["why"] = g["headlines"][0] if g["headlines"] else ""

    yahoo_watches = yahoo_watchlist()

    write_dashboard(results, gainers, yahoo_watches)

    msg = format_message(results, gainers, yahoo_watches)
    print("\n" + msg)
    today = datetime.now(timezone.utc).astimezone().strftime("%b %d")
    notify.send(msg, subject=f"Stock Radar · {today}")


if __name__ == "__main__":
    main()
