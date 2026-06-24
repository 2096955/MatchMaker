"""REST API routes for the scudo mapping MCP (``/api/mapping``).

Thin HTTP facade over the ``scudo_mapping_mcp`` package — same package, two
transports. The MCP server in ``scudo_mapping_mcp/mcp_server.py`` exposes these
same tools over streamable HTTP for AI/automation clients; this blueprint
exposes them over plain JSON for the browser:

- ``GET  /api/mapping/graph``                       — pipeline + match graph payload
- ``GET  /api/mapping/vendors``                       — priority vendor enum
- ``GET  /api/mapping/taxonomy/<node_iri>``           — one-hop taxonomy node
- ``GET  /api/mapping/neighbourhood/<node_iri>``      — bounded subgraph
- ``POST /api/mapping/similar``                       — top-N CDAO candidates
- ``POST /api/mapping/map``                           — map one vendor product
- ``POST /api/mapping/decision``                      — record a HITL decision
- ``GET  /api/mapping/bundle``                        — export the M6 bundle
- ``POST /api/mapping/bundle/import``                 — import an M6 bundle
- ``POST /api/mapping/ingest``                        — drop in a vendor file
- ``GET  /api/mapping/working_set``                   — list ingested products
- ``GET  /api/mapping/agent/describe``                — which agent backend
- ``POST /api/mapping/agent/run``                     — SSE-stream an agent run

Tool envelopes (clamps, scope gate, 0.80 confidence floor) are enforced inside
the package, not here — this file only translates HTTP <-> the typed contracts.
On the Neptune cutover the package's ``store/`` swap is the only change; the
routes do not move.
"""

import os

from flask import Blueprint, Response, g, jsonify, request, stream_with_context
from pydantic import ValidationError

from logger import ui_logger
from scudo.build_matching_graph import build_match_payload
from scudo_mapping_mcp.agent import get_agent
from scudo_mapping_mcp.bundle import export_bundle, import_bundle
from scudo_mapping_mcp.config import PRIORITY_VENDORS
from scudo_mapping_mcp.feedback import apply_decision
from scudo_mapping_mcp.frames import FrameDataError, _read_vendor_frame, all_frames
from scudo_mapping_mcp.hydrate import HydrationError, hydrate
from scudo_mapping_mcp.ingest import ingest_bytes, seed_taxonomy
from scudo_mapping_mcp.matching import map_vendor_product
from scudo_mapping_mcp.models import MappingBundle, VendorProductRef
from scudo_mapping_mcp.store import get_store

mapping_bp = Blueprint("mapping", __name__)

# One-shot best-effort seed of the CDAO taxonomy. Mirrors the MCP server's
# lifespan hook: the store stays usable even if it isn't reachable yet (a later
# read just returns empty). The flag prevents repeat seeding under the Flask
# debug reloader.
_seeded = False


def _ensure_seeded():
    global _seeded
    if _seeded:
        return
    try:
        n = seed_taxonomy()
        ui_logger.info("CDAO taxonomy seeded", nodes=n)
    except Exception as e:  # noqa: BLE001 — best-effort, store may not be up
        ui_logger.warning(
            "CDAO taxonomy seed skipped", error=f"{type(e).__name__}: {e}"
        )
        _seeded = True
        return
    # Replay the M6 canonical bundle into FalkorDB so confirmed precedents are
    # available on boot — the resilience pin from the matching strategy:
    # stale or empty FalkorDB serves confident-but-wrong matches. Best-effort
    # here (we never block Flask startup); the deploy stack can layer a strict
    # /health probe on top for the production posture.
    try:
        result = hydrate(strict=False)
        if result.skipped_no_bundle:
            ui_logger.warning("hydration skipped (no canonical bundle)")
        else:
            ui_logger.info(
                "hydration applied",
                applied=result.applied,
                skipped_unknown_node=result.skipped_unknown_node,
                skipped_out_of_scope=result.skipped_out_of_scope,
                bundle_version=result.bundle_version,
                taxonomy_version=result.bundle_taxonomy_version,
            )
    except HydrationError as e:
        ui_logger.error(
            "hydration failed (proceeding empty)", error=f"{type(e).__name__}: {e}"
        )
    _seeded = True


@mapping_bp.before_request
def _before():
    _ensure_seeded()


