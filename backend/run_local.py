"""Local demo launcher — laptop, no containers, no cloud.

Pins the demo environment IN-PROCESS before any scudo import so the env
survives every shell / reloader quirk, then serves Flask without the
debug reloader (the reloader respawn is what dropped inline env vars on
Windows). Run:

    python run_local.py

Then open the Vite dev server (npm run dev in ../frontend) on
http://localhost:3000 — it proxies /api here.

DEV ONLY. Memory store, dev-auth principal, mock frames, env-var verdict
key. Nothing here is deployable posture.
"""

import os

# Must land BEFORE scudo_mapping_mcp.config is imported — Settings reads
# the environment exactly once at module import. Force-assign (not
# setdefault): an inherited empty-string value would silently disable
# dev auth, which is exactly the failure this launcher exists to stop.
os.environ["STORE_BACKEND"] = "memory"
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
    # use_reloader=False: the watchdog respawn was observed dropping the
    # process environment on Windows, which silently disabled dev auth.
    app.run(port=5000, debug=False, use_reloader=False)
