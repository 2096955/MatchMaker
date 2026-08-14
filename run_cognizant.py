#!/usr/bin/env python3
"""SCUDO MatchMaker — one-command run on a Cognizant machine.

WHAT THIS GIVES YOU
    Everything, from one command, on one port:

      http://localhost:5055/streamlit-note   where to find the Streamlit UI
      http://localhost:5055/app/             React console (Providers, Datasets,
                                             Admin, Ingestion, Catalogue)
      http://localhost:5055/demo/            matching dashboard (the story view)
      http://localhost:5055/api/...          the REST API
      http://localhost:5055/healthz          liveness

    Plus, started separately (see the banner this prints):

      http://localhost:8501                  Streamlit matching console with the
                                             agent reasoning trace and the
                                             Approve / Reject review buttons

WHY THIS FILE EXISTS
    There were already three ways to start this and each had a footgun:

      start_all.sh    runs `python3 app.py` with NO environment, so the auth
                      gate 401s every /api/* call and only the shell renders.
      start_local.py  correct env, but ALSO tries to run Vite (`npm install`),
                      which is unnecessary here because the console is already
                      built and vendored in frontend/dist/.
      streamlit run   works, but serves only the matching path — no Providers,
                      no Datasets, no Admin, no dashboard.

    This script needs NO Node, NO npm, NO Docker, NO PostgreSQL, NO FalkorDB,
    NO Neptune and NO AWS credentials. It serves the PRE-BUILT front ends
    straight from Flask, so there is no bundler in the picture at all.

    The environment is set BEFORE app.py is imported. That ordering is the whole
    point: scudo_mapping_mcp/config.py reads these at import time, so setting
    them afterwards silently selects the wrong backend.

RUN IT
    python run_cognizant.py

    Then, in a SECOND terminal, for the Streamlit UI:
    streamlit run streamlit_app.py

INSTALL FIRST (once)
    pip install -r backend/requirements-local.txt

WHAT IS REAL AND WHAT IS NOT
    The matching score is REAL — deterministic Jaro-Winkler over the CDAO
    taxonomy, the same code the AWS deployment runs. Reviewer decisions are
    REAL and persist to the durable matching store
    (backend/.local/scudo_matching.sqlite3 under the default
    STORE_BACKEND=scipy_sqlite; a readable JSONL journal under local_file).

    Two things to know, both measured, so they do not surprise you live:

    1. YOU MUST INGEST BEFORE YOU MATCH. Matching a product with no ingested
       frame returns 404, deliberately: the matcher refuses to invent a product
       name from an identifier. Upload a file first (Streamlit step 01, or the
       console's Ingestion page).
    2. THE JOURNAL OUTLIVES THE FRAMES. Reviewer decisions survive a restart;
       the ingested frames do not (they are in-process under FRAME_SOURCE=mock).
       So after restarting, re-ingest the file and the approval is reused —
       the decision is remembered, the upload is not.

    By default the agent is the SCRIPTED narrator: it walks the same tools a
    Bedrock agent would and narrates the matcher's own steps, with no AWS call.
    Set SCUDO_AGENT_BACKEND=bedrock (plus credentials) for real Claude
    narration. The SCORE does not change either way — the model narrates, the
    matcher scores.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / "backend"

# ── 1. Environment BEFORE any SCUDO import ─────────────────────────────────
#
# Every one of these is a deliberate choice, not a default worth guessing at.
_ENV: dict[str, str] = {
    # Storage: scipy_sqlite is the durable single-host matching store — the
    # full RetrievalStore contract over SQLite with revision-stamped SciPy
    # sparse indexes. HITL decisions survive a restart.
    #
    # This MUST match what streamlit_app.py picks (_best_local_store prefers
    # scipy_sqlite when the file is present) and what start_local.py sets.
    # When they disagree the two halves of the demo write to DIFFERENT stores,
    # so a decision approved in Streamlit is invisible to the Flask API — a
    # silent split-brain that looks like the memory not working at all.
    "STORE_BACKEND": "scipy_sqlite",
    "SCUDO_PERSIST_TARGET": "scipy_sqlite",
    # Console CRUD pages (Providers/Datasets/Admin/Ingestion) against a
    # file-backed SQLite stand-in — no PostgreSQL, no Docker.
    "CONSOLE_DB_BACKEND": "sqlite",
    # Vendor frames come from the bundled fixtures, not S3.
    "FRAME_SOURCE": "mock",
    # app.py's before_request gate rejects EVERY /api/* call without these.
    # This is the single most common cause of "only one page opens".
    "SCUDO_AUTH_ALLOW_DEV": "1",
    "SCUDO_AUTH_DEV_PRINCIPAL": "demo@local",
    # Local write paths. THREE separate gates, all fail-closed by default, and
    # they are NOT interchangeable — this bit us during verification:
    #   SCUDO_VERDICT_ALLOW_DEV        selects the dev HMAC signing key
    #   SCUDO_PERSIST_ALLOW_DEV_WRITES opens the persistence MCP write tools
    #   SCUDO_AUTH_ALLOW_DEV_WRITES    lets the DEV PRINCIPAL write at all
    # Without the third, POST /api/mapping/decision returns 403
    # ("dev-env principal cannot write") — reads and matches still work, so the
    # Approve / Reject buttons look broken for no visible reason.
    "SCUDO_VERDICT_ALLOW_DEV": "1",
    "SCUDO_PERSIST_ALLOW_DEV_WRITES": "1",
    "SCUDO_AUTH_ALLOW_DEV_WRITES": "1",
    # Serve the PRE-BUILT front ends from Flask. Both are off by default in
    # app.py, which is why /app/ and /demo/ 404 without them — that is not a
    # missing build, it is an unset flag.
    "SCUDO_SERVE_FRONTEND_DIST": "1",
    "SCUDO_SERVE_DASHBOARD_DIST": "1",
    # Offline narrator by default: no AWS account needed to demo.
    "SCUDO_AGENT_BACKEND": "scripted",
}

for _k, _v in _ENV.items():
    os.environ.setdefault(_k, _v)

# The durable matching database. Same path streamlit_app.py and start_local.py
# default to — they must agree or the two halves split-brain (see above).
os.environ.setdefault(
    "SCUDO_SCIPY_SQLITE_PATH",
    str(_ROOT / "backend" / ".local" / "scudo_matching.sqlite3"),
)
# Only used by STORE_BACKEND=local_file. Kept so switching back to that store
# still writes its journal somewhere predictable.
os.environ.setdefault(
    "SCUDO_MEMORY_PATH", str(_ROOT / "backend" / "local_memory" / "precedents.jsonl")
)

# macOS AirPlay Receiver squats on :5000, which is Flask's default and the
# cause of an instant, confusing "port in use". Default to 5055.
PORT = int(os.environ.get("PORT", "5055"))

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _preflight() -> list[str]:
    """Check the things whose absence produces a confusing failure later."""
    problems: list[str] = []

    if not (_ROOT / "frontend" / "dist" / "index.html").is_file():
        problems.append(
            "frontend/dist/index.html is missing — the React console at /app/ "
            "will 404. It is vendored in git; a partial checkout is the usual "
            "cause."
        )
    if not (_ROOT / "dashboard-dist" / "index.html").is_file():
        problems.append(
            "dashboard-dist/index.html is missing — the dashboard at /demo/ will 404."
        )
    try:
        import flask  # noqa: F401
    except ImportError:
        problems.append(
            "Flask is not installed. Run:\n"
            "    pip install -r backend/requirements-local.txt"
        )
    return problems


def main() -> int:
    problems = _preflight()
    if problems:
        print("\n  Cannot start:\n")
        for p in problems:
            print(f"  - {p}\n")
        return 1

    # Imported HERE, after the environment is set. Not at module top — that is
    # the ordering contract this file exists to enforce.
    from app import app  # noqa: PLC0415

    # Where the learned decisions live depends on the store. Counting JSONL
    # lines against a SQLite store would silently report 0 for ever.
    _store = os.environ["STORE_BACKEND"]
    if _store == "scipy_sqlite":
        evidence = Path(os.environ["SCUDO_SCIPY_SQLITE_PATH"])
        evidence_note = "durable matching database (SQLite)"
        remembered = "?"
        if evidence.is_file():
            try:
                import sqlite3  # noqa: PLC0415 - only needed on this branch

                with sqlite3.connect(f"file:{evidence}?mode=ro", uri=True) as _c:
                    # Two tables: approvals/overrides and rejections. Both are
                    # decisions the system has learned from, so count both.
                    remembered = (
                        _c.execute(
                            "select count(*) from positive_precedents"
                        ).fetchone()[0]
                        + _c.execute(
                            "select count(*) from negative_precedents"
                        ).fetchone()[0]
                    )
            except Exception:  # noqa: BLE001 - a count must never block startup
                remembered = "?"
    else:
        evidence = Path(os.environ["SCUDO_MEMORY_PATH"])
        evidence_note = "decision journal (JSONL — open it to read what it learned)"
        remembered = 0
        if evidence.is_file():
            remembered = sum(
                1 for line in evidence.read_text().splitlines() if line.strip()
            )

    base = f"http://localhost:{PORT}"
    # When run_demo.py is the parent it already started Streamlit and printed
    # its own banner — telling the user to open a second terminal would then be
    # actively wrong.
    _streamlit_hint = (
        ""
        if os.environ.get("SCUDO_NO_BROWSER")
        else """
  For the matching console with the agent reasoning trace, the
  Approve / Reject review buttons and the agent chat, run this in a
  SECOND terminal:

    streamlit run streamlit_app.py        ->  http://localhost:8501