def _validate_vendor(vendor):
    """Reject vendors outside the priority set up front; matches the scope gate.

    This is a MEMBERSHIP check and nothing more — priority does NOT lower the
    0.80 confidence floor for any vendor. The auto-mapping threshold is read
    once from ``config.settings.confidence_floor`` inside
    ``matching.map_vendor_product``; no other code path touches it. Adding a
    vendor-specific override here would re-introduce a hidden second decision
    surface and violate I4 (floor in code, in one place).
    """
    if vendor not in PRIORITY_VENDORS:
        return f"unknown vendor {vendor!r} (valid: {', '.join(PRIORITY_VENDORS)})"
    return None


def _frame(vendor, product_id, name="", description=""):
    """Same resolution rule as scudo_mapping_mcp.mcp_server._frame.

    Inline name/description wins so the endpoint is exercisable without an
    ingestion pipeline; otherwise fall back to the working-set / S3 frame
    lookup. If neither is available we still return a minimal ref so the
    matcher can run on the product_id alone.
    """
    if name or description:
        return VendorProductRef(
            vendor=vendor,
            product_id=product_id,
            name=name,
            description=description,
        )
    ref = _read_vendor_frame(vendor, product_id)
    if ref is None:
        return VendorProductRef(vendor=vendor, product_id=product_id, name=product_id)
    return ref


@mapping_bp.get("/mapping/vendors")
def list_vendors():
    """Return the priority vendor enum so the UI dropdown stays in sync."""
    return jsonify(
        {
            "vendors": list(PRIORITY_VENDORS),
            "default": PRIORITY_VENDORS[0] if PRIORITY_VENDORS else None,
        }
    )


@mapping_bp.get("/mapping/graph")
def get_mapping_graph():
    """Return the MatchPayload graph for the pipeline / drill-down views.

    Query params:
        vendor (str, optional): Focus on one vendor product drill-down.
        ref (str, optional):    Product id (requires vendor).
    """
    vendor = (request.args.get("vendor") or "").strip() or None
    ref = (request.args.get("ref") or "").strip() or None
    if ref and not vendor:
        return jsonify({"error": "vendor is required when ref is set"}), 400
    if vendor:
        err = _validate_vendor(vendor)
        if err:
            return jsonify({"error": err}), 400

    try:
        payload = build_match_payload(vendor=vendor, ref=ref)
    except Exception as e:  # noqa: BLE001
        ui_logger.error(
            "Mapping graph build failed",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "could not build mapping graph"}), 500
    return jsonify(payload)


