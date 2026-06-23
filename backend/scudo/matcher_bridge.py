"""Runtime-A → Runtime-B bridge: the REAL cost-ladder over FalkorDB.

Two runtimes ship in this repo and they are deliberately separate packages:

  Runtime-A  — ``scudo`` (THIS package). The deployed us-east-1 Lambda
               (``scudo.lambda_handler``) plus the Strands orchestrator. Its
               ``/run`` path today assembles candidates from an in-memory
               sidecar MOCK (``scudo.sidecar.mock``) — fine for a wiring
               demo, but not the real matcher.

  Runtime-B  — ``scudo_mapping_mcp`` (a DIFFERENT package). The strict
               first-match-wins COST LADDER (scope gate → precedent reuse →
               Falkor dense+lexical fusion → Opus specialist → 3-band gate)
               in ``scudo_mapping_mcp.matching.map_vendor_product``, talking
               to a FalkorDB OSS instance via native Cypher.

This module is the thin seam between the two. The Lambda ``/run`` path calls
``run_match(...)`` to run the REAL ladder over FalkorDB instead of the mock,
and gets back a plain JSON-able dict it can fold into its response.

WHY THE IMPORTS ARE LAZY. ``scudo_mapping_mcp`` pulls in ``pydantic`` and, on
the live path, the ``falkordb`` client; the FalkorDB store also opens a TCP
connection at construction time. None of that may run at module import — the
offline smoke (``python -m scudo.tests.smoke``) must import ``scudo`` with no
creds, no deps, and no network. So EVERY heavy import and the store
construction live INSIDE ``run_match``; importing this module is free.

CONFIGURATION (no hard-coded URL). The backend is chosen entirely by the
environment that ``scudo_mapping_mcp.config.Settings`` reads:

    STORE_BACKEND=falkordb
    FALKORDB_URL=falkordb://falkordb.scudo.local:6379   (once infra is up)
    GRAPH_NAME=scudo_mapping

``run_match`` never names the URL; it asserts the backend is FalkorDB and lets
the config-driven store factory build the connection. Flip STORE_BACKEND and
nothing here changes — that is the whole point of the store seam.

FAILURE POLICY. On ANY failure — import error (deps missing), wrong backend
configured, FalkorDB unreachable, or the matcher raising — this module raises
``BridgeError`` with context. It NEVER silently falls back to mock candidates:
the integrator (the Lambda handler) decides whether to surface the error or
degrade, because only it knows the request contract. A silent mock here would
make a broken FalkorDB look like a successful real match.
"""

from __future__ import annotations

from typing import Any


class BridgeError(RuntimeError):
    """Raised when the real cost-ladder cannot be run over FalkorDB.

    Wraps the underlying cause (import failure, misconfigured backend,
    FalkorDB unreachable, matcher exception) so the integrator gets one
    catchable type with a clear message and the original exception chained
    via ``__cause__``. Never raised to mean "no match found" — a genuine
    "needs review / no candidate" outcome comes back as a normal result
    dict; ``BridgeError`` always means the ladder did not run.
    """


# Coarse outcome label derived from the matcher's MappingStatus. The
# MappingResult itself has no ``outcome`` field (status is the fine-grained
# truth); the Lambda /run contract asks for a coarse ``outcome`` so callers
# can branch without importing the enum. Anything not in this map (future
# statuses) falls through to the raw status string — fail-open on the LABEL
# only, never on the decision.
_OUTCOME_BY_STATUS: dict[str, str] = {
    "auto_mapped": "mapped",
    "approved": "mapped",
    "overridden": "mapped",
    "needs_review": "needs_review",
    "rejected": "needs_review",
    "out_of_scope": "out_of_scope",
}


def _coerce_str(value: Any) -> str:
    """Best-effort str of a possibly-Enum / possibly-None field."""
    if value is None:
        return ""
    # MappingStatus is a str-Enum; ``.value`` gives the wire token.
    return getattr(value, "value", value) if not isinstance(value, str) else value


