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


FEED_STATUS = {}   # feed name -> tickers returned, or None if the feed failed


def _from_apewisdom():
    """Fetch one or more comma-separated ApeWisdom feeds and merge by ticker."""
    agg = {}
    for filt in [f.strip() for f in APEWISDOM_FILTER.split(",") if f.strip()]:
        url = f"https://apewisdom.io/api/v1.0/filter/{filt}/page/1"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])
            FEED_STATUS[filt] = len(results)
        except Exception as e:
            print(f"[warn] apewisdom {filt}: {e}")
            FEED_STATUS[filt] = None
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
_valid, _exch, _hist5 = {}, {}, {}
def is_real_ticker(sym):
    if sym in _valid:
        return _valid[sym]
    ok = False
    try:
        t = yf.Ticker(sym)
        h = t.history(period="5d")
        ok = not h.empty
        if ok:
            # keep the frame — the dashboard's range band and sparkline reuse it
            # rather than paying for a second download per ticker
            try:
                _hist5[sym] = [float(c) for c in h["Close"].tolist() if c == c]
            except Exception:
                pass
            md = getattr(t, "history_metadata", {}) or {}
            _exch[sym] = (md.get("exchangeName") or md.get("fullExchangeName") or "")
    except Exception:
        ok = False
    _valid[sym] = ok
    return ok


_info_cache = {}
def ticker_info(sym):
    """yf .info is a slow call — fetch at most once per symbol per run."""
    if sym not in _info_cache:
        try:
            _info_cache[sym] = yf.Ticker(sym).info or {}
        except Exception:
            _info_cache[sym] = {}
    return _info_cache[sym]


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


def news_tone(headlines):
    """
    Tone of the coverage, from VADER over the headlines already fetched.

    This is NOT Reddit sentiment — ApeWisdom returns mention counts only, never
    post text, so calling it Reddit sentiment would be inventing a measurement.
    It answers a narrower question honestly: is the news around this name being
    written up positively or negatively right now.
    """
    if not headlines:
        return None
    scores = [analyzer.polarity_scores(h)["compound"] for h in headlines]
    avg = sum(scores) / len(scores)
    label = "positive" if avg >= 0.15 else "negative" if avg <= -0.15 else "mixed"
    return {"label": label, "score": round(avg, 2)}


def crowd_read(pct, mom, tone):
    """
    Price direction crossed with attention direction — the thing a mention count
    alone cannot tell you: whether the crowd is arriving before a move, chasing
    one, or catching a falling knife.
    """
    if pct is None:
        return None
    rising = mom == "↑"
    if pct <= -5 and rising:
        return ["falling knife", "bad",
                "Price is dropping while chatter climbs — the crowd is arriving into a decline."]
    if pct >= 10 and rising:
        return ["chasing", "warn",
                "Already run hard today and the crowd is still piling in — late-arrival risk."]
    if pct >= 2 and rising:
        return ["moving with attention", "good",
                "Up on the day with attention still building."]
    if pct <= -5:
        return ["fading", "muted",
                "Down on the day and the chatter is cooling with it."]
    if tone and tone["label"] == "negative" and rising:
        return ["negative buzz", "bad",
                "Attention is rising but the coverage around it is negative."]
    return ["quiet", "muted", "No strong pattern between price and attention today."]


def _num(v):
    """float(v) or None — yfinance mixes NaN, None and strings across frames."""
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pick(row, *names):
    for n in names:
        try:
            v = row[n]
        except Exception:
            continue
        got = _num(v)
        if got is not None:
            return got
    return None


