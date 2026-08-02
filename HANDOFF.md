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

## Resolved (branch `fix/backtest-capital-model`)

### 1. Stale backtest output — was a crash, not a race
Not a race and not a stale write: `main()` built `pick_days` at line 389 but the
exit-rule replay read it at line 369, so every run died with
`UnboundLocalError` before the commit step. A failed step means the commit step
never runs, so `docs/backtest.json` was simply the **last successful** run's
file (14:39 UTC), not new output in an old format. Three runs had failed that
way. Fixed by constructing `pick_days` before either consumer.

`ast.parse` cannot catch this — it is a runtime scope error. To check offline
without Yahoo, stub `yfinance` on `PYTHONPATH` and run the script.

Also removed: three copies of a `per_day`/`daily` block, none of which were ever
put in the payload, the survivor of which mixed buzz and mover tiers back into a
picks-only view. `basket([])` now returns a zeroed shape instead of dying on
`max()`.

### 2 & 3. Capital model and the ±15% bracket
Implemented option (c) with (a)'s staggering. Each pick day deploys
`DEPLOY_FRAC` (20%) of the account across that day's picks; each position runs
until the ±15% bracket closes it; freed cash funds later days. Baskets overlap,
the account is marked to market, and `final_balance` is today's value rather
than the last pick day's. The curve now trades the same rule the sell panel
measures.

**The ±15% bracket did not come out on top**, so it is not the headline. On the
18 picks measured: best is "Sell after 5 days" (+8.8%), then −15%/+25% (+7.1%),
then ±15% (+6.9%, 72% win, 4.6 days). *Every* rule beat holding — "Hold, never
sell" is last at −0.7%. The sell table pins the ±15% row with a "your curve"
badge and states its rank, so the two panels cannot imply the same rule won.
The small-sample caveat now runs to 30 picks; at 15 it disappeared at 18.

**Open question — sizing concentration.** A fixed 20%-per-*day* budget means a
day with one pick puts the whole 20% into that name ($1,988 into a single
ticker on 2026-07-06, which then lost 16.2%), while an 8-pick day puts $241
into each. That is why the account reads −1.0% while the average pick under the
same rule reads +6.9%: the table equal-weights positions, the account
equal-weights days. Sizing per *position* instead of per day would fix the
concentration. Not changed — it is a real decision about the model, not a bug.

## Open threads

### 1. GitHub Pages still needs one manual click
Unchanged — see above. Settings → Pages → Source: GitHub Actions.

### 2. Decide per-day vs per-position sizing
See the open question under "Capital model" above.

## Things that bit me — don't repeat

- **`recordPanel()` referenced `all`, a local of `history()`.** Every screen
  rendered blank, because the digest builds through that panel. Same family as
  the `verdict()` collision — this one shipped to `main` and sat there. If the
  page is blank, read the error inside the "NO DATA" card; it prints it.
- **The verification environment is not the previous one.** The handoff's
  `/opt/pw-browsers/chromium` is Linux-container-specific. On the user's Mac,
  `npm install playwright` in the scratchpad and launch Chrome with
  `executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'`.
- **`git push origin HEAD:main` was hardcoded in `backtest.yml`.** Running the
  workflow on a branch would have pushed branch commits onto main. It now pushes
  to `$GITHUB_REF_NAME`, so a branch run commits to that branch.
- **Table `min-width` has to keep up with added columns.** The compounded table
  was still 440px after Each and Held were added, so at 390px the one flexible
  cell collapsed and the "open" pill overlapped the pick count.

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
