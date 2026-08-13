#!/usr/bin/env python3
"""JPMC-LOCAL: cross-platform starter (Windows / macOS / Linux).

WHY THIS EXISTS
    start_all.sh is a zsh script, so it does not run on Windows. Worse, it
    launches `python3 app.py` directly, which sets NONE of the local
    environment variables. Without them app.py's auth gate rejects every
    /api/* call with HTTP 401 -- which is why "only one page opens".

    This script sets the environment FIRST, then starts both servers.

USAGE
    python start_local.py            # backend + frontend
    python start_local.py --backend  # backend only (no Node needed)

    Backend  -> http://localhost:5000   (open this to check it is alive)
    Frontend -> http://localhost:3000   (this is the actual UI)

    Port 5000 already taken? (macOS AirPlay and some corporate agents squat
    on it.) Set PORT, and tell the UI where to find the backend:
        PORT=5050 VITE_API_PROXY=http://localhost:5050 python start_local.py
    On Windows (cmd), set them first:
        set PORT=5050
        set VITE_API_PROXY=http://localhost:5050
        python start_local.py

NO EXTERNAL DATABASE IS REQUIRED. Matching uses a local SciPy/SQLite store,
and Providers / Datasets / Admin / Ingestion use the separate console SQLite
fallback. PostgreSQL remains available when explicitly configured.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
BACKEND = BASE / "backend"
FRONTEND = BASE / "frontend"

# The environment that makes a local run work. Set BEFORE app.py is imported.
LOCAL_ENV = {
    # Full matching state and SciPy taxonomy snapshots in a dedicated SQLite
    # file. This is intentionally separate from the console CRUD database.
    "STORE_BACKEND": "scipy_sqlite",
    "SCUDO_SCIPY_SQLITE_PATH": str(BACKEND / ".local" / "scudo_matching.sqlite3"),
    # Local dev identity. Without these every /api/* call returns 401.
    "SCUDO_AUTH_ALLOW_DEV": "1",
    "SCUDO_AUTH_DEV_PRINCIPAL": "demo@local",
    "SCUDO_AUTH_ALLOW_DEV_WRITES": "1",  # lets you record HITL decisions locally
    "SCUDO_VERDICT_ALLOW_DEV": "1",
    # Read vendor data from the bundled sample files, not S3.
    "FRAME_SOURCE": "mock",
    # Providers / Datasets / Admin / Ingestion are the only pages that need a
    # relational DB. 'sqlite' points them at a file-backed stand-in
    # (backend/.local/console.sqlite3) so they work with NO PostgreSQL and NO
    # Docker -- previously these four pages returned HTTP 500 and looked
    # broken. Delete this line (or set it to anything else) to go back to real
    # PostgreSQL via docker-compose. See backend/db_sqlite_fallback.py.
    "CONSOLE_DB_BACKEND": "sqlite",
    # Preselect the offline narrator in the Matching Test dropdown. WITHOUT
    # this the UI defaults to "bedrock", and an explicitly-chosen provider
    # overrides SCUDO_AGENT_BACKEND -- so the "offline" demo would call AWS
    # and fail with no credentials. Set this to "bedrock" once Bedrock works.
    "SCUDO_AGENT_PROVIDER_DEFAULT": "scripted",
}


def _apply_local_defaults(env: dict[str, str]) -> None:
    """Apply local defaults while keeping backend and persistence coherent."""
    for key, value in LOCAL_ENV.items():
        env.setdefault(key, value)
    effective_backend = env.get("STORE_BACKEND") or "scipy_sqlite"
    env["STORE_BACKEND"] = effective_backend
    if not env.get("SCUDO_PERSIST_TARGET", "").strip():
        env["SCUDO_PERSIST_TARGET"] = effective_backend


def main() -> int:
    env = os.environ.copy()
    _apply_local_defaults(env)

    backend_only = "--backend" in sys.argv

    # Honour PORT so nothing printed below can lie about where the app is.
    port = env.get("PORT", "5000")

    print("SCUDO local startup")
    print("-" * 60)
    for key in sorted(LOCAL_ENV):
        print(f"  {key}={env[key]}")
    print("-" * 60)

    procs = []
    print(f"Starting Flask backend on :{port} ...")
    procs.append(subprocess.Popen([sys.executable, "app.py"], cwd=BACKEND, env=env))

    if not backend_only:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        # `npm run dev` exits instantly if dependencies were never installed,
        # and the parent used to wait on the backend first -- so the UI was
        # simply dead with nothing on screen explaining why. Check up front.
        if not (FRONTEND / "node_modules").is_dir():
            print()
            print("  !! frontend/node_modules is missing -- the UI cannot start.")
            print("     Run this once:   cd frontend && npm install")
            print("     Continuing with the backend only.")
            backend_only = True
        else:
            print("Starting React frontend on :3000 ...")
            try:
                procs.append(
                    subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND, env=env)
                )
            except FileNotFoundError:
                print(f"  npm not found -- backend only. Open http://localhost:{port}/")
                backend_only = True

    print()
    print(f"  Backend   http://localhost:{port}/       (JSON index; proves it is up)")
    if not backend_only:
        print("  Frontend  http://localhost:3000/       <-- THE UI")
    print()
    print("Press Ctrl+C to stop.")

    # Wait on whichever child exits FIRST, not on the backend specifically:
    # if the UI dies you want to know immediately, not sit looking at a
    # half-running system waiting for a backend that never exits.
    try:
        while procs:
            for proc in list(procs):
                if proc.poll() is not None:
                    print(f"\n  child pid {proc.pid} exited ({proc.returncode}).")
                    procs.remove(proc)
                    for other in procs:
                        other.terminate()
                    return proc.returncode or 0
            time.sleep(0.4)
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