def earnings_detail(sym, days=45):
    """
    Next earnings date, the EPS the street expects, and how this name has
    handled recent reports.

    Built on .calendar and .earnings_history rather than .earnings_dates: the
    latter scrapes a Yahoo page and returned nothing usable for every ticker,
    while .calendar demonstrably works. The beat record is history and is
    labelled that way wherever it is shown — never as a read on the next print.
    """
    out = {}
    today = datetime.now().date()
    t = yf.Ticker(sym)

    try:
        cal = t.calendar
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            for d in (raw if isinstance(raw, list) else [raw]):
                if d is None:
                    continue
                if hasattr(d, "date"):
                    d = d.date()
                delta = (d - today).days
                if 0 <= delta <= days:
                    out["date"] = d.strftime("%b %d")
                    out["days_away"] = delta
                    break
            est = (_num(cal.get("Earnings Average"))
                   or _num(cal.get("EPS Estimate Avg")))
            if est is not None:
                out["eps_estimate"] = round(est, 2)
    except Exception as e:
        print(f"[warn] earnings calendar {sym}: {e}")

    if "eps_estimate" not in out:
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty and "0q" in ee.index:
                est = _pick(ee.loc["0q"], "avg", "Avg")
                if est is not None:
                    out["eps_estimate"] = round(est, 2)
        except Exception:
            pass

    try:
        eh = t.earnings_history
        if eh is not None and not eh.empty:
            surprises = []
            for _, row in eh.iterrows():
                s = _pick(row, "surprisePercent", "Surprise(%)", "epsDifference")
                if s is not None:
                    surprises.append(s)
            if surprises:
                recent = surprises[-4:]          # frame runs oldest -> newest
                beats = sum(1 for s in recent if s > 0)
                out["beat_record"] = f"beat {beats} of last {len(recent)}"
                out["avg_surprise"] = round(sum(recent) / len(recent) * 100, 1) \
                    if max(abs(s) for s in recent) < 1 else round(sum(recent) / len(recent), 1)
                if beats >= 3:
                    out["expect"] = ["good", "Beaten estimates in most recent quarters"]
                elif beats <= 1:
                    out["expect"] = ["bad", "Missed estimates in most recent quarters"]
                else:
                    out["expect"] = ["mixed", "Mixed record against estimates"]
    except Exception as e:
        print(f"[warn] earnings history {sym}: {e}")

    return out or None


_chart_cache = {}
def price_chart(sym, period="1y", interval="1wk"):
    """Weekly closes for the detail view's long-term chart (~52 points)."""
    if sym in _chart_cache:
        return _chart_cache[sym]
    out = None
    try:
        h = yf.Ticker(sym).history(period=period, interval=interval)
        if not h.empty:
            closes, dates = [], []
            for idx, c in h["Close"].items():
                if c != c:
                    continue
                closes.append(round(float(c), 2))
                dates.append(idx.strftime("%Y-%m-%d"))
            if len(closes) >= 8:
                out = {"period": period, "closes": closes, "dates": dates}
    except Exception as e:
        print(f"[warn] chart {sym}: {e}")
    _chart_cache[sym] = out
    return out


def analyst_info(sym):
    """Return (consensus: str|None, target_price: float|None) from Yahoo."""
    try:
        info = ticker_info(sym)
        rec = (info.get("recommendationKey") or "").upper()
        target = info.get("targetMeanPrice")
        return rec or None, float(target) if target else None
    except Exception:
        return None, None


def _human(n):
    """1234567 -> '$1.2M'. Returns None when the value is missing."""
    if not isinstance(n, (int, float)) or n != n:
        return None
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= cutoff:
            return f"{n / cutoff:.1f}{suffix}"
    return f"{n:.0f}"


def key_stats(sym):
    """Rows for the detail screen's 'Key stats' block. Missing fields dropped."""
    i = ticker_info(sym)
    mc, fl = _human(i.get("marketCap")), _human(i.get("floatShares"))
    av, vol = _human(i.get("averageVolume")), _human(i.get("volume"))
    si = i.get("shortPercentOfFloat")
    lo, hi = i.get("fiftyTwoWeekLow"), i.get("fiftyTwoWeekHigh")
    rows = [
        ("Market cap", f"${mc}" if mc else None),
        ("Float", fl),
        ("Short interest", f"{si * 100:.1f}%" if isinstance(si, (int, float)) else None),
        ("Avg vol", av),
        ("Vol today", vol),
        ("52w range", f"${lo:.2f} – ${hi:.2f}"
         if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else None),
    ]
    return [[k, v] for k, v in rows if v]


def band_and_spark(sym, price):
    """5-session low/high band plus the closes behind it, from the cached frame."""
    closes = _hist5.get(sym) or []
    if not closes:
        return None, []
    lo, hi = min(closes), max(closes)
    if isinstance(price, (int, float)):     # today's move can exceed the 5d frame
        lo, hi = min(lo, price), max(hi, price)
    if hi <= lo:
        hi = lo * 1.01 + 0.01
    return [lo, hi], closes