def run_match(vendor_product: dict, *, max_candidates: int = 8) -> dict:
    """Run the REAL cost-ladder matcher over FalkorDB for one vendor product.

    Parameters
    ----------
    vendor_product:
        A dict describing the vendor product. Recognised keys (all best-effort,
        matched against the Lambda /run body shape):

          vendor        — vendor name (required; e.g. "LSEG", "lseg")
          product_id    — vendor-native identifier. Falls back to
                          ``vendor_product_ref`` / ``id`` / ``ref`` when absent.
          name / title  — display name (either key accepted)
          description   — free-text description (optional)
          raw           — original row, carried through untouched (optional)

    max_candidates:
        Top-N candidates the dense+lexical retrieval should surface (passed
        straight through to ``map_vendor_product``). Default 8 matches the
        matcher's own default.

    Returns
    -------
    dict
        A plain JSON-able adaptation of the matcher's ``MappingResult``:

          {
            "outcome":           coarse label ("mapped" | "needs_review" |
                                 "out_of_scope"),
            "status":            fine-grained MappingStatus value,
            "mapped_node_iri":   str | None,
            "mapped_node_label": str | None,
            "confidence":        float,
            "band":              "pass" | "borderline" | "fail" | "n/a",
            "candidates": [ {"iri": str, "label": str, "similarity": float},
                            ... ],
            "rationale":         str,
            "raw":               full MappingResult.model_dump() (JSON-mode),
          }

    Raises
    ------
    BridgeError
        On any failure that prevents the ladder from running (deps missing,
        wrong backend configured, FalkorDB unreachable, matcher raised).
        Never raised for a legitimate "no match" outcome.
    """
    # ── Heavy imports stay INSIDE the function (offline-smoke contract). ──
    try:
        from scudo_mapping_mcp import matching as _matching
        from scudo_mapping_mcp.config import settings as _settings
        from scudo_mapping_mcp.models import VendorProductRef as _VendorProductRef
        from scudo_mapping_mcp.store import get_store as _get_store
    except Exception as exc:  # ImportError, and anything import-time config raises
        raise BridgeError(
            "could not import scudo_mapping_mcp (the Runtime-B cost ladder). "
            "The Lambda image must vendor the scudo_mapping_mcp package and its "
            f"deps (pydantic, falkordb). Underlying error: {exc!r}"
        ) from exc

    # ── Confirm the config-driven backend is FalkorDB (no hard-coded URL). ──
    # The URL/graph come from STORE_BACKEND / FALKORDB_URL / GRAPH_NAME via
    # Settings.from_env(). We assert the *backend* only; if it isn't FalkorDB
    # the bridge is being asked to do something it cannot promise, so fail
    # loudly rather than silently matching against memory/neptune.
    backend = (_settings.store_backend or "").strip().lower()
    if backend != "falkordb":
        raise BridgeError(
            f"STORE_BACKEND={backend!r} but this bridge runs the cost ladder "
            "over FalkorDB. Set STORE_BACKEND=falkordb and FALKORDB_URL "
            "(e.g. falkordb://falkordb.scudo.local:6379) in the Lambda env."
        )

    # ── Build the FalkorDB-backed store from settings. ──
    # The matcher (map_vendor_product) calls get_store() internally too; we
    # call it here FIRST so a connection failure surfaces as a clean
    # BridgeError with the configured URL in context — and so an optional
    # health() probe can run before we pay for retrieval. get_store is
    # lru_cached, so this does not double-connect.
    try:
        store = _get_store()
    except Exception as exc:
        raise BridgeError(
            "failed to construct the FalkorDB store from settings "
            f"(FALKORDB_URL={_settings.falkordb_url!r}, "
            f"GRAPH_NAME={_settings.graph_name!r}). Is the falkordb client "
            f"installed and the instance reachable? Underlying error: {exc!r}"
        ) from exc

    # Best-effort reachability probe. health() swallows its own exceptions and
    # returns a bool, so a False here is an unreachable / unhealthy FalkorDB —
    # surface it as BridgeError before the matcher hits the same wall mid-ladder
    # with a less specific traceback. Skipped if the store has no health() (the
    # seam guarantees it, but stay defensive against future backends).
    health = getattr(store, "health", None)
    if callable(health):
        try:
            healthy = bool(health())
        except Exception as exc:
            raise BridgeError(
                "FalkorDB health check raised "
                f"(FALKORDB_URL={_settings.falkordb_url!r}): {exc!r}"
            ) from exc
        if not healthy:
            raise BridgeError(
                "FalkorDB is unreachable or unhealthy "
                f"(FALKORDB_URL={_settings.falkordb_url!r}, "
                f"GRAPH_NAME={_settings.graph_name!r}). Bring the FalkorDB "
                "OSS instance up before running the real matcher."
            )

    # ── Build a VendorProductRef from the loosely-shaped input dict. ──
    vp = vendor_product or {}
    vendor = str(vp.get("vendor") or "").strip()
    if not vendor:
        raise BridgeError(
            "vendor_product is missing a 'vendor' key; the scope gate and IRI "
            "derivation both require it."
        )
    # product_id is the vendor-native identifier; accept the Lambda /run
    # alias 'vendor_product_ref' and a couple of common fallbacks.
    product_id = str(
        vp.get("product_id")
        or vp.get("vendor_product_ref")
        or vp.get("id")
        or vp.get("ref")
        or ""
    ).strip()
    if not product_id:
        raise BridgeError(
            "vendor_product is missing a product identifier "
            "(tried 'product_id', 'vendor_product_ref', 'id', 'ref'); the "
            "matcher needs one to derive a deterministic IRI."
        )
    # name: accept either 'name' or 'title' (the Lambda body uses 'title').
    name = str(vp.get("name") or vp.get("title") or "").strip()
    description = str(vp.get("description") or "").strip()
    raw = vp.get("raw")

    try:
        ref = _VendorProductRef(
            vendor=vendor,
            product_id=product_id,
            name=name,
            description=description,
            raw=raw if isinstance(raw, dict) else {},
        )
    except Exception as exc:  # pydantic validation
        raise BridgeError(
            f"could not build VendorProductRef from {vp!r}: {exc!r}"
        ) from exc

    # ── Run the ladder. No specialist wired here (deterministic default); the
    # integrator can extend this seam later if it wants the Opus rung. ──
    try:
        result = _matching.map_vendor_product(ref, max_candidates=max_candidates)
    except Exception as exc:
        raise BridgeError(
            "the cost-ladder matcher raised while mapping "
            f"{vendor!r}/{product_id!r} over FalkorDB "
            f"(FALKORDB_URL={_settings.falkordb_url!r}): {exc!r}"
        ) from exc

    return _adapt_result(result)


