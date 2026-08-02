# Handoff — Reddit Stock Radar

Written at the end of a long session. Read this first, then `main.py`,
`docs/index.html`, `backtest.py`.

## What this is

A Reddit/Yahoo stock radar that runs on GitHub Actions 3× per weekday
(13:00, 17:00, 21:00 UTC), pushes a digest to ntfy, and publishes a dashboard.

- `main.py` — scan, enrich, notify, write `docs/data.json` + `docs/history.json`
- `docs/index.html` — single-file dashboard (4 screens: Digest, Watchlist,
  History, Detail). No build step. Fonts self-hosted in `docs/fonts/`.
  **Zero external requests** — keep it that way.
- `backtest.py` — replays the pick record against real prices
- `backfill_history.py` — reconstructs pre-history-file picks
- `build_artifact.py` — bundles the dashboard into one self-contained file

## Ground truth / hard-won facts

- **Everything is picks-only now.** Reddit Buzz and Market Gainers are still
  *computed* (picks derive from the buzz ranking) but are not shown anywhere:
  not the digest, not ntfy, not the backtest. Both lost money over the full
  record (−17.4% and −16.6%). Do not reintroduce them into the UI.
- **The three section names are fixed** and must match in ntfy and the board:
  Picks of the day (act on these first) / Reddit Buzz (watch — don't act) /
  Biggest movers (don't chase).
- **GitHub Pages is not enabled** and the workflow token *cannot* enable it —
  `configure-pages` returns "Resource not accessible by integration". It needs
  one manual click: Settings → Pages → Source: GitHub Actions. Until then the
  deploy step 404s harmlessly (`continue-on-error`). The user has been told.
  The live link is a published Artifact snapshot instead:
  https://claude.ai/code/artifact/cd0a05b4-8697-4eba-9f78-bf2f3fb472a0
  (republish by running `python3 build_artifact.py` then the Artifact tool with
  that URL.)
- **Skipped runs collected no data.** The old ET gate returned before `gather()`,
  so 181 of 204 historical runs have nothing in them but `[skip] ET hour ...`.
  There is no hidden dataset. Do not go looking again.
- **`yf.Ticker().earnings_dates` returns nothing usable.** Earnings come from
  `.calendar` + `.earnings_history` + `.earnings_estimate`. Do not "simplify"
  back to `earnings_dates`.
- **`tradingwithcongress` is not a real ApeWisdom feed** — it returns 0 tickers.
  `stocks` works (100). Feed status is recorded in `data.json.feeds`.
- **News tone is VADER over headlines, not Reddit sentiment.** ApeWisdom gives
  mention counts only, never post text. It is labelled "news positive/negative"
  deliberately — do not relabel it as Reddit sentiment.
- **Analyst targets have no date.** Conventionally 12 months; Yahoo publishes no
  date field. Labelled "12-mo" with that caveat stated.
- **15 of 34 history entries are `simulated: true`** — reconstructed by replaying
  the pick rule over digests sent before the pick section existed. They were
  never sent. They must stay visibly marked.

## Open threads, in priority order

### 1. The backtest run emits stale output (blocking)
`backtest.py` in the repo is correct: `START_CASH` defaults to 10000, the equity
curve emits `per_ticker` and `held_days`. But the last two Backtest workflow runs
wrote `docs/backtest.json` with `start_cash: 1000`, no `per_ticker`, no
`held_days`, and exit rules over a $9,000 / 9-pick account.

The dashboard reads those fields, so the "When to sell" panel still shows
"$9,000 · 9 picks" and the curve has no Each/Held columns.

Diagnose first: run the Backtest workflow and read the job log. Local
`ast.parse` is clean and there is no use-before-assign on `now`. Suspect the
run raced a push, or a step failed before the commit step.

### 2. The capital model contradicts itself (design flaw, user spotted it)
The compounded curve rotates the *whole* balance into every pick day. The
"sell after 5 days" rule holds each position 5 days. With one account both
cannot be true — pick days are near-daily, so days 2–5 have no cash.

The user asked how to resolve it. Options given:
 a. stagger ~1/5 of the account per pick day so five baskets overlap
 b. deploy fully once a week, hold 5 days, redeploy
 c. bracket exits (−15% stop / +25% target) so positions close at their own pace

Recommended (c), tightened to **±15%**, which the user explicitly asked for
("safer"). Not yet implemented.

### 3. Re-measure exit rules on the ±15% bracket
Add a `_bracket(-15, 15)` rule and make it the headline if it holds up. Present
it as the user's own record, not an industry rule — the wording is already
"your own picks", keep it.

## Things that bit me — don't repeat

- **Function-name collisions.** I declared `verdict()` twice and the second
  silently replaced the first; the whole dashboard rendered blank. There is now
  `verdict()` (hero) and `tierVerdict()` (backtest). Check before naming.
- **Slicing by string index in `index.html`.** Replacing `backtest()` by slicing
  from its opening to the next marker also deleted `history()` sitting between
  them. Verify what a range actually spans.
- **Always render and look.** Every real bug this session was found by
  screenshotting, not by reading code: invisible bars, a red chart on a +53%
  year, duplicated tickers, unsorted "biggest movers", blank screens.
- **Workflow inputs override code defaults.** `backtest.yml`'s `stake` input
  default silently beat the Python default and reported $690,000 invested.
- **The container resets.** Local checkouts reverted to old commits twice
  mid-session. `git fetch && git reset --hard origin/main` before trusting the
  working tree.

## Verification loop that works

```bash
# serve and screenshot
cd docs && python3 -m http.server 8899 &
# playwright is NOT preinstalled in the scratchpad; npm install playwright first
# launch with executablePath: '/opt/pw-browsers/chromium'
```
Check: no `pageerror`, zero external requests, no horizontal overflow at 390px.
Sandbox cannot reach Yahoo or ApeWisdom — run anything needing prices via the
GitHub Actions workflows and read the committed JSON back.