def reason_text(r):
    """
    Plain-language 'why it's here'. Assembled from what the scan actually saw —
    never a forecast, and never worded as one.
    """
    sym, bits = r["sym"], []
    mentions, prev = r.get("mentions") or 0, r.get("mentions_prev") or 0
    if mentions and prev:
        mult = mentions / max(prev, 1)
        if mult >= 2:
            bits.append(f"{mentions:,} mentions today, {mult:.0f}x yesterday's {prev:,}")
        elif r.get("mom") == "↑":
            bits.append(f"{mentions:,} mentions, up from {prev:,} yesterday")
        elif r.get("mom") == "↓":
            bits.append(f"{mentions:,} mentions, cooling from {prev:,}")
        else:
            bits.append(f"{mentions:,} mentions, flat against yesterday")
    elif mentions:
        bits.append(f"{mentions:,} mentions and new to the board")

    pct = r.get("pct")
    if isinstance(pct, (int, float)):
        if pct >= 10:
            bits.append(f"already up {pct:.0f}% today — the crowd is arriving after the move")
        elif pct <= -10:
            bits.append(f"down {abs(pct):.0f}% today while chatter keeps climbing")
        else:
            bits.append(f"price is {pct:+.1f}% — the attention has moved further than the price")

    rec, target, price = r.get("analyst_rec"), r.get("analyst_target"), r.get("price")
    if rec and target and price:
        up = (target - price) / price * 100
        bits.append(f"analyst consensus is {rec.replace('_', ' ').lower()} "
                    f"with a ${target:,.2f} mean target ({up:+.0f}%)")
    if r.get("earnings"):
        bits.append(f"earnings land {r['earnings']}")

    flags = r.get("flags") or []
    if flags:
        bits.append("the scan flagged " + ", ".join(flags))

    if not bits:
        return f"{sym} surfaced on mention volume alone; nothing else stood out."
    cap = lambda s: s[0].upper() + s[1:] if s else s
    if len(bits) == 1:
        return cap(bits[0]) + "."
    # first clause is its own sentence; the rest run on semicolons after it
    return f"{cap(bits[0])}. {cap(bits[1])}" + \
           ("; " + "; ".join(bits[2:]) if len(bits) > 2 else "") + "."


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
        price = float(q.get("regularMarketPrice") or 0) or None
        w = {
            "sym": sym,
            "name": q.get("shortName") or q.get("longName") or "",
            "price": price,
            "pct": pct,
            "analyst_rec": rec,
            "analyst_target": target,
            "headlines": news_headlines(sym, n=3),
            "earnings": earnings_soon(sym),
            "stats": key_stats(sym),
            "source": "yahoo",
        }
        is_real_ticker(sym)      # warms the 5d frame the band/sparkline need
        w["band"], w["spark"] = band_and_spark(sym, price)
        w["tone"] = news_tone(w["headlines"])
        w["read"] = crowd_read(pct, None, w["tone"])
        w["er"] = earnings_detail(sym)
        w["chart"] = price_chart(sym)
        w["reason"] = reason_text(w)
        out.append(w)
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
    # the screener does not return them in order; "biggest movers" must be sorted
    out.sort(key=lambda g: g["pct"], reverse=True)
    return out[:n]


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

    # ── 🎯 Picks of the day ────────────────────────────────────────
    # Same three section names as the dashboard, in the same order, so the
    # notification and the board can never disagree about what to act on.
    L += ["## 🎯 Picks of the day",
          "*Act on these first — rising chatter or an analyst buy, room to move*", ""]

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
        L += ["*No picks today — nothing cleared the filter.*", ""]

    # The strictest test the radar applies (up today AND gaining attention) fires
    # so rarely that it used to read as "don't buy anything" on its own line every
    # day. It belongs as a highlight on the picks above, not as its own section.
    picks = _standouts(results, mkt)
    if picks:
        L += ["**⭐ Strongest of these** — also up today and still gaining attention:", ""]
        for sym, name, why in picks:
            nm = f" ({name[:18]})" if name else ""
            L.append(f"**${sym}**{nm}: {', '.join(why)}")
        L += ["", "*→ Find out WHY it's moving before risking a cent.*", ""]

    # Reddit Buzz and Biggest movers are computed (picks are derived from the
    # buzz ranking) but no longer sent: both lost money over the whole recorded
    # history, and a digest that lists them invites acting on them.
    return "\n".join(L)


