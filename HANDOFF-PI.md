# Handoff — moving Stock Radar to the Raspberry Pi

Written at the end of the session that built the current dashboard. Read this,
then `HANDOFF.md` (the radar's own hard-won facts), then `docs/index.html`,
`backtest.py`, `main.py`.

**The repo is `Tabarnik/stock-radar`, public, live at
https://tabarnik.github.io/stock-radar/**

---

## What exists today

A Reddit/Yahoo stock radar that runs on GitHub Actions 3× per weekday, pushes a
digest to ntfy, and publishes a static dashboard to GitHub Pages.

| File | Role |
|---|---|
| `main.py` | scan, enrich, notify; writes `docs/data.json` + `docs/history.json` |
| `backtest.py` | replays the pick record; writes `docs/backtest.json` |
| `docs/index.html` | the whole dashboard, single file, no build step |
| `trade.py` | CLI writing `docs/trades.json` (shared/committed trades) |
| `backfill_history.py` | reconstructs pre-history-file picks |
| `.github/workflows/daily.yml` | the 3×/weekday run: radar → backtest → commit → Pages |
| `.github/workflows/trade-issue.yml` | records a trade from a GitHub issue (owner-only) |

**Dashboard screens:** Digest · Watchlist · History · Nik's stocks · (Detail)

**The strategy:** pick → hold **5 trading days** → sell. `CURVE_DAYS = 5` in
`backtest.py` drives both the modelled account and the plan the board shows.
Position size is `POSITION_FRAC` (5%) of the account, and `buyStake()` in the
page derives from it — never hardcode a stake, the two must agree.

---

## The three problems the Pi is meant to solve

### 1. Timing — the digest is late, every time

GitHub's cron is best-effort and only ever runs **late**. Measured over ten
scheduled runs: **median 74 minutes, worst 156**, and the morning slot is
consistently the worst. Crons now sit at `:50`/`:05` instead of `:00` to dodge
peak contention; it helps a little and fixes nothing.

**The fix is not a better cron.** A `workflow_dispatch` starts within *seconds*.
Anything that can call the GitHub API on a schedule makes the digest punctual.

Smallest possible win, worth doing before any migration:

```cron
50 7 * * 1-5  cd /home/pi/stock-radar && gh workflow run daily.yml --ref main
```

That alone gets an 8 AM digest at 8 AM with no hosting, no auth, no migration.

### 2. Cross-device storage — the trade book lives in one browser

`localStorage` key `nik.trades.v1` (positions) and `nik.log.v1` (action log).
A buy marked on the laptop does not exist on the phone. Clearing site data
destroys the record. There is EXPORT/IMPORT on Nik's stocks as the only defence.

**This is the highest-risk item.** It is the user's actual trading record and it
sits in one browser profile.

### 3. Real login and access from anywhere

There is none, and on GitHub Pages there cannot be — it serves static files, so
any password check would run in readable JavaScript over data that is already
public. **Do not build a client-side login; it would be security theatre.**

---

## Why the port is small

Every storage touchpoint in `docs/index.html` goes through **four functions**
(six `localStorage` calls in total):

```
localTrades()      -> GET  /api/trades
saveLocalTrades()  -> PUT  /api/trades
readLog()          -> GET  /api/log
logEvent()         -> POST /api/log
```

Swap those four bodies for `fetch` and the entire dashboard is server-backed.
`exportAll()` / `importAll()` should stay — they are the backup story regardless.

Everything downstream (`myPositions`, `positionFrom`, `myPos`, the panels) reads
through those functions and needs no change. `positionFrom()` computes a
position client-side from `B.recent_closes`, which `backtest.py` emits for every
symbol on the board — keep emitting it, or move that maths server-side.

---

## Suggested shape on the Pi

- **FastAPI + SQLite** (~200 lines). Serve `docs/` statically, add `/api/trades`
  and `/api/log`.
- **Auth:** single user, bcrypt hash in env or a config file, HttpOnly +
  `Secure` + `SameSite=Lax` session cookie. No third-party identity provider
  needed for one user.
- **Schedule:** systemd timer running `main.py` then `backtest.py` — Pi cron is
  punctual, which is the whole point.
- **Remote access:** **Tailscale** (nothing exposed, works on cellular, free) or
  **Cloudflare Tunnel** (real HTTPS URL, no open ports). Do not port-forward.
- **HTTPS is mandatory once a password exists.** Tailscale/Cloudflare give it.
- **Back up the SQLite file.** SD cards fail. A nightly copy off-device.

### Staging that avoids a risky big-bang

1. Pi cron dispatches the GitHub workflow → timing fixed, nothing else touched.
2. Pi serves the dashboard + API with login → storage and access fixed; the
   radar keeps running on GitHub Actions, and the Pi reads the committed JSON.
3. Only then, if desired, move the radar itself to the Pi — that trades GitHub's
   uptime for punctuality already won in step 1.

### Migration detail that will bite

Existing users have trades in `localStorage`. On first login the page should
offer to push the local book up to the server once, then read from the server
thereafter. Silently switching to an empty server-side book looks like data loss.

---

## Facts worth not rediscovering

- **The evidence is thin and rests on one stock.** Over 20 measured picks the
  5-day rule returns +10.4%; **SLS alone is +80.7% and the other 19 average
  +6.7%**. Seven weeks, one rising market. Do not present the headline without
  this.
- **9 of 20 measured picks were never sent** — reconstructed by hand from old
  digests, marked `simulated`. `backfill_history.py` hardcodes them; the rule was
  never actually re-run, and the momentum data it needs is gone.
- **Counting rule: trades, not flags.** A name flagged while its earlier position
  is still open is not a second buy. Everything filters through `boughtOnly()` /
  `heldNotBought()`. Applying this moved the record from 5/6/23 over 34 "picks"
  to 5/4/13 over 22 real buys.
- **Two books, never conflated.** *Model portfolio* assumes every pick was bought
  the moment it appeared. *Nik's stocks* is what was actually bought. Labelling
  the model as the user's own was a real bug, twice.
- **`data.json` embeds its own copy of the history.** Fixing a field in
  `history.json` alone leaves the board rendering the stale one. Fix both.
- **Dates are ET, not UTC.** `update_history` refuses to record a pick day at a
  weekend — a manual dispatch used to file Friday's stale close under Saturday.
- **GitHub Pages caches `index.html` for 10 minutes** and the page only refreshed
  its data, so a tab left open ran old UI indefinitely. There is now an ETag
  check that offers a reload. Keep something equivalent on the Pi.
- **Zero external requests from the page.** Charts are *links* to Yahoo, never
  embeds. Keep it that way.

## Traps that cost time this session

- **`js ok` proves nothing.** ``return `…`; + heldPanel;`` is valid JavaScript
  that silently drops the panel. Twice a template-literal edit parsed cleanly and
  did nothing. **Render it and look.**
- **Name collisions.** `verdict()` declared twice blanked the dashboard;
  `.dot` collided with the header's status light and the button rendered
  unstyled. Check before naming.
- **Dispatching a workflow in the same breath as `git push`** races GitHub's
  replication — the run checks out the *previous* commit. Sleep a few seconds.
- **Scripted multi-edit with a failing assert writes nothing**, so earlier
  replacements in the same script are silently lost. Assert per edit, or verify
  after.
- **The sandbox cannot reach Yahoo or ApeWisdom.** Anything needing live prices
  runs through the workflows; read the committed JSON back.

## Verification loop that works

```bash
cd docs && python3 -m http.server 8899 &
# playwright is not preinstalled: npm install playwright && npx playwright install chromium
```

Check: no `pageerror`, no third-party requests, no horizontal overflow at 390px.
Screenshot every change — every real bug this session was found by looking.
