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
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import verdict as verdict_seal
from .config import PRIORITY_VENDORS
from .frames import _read_vendor_frame
from .hydrate import HydrationError, hydrate
from .ingest import seed_taxonomy
from .matching import map_vendor_product
from .models import VendorProductRef
from .store import get_store

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
            f"[scudo_match_verify_mcp] taxonomy seed skipped: "
            f"{type(e).__name__}: {e}"
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
        "", description="Inline name — avoids a frame lookup."
    )
    description: str = Field("")


def _frame(vendor: str, product_id: str, name: str, description: str) -> VendorProductRef:
    if name or description:
        return VendorProductRef(
            vendor=vendor, product_id=product_id,
            name=name, description=description,
        )
    ref = _read_vendor_frame(vendor, product_id)
    if ref is None:
        return VendorProductRef(
            vendor=vendor, product_id=product_id, name=product_id,
        )
    return ref


@mcp.tool(
    name="matchverify.find_candidates",
    annotations={"title": "Find candidate CDAO nodes", **_RO},
)
async def find_candidates(params: SimilarInput) -> str:
    """Top-N candidate CDAO nodes for a vendor product, clamped to 25.

    Read-only against Falkor (match-and-check tier). No verdict is signed
    here — the agent uses this to explore before asking
    ``verify_mapping`` for the authoritative answer.
    """
    ref = _frame(params.vendor, params.product_id, params.name, params.description)
    cands = get_store().find_similar_products(
        ref,
        max_results=params.max_results,
        min_similarity=params.min_similarity,
    )
    return json.dumps({
        "count": len(cands),
        "candidates": [c.model_dump(mode="json") for c in cands],
    })


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
        "seal":    {"payload_b64": str, "hmac_b64": str}
      }
    """
    ref = _frame(params.vendor, params.product_id, params.name, params.description)
    result = map_vendor_product(ref)
    seal = verdict_seal.sign(
        vendor=result.vendor,
        product_id=result.product_id,
        mapped_node_iri=result.mapped_node_iri,
        status=result.status.value,
        confidence=result.confidence,
        band=result.band,
    )
    return json.dumps({
        "verdict": result.model_dump(mode="json"),
        "seal": seal,
    })


if __name__ == "__main__":
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8002"))
    mcp.run(transport="streamable_http")
