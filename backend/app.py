"""Flask application entry point for the Data Ingestion Framework API.

All routes are mounted under the ``/api`` prefix via six Blueprints:

- ``providers_bp`` — CRUD for ``tp_provider`` (SCD-2 versioned).
- ``datasets_bp``  — CRUD for ``tp_dataset`` + ``tp_dataset_col``; also
  manages the physical ingestion tables in the ``ingestion`` database.
- ``admin_bp``     — Roles, privileges, and users management.
- ``ingest_bp``    — Ingestion trigger endpoint and ETL run-log queries.
- ``catalogue_bp`` — Vendor catalogue HTTP facade over ``vendor_catalogue_mcp``.
- ``mapping_bp``   — Vendor → CDAO mapping HTTP facade over ``scudo_mapping_mcp``.
- ``ifusion_bp``   — Mock iFusion publish seam (SPI V2 stand-in for the demo).
- ``ingestion_mock_bp`` — Demo-only seam: paste a vendor row, get a frame.

CORS is enabled for all ``/api/*`` routes to allow the Vite dev server
(running on a different port) to call the API without proxy restrictions.

AUTH MODEL (read before deploying — see ``auth.py`` for the trust boundary):

  Every request under ``/api/*`` must carry an authenticated principal. The
  ``before_request`` hook below resolves it via ``auth.resolve_principal`` and
  rejects with 401 on failure. Today's policy reads a trusted upstream header
  (``X-Authenticated-User`` by default); this is SOUND ONLY when

    a) the service is reachable EXCLUSIVELY through the gateway, and
    b) the gateway strips any client-supplied copy of the header before
       setting its own.

  Both assumptions are LOAD-BEARING. Confirm on every deploy. If either fails
  — or the edge already issues signed JWTs — swap the policy inside
  ``auth.py`` to JWT verification; no route changes.
"""

import os
import uuid

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from auth import AuthError, resolve_principal
from logger import ui_logger
from routes.providers import providers_bp
from routes.datasets import datasets_bp
from routes.admin import admin_bp
from routes.ingest import ingest_bp
from routes.catalogue import catalogue_bp
from routes.mapping import mapping_bp
from routes.ifusion import ifusion_bp
from routes.ingestion_mock import ingestion_mock_bp
from routes.visibility import visibility_bp

app = Flask(__name__)

# Reject oversized uploads before they are read into memory (Codex A3).
# 5 MB default; override with SCUDO_MAX_UPLOAD_BYTES. Flask returns 413 when a
# request body exceeds this.
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("SCUDO_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))
)

# CORS for /api/* (Codex B6). In prod the SPA is same-origin via CloudFront, so
# no cross-origin is needed; default to the deployed dashboard origin and only
# fall back to "*" when SCUDO_CORS_ORIGINS is unset (local dev convenience).
# Set SCUDO_CORS_ORIGINS to a comma-separated allowlist in any shared/deployed
# environment.
_cors_origins_env = os.getenv("SCUDO_CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env
    else "*"
)
CORS(app, resources={r"/api/*": {"origins": _cors_origins}})

app.register_blueprint(providers_bp, url_prefix="/api")
app.register_blueprint(datasets_bp, url_prefix="/api")
app.register_blueprint(admin_bp, url_prefix="/api")
app.register_blueprint(ingest_bp, url_prefix="/api")
app.register_blueprint(catalogue_bp, url_prefix="/api")
app.register_blueprint(mapping_bp, url_prefix="/api")
app.register_blueprint(ifusion_bp, url_prefix="/api")
app.register_blueprint(ingestion_mock_bp, url_prefix="/api")
app.register_blueprint(visibility_bp, url_prefix="/api")

# Off-by-default local-dev convenience: serve the vendored, READ-ONLY
# dashboard-dist/ bundle same-origin so its relative /api/* fetches
# (confirmed empty base URL in the bundle) reach this same Flask process.
# Unset in every deployed environment (the SPA is served via CloudFront
# there) - this changes NOTHING when SCUDO_SERVE_DASHBOARD_DIST is unset.
if os.getenv("SCUDO_SERVE_DASHBOARD_DIST"):
    from flask import send_from_directory

    _DASHBOARD_DIST_DIR = os.path.join(
        os.path.dirname(__file__), "..", "dashboard-dist"
    )

    @app.get("/demo/")
    def _serve_dashboard_dist_index():
        return send_from_directory(_DASHBOARD_DIST_DIR, "index.html")

    @app.get("/demo/<path:filename>")
    def _serve_dashboard_dist_asset(filename):
        return send_from_directory(_DASHBOARD_DIST_DIR, filename)


# JPMC-LOCAL: serve the CONSOLE frontend (frontend/dist) from Flask too.
#
# WHY. On a locked-down desktop, Citrix group policy blocks
# node_modules/@esbuild/win32-x64/esbuild.exe, so `npm run dev` and
# `npm run build` both fail with spawn UNKNOWN and Vite cannot start at all.
# That leaves the API reachable and the UI unreachable, with no Node fix
# available to the person sitting at the machine.
#
# A pre-built bundle needs no Node, no Vite and no esbuild — only Flask, which
# is already running. Serving it SAME-ORIGIN also removes the dev-server proxy
# from the picture entirely: the bundle's relative /api/* fetches land on this
# process, so VITE_API_PROXY becomes irrelevant.
#
# This is the console (frontend/), NOT the separate dashboard-dist application
# served at /demo/ above.
#
# Unset by default, so nothing changes for anyone running Vite normally.
if os.getenv("SCUDO_SERVE_FRONTEND_DIST"):
    from flask import send_from_directory

    _FRONTEND_DIST_DIR = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "dist"
    )

    @app.get("/app/")
    def _serve_frontend_dist_index():
        return send_from_directory(_FRONTEND_DIST_DIR, "index.html")

    @app.get("/app/<path:filename>")
    def _serve_frontend_dist_asset(filename):
        # SPA fallback: unknown paths are client-side routes, not 404s, so a
        # deep link or a refresh on /app/providers still loads the app.
        full = os.path.join(_FRONTEND_DIST_DIR, filename)
        if not os.path.isfile(full):
            return send_from_directory(_FRONTEND_DIST_DIR, "index.html")
        return send_from_directory(_FRONTEND_DIST_DIR, filename)


