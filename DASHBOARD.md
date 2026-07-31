# Stock Radar Dashboard

A single-file HTML dashboard (`docs/index.html`) that renders the same data the
ntfy notifications carry: worth-watching names, Reddit mention volume, buzz
cards with analyst targets and earnings dates, and the day's market gainers.

Every radar run writes `docs/data.json`; the page fetches it on load. No build
step, no framework, no external requests — it works from any static file server.

---

## Option A — GitHub Pages (zero maintenance)

Already wired up. The workflow publishes `docs/` after every run, so the board
refreshes three times each weekday on its own.

**One-time setup:**

1. Go to your repo → **Settings** → **Pages**
2. Under *Build and deployment* → **Source**, pick **GitHub Actions**
3. Save

Your board will be live at:

```
https://tabarnik.github.io/reddit-stock-radar/
```

The first deploy happens on the next run (or trigger one manually from the
Actions tab). Bookmark it on your phone — it's responsive and follows your
system light/dark theme.

---

## Option B — Self-hosted on a Raspberry Pi

Runs entirely on your own hardware, on your own schedule. Nothing leaves the Pi
except the API calls to ApeWisdom and Yahoo.

### 1. Install

```bash
sudo apt update && sudo apt install -y python3-pip git
git clone https://github.com/Tabarnik/reddit-stock-radar.git ~/stock-radar
cd ~/stock-radar
pip3 install -r requirements.txt --break-system-packages
```

### 2. Test one run

```bash
cd ~/stock-radar
NOTIFY_METHOD=print \
APEWISDOM_FILTER=Shortsqueeze,pennystocks,wallstreetbets,all-stocks \
TOP_N=10 MAX_VALIDATE=50 \
python3 main.py
```

You should see `[dashboard] wrote docs/data.json`.

### 3. Serve the page

The lightest option — a systemd service running Python's built-in server:

```bash
sudo tee /etc/systemd/system/radar-web.service >/dev/null <<'EOF'
[Unit]
Description=Stock Radar dashboard
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/stock-radar/docs
ExecStart=/usr/bin/python3 -m http.server 8080 --bind 0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now radar-web
```

Reachable at `http://<pi-ip>:8080` from anywhere on your LAN — and over
Tailscale from outside, same as Infuse.

> Already running nginx? Point a server block at `/home/pi/stock-radar/docs`
> instead and skip the service above.

### 4. Refresh the data on a schedule

```bash
crontab -e
```

Add these lines (times are the Pi's local clock — adjust if it isn't on ET):

```cron
0 9,13,17 * * 1-5 cd /home/pi/stock-radar && NOTIFY_METHOD=ntfy NTFY_TOPIC=your-topic-here APEWISDOM_FILTER=Shortsqueeze,pennystocks,wallstreetbets,all-stocks TOP_N=10 MAX_VALIDATE=50 /usr/bin/python3 main.py >> /tmp/radar.log 2>&1
```

Set `NOTIFY_METHOD=print` instead if you'd rather the Pi only update the board
and let GitHub Actions keep sending the phone notifications.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DASHBOARD_DIR` | `docs` | Where `data.json` is written |
| `TOP_N` | `10` | Tickers in the buzz section |
| `MAX_VALIDATE` | `50` | Cap on Yahoo ticker-validation calls per run |
| `APEWISDOM_FILTER` | `Shortsqueeze,pennystocks` | Comma-separated Reddit feeds |
| `GAINERS_N` | `5` | Rows in the market-gainers table |
| `NOTIFY_METHOD` | `print` | `ntfy`, `pushover`, `telegram`, `email`, or `print` |

---

*Trends and public data only — not financial advice.*
