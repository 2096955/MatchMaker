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
    app.run(debug=True, port=5000)