# JPMC-LOCAL: without this, http://127.0.0.1:5000/ returns a raw 404 JSON blob
# and looks broken. It is not broken — the backend is an API, the UI is served
# by Vite on :3000. This route says so, and lists the endpoints that need no
# database so you can confirm the backend works before touching Postgres.
@app.get("/")
def _index():
    """Human-readable landing page for the API process."""
    return (
        jsonify(
            {
                "service": "SCUDO backend API",
                "ui": "http://localhost:3000  <-- open THIS in the browser",
                "note": "This process serves /api/* only. It is working if you can see this.",
                "no_database_needed": [
                    "/healthz",
                    "/readyz",
                    "/api/catalogue/products",
                    "/api/mapping/vendors",
                ],
                "needs_postgres": [
                    "/api/providers",
                    "/api/datasets",
                    "/api/admin/users",
                ],
            }
        ),
        200,
    )


@app.get("/healthz")
def _healthz():
    """Unauthenticated liveness probe for the ALB target group.

    Lives OUTSIDE ``/api/*`` so the ``before_request`` auth gate passes it
    through (see the non-API pass-through below). No DB, no auth — a 200 means
    the process is up, independent of Aurora/FalkorDB readiness.
    """
    return jsonify({"status": "ok"}), 200


@app.get("/readyz")
def _readyz():
    """Readiness probe (Codex A8): 200 only once the CDAO taxonomy has actually
    seeded. Distinct from /healthz (liveness) — a process can be UP but not yet
    ready to serve matching. Returns 503 + the last seed error until seeding
    succeeds, so a load balancer won't route traffic to a not-ready instance.
    """
    try:
        from routes.mapping import readiness

        state = readiness()
    except Exception as e:  # noqa: BLE001
        return jsonify({"ready": False, "error": f"{type(e).__name__}: {e}"}), 503
    if state.get("seed_ok"):
        return jsonify({"ready": True}), 200
    return jsonify({"ready": False, "error": state.get("last_error")}), 503


@app.before_request
def _require_principal():
    """Gate every ``/api/*`` request behind ``auth.resolve_principal``.

    Routes read the result via ``flask.g.principal``. Routes do NOT call
    ``resolve_principal`` themselves — that would scatter the trust
    boundary; keeping it here makes the gate easy to audit and easy to
    replace (header → JWT in one file).
    """
    # Pass-through for non-API paths (static, health, etc).
    if not (request.path or "").startswith("/api/"):
        return None
    # CORS preflight: the browser sends OPTIONS without credentials; the
    # CORS extension answers it. Gating preflight would block the actual
    # request from ever being sent.
    if request.method == "OPTIONS":
        return None
    try:
        g.principal = resolve_principal(request.headers)
    except AuthError:
        # Audit the rejection so operators can distinguish "gateway
        # misconfigured (header always missing)" from "caller bypassed the
        # gateway (header present but with junk)". We log only the PRESENCE
        # of the header as a boolean — never the value, which could be PII
        # or attacker-controlled.
        header_name = (
            os.getenv("SCUDO_AUTH_PRINCIPAL_HEADER", "") or "X-Authenticated-User"
        )
        ui_logger.warning(
            "auth rejected",
            path=request.path,
            method=request.method,
            has_header=bool((request.headers.get(header_name) or "").strip()),
        )
        return jsonify({"error": "authentication required"}), 401
    return None


@app.errorhandler(Exception)
def handle_error(e):
    """Catch-all exception handler — logs the full traceback, returns JSON.

    HTTPExceptions (404/405/413 from MAX_CONTENT_LENGTH, explicit aborts) keep
    their own status + safe description. For genuinely unexpected errors we
    return a GENERIC message plus a correlation id (Codex A7) — the detail goes
    to logs only, never to the client, so parser/store internals don't leak.
    """
    from werkzeug.exceptions import HTTPException

    if isinstance(e, HTTPException):
        app.logger.warning("http error %s: %s", e.code, e)
        return jsonify({"error": e.description}), e.code or 500

    error_id = uuid.uuid4().hex[:12]
    app.logger.exception("unhandled error [%s]: %s", error_id, e)
    return (
        jsonify(
            {
                "error": "internal server error",
                "error_id": error_id,
            }
        ),
        500,
    )


if __name__ == "__main__":
    # Run in debug mode on port 5000 for local development.
    # JPMC-LOCAL: port made configurable. 5000 was hard-coded, so if anything
    # already held that port the process died with "Address already in use"
    # and there was no way out without editing code. macOS AirPlay Receiver
    # squats on 5000 by default, and locked-down desktops often have their own
    # agent there. Now: PORT=5050 python start_local.py (and point the UI at
    # it with VITE_API_PROXY=http://localhost:5050). Default is unchanged.
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))
