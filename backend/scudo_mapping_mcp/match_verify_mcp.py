"""
Match & Verify MCP — tier 2 of the trust gradient.

ROLE
----
Runs the deterministic matcher: scope gate, precedent reuse, bounded
candidate retrieval (Falkor's match-and-check tier), the M5 validations
layer, and the 0.80 floor. Returns a typed ``MappingResult`` plus an
HMAC seal computed over (input_hash, mapped_node_iri, status,
confidence, ts) — the seal is what proves to Persistence that the
verdict came from the deterministic matcher and not from the agent.

WRITE BOUNDARY
--------------
This server is READ-ONLY. It deliberately does NOT import the package's
write surface (``feedback``, ``bundle.import_bundle``, ``store.upsert_*``,
``store.bump_*``). Smoke gate ``TRUST_match_verify_mcp_imports_no_writers``
asserts this statically.

SCOPE GATE — LAYER 2
--------------------
``frames.check_scope`` is called inside ``matching.map_vendor_product``
which runs before the verdict is signed. Defense in depth with Ingestion
(layer 1) and Persistence (layer 3).

FRAME RESOLUTION — WHAT GETS SCORED
-----------------------------------
Every tool here scores a ``VendorProductRef``. Where that ref comes from is
a trust question, because the HMAC seal binds only
``(input_hash, mapped_node_iri, status, confidence, band, ts_ms)`` — NOT the
text that was scored and NOT the frame's provenance. A verdict produced from
caller-supplied text is therefore byte-indistinguishable, downstream, from
one produced from the real ingested frame. Two rules follow:

  1. INLINE TEXT IS GATED, DEFAULT OFF. ``SCUDO_MV_ALLOW_INLINE_FRAME``
     (truthy: 1/true/yes/on) lets a caller pass ``name``/``description``
     inline and skip the frame lookup. Unset — the production default — the
     inline fields are IGNORED and the real frame is always read. Fail-closed:
     only an explicit opt-in opens the bypass, and the response marks the
     resulting frame ``source="inline"`` so it is visible in the trace.

  2. A MISSING FRAME IS A REFUSAL, NOT A FABRICATION. The previous code
     returned ``VendorProductRef(name=product_id)`` when the lookup missed,
     and signed a verdict over that invented name. That is not a rare path:
     the deployed Match & Verify task runs ``FRAME_SOURCE=mock``
     (infra/scudo-dev-deploy.yaml) while the mock working set is a
     process-local dict (frames.py), so this container never sees frames the
     Ingestion container wrote. Now the tools return a typed refusal
     (``reason="frame_not_found"``) and — critically — NO SEAL. Persistence
     is handed nothing it could trust.

WHY PROVENANCE IS REPORTED BUT NOT SEALED
-----------------------------------------
``verify_mapping`` returns an unsealed ``frame`` block carrying
``source`` / ``content_hash`` / ``file_audit_id`` and an explicit
``sealed: false``. Binding those INTO the HMAC would close the remaining
gap (a compromised M&V could still report one provenance and score
another), but it requires a ``verdict.py`` payload bump to v=3 plus
matching read-side changes in ``persistence_mcp`` — a cross-module change
outside this module's blast radius. See the RECOMMENDATION note on
``_frame_provenance`` for the concrete sketch.

WHERE THE VERIFIER LIVES
------------------------
The verifier IS the deterministic gate — validations + floor + scope. It
runs HERE, not in Persistence. Persistence is a thin gate that trusts a
signed verdict, refuses if the seal is bad / stale / wrong-identity.
That's the C4 commitment: M&V emits the verdict, Persistence trusts it
on the wire.

TOOLS
-----
  - ``matchverify.find_candidates``       — top-N CDAO candidates (≤25)
  - ``matchverify.get_node``              — one CDAO node (1 hop)
  - ``matchverify.get_neighbourhood``     — bounded subgraph (depth ≤3,
                                            nodes ≤100)
  - ``matchverify.verify_mapping``        — run the full matcher and emit
                                            a SIGNED verdict (this is the
                                            tool Persistence trusts)

All four are ``readOnlyHint=true``. Run locally with:
    python -m scudo_mapping_mcp.match_verify_mcp
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import frames as _frames_mod
from . import verdict as verdict_seal
from .frames import _read_vendor_frame
from .hydrate import HydrationError, hydrate
from .ingest import seed_taxonomy
from .matching import map_vendor_product
from .models import VendorProductRef
from .specialist import specialist_from_env
from .store import get_store
from .taxonomy_graph import analyse_taxonomy

_RO = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


@asynccontextmanager
async def _lifespan(server: "FastMCP"):
    try:
        n = seed_taxonomy()
        print(f"[scudo_match_verify_mcp] seeded {n} CDAO taxonomy nodes")
    except Exception as e:  # noqa: BLE001
        print(
            f"[scudo_match_verify_mcp] taxonomy seed skipped: {type(e).__name__}: {e}"
        )
    else:
        # Replay the M6 canonical bundle so Falkor's working graph is hydrated
        # before this MCP serves match-and-check requests. Strategy resilience
        # pin: stale or empty Falkor serves confident-but-wrong matches.
        try:
            result = hydrate(strict=False)
            if result.skipped_no_bundle:
                print(
                    "[scudo_match_verify_mcp] hydration skipped "
                    "(cold start: no canonical bundle yet)"
                )
            else:
                print(
                    f"[scudo_match_verify_mcp] hydration applied "
                    f"{result.applied}/{result.total} patterns "
                    f"(bundle version={result.bundle_version})"
                )
        except HydrationError as e:
            print(
                f"[scudo_match_verify_mcp] hydration failed (proceeding empty): "
                f"{type(e).__name__}: {e}"
            )
    yield {}


mcp = FastMCP("scudo_match_verify_mcp", lifespan=_lifespan)


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class NodeInput(_Base):
    node_iri: str = Field(..., min_length=1)


class NeighbourhoodInput(_Base):
    node_iri: str = Field(..., min_length=1)
    max_depth: int = Field(2, ge=1, le=3)
    max_nodes: int = Field(50, ge=1, le=100)


class TaxonomyAnalysisInput(_Base):
    candidate_iris: list[str] = Field(..., max_length=25)
    anchor_iris: list[str] = Field(default_factory=list, max_length=25)
    max_depth: int = Field(8, ge=1, le=100)
    max_nodes: int = Field(100, ge=1, le=100)


class SimilarInput(_Base):
    vendor: str = Field(...)
    product_id: str = Field(..., min_length=1)
    name: str = Field("")
    description: str = Field("")
    max_results: int = Field(10, ge=1, le=25)
    min_similarity: float = Field(0.0, ge=0.0, le=1.0)


class VerifyInput(_Base):
    vendor: str = Field(...)
    product_id: str = Field(..., min_length=1)
    name: str = Field(
        "",
        description=(
            "Inline name — IGNORED unless SCUDO_MV_ALLOW_INLINE_FRAME is on. "
            "The ingested frame is authoritative by default."
        ),
    )
    description: str = Field(
        "",
        description=(
            "Inline description — IGNORED unless SCUDO_MV_ALLOW_INLINE_FRAME is on."
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Frame resolution — the gate. See module docstring "FRAME RESOLUTION".
# ──────────────────────────────────────────────────────────────────────
_TRUTHY_ENV = frozenset({"1", "true", "yes", "on"})

_INLINE_FRAME_FLAG = "SCUDO_MV_ALLOW_INLINE_FRAME"


def env_allow_inline_frame() -> bool:
    """Live ``SCUDO_MV_ALLOW_INLINE_FRAME`` — re-read per call.

    Call-time read (not a ``Settings`` snapshot field) matching the other
    measured-rollout levers in ``config.py`` — ``env_margin_gate_enabled``,
    ``env_input_completeness_validation_enabled`` — so tests and operators
    can flip it without re-importing the package.

    Default FALSE. Only the explicit truthy tokens open the bypass; unset,
    empty, "0", "false" and anything unrecognised all keep it shut.
    """
    return os.getenv(_INLINE_FRAME_FLAG, "").strip().lower() in _TRUTHY_ENV


def _configured_frame_source() -> str:
    """The FRAME_SOURCE ``_read_vendor_frame`` will actually branch on.

    Read through the ``frames`` MODULE attribute rather than importing
    ``settings`` here, because ``frames._read_vendor_frame`` resolves
    ``settings`` from its own module globals — and the existing smoke
    helper ``_swap_settings`` rebinds exactly that. Importing the symbol
    directly would let the reported provenance disagree with the source
    the read actually used.
    """
    return _frames_mod.settings.frame_source


class FrameRefusal(Exception):
    """The frame could not be resolved — refuse rather than invent one.

    Carries a typed ``reason`` (and optional ``detail``) so the tool wrapper
    can render the same refusal envelope ``persistence_mcp._refusal`` emits.
    Raised, not returned, so no caller can accidentally proceed to sign a
    verdict over a ref that does not exist.
    """

    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _refusal(reason: str, **detail) -> str:
    """Typed refusal envelope — same ``refusal`` block Persistence emits.

    ``refused: true`` replaces Persistence's ``committed: false`` because
    this server never commits anything; the nested ``refusal`` object is
    identical in shape so the agent reasons over one vocabulary.
    """
    return json.dumps(
        {
            "refused": True,
            "refusal": {"reason": reason, **detail},
        }
    )


def _resolve_frame(
    vendor: str, product_id: str, name: str, description: str
) -> tuple[VendorProductRef, str]:
    """Resolve the ref this server will score, plus WHERE it came from.

    Returns ``(ref, source)`` where ``source`` is ``"inline"`` or the
    configured ``FRAME_SOURCE``. The source is returned rather than
    re-derived by the caller so the flag is read EXACTLY ONCE per request —
    a second read could disagree with the first if the env changed mid-call,
    and the provenance block would then lie about what was scored.

    Raises:
        FrameRefusal: no frame exists for (vendor, product_id) and the
            inline bypass is not open. The caller MUST NOT fall back to a
            synthesised ref — that is the defect this replaces.
    """
    has_inline = bool(name or description)
    if has_inline and env_allow_inline_frame():
        # Explicit opt-in only. source_content_hash / source_file_audit_id
        # stay None — an inline frame has no upstream provenance and must
        # not be able to claim any.
        return (
            VendorProductRef(
                vendor=vendor,
                product_id=product_id,
                name=name,
                description=description,
            ),
            "inline",
        )
    ref = _read_vendor_frame(vendor, product_id)
    if ref is None:
        raise FrameRefusal(
            "frame_not_found",
            vendor=vendor,
            product_id=product_id,
            frame_source=_configured_frame_source(),
            inline_ignored=has_inline,
            detail=(
                f"No ingested frame for ({vendor!r}, {product_id!r}) via "
                f"FRAME_SOURCE={_configured_frame_source()!r}. Match & Verify "
                f"refuses to score a fabricated frame"
                + (
                    f"; inline name/description were supplied but "
                    f"{_INLINE_FRAME_FLAG} is not enabled."
                    if has_inline
                    else "."
                )
            ),
        )
    return ref, _configured_frame_source()


def _frame(
    vendor: str, product_id: str, name: str, description: str
) -> VendorProductRef:
    """``_resolve_frame`` without the source label. Same fail-closed rules."""
    return _resolve_frame(vendor, product_id, name, description)[0]


def _frame_provenance(ref: VendorProductRef, source: str) -> dict:
    """UNSEALED provenance block describing which frame was scored.

    ``sealed: false`` is stated explicitly so no reader mistakes this for
    part of the HMAC-protected payload.

    RECOMMENDATION (deliberately NOT implemented here — cross-module):
    bind provenance into the seal itself. Sketch:
      1. ``verdict.sign`` gains ``frame_source: str`` +
         ``frame_content_hash: str`` and emits ``"v": 3``.
      2. ``verdict.verify`` accepts ``v in (1, 2, 3)`` and
         ``setdefault``s both new keys ("unknown"/"") for v=1/v=2 seals, the
         same forward-compatible trick already used for ``band`` — existing
         in-flight v=2 seals keep verifying unchanged.
      3. ``persistence_mcp.commit_mapping`` reads the sealed provenance and
         can refuse ``frame_source == "inline"`` in prod.
    Left out of this change because it touches ``verdict.py`` and
    ``persistence_mcp.py``, which this module does not own, and because a
    partial rollout (M&V signing v=3 before Persistence accepts it) breaks
    every commit. Do it as one atomic change across the three files.
    """
    return {
        "source": source,
        "content_hash": ref.source_content_hash,
        "file_audit_id": ref.source_file_audit_id,
        "sealed": False,
    }


@mcp.tool(
    name="matchverify.find_candidates",
    annotations={"title": "Find candidate CDAO nodes", **_RO},
)
async def find_candidates(params: SimilarInput) -> str:
    """Top-N candidate CDAO nodes for a vendor product, clamped to 25.

    Read-only against Falkor (match-and-check tier). No verdict is signed
    here — the agent uses this to explore before asking
    ``verify_mapping`` for the authoritative answer.

    Refuses (``{"refused": true, "refusal": {...}}``) when no frame exists
    for (vendor, product_id) and the inline bypass is not enabled — the same
    fail-closed rule ``verify_mapping`` applies, so the agent can't explore
    against a fabricated frame and then be surprised by the verdict.
    """
    try:
        ref, _source = _resolve_frame(
            params.vendor, params.product_id, params.name, params.description
        )
    except FrameRefusal as e:
        return _refusal(e.reason, **e.detail)
    cands = get_store().find_similar_products(
        ref,
        max_results=params.max_results,
        min_similarity=params.min_similarity,
    )
    return json.dumps(
        {
            "count": len(cands),
            "candidates": [c.model_dump(mode="json") for c in cands],
        }
    )


@mcp.tool(
    name="matchverify.get_node",
    annotations={"title": "Get one CDAO node", **_RO},
)
async def get_node(params: NodeInput) -> str:
    """Return a CDAO taxonomy node with its immediate parent and children."""
    node = get_store().get_taxonomy_node(params.node_iri)
    if node is None:
        return json.dumps({"error": f"No taxonomy node {params.node_iri!r}"})
    return node.model_dump_json()


@mcp.tool(
    name="matchverify.get_neighbourhood",
    annotations={"title": "Get bounded subgraph around a node", **_RO},
)
async def get_neighbourhood(params: NeighbourhoodInput) -> str:
    """Bounded subgraph around a node (depth ≤3, nodes ≤100)."""
    sg = get_store().get_ontology_neighbourhood(
        params.node_iri,
        max_depth=params.max_depth,
        max_nodes=params.max_nodes,
    )
    return sg.model_dump_json()


@mcp.tool(
    name="matchverify.analyse_taxonomy_candidates",
    annotations={"title": "Analyse bounded taxonomy graph evidence", **_RO},
)
async def analyse_taxonomy_candidates(params: TaxonomyAnalysisInput) -> str:
    """Return deterministic read-only graph evidence for candidate IRIs."""
    evidence = analyse_taxonomy(
        get_store().list_taxonomy_nodes(),
        candidate_iris=params.candidate_iris,
        anchor_iris=params.anchor_iris,
        max_nodes=params.max_nodes,
        max_depth=params.max_depth,
    )
    return evidence.model_dump_json()


@mcp.tool(
    name="matchverify.verify_mapping",
    annotations={"title": "Run the deterministic matcher and sign the verdict", **_RO},
)
async def verify_mapping(params: VerifyInput) -> str:
    """Run the full matcher and return a SIGNED verdict.

    This is the tool Persistence trusts: the seal proves the verdict came
    from the deterministic matcher (scope gate + validations + floor),
    not from the agent. Persistence verifies the HMAC and the identity
    binding before any write.

    Returns JSON:
      {
        "verdict": <full MappingResult>,
        "seal":    {"payload_b64": str, "hmac_b64": str},
        "frame":   {"source", "content_hash", "file_audit_id",
                    "sealed": false}   # provenance, NOT inside the HMAC
      }

    Or, when the frame cannot be resolved:
      {"refused": true, "refusal": {"reason": "frame_not_found", ...}}
    — deliberately WITHOUT a seal. Refusing to sign is the whole point: a
    fabricated frame must not produce a verdict Persistence would honour.
    """
    try:
        ref, source = _resolve_frame(
            params.vendor, params.product_id, params.name, params.description
        )
    except FrameRefusal as e:
        return _refusal(e.reason, **e.detail)
    result = map_vendor_product(ref, specialist=specialist_from_env())
    seal = verdict_seal.sign(
        vendor=result.vendor,
        product_id=result.product_id,
        mapped_node_iri=result.mapped_node_iri,
        status=result.status.value,
        confidence=result.confidence,
        band=result.band,
    )
    return json.dumps(
        {
            "verdict": result.model_dump(mode="json"),
            "seal": seal,
            "frame": _frame_provenance(ref, source),
        }
    )


if __name__ == "__main__":
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8002"))
    mcp.run(transport="streamable-http")