# ---------------------------------------------------------------- dashboard
DASHBOARD_DIR = os.getenv("DASHBOARD_DIR", "docs")


# long enough to keep the whole reconstructed record (mid-June onward), which a
# 30-day window silently dropped
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "400"))


def _outcome(pct):
    """How a flagged name actually did. Thresholds are deliberately wide —
    anything inside ±10% on a meme ticker is noise, not a result."""
    if pct >= 10:
        return "worked"
    if pct <= -10:
        return "faded"
    return "flat"


def split_factor(sym, since, _cache={}):
    """Cumulative split ratio applied to `sym` after `since` (YYYY-MM-DD).

    Prices are recorded raw at flag time, but a split changes the scale of every
    later price. CXAI's 1:50 reverse split made a $0.20 entry look like a 2030%
    gain against a $4.26 quote, on a stock that had actually fallen 58%.

    yfinance reports a 2:1 forward split as 2.0 and a 1:50 reverse as 0.02, so
    the recorded price divided by the product of later ratios lands back on
    today's scale. Cached per run: this is called once per symbol in the record.
    """
    if sym not in _cache:
        try:
            _cache[sym] = {ts.strftime("%Y-%m-%d"): float(r)
                           for ts, r in yf.Ticker(sym).splits.items() if r}
        except Exception as e:
            print(f"[warn] splits {sym}: {e}")
            _cache[sym] = {}
    f = 1.0
    for d, ratio in _cache[sym].items():
        if d > since:
            f *= ratio
    return f


