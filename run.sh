#!/bin/bash
# Runs the radar using the project's virtualenv and .env config.
# Used by the launchd schedule and fine to run by hand: ./run.sh
DIR="/Users/nikolaslepore/reddit-stock-radar"
cd "$DIR" || exit 1
set -a
[ -f "$DIR/.env" ] && . "$DIR/.env"
set +a
exec "$DIR/.venv/bin/python" "$DIR/main.py" >> "$DIR/radar.log" 2>&1