"""
    )
    print(
        f"""
╭──────────────────────────────────────────────────────────────────────╮
│  SCUDO MatchMaker — running locally                                  │
╰──────────────────────────────────────────────────────────────────────╯

  Open these:

    {base}/app/          React console — Providers, Datasets,
                                       Admin, Ingestion, Catalogue
    {base}/demo/         Matching dashboard — the story view
    {base}/healthz       Liveness check

{_streamlit_hint}
  This run needs no Node, no Docker, no database and no AWS account.

    Store            {_store:<11} (decisions survive a restart)
    Console DB       sqlite      (no PostgreSQL needed)
    Agent            {os.environ["SCUDO_AGENT_BACKEND"]:<11} (the matcher scores; the agent narrates)
    Decisions known  {remembered}

  Learned decisions live here — {evidence_note}:
    {evidence}

  Ctrl-C to stop.
"""
    )

    # run_demo.py starts this process AND Streamlit, and opens Streamlit itself
    # — without this guard the user gets two tabs and lands on the wrong one.
    if not os.environ.get("SCUDO_NO_BROWSER"):
        try:
            webbrowser.open(f"{base}/app/")
        except Exception:  # noqa: BLE001 — a headless box must not fail on this
            pass

    # threaded=True: the SSE reasoning stream holds a connection open, and a
    # single-threaded server would then refuse every other request.
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
