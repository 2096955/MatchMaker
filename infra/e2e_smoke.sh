#!/usr/bin/env bash
# Local E2E smoke: start backend + frontend in demo/memory-safe mode, run
# the live Playwright suite against them, tear both down. No real AWS/
# network calls (SCUDO_AGENT_BACKEND left unset -> scripted; the one
# website-URL fetch targets an in-test local fixture server, never the
# real internet - SCUDO_URL_INGEST_ALLOW_LOOPBACK is a narrow, off-by-
# default escape hatch that ONLY relaxes the SSRF guard's loopback check,
# see scudo_mapping_mcp/url_ingest.py). DEV/CI ONLY - never set these env
# vars in a deployed environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# BACKEND_PORT defaults to 5050, not 5000: macOS's AirPlay Receiver squats
# on port 5000 by default on some machines (confirmed on this one - Flask
# failed to bind, and requests to :5000 silently hit AirTunes instead,
# producing confusing empty-body/timeout failures). Override with
# BACKEND_PORT=5000 if your machine doesn't have this conflict.
BACKEND_PORT="${BACKEND_PORT:-5050}"

# FRONTEND_PORT defaults to 3010, not 3000: this machine has an unrelated,
# pre-existing process (a WhatsApp bridge, nothing to do with MatchMaker)
# already listening on 3000 - confirmed via lsof, left completely untouched.
# --strictPort makes vite FAIL LOUD instead of silently auto-incrementing
# to some other port if 3010 is ever unavailable too, so this script never
# silently talks to the wrong server.
FRONTEND_PORT="${FRONTEND_PORT:-3010}"

echo "[e2e_smoke] starting backend on :${BACKEND_PORT}"
(
  cd "$ROOT/backend" && \
  PORT="$BACKEND_PORT" \
  SCUDO_SERVE_DASHBOARD_DIST=1 \
  SCUDO_URL_INGEST_ALLOW_LOOPBACK=1 \
  python run_local.py > /tmp/e2e_backend.log 2>&1 &
  echo $! > /tmp/e2e_backend.pid
)

echo "[e2e_smoke] starting frontend on :${FRONTEND_PORT} (proxying /api -> :${BACKEND_PORT})"
(
  cd "$ROOT/frontend" && \
  VITE_API_PROXY="http://localhost:${BACKEND_PORT}" \
  npm run dev -- --port "$FRONTEND_PORT" --strictPort > /tmp/e2e_frontend.log 2>&1 &
  echo $! > /tmp/e2e_frontend.pid
)

cleanup() {
  echo "[e2e_smoke] tearing down"
  # NOTE: killing by the $! PID captured above is unreliable here - `npm run
  # dev` spawns vite as a CHILD process with a different PID (confirmed:
  # the captured PID was the npm/subshell wrapper, and the actual vite
  # process was orphaned and kept running after `kill` on the wrapper).
  # pgrep -f matching this project's EXACT binary paths is robust against
  # that AND safe against unrelated same-port processes on a shared dev
  # machine (e.g. this machine also runs an unrelated WhatsApp bridge and
  # the Understand-Anything dashboard's OWN separate vite dev server, on
  # different paths - do not broaden these patterns to a bare "vite").
  # NOTE: the backend is launched after `cd backend/`, so its argv is
  # literally "python run_local.py" - NOT "backend/run_local.py" (confirmed
  # by inspecting a leaked process's actual command line). Match on the
  # bare script name, which is unique on this machine (checked via pgrep).
  pkill -f "run_local.py" 2>/dev/null || true
  pkill -f "frontend/node_modules/.bin/vite" 2>/dev/null || true
}
trap cleanup EXIT

echo "[e2e_smoke] waiting for backend readiness"
for _ in $(seq 1 30); do
  curl -sf "http://localhost:${BACKEND_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done

echo "[e2e_smoke] waiting for frontend"
for _ in $(seq 1 30); do
  curl -sf "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1 && break
  sleep 1
done

echo "[e2e_smoke] running the Playwright E2E suite"
cd "$ROOT" && \
  E2E_BACKEND_URL="http://localhost:${BACKEND_PORT}" \
  E2E_FRONTEND_URL="http://localhost:${FRONTEND_PORT}" \
  python -m pytest backend/tests/e2e/test_matching_ui_e2e.py -v
