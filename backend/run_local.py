"""Local demo launcher — laptop, no containers, no cloud.

Pins the demo environment IN-PROCESS before any scudo import so the env
survives every shell / reloader quirk, then serves Flask without the
debug reloader (the reloader respawn is what dropped inline env vars on
Windows). Run:

    python run_local.py

Then open the Vite dev server (npm run dev in ../frontend) on
http://localhost:3000 — it proxies /api here.

DEV ONLY. Single-host SQLite store, dev-auth principal, mock frames, env-var
verdict key. Nothing here is deployable posture.
"""

import os
from pathlib import Path

# Must land BEFORE scudo_mapping_mcp.config is imported — Settings reads
# the environment exactly once at module import. Force-assign (not
# setdefault for operator-selectable store settings; explicit environment wins.
_BACKEND = Path(__file__).resolve().parent
_EFFECTIVE_STORE = os.environ.get("STORE_BACKEND") or "scipy_sqlite"
os.environ["STORE_BACKEND"] = _EFFECTIVE_STORE
if not os.environ.get("SCUDO_PERSIST_TARGET", "").strip():
    os.environ["SCUDO_PERSIST_TARGET"] = _EFFECTIVE_STORE
os.environ.setdefault(
    "SCUDO_SCIPY_SQLITE_PATH",
    str(_BACKEND / ".local" / "scudo_matching.sqlite3"),
)
# Force-assign auth values: an inherited empty string would silently disable
# dev auth, which is exactly the failure this launcher exists to stop.
os.environ["SCUDO_AUTH_ALLOW_DEV"] = "1"
os.environ["SCUDO_AUTH_DEV_PRINCIPAL"] = "demo@local"
# Local only: let the dev-env principal write HITL decisions so the feedback
# loop closes on a developer machine. Deployments MUST leave this unset —
# see auth.can_write_decision (finding-1 fail-closed guard).
os.environ["SCUDO_AUTH_ALLOW_DEV_WRITES"] = "1"
os.environ["SCUDO_VERDICT_ALLOW_DEV"] = "1"
os.environ["FRAME_SOURCE"] = "mock"

from app import app  # noqa: E402  — env must be pinned first

if __name__ == "__main__":
    # PORT is configurable (default 5000, unchanged) - macOS's AirPlay
    # Receiver squats on 5000 by default on some machines, so e.g.
    # `PORT=5050 python run_local.py` is the escape hatch, no code change
    # needed for that case.
    # use_reloader=False: the watchdog respawn was observed dropping the
    # process environment on Windows, which silently disabled dev auth.
    app.run(port=int(os.getenv("PORT", "5000")), debug=False, use_reloader=False)