def update_history(watch, results):
    """
    Maintain docs/history.json: what the radar flagged, at what price, and where
    that price sits now. This is the only honest way to show a track record —
    it records the call before the outcome is known, then marks it to market.
    """
    path = os.path.join(DASHBOARD_DIR, "history.json")
    try:
        with open(path) as f:
            hist = json.load(f)
    except Exception:
        hist = []

    today = datetime.now(timezone.utc).date()
    # Dated in ET, not UTC: the schedule is built around market hours, and a late
    # close run (20:00 UTC plus GitHub's lag) would otherwise file a Friday pick
    # under Saturday.
    try:
        from zoneinfo import ZoneInfo
        market_day = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        market_day = today
    # today's live prices, for marking every open entry to market
    live = {r["sym"]: r.get("price") for r in results if r.get("price")}
    live.update({w["sym"]: w.get("price") for w in watch if w.get("price")})

    # The cron is Mon-Fri, so only a hand-run lands here at a weekend. Prices are
    # Friday's stale close, and filing it would invent a pick day the market
    # never had — mark the record to market, but do not add to it.
    # A price-refresh run re-prices what is already recorded but must not add to
    # the pick record: refreshing every 15 minutes would otherwise file every
    # name that passed the filter at any point in the day, which is a different
    # and much looser record than three considered snapshots.
    record = os.getenv("RECORD_PICKS", "1") == "1"
    if not record:
        print("[history] RECORD_PICKS=0 — marking to market, not recording picks")
    weekend = market_day.weekday() >= 5
    if weekend:
        print(f"[history] {market_day} is a {market_day.strftime('%A')} — "
              f"marking to market, not recording a pick day")

    flagged_today = {e["sym"] for e in hist if e.get("date") == market_day.isoformat()}
    for w in (() if (weekend or not record) else watch):
        if w["sym"] in flagged_today or not w.get("price"):
            continue
        hist.append({
            "date": market_day.isoformat(), "sym": w["sym"],
            "name": (w.get("name") or "")[:40],
            "flag_price": w["price"], "last_price": w["price"],
            "pct": 0.0, "outcome": "flat",
            "note": (w.get("headlines") or [""])[0][:90],
        })

    kept = []
    for e in hist:
        try:
            age = (today - datetime.fromisoformat(e["date"]).date()).days
        except Exception:
            continue
        if age > HISTORY_DAYS:
            continue
        now = live.get(e["sym"])
        if now and e.get("flag_price"):
            # Rescale the recorded price for any split since it was flagged, and
            # keep the correction: leaving it would mean recomputing a wrong
            # percentage on every run for the rest of the record's life.
            f = split_factor(e["sym"], e["date"])
            if f != 1.0:
                e["flag_price"] = round(e["flag_price"] / f, 6)
                e["split_adjusted"] = True
                print(f"[history] {e['sym']} {e['date']}: split x{1/f:.4g} — "
                      f"entry rescaled to {e['flag_price']}")
            e["last_price"] = now
            e["pct"] = (now - e["flag_price"]) / e["flag_price"] * 100
            e["outcome"] = _outcome(e["pct"])
        e["age_days"] = age
        kept.append(e)

    kept.sort(key=lambda e: (e["date"], e["sym"]), reverse=True)
    try:
        with open(path, "w") as f:
            json.dump(kept, f, indent=1)
        print(f"[dashboard] history: {len(kept)} entries")
    except Exception as e:
        print(f"[warn] history write: {e}")
    return kept


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
        "headlines": (r.get("headlines") or [])[:3],
        "band": r.get("band"),
        "spark": r.get("spark") or [],
        "stats": r.get("stats") or [],
        "reason": r.get("reason") or "",
        "tone": r.get("tone"),
        "read": r.get("read"),
        "er": r.get("er"),
        "chart": r.get("chart"),
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

    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    history = update_history(watch, results)
    up = [r for r in results if (r.get("pct") or 0) > 0]

    payload = {
        "generated_at": now.isoformat(),
        "watch": watch,
        "buzz": [_dash_ticker(r) for r in results],
        "gainers": [_dash_ticker(g) for g in gainers],
        "standouts": [{"sym": s, "name": n, "why": w}
                      for s, n, w in _standouts(results, gainers)],
        "session": {
            "tracked": len(results),
            "up": len(up),
            "down": len(results) - len(up),
            "flagged": len(watch),
            "standouts": len(_standouts(results, gainers)),
        },
        "history": history,
        # which Reddit feeds answered this run, and with how many tickers.
        # In the payload rather than only the log, because a feed that quietly
        # stops resolving is invisible in a truncated Actions log tail.
        "feeds": FEED_STATUS,
    }
    try:
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
        r["headlines"] = news_headlines(r["sym"], n=3)
        r["why"] = r["headlines"][0] if r["headlines"] else ""
        r["label"] = momentum_label(r.get("pct"), r.get("mom") == "↑")
        r["analyst_rec"], r["analyst_target"] = analyst_info(r["sym"])
        r["earnings"] = earnings_soon(r["sym"])
        r["band"], r["spark"] = band_and_spark(r["sym"], r.get("price"))
        r["stats"] = key_stats(r["sym"])
        r["tone"] = news_tone(r["headlines"])
        r["read"] = crowd_read(r.get("pct"), r.get("mom"), r["tone"])
        r["er"] = earnings_detail(r["sym"])
        r["chart"] = price_chart(r["sym"])
        r["reason"] = reason_text(r)     # needs the fields set above

    gainers = market_gainers(int(os.getenv("GAINERS_N", "5")))
    for g in gainers:
        g["headlines"] = news_headlines(g["sym"])
        g["why"] = g["headlines"][0] if g["headlines"] else ""

    # Yahoo most-actives overlap the Reddit watch list; keep the Reddit entry
    # (it carries mention counts) and drop the duplicate rather than showing
    # the same ticker twice at two slightly different prices.
    seen = {r["sym"] for r in _worth_watching(results)}
    yahoo_watches = [w for w in yahoo_watchlist(n=8) if w["sym"] not in seen][:4]

    write_dashboard(results, gainers, yahoo_watches)

    msg = format_message(results, gainers, yahoo_watches)
    print("\n" + msg)
    today = datetime.now(timezone.utc).astimezone().strftime("%b %d")
    notify.send(msg, subject=f"Stock Radar · {today}")

    # Last line of the step on purpose: the digest is ~90 lines and the Pages
    # steps add ~110 more, so anything printed earlier falls outside a readable
    # log tail. A feed that quietly stops resolving should not be easy to miss.
    if FEED_STATUS:
        ok = [f"{k}={v}" for k, v in FEED_STATUS.items() if v is not None]
        dead = [k for k, v in FEED_STATUS.items() if v is None]
        print(f"[feeds] ok: {', '.join(ok) or 'none'}"
              + (f" | FAILED: {', '.join(dead)}" if dead else ""))


if __name__ == "__main__":
    main()