@mapping_bp.get("/mapping/taxonomy/<path:node_iri>")
def get_taxonomy_node(node_iri):
    """One CDAO node with its immediate parent and children (single hop).

    Path params:
        node_iri (str): Taxonomy node IRI, e.g. ``cdao:equities``.

    Returns:
        flask.Response: JSON ``{iri, label, parent_iri, children_iris[]}``.
            404 if no node with that IRI exists.
    """
    if not node_iri or not node_iri.strip():
        return jsonify({"error": "node_iri is required"}), 400
    try:
        node = get_store().get_taxonomy_node(node_iri)
    except Exception as e:  # noqa: BLE001 — store/transport failure
        ui_logger.error(
            "Mapping store unavailable",
            op="get_taxonomy_node",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503
    if node is None:
        return jsonify({"error": f"no taxonomy node {node_iri!r}"}), 404
    return jsonify(node.model_dump(mode="json"))


@mapping_bp.get("/mapping/neighbourhood/<path:node_iri>")
def get_ontology_neighbourhood(node_iri):
    """Bounded subgraph around a node. Depth and node count are clamped.

    Path params:
        node_iri (str): Root taxonomy node IRI.

    Query params:
        max_depth (int): 1-3, default 2.
        max_nodes (int): 1-100, default 50.

    Returns:
        flask.Response: JSON ``{root_iri, nodes:[{iri,label}], edges:[[p,c]]}``.
    """
    if not node_iri or not node_iri.strip():
        return jsonify({"error": "node_iri is required"}), 400
    try:
        max_depth = int(request.args.get("max_depth", 2))
        max_nodes = int(request.args.get("max_nodes", 50))
    except ValueError:
        return jsonify({"error": "max_depth/max_nodes must be integers"}), 400
    if not 1 <= max_depth <= 3:
        return jsonify({"error": "max_depth must be between 1 and 3"}), 400
    if not 1 <= max_nodes <= 100:
        return jsonify({"error": "max_nodes must be between 1 and 100"}), 400
    try:
        sg = get_store().get_ontology_neighbourhood(
            node_iri,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    except Exception as e:  # noqa: BLE001
        ui_logger.error(
            "Mapping store unavailable",
            op="get_ontology_neighbourhood",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503
    return jsonify(sg.model_dump(mode="json"))


@mapping_bp.post("/mapping/similar")
def find_similar_products():
    """Top-N candidate CDAO nodes for a vendor product (clamped to 25).

    JSON body:
        vendor (str): One of ``PRIORITY_VENDORS``.
        product_id (str): Vendor-native product id.
        name (str, optional): Product name (inline avoids a frame lookup).
        description (str, optional): Product description.
        max_results (int, optional): 1-25, default 10.
        min_similarity (float, optional): 0.0-1.0, default 0.0.

    Returns:
        flask.Response: JSON ``{count, candidates:[{node, similarity}]}``.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required"}), 400
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400
    err = _validate_vendor(vendor)
    if err:
        return jsonify({"error": err}), 400

    try:
        max_results = int(body.get("max_results", 10))
        min_similarity = float(body.get("min_similarity", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "max_results/min_similarity must be numeric"}), 400
    if not 1 <= max_results <= 25:
        return jsonify({"error": "max_results must be between 1 and 25"}), 400
    if not 0.0 <= min_similarity <= 1.0:
        return jsonify({"error": "min_similarity must be between 0.0 and 1.0"}), 400

    try:
        ref = _frame(
            vendor,
            product_id,
            body.get("name", "") or "",
            body.get("description", "") or "",
        )
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    except FrameDataError as e:
        # Upstream wrote a bad frame; not the caller's fault. 502 distinguishes
        # "we couldn't get usable data from upstream" from "client sent a bad
        # body" (400) and "frame source unwired" (503).
        ui_logger.error(
            "Frame data invalid",
            op="find_similar_products",
            vendor=vendor,
            product_id=product_id,
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"upstream frame invalid: {e}"}), 502
    except NotImplementedError as e:
        ui_logger.error(
            "Frame source unavailable",
            op="find_similar_products",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"frame source unavailable: {e}"}), 503

    try:
        cands = get_store().find_similar_products(
            ref,
            max_results=max_results,
            min_similarity=min_similarity,
        )
    except (ConnectionError, TimeoutError, OSError) as e:
        ui_logger.error(
            "Mapping store unavailable",
            op="find_similar_products",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503

    return jsonify(
        {
            "count": len(cands),
            "candidates": [c.model_dump(mode="json") for c in cands],
        }
    )


@mapping_bp.post("/mapping/map")
def map_product():
    """Map one vendor product to a CDAO node.

    Applies the deterministic scope gate (out-of-scope vendors are blocked) and
    the 0.80 confidence floor (below -> ``needs_review`` for a human). Reuses a
    prior approved mapping when one exists.

    JSON body:
        vendor (str): One of ``PRIORITY_VENDORS``.
        product_id (str): Vendor-native product id.
        name (str, optional): Product name (inline avoids a frame lookup).
        description (str, optional): Product description.

    Returns:
        flask.Response: JSON ``MappingResult`` —
            ``{vendor_product_iri, vendor, product_id, product_name,
               mapped_node_iri, mapped_node_label, confidence, status,
               candidates[], rationale}``.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required"}), 400
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400
    # Note: out-of-scope vendors aren't a 400 here — the matcher's scope gate
    # is the authoritative answer and surfaces them as status='out_of_scope'.

    try:
        ref = _frame(
            vendor,
            product_id,
            body.get("name", "") or "",
            body.get("description", "") or "",
        )
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    except FrameDataError as e:
        ui_logger.error(
            "Frame data invalid",
            op="map_vendor_product",
            vendor=vendor,
            product_id=product_id,
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"upstream frame invalid: {e}"}), 502
    except NotImplementedError as e:
        ui_logger.error(
            "Frame source unavailable",
            op="map_vendor_product",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"frame source unavailable: {e}"}), 503

    try:
        result = map_vendor_product(ref)
    except (ConnectionError, TimeoutError, OSError) as e:
        ui_logger.error(
            "Mapping store unavailable",
            op="map_vendor_product",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503

    return jsonify(result.model_dump(mode="json"))


@mapping_bp.post("/mapping/decision")
def record_decision():
    """Record a HITL decision (approve / override / reject).

    Closes the M4-new feedback loop: a CONFIRMED precedent is written so the
    next call to ``/mapping/map`` for this product short-circuits to the
    human-confirmed result, and the rank signal boosts that node in future
    candidate ranking for products with the same vendor signature.

    JSON body:
        vendor (str):           One of ``PRIORITY_VENDORS``.
        product_id (str):       Vendor-native product id.
        decision (str):         ``'approve' | 'override' | 'reject'``.
        node_iri (str):         CDAO node the decision is about. For approve
                                this is the suggested node; for override the
                                chosen alternative; for reject the node that
                                was rejected.
        name (str, optional):           Product name (inline frame).
        description (str, optional):    Product description.
        suggested_confidence (float):   REQUIRED for decision='approve' — the
                                        matcher's score, preserved on the
                                        precedent edge. Ignored otherwise.

    Identity (``decided_by``) is bound to the AUTHENTICATED PRINCIPAL — read
    from ``flask.g.principal`` via the ``auth.resolve_principal`` accessor in
    ``app.before_request``. The body's ``decided_by`` field is IGNORED if
    present; supplying it does not change what is recorded on the precedent
    edge. The audit trail is therefore not forgeable from the request body.

    Returns:
        flask.Response: JSON ``MappingResult`` with the appropriate
            ``approved``/``overridden``/``rejected`` status.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    decision_raw = (body.get("decision") or "").strip()
    decision_lower = decision_raw.lower()
    node_iri = (body.get("node_iri") or "").strip()

    # Authoritative identity: the authenticated principal, never the body.
    # app.before_request has already rejected the request with 401 if
    # no principal could be resolved, so g.principal is guaranteed here.
    decided_by = g.principal.user_id
    if "decided_by" in body:
        ui_logger.warning(
            "HITL decided_by in body ignored — authenticated principal used",
            vendor=vendor,
            product_id=product_id,
            body_decided_by=body.get("decided_by"),
            principal=decided_by,
        )

    # Attempt-level audit (visible BEFORE any validation that might fail) so
    # operators can correlate every HITL action with its outcome — including
    # rejections and partial-failure recoveries.
    ui_logger.info(
        "HITL mapping decision attempted",
        vendor=vendor,
        product_id=product_id,
        decision=decision_lower,
        decided_by=decided_by,
        node_iri=node_iri,
    )

    if not vendor:
        return jsonify({"error": "vendor is required"}), 400
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400
    # The scope gate is the authoritative answer at the matcher boundary, but
    # decisions are about MAPPING, not retrieval — we still want vendor in
    # the priority set up front to avoid recording decisions for vendors we
    # have no business mapping (apply_decision re-checks the gate too).
    err = _validate_vendor(vendor)
    if err:
        ui_logger.warning(
            "HITL mapping decision rejected",
            vendor=vendor,
            product_id=product_id,
            reason=err,
        )
        return jsonify({"error": err}), 400

    # suggested_confidence is REQUIRED for approve so the precedent's
    # confidence preserves the matcher's score (Section 10a); for override
    # and reject the value is overridden by apply_decision so we just default
    # to None and let the verb's policy apply.
    suggested_confidence = None
    if "suggested_confidence" in body and body.get("suggested_confidence") is not None:
        try:
            suggested_confidence = float(body["suggested_confidence"])
        except (TypeError, ValueError):
            ui_logger.warning(
                "HITL mapping decision rejected",
                vendor=vendor,
                product_id=product_id,
                reason="non-numeric suggested_confidence",
            )
            return jsonify({"error": "suggested_confidence must be numeric"}), 400

    try:
        ref = _frame(
            vendor,
            product_id,
            body.get("name", "") or "",
            body.get("description", "") or "",
        )
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    except FrameDataError as e:
        ui_logger.error(
            "Frame data invalid",
            op="record_decision",
            vendor=vendor,
            product_id=product_id,
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"upstream frame invalid: {e}"}), 502
    except NotImplementedError as e:
        # FRAME_SOURCE=s3 set but not yet wired (M8 swap point). Surface as
        # 503 with the underlying message instead of leaking the internal
        # exception class as an unhandled 500.
        ui_logger.error(
            "Frame source unavailable",
            op="record_decision",
            frame_source="s3",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"frame source unavailable: {e}"}), 503

    try:
        result = apply_decision(
            ref,
            decision=decision_raw,
            decided_by=decided_by,
            node_iri=node_iri,
            suggested_confidence=suggested_confidence,
        )
    except ValueError as e:
        ui_logger.warning(
            "HITL mapping decision rejected",
            vendor=vendor,
            product_id=product_id,
            decision=decision_lower,
            reason=str(e),
        )
        return jsonify({"error": str(e)}), 400
    except (ConnectionError, TimeoutError, OSError) as e:
        # Narrowed from a bare `except Exception` — transport failures only.
        # Programmer errors (TypeError/AttributeError/etc) now bubble to the
        # global error handler as a real 500 so operators don't chase a
        # phantom store outage.
        ui_logger.error(
            "Mapping store unavailable",
            op="record_decision",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503

    ui_logger.info(
        "HITL mapping decision recorded",
        vendor=vendor,
        product_id=product_id,
        decision=decision_lower,
        decided_by=decided_by,
        node_iri=node_iri,
    )
    return jsonify(result.model_dump(mode="json"))


@mapping_bp.get("/mapping/bundle")
def export_mapping_bundle():
    """Export the M6 portable mapping bundle.

    The cutover artifact: every CONFIRMED MAPPED_TO precedent + its rank
    signal + the self-describing artifact, plus a deterministic taxonomy
    fingerprint. Re-export of the same store with ``?created_at=`` pinned
    is byte-identical.

    Query params:
        source_env (str, optional): Logical env name written into the bundle's
            provenance. Defaults to ``SCUDO_SOURCE_ENV`` env var or
            ``'unknown'``.
        created_at (str, optional): ISO-8601 UTC override (for diffability /
            reproducible builds). Defaults to now.

    Returns:
        flask.Response: JSON ``MappingBundle``.
    """
    source_env = (request.args.get("source_env") or "").strip() or None
    created_at = (request.args.get("created_at") or "").strip() or None
    try:
        bundle = export_bundle(source_env=source_env, created_at=created_at)
    except (ConnectionError, TimeoutError, OSError) as e:
        ui_logger.error(
            "Mapping store unavailable",
            op="export_bundle",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503
    ui_logger.info(
        "Mapping bundle exported",
        principal=g.principal.user_id,
        patterns=len(bundle.patterns),
        source_env=bundle.source_env,
        taxonomy_version=bundle.taxonomy_version,
    )
    return jsonify(bundle.model_dump(mode="json"))


@mapping_bp.post("/mapping/bundle/import")
def import_mapping_bundle():
    """Import an M6 mapping bundle.

    Idempotent: re-importing the same bundle leaves the store identical.
    Patterns whose ``mapped_node_iri`` is absent locally (or whose vendor is
    out of scope locally) are SKIPPED, not failed — the summary reports the
    counts so operators can spot divergence between source and local taxonomies.

    JSON body: a ``MappingBundle`` (typically the result of GET /mapping/bundle
    on the source environment).

    Returns:
        flask.Response: JSON ``BundleImportSummary``.
    """
    body = request.get_json(silent=True) or {}
    try:
        bundle = MappingBundle.model_validate(body)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400

    try:
        summary = import_bundle(bundle)
    except ValueError as e:
        # Format incompat, etc.
        ui_logger.warning(
            "Mapping bundle import rejected",
            principal=g.principal.user_id,
            source_env=bundle.source_env,
            reason=str(e),
        )
        return jsonify({"error": str(e)}), 400
    except (ConnectionError, TimeoutError, OSError) as e:
        ui_logger.error(
            "Mapping store unavailable",
            op="import_bundle",
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": "mapping store unavailable"}), 503

    ui_logger.info(
        "Mapping bundle imported",
        principal=g.principal.user_id,
        source_env=bundle.source_env,
        applied=summary.applied,
        total=summary.total,
        skipped_unknown_node=summary.skipped_unknown_node,
        skipped_out_of_scope=summary.skipped_out_of_scope,
        taxonomy_version_source=summary.taxonomy_version_source,
        taxonomy_version_local=summary.taxonomy_version_local,
    )
    return jsonify(summary.model_dump(mode="json"))


# ──────────────────────────────────────────────────────────────────────────
# Demo loop — files in, agent activity, matcher decisions out
# ──────────────────────────────────────────────────────────────────────────
@mapping_bp.post("/mapping/ingest")
def ingest_vendor_file():
    """Drop in a vendor file (CSV / JSON) and populate the working set.

    Form fields:
        vendor (str):       One of ``PRIORITY_VENDORS``.
        file (multipart):   The vendor file. CSV (.csv/.tsv) or JSON (.json).

    Returns:
        flask.Response: JSON ``{ingested: int, products: [{vendor, product_id,
            name}]}``. Products land in the in-memory working set so the
            matcher / agent path can immediately read them.
    """
    vendor = (request.form.get("vendor") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required (form field)"}), 400
    err = _validate_vendor(vendor)
    if err:
        return jsonify({"error": err}), 400

    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "file is required (multipart)"}), 400

    data = f.read()
    if not data:
        return jsonify({"error": "file is empty"}), 400

    try:
        frames = ingest_bytes(vendor, f.filename, data, upsert=True)
    except (UnicodeDecodeError, ValueError) as e:
        ui_logger.warning(
            "Vendor file ingest rejected",
            vendor=vendor,
            filename=f.filename,
            reason=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"cannot parse vendor file: {e}"}), 400
    except (ConnectionError, TimeoutError, OSError) as e:
        ui_logger.error(
            "Mapping store unavailable", op="ingest", error=f"{type(e).__name__}: {e}"
        )
        return jsonify({"error": "mapping store unavailable"}), 503

    ui_logger.info(
        "Vendor file ingested",
        principal=g.principal.user_id,
        vendor=vendor,
        filename=f.filename,
        products=len(frames),
    )
    return jsonify(
        {
            "ingested": len(frames),
            "products": [
                {"vendor": fr.vendor, "product_id": fr.product_id, "name": fr.name}
                for fr in frames
            ],
        }
    )


# ETL stage key → matching-graph node id(s). Lets the dashboard light up the
# real ETL nodes (EventBridge → SQS → Lambda → Validate → S3/DynamoDB) from
# genuine pipeline telemetry, not a scripted shimmer.
_ETL_STAGE_NODES: dict[str, list[str]] = {
    "received": ["etl-eventbridge", "etl-sqs"],
    "parse": ["etl-lambda"],
    "validate": ["etl-validate", "etl-s3-quarantine"],
    "sink": ["etl-s3-sink", "etl-dynamodb"],
}


@mapping_bp.post("/mapping/ingest/stream")
def ingest_vendor_file_stream():
    """Ingest a vendor file and stream REAL ETL stage events over SSE.

    Same inputs as ``/mapping/ingest`` (multipart ``file`` + ``vendor`` form
    field). Streams ``text/event-stream`` frames as the pipeline runs:

        {"type":"stage","stage":"received","nodeIds":[...],"detail":{...}}
        {"type":"stage","stage":"parse","nodeIds":["etl-lambda"],"detail":{"rows":N}}
        {"type":"stage","stage":"validate",...}
        {"type":"stage","stage":"sink",...}
        {"type":"final_result","ingested":N,"products":[...]}
        {"type":"done"}

    Each ``detail`` carries real counts produced by this run. The frontend maps
    ``nodeIds`` onto the ETL graph nodes to animate the live flow.
    """
    import json as _json
    import queue
    import threading

    vendor = (request.form.get("vendor") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required (form field)"}), 400
    err = _validate_vendor(vendor)
    if err:
        return jsonify({"error": err}), 400

    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "file is required (multipart)"}), 400
    filename = f.filename
    data = f.read()
    if not data:
        return jsonify({"error": "file is empty"}), 400

    principal = g.principal.user_id
    q: "queue.Queue[dict | None]" = queue.Queue()

    def on_stage(stage: str, detail: dict) -> None:
        q.put(
            {
                "type": "stage",
                "stage": stage,
                "nodeIds": _ETL_STAGE_NODES.get(stage, []),
                "detail": detail,
            }
        )

    def worker() -> None:
        try:
            frames = ingest_bytes(
                vendor, filename, data, upsert=True, on_stage=on_stage
            )
            q.put(
                {
                    "type": "final_result",
                    "ingested": len(frames),
                    "products": [
                        {
                            "vendor": fr.vendor,
                            "product_id": fr.product_id,
                            "name": fr.name,
                        }
                        for fr in frames
                    ],
                }
            )
        except (UnicodeDecodeError, ValueError) as e:
            q.put({"type": "error", "error": f"cannot parse vendor file: {e}"})
        except (ConnectionError, TimeoutError, OSError) as e:
            q.put(
                {
                    "type": "error",
                    "error": "mapping store unavailable",
                    "detail": f"{type(e).__name__}: {e}",
                }
            )
        except Exception as e:  # noqa: BLE001 — surface in stream, don't 500
            q.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)  # sentinel: stream complete

    def event_stream():
        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {_json.dumps(item)}\n\n"
        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    ui_logger.info(
        "Vendor file ingest (stream) started",
        principal=principal,
        vendor=vendor,
        filename=filename,
    )
    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@mapping_bp.get("/mapping/working_set")
def list_working_set():
    """List vendor product refs currently in the in-memory working set.

    The frontend's file panel reads this to render "what's available to
    map". Drops the heavy ``raw`` dict to keep the response cheap.
    """
    refs = all_frames()
    return jsonify(
        {
            "count": len(refs),
            "products": [
                {
                    "vendor": r.vendor,
                    "product_id": r.product_id,
                    "name": r.name,
                    "description": r.description,
                }
                for r in refs
            ],
        }
    )


@mapping_bp.get("/mapping/agent/describe")
def describe_agent():
    """Tell the frontend which agent backend is wired (scripted | bedrock)."""
    backend = (os.getenv("SCUDO_AGENT_BACKEND") or "scripted").strip().lower()
    return jsonify(
        {
            "backend": backend,
            "model_id": (
                os.getenv("SCUDO_BEDROCK_MODEL_ID") or "eu.anthropic.claude-opus-4-8"
                if backend == "bedrock"
                else None
            ),
            "region": (
                os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
                if backend == "bedrock"
                else None
            ),
        }
    )


@mapping_bp.post("/mapping/agent/run")
def run_agent():
    """Stream an agent run over SSE.

    JSON body:
        vendor (str):       One of ``PRIORITY_VENDORS``.
        product_id (str):   Vendor-native product id (must already be in
                            the working set OR have ``name`` inline).
        name (str, optional):           Inline product name (avoids frame lookup).
        description (str, optional):    Inline product description.

    SSE event types streamed: start · tool_call · tool_result ·
        agent_message · final_result · error · done. The frontend's
        EventSource consumer renders these incrementally. Same shape
        regardless of which agent backend is wired.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required"}), 400
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400
    err = _validate_vendor(vendor)
    if err:
        return jsonify({"error": err}), 400

    try:
        ref = _frame(
            vendor,
            product_id,
            body.get("name", "") or "",
            body.get("description", "") or "",
        )
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    except FrameDataError as e:
        ui_logger.error(
            "Frame data invalid",
            op="agent_run",
            vendor=vendor,
            product_id=product_id,
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"upstream frame invalid: {e}"}), 502
    except NotImplementedError as e:
        return jsonify({"error": f"frame source unavailable: {e}"}), 503

    agent = get_agent()
    ui_logger.info(
        "Agent run started",
        principal=g.principal.user_id,
        vendor=vendor,
        product_id=product_id,
        backend=type(agent).__name__,
    )

    def event_stream():
        try:
            for event in agent.run(ref):
                yield f"data: {event.to_json()}\n\n"
        except (ConnectionError, TimeoutError, OSError) as e:
            ui_logger.error(
                "Mapping store unavailable",
                op="agent_run",
                error=f"{type(e).__name__}: {e}",
            )
            yield (
                "data: "
                + __import__("json").dumps(
                    {
                        "type": "error",
                        "error": "mapping store unavailable",
                        "detail": f"{type(e).__name__}: {e}",
                    }
                )
                + "\n\n"
            )
        except Exception as e:  # noqa: BLE001 — surface in stream, don't 500
            ui_logger.error(
                "Agent run failed", op="agent_run", error=f"{type(e).__name__}: {e}"
            )
            yield (
                "data: "
                + __import__("json").dumps(
                    {
                        "type": "error",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
                + "\n\n"
            )

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            # Disable any intermediate buffering so the browser receives
            # each event as it's emitted, not in a batch at end-of-stream.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