def retrieve_candidates(
    vendor_product: dict, *, term: str = "", limit: int = 10
) -> list[dict]:
    """Real FalkorDB candidate retrieval for the Lambda /run bundle assembler.

    Returns ``[{iri, label, score}]`` shaped for ``scudo.schemas.CandidateNode``,
    drawn from the cost-ladder store's dense+lexical retrieval (the candidate
    nominator only — NO ladder gating). Lets the deployed orchestrator's
    specialist+verifier run over REAL candidates instead of the sidecar mock.
    Raises ``BridgeError`` on any failure; the caller decides fallback.
    """
    try:
        from scudo_mapping_mcp.config import settings as _settings
        from scudo_mapping_mcp.models import VendorProductRef as _VendorProductRef
        from scudo_mapping_mcp.store import get_store as _get_store
    except Exception as exc:
        raise BridgeError(
            f"could not import scudo_mapping_mcp for retrieval: {exc!r}"
        ) from exc

    backend = (_settings.store_backend or "").strip().lower()
    if backend != "falkordb":
        raise BridgeError(
            f"STORE_BACKEND={backend!r} but candidate retrieval needs falkordb."
        )

    try:
        store = _get_store()
    except Exception as exc:
        raise BridgeError(
            "failed to construct the FalkorDB store "
            f"(FALKORDB_URL={_settings.falkordb_url!r}): {exc!r}"
        ) from exc

    vp = vendor_product or {}
    vendor = str(vp.get("vendor") or "").strip()
    product_id = str(
        vp.get("product_id")
        or vp.get("vendor_product_ref")
        or vp.get("id")
        or vp.get("ref")
        or ""
    ).strip()
    if not vendor or not product_id:
        raise BridgeError(
            "vendor_product needs 'vendor' and a product id for retrieval."
        )
    name = str(vp.get("name") or vp.get("title") or term or "").strip()
    raw = vp.get("raw")
    try:
        ref = _VendorProductRef(
            vendor=vendor,
            product_id=product_id,
            name=name,
            description=str(vp.get("description") or "").strip(),
            raw=raw if isinstance(raw, dict) else {},
        )
        cands = store.find_similar_products(ref, max_results=limit)
    except Exception as exc:
        raise BridgeError(
            f"FalkorDB candidate retrieval failed for {vendor!r}/{product_id!r}: {exc!r}"
        ) from exc

    out: list[dict] = []
    for cand in cands or []:
        node = getattr(cand, "node", None)
        iri = getattr(node, "iri", None)
        label = getattr(node, "label", None)
        if not iri or not label:
            continue
        sim = getattr(cand, "similarity", 0.0)
        try:
            sim = max(0.0, min(1.0, float(sim)))
        except (TypeError, ValueError):
            sim = 0.0
        out.append({"iri": iri, "label": label, "score": sim})
    return out


def _adapt_result(result: Any) -> dict:
    """Flatten a scudo_mapping_mcp.MappingResult into a JSON-able dict.

    Tolerant of field absence so a future MappingResult shape change degrades
    to a thinner dict rather than raising — the ``raw`` key always carries the
    full ``model_dump()`` so nothing is lost even if a top-level convenience
    field is missed.
    """
    # Full, JSON-mode dump (enums → their .value, no datetimes left raw).
    try:
        raw_dump = result.model_dump(mode="json")
    except Exception:
        # Extremely defensive: a non-pydantic result still gets *something*.
        raw_dump = {
            k: getattr(result, k, None)
            for k in (
                "vendor",
                "product_id",
                "status",
                "confidence",
                "band",
                "mapped_node_iri",
                "mapped_node_label",
                "rationale",
            )
        }

    status = _coerce_str(getattr(result, "status", None))
    outcome = _OUTCOME_BY_STATUS.get(status, status or "unknown")

    candidates: list[dict] = []
    for cand in getattr(result, "candidates", None) or []:
        node = getattr(cand, "node", None)
        candidates.append(
            {
                "iri": getattr(node, "iri", None),
                "label": getattr(node, "label", None),
                "similarity": getattr(cand, "similarity", None),
            }
        )

    confidence = getattr(result, "confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "outcome": outcome,
        "status": status,
        "mapped_node_iri": getattr(result, "mapped_node_iri", None),
        "mapped_node_label": getattr(result, "mapped_node_label", None),
        "confidence": confidence,
        "band": _coerce_str(getattr(result, "band", None)) or "n/a",
        "candidates": candidates,
        "rationale": getattr(result, "rationale", "") or "",
        "raw": raw_dump,
    }
