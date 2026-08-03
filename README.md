# Reddit Stock Radar

A daily digest of which tickers Reddit is loudest about — ranked by mention
volume + 24h momentum, filtered to names tradeable on Webull, enriched with live
price, and flagged for penny/pump risk. Pushed to your phone every weekday morning.

> **This is an information tool. It is NOT financial advice and it never trades.**
> These communities are full of pump-and-dump activity. Treat every name as
> high-risk, **verify the ticker and price on Webull**, and do your own research —
> by the time something is "trending," early posters may be selling to you.

## Sample output
```
Stock Radar — Jun 15
Trends only · not advice · high risk

Stock Radar — Jun 15
Trends only · not advice · high risk

🔥 REDDIT SQUEEZE/PENNY BUZZ
🟢 GAINERS
  $XYZ (Some Co) $4.20 +18% · 22 mentions ↑
   📈 running today + chatter rising
   why: <latest headline>
🔻 DECLINERS
  $LFVN (Lifevantage) $6.46 -29% · 40 mentions ↑
   🔻 already dumped today (crowd still piling in)
   ⚠️ micro-cap, vol 4x normal, squeeze/penny sub
   why: LifeVantage pays a US$0.05 dividend...

📈 BIGGEST MARKET GAINERS TODAY
(already up most — NOT a prediction)
  $XNDU (Xanadu Quantum) +19%  $13.99
   why: Xanadu quantum breakthroughs reshape photonic hardware...
```
↑/↓/→ = mentions vs. 24h ago, ✦ = new on the board.

## It's already set up
On this Mac it runs **keyless** (no Reddit or Twilio account needed):
- **Data:** ApeWisdom public API — the **Shortsqueeze + pennystocks** feeds merged (squeeze/penny plays, not the big-cap firehose).
- **Alerts:** ntfy push (free).
- **Schedule:** launchd, **8:00 AM ET, Mon–Fri** (`com.nikolaslepore.stockradar`).

### The one thing you must do: subscribe to your alert channel
1. Install the **ntfy** app (iOS App Store / Google Play / F-Droid), or just open
   the topic URL in any browser.
2. Subscribe to your topic (the `NTFY_TOPIC` value in `.env`):
   **`https://ntfy.sh/<your-topic>`**
3. Done — the 8 AM push will arrive there. Anyone who knows the topic name can read
   it, so change it in `.env` to something only you know if you like (then re-subscribe).

## Run / check / stop
```bash
./run.sh                       # run once now (uses .env); logs to radar.log
NOTIFY_METHOD=print ./run.sh   # print only, send nothing
launchctl list | grep stockradar          # confirm it's scheduled
tail -f radar.log                          # watch output
# stop it:
launchctl unload ~/Library/LaunchAgents/com.nikolaslepore.stockradar.plist
```
**Caveat:** launchd only fires if the Mac is **awake** at 8 AM. If it's often
asleep, use the cloud option below instead.

## Optional upgrades
- **Cloud (runs with Mac off):** push this folder to a private GitHub repo and
  enable `.github/workflows/daily.yml` (also keyless — just set an `NTFY_TOPIC`
  secret). See the workflow file.
- **Real SMS instead of push:** set `NOTIFY_METHOD=twilio` + the Twilio vars in `.env`.
- **Add sentiment + custom subs:** register a free Reddit "script" app at
  <https://www.reddit.com/prefs/apps> and set `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
  The script auto-switches to the Reddit API and adds 🟢/🔴 sentiment dots.

## Config (`.env`)
| Var | Default | Meaning |
|---|---|---|
| `NOTIFY_METHOD` | `ntfy` | `ntfy` / `twilio` / `pushover` / `telegram` / `email` / `print` |
| `NTFY_TOPIC` | — | your private ntfy channel |
| `APEWISDOM_FILTER` | `Shortsqueeze,pennystocks` | comma-separated feeds, merged (`wallstreetbets`, `all-stocks`, …) |
| `EXCHANGE_FILTER` | `1` | keep only US major-exchange (Webull-tradeable) names |
| `INCLUDE_CANADIAN` | `0` | `1` also allows TSX/Toronto-listed names |
| `TOP_N` | `5` | how many tickers in the digest |

## How the ranking works (no magic)
Mention count (lightly weighted by upvotes), with a 24h momentum arrow, then
filtered to tradeable names and annotated with price + risk flags (penny <$5,
micro-cap, already-spiked ≥+20%, abnormal volume). It deliberately prints **no
price target or "expected profit"** — nobody can predict that, and pretending
otherwise is how you lose money.
