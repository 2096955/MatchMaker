#!/usr/bin/env python3
"""Local ship surface: matching dashboard (/demo/) + Capone-shaped /api/mapping/* + port APIs.

SCUDO_LOCAL=1 SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py
open http://localhost:5001/demo/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Local defaults before importing scudo
os.environ.setdefault("SCUDO_LOCAL", "1")
os.environ.setdefault("SCUDO_SERVE_DASHBOARD_DIST", "1")
os.environ.setdefault("SCUDO_AGENT_MODE", "deterministic")

from flask import Flask, Response, jsonify, request, send_from_directory

from scudo import dashboard_api
from scudo.handler import handle

DASHBOARD_DIST = ROOT / "dashboard-dist"
PORT = int(os.environ.get("SCUDO_PORT", "5001"))


def create_app() -> Flask:
    app = Flask(__name__)

    @app.after_request
    def _cors(resp: Response):
        # Dev dashboard on :5173 can point VITE_API_BASE here
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Api-Key, X-Authenticated-User",
        )
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        return resp

    @app.route("/api/mapping/<path:subpath>", methods=["OPTIONS"])
    def _options(subpath: str):
        return ("", 204)

    # ── Matching dashboard SPA (vendored Capone / Understand-Anything build) ──
    if os.environ.get("SCUDO_SERVE_DASHBOARD_DIST", "").strip() in {"1", "true", "yes"}:
        if not DASHBOARD_DIST.is_dir():
            raise SystemExit(
                f"dashboard-dist missing at {DASHBOARD_DIST} — copy Capone dashboard-dist/"
            )

        @app.get("/demo/")
        def demo_index():
            return send_from_directory(DASHBOARD_DIST, "index.html")

        @app.get("/demo/<path:filename>")
        def demo_asset(filename: str):
            return send_from_directory(DASHBOARD_DIST, filename)

    # ── Capone-shaped dashboard API ───────────────────────────────────────────
    @app.get("/api/mapping/vendors")
    def vendors():
        return jsonify(dashboard_api.list_vendors())

    @app.post("/api/mapping/ingest/stream")
    def ingest_stream():
        vendor = (request.form.get("vendor") or "").strip()
        f = request.files.get("file")
        if f is None or not f.filename:
            return jsonify({"error": "file is required (multipart)"}), 400
        data = f.read()
        return Response(
            dashboard_api.iter_ingest_sse(vendor, f.filename, data),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/mapping/agent/run")
    def agent_run():
        body = request.get_json(silent=True) or {}
        return Response(
            dashboard_api.iter_agent_run_sse(body),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/mapping/decision")
    def decision():
        body = request.get_json(silent=True) or {}
        status, payload = dashboard_api.record_dashboard_decision(body)
        return jsonify(payload), status

    # ── Existing port Lambda-shaped APIs (JSON) ───────────────────────────────
    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def port_api(path: str):
        # Avoid stealing /demo and /api/mapping (registered above)
        if path.startswith("demo") or path.startswith("api/mapping"):
            return jsonify({"error": "not found"}), 404
        event = {
            "path": "/" + path if path else "/",
            "httpMethod": request.method,
            "headers": {k: v for k, v in request.headers.items()},
            "body": request.get_json(silent=True)
            if request.is_json
            else (request.get_data(as_text=True) or {}),
        }
        result = handle(event)
        return jsonify(result.get("body")), int(result.get("statusCode") or 200)

    return app


def main() -> int:
    app = create_app()
    print(f"SCUDO jpmc-port listening on http://127.0.0.1:{PORT}", flush=True)
    if DASHBOARD_DIST.is_dir():
        print(f"  Matching dashboard: http://127.0.0.1:{PORT}/demo/", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
