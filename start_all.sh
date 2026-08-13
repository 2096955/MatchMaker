#!/bin/zsh
# Start both Flask backend and React frontend dev server.
#
# JPMC-LOCAL: this script used to run `python3 app.py` directly, setting NO
# environment. Two things went wrong every time, and both looked like bugs:
#
#   1. app.py's auth gate returned 401 on EVERY /api/* call, so the UI shell
#      rendered and every data call failed -- the "only one page opens"
#      symptom.
#   2. STORE_BACKEND was unset, so the configured default could diverge from
#      the supported single-host local matching store.
#
# start_local.py sets the environment BEFORE importing app.py (the ordering is
# the whole point) and works on Windows too. This script now delegates to it
# rather than keeping a second, silently-diverging copy of the env block.
#
# Port 5000 taken? (macOS AirPlay squats on it.) Pass it through:
#   PORT=5055 VITE_API_PROXY=http://localhost:5055 ./start_all.sh

BASE="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$HOME/.zshrc" ]; then
  source "$HOME/.zshrc"
fi

exec python3 "$BASE/start_local.py" "$@"
