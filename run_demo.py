#!/usr/bin/env python3
"""SCUDO MatchMaker — click-to-run demo.

    python run_demo.py

Starts EVERYTHING and opens the browser:

    :8501  Streamlit — upload, match, agent reasoning, reviewer Approve /
           Reject, and free-text chat with the agent
    :5055  React console (/app/), matching dashboard (/demo/), REST API

Needs NO Node, NO Docker, NO database and NO AWS account. The matching score is
real and deterministic; reviewer decisions persist to a readable JSONL journal
and survive a restart.

This is the single entry point. run_cognizant.py starts only the Flask half;
this starts both and is what a first-time user should double-click.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "5055"))
ST_PORT = int(os.environ.get("STREAMLIT_PORT", "8501"))


def _missing() -> list[str]:
    out: list[str] = []
    try:
        import flask  # noqa: F401
    except ImportError:
        out.append("flask")
    try:
        import streamlit  # noqa: F401
    except ImportError:
        out.append("streamlit")
    return out


def main() -> int:
    missing = _missing()
    if missing:
        print(
            "\n  Missing packages: " + ", ".join(missing) + "\n\n"
            "  Install everything with:\n"
            "      pip install -r backend/requirements-local.txt\n"
        )
        return 1

    print("\n  Starting SCUDO MatchMaker …\n")

    # Flask (console + dashboard + API). run_cognizant.py owns the environment
    # contract -- it sets the env BEFORE importing app.py, which is the whole
    # reason it exists. Do not inline that here and risk the orderings drifting.
    flask_proc = subprocess.Popen(
        [sys.executable, str(_ROOT / "run_cognizant.py")],
        cwd=str(_ROOT),
        env={
            **os.environ,
            "PORT": str(PORT),
            "SCUDO_NO_BROWSER": "1",
            # Same store/path the Streamlit child gets, resolved once below.
            "STORE_BACKEND": os.environ.get("STORE_BACKEND", "scipy_sqlite"),
            "SCUDO_SCIPY_SQLITE_PATH": os.environ.get(
                "SCUDO_SCIPY_SQLITE_PATH",
                str(_ROOT / "backend" / ".local" / "scudo_matching.sqlite3"),
            ),
        },
    )

    # Streamlit sets its own env at the top of streamlit_app.py, but the two
    # halves MUST agree on the store or they split-brain: a precedent approved
    # in the UI is then unreadable by the API, which looks exactly like the
    # memory not working. This defaulted to local_file while run_cognizant.py
    # gave Flask scipy_sqlite — verified in review as still live, so the two
    # values are now derived from ONE constant instead of repeated literals.
    _STORE = os.environ.get("STORE_BACKEND", "scipy_sqlite")
    _DB = os.environ.get(
        "SCUDO_SCIPY_SQLITE_PATH",
        str(_ROOT / "backend" / ".local" / "scudo_matching.sqlite3"),
    )
    st_env = {
        **os.environ,
        "STORE_BACKEND": _STORE,
        "SCUDO_PERSIST_TARGET": os.environ.get("SCUDO_PERSIST_TARGET", _STORE),
        "SCUDO_SCIPY_SQLITE_PATH": _DB,
        # Only used by STORE_BACKEND=local_file; harmless otherwise.
        "SCUDO_MEMORY_PATH": os.environ.get(
            "SCUDO_MEMORY_PATH",
            str(_ROOT / "backend" / "local_memory" / "precedents.jsonl"),
        ),
    }
    streamlit_proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(_ROOT / "streamlit_app.py"),
            "--server.port", str(ST_PORT),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(_ROOT),
        env=st_env,
    )

    time.sleep(6)  # let both bind before opening a tab at a dead port
    # flush=True: the two children inherit stdout, and without an explicit
    # flush this banner is buffered behind Flask's startup noise -- the user
    # then never sees the "START HERE" line, which defeats the point.
    print(
        f"""
╭──────────────────────────────────────────────────────────────────────╮
│  SCUDO MatchMaker is running                                         │
╰──────────────────────────────────────────────────────────────────────╯

  START HERE
    http://localhost:{ST_PORT}                Upload → match → agent
                                          reasoning → review → chat

  ALSO RUNNING
    http://localhost:{PORT}/app/         React console
    http://localhost:{PORT}/demo/        Matching dashboard
    http://localhost:{PORT}/healthz      Liveness

  TRY THIS (five minutes)
    1. Upload a file from sample_data/provider/bloomberg/
    2. Pick the product, press Run match
    3. Read the agent's reasoning trace
    4. Press Approve, then Run match again
       -> status becomes 'approved', rationale becomes 'precedent'
    5. Ask the agent in step 04: "how does the scoring work?"

  No AWS needed. For real Claude narration set SCUDO_AGENT_BACKEND=bedrock
  with credentials, then pick 'bedrock' in the sidebar.

  Ctrl-C to stop both.
""",
        flush=True,
    )
    try:
        webbrowser.open(f"http://localhost:{ST_PORT}")
    except Exception:  # noqa: BLE001 — headless boxes must not fail here
        pass

    try:
        flask_proc.wait()
    except KeyboardInterrupt:
        print("\n  Stopping …")
    finally:
        for p in (streamlit_proc, flask_proc):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
