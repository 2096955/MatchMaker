"""
scudo_mapping_mcp — the MCP server, standalone.

One logical server. Vendor is a tool ARGUMENT, never a server boundary. Every
tool is a bounded, parameterised retrieval or the mapping entry point — there is
deliberately NO run_cypher / run_sparql tool, because raw query passthrough is
exactly the surface the agent must not get. Tools are read-only and write nothing
to the canonical store.

It is self-sufficient: the mapping tools accept the product's name/description
inline, so you can exercise the server with nothing but a FalkorDB container —
no ingestion pipeline, no S3. When only an id is given, _read_vendor_frame is
the single point that resolves it (mock working set locally, S3 in prod).

Run locally:   python -m app.mcp_server        (streamable HTTP on :8000)
Inspect:       npx @modelcontextprotocol/inspector
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .config import PRIORITY_VENDORS
from .frames import _read_vendor_frame
from .hydrate import HydrationError, hydrate
from .ingest import seed_taxonomy
from .matching import map_vendor_product
from .models import VendorProductRef
from .store import get_store

_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


@asynccontextmanager
async def _lifespan(server: "FastMCP"):
    # Seed the CDAO taxonomy so the store has something to map against on boot.
    # Idempotent (MERGE), best-effort: the server still starts if the store is
    # not yet reachable; a get_taxonomy_node call will simply return empty.
    try:
        n = seed_taxonomy()
        print(f"[scudo_mapping_mcp] seeded {n} CDAO taxonomy nodes")
    except Exception as e:  # noqa: BLE001
        print(f"[scudo_mapping_mcp] taxonomy seed skipped: {type(e).__name__}: {e}")
    else:
        # Replay the M6 canonical bundle so FalkorDB is hydrated before serving.
        try:
            result = hydrate(strict=False)
            if result.skipped_no_bundle:
                print("[scudo_mapping_mcp] hydration skipped (cold start: no canonical bundle yet)")
            else:
                print(
                    f"[scudo_mapping_mcp] hydration applied "
                    f"{result.applied}/{result.total} patterns "
                    f"(bundle version={result.bundle_version})"
                )
        except HydrationError as e:
            print(f"[scudo_mapping_mcp] hydration failed (proceeding empty): {type(e).__name__}: {e}")
    yield {}


mcp = FastMCP("scudo_mapping_mcp", lifespan=_lifespan)


# --- input models --------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class NodeInput(_Base):
    node_iri: str = Field(..., description="Taxonomy node IRI, e.g. 'cdao:equities'", min_length=1)


class NeighbourhoodInput(_Base):
    node_iri: str = Field(..., min_length=1, description="Root taxonomy node IRI")
    max_depth: int = Field(2, ge=1, le=3, description="Hops from the root (clamped to 3)")
    max_nodes: int = Field(50, ge=1, le=100, description="Node cap (clamped to 100)")


class SimilarInput(_Base):
    vendor: str = Field(..., description=f"One of: {', '.join(PRIORITY_VENDORS)}")
    product_id: str = Field(..., min_length=1, description="Vendor-native product id")
    name: str = Field("", description="Product name (pass inline to avoid an S3/frame lookup)")
    description: str = Field("", description="Product description (optional, improves matching)")
    max_results: int = Field(10, ge=1, le=25, description="Top-N candidates (clamped to 25)")
    min_similarity: float = Field(0.0, ge=0.0, le=1.0, description="Minimum similarity")


class MapInput(_Base):
    vendor: str = Field(..., description=f"One of: {', '.join(PRIORITY_VENDORS)}")
    product_id: str = Field(..., min_length=1, description="Vendor-native product id")
    name: str = Field("", description="Product name (pass inline to avoid an S3/frame lookup)")
    description: str = Field("", description="Product description (optional, improves matching)")


# --- helpers -------------------------------------------------------------
def _frame(vendor: str, product_id: str, name: str = "", description: str = "") -> VendorProductRef:
    # Inline attributes win — makes the server testable with no ingestion wired.
    if name or description:
        return VendorProductRef(vendor=vendor, product_id=product_id, name=name, description=description)
    ref = _read_vendor_frame(vendor, product_id)
    if ref is None:
        return VendorProductRef(vendor=vendor, product_id=product_id, name=product_id)
    return ref


def _frame_error_envelope(e: BaseException) -> str:
    """Same JSON shape the HTTP route returns when the frame source is unwired.

    Keeps transport behaviour identical: ``FRAME_SOURCE=s3`` against an
    unwired ``_read_vendor_frame`` surfaces as a structured error on BOTH
    transports rather than propagating a raw ``NotImplementedError`` through
    FastMCP. The 503 -> JSON-envelope translation lives only here and in
    ``routes/mapping.py`` (the two transport edges).
    """
    return json.dumps({"error": f"frame source unavailable: {e}"})


# --- tools ---------------------------------------------------------------
@mcp.tool(name="get_taxonomy_node", annotations={"title": "Get taxonomy node", **_RO})
async def get_taxonomy_node(params: NodeInput) -> str:
    """Return a CDAO taxonomy node with its immediate parent and children (one hop).

    Returns JSON: {iri, label, parent_iri, children_iris[]} or {error}.
    """
    node = get_store().get_taxonomy_node(params.node_iri)
    if node is None:
        return json.dumps({"error": f"No taxonomy node '{params.node_iri}'."})
    return node.model_dump_json()


@mcp.tool(name="find_similar_products", annotations={"title": "Find candidate CDAO nodes", **_RO})
async def find_similar_products(params: SimilarInput) -> str:
    """Return the top-N candidate CDAO nodes for a vendor product, by similarity.

    Bounded to 25 results. Pass name/description inline, or rely on the frame
    lookup by (vendor, product_id). Returns JSON:
    {count, candidates:[{node{iri,label,parent_iri,children_iris}, similarity}]}.
    """
    try:
        ref = _frame(params.vendor, params.product_id, params.name, params.description)
    except NotImplementedError as e:
        return _frame_error_envelope(e)
    cands = get_store().find_similar_products(
        ref, max_results=params.max_results, min_similarity=params.min_similarity
    )
    return json.dumps({"count": len(cands), "candidates": [c.model_dump() for c in cands]})


@mcp.tool(name="get_ontology_neighbourhood", annotations={"title": "Get bounded neighbourhood", **_RO})
async def get_ontology_neighbourhood(params: NeighbourhoodInput) -> str:
    """Return a bounded subgraph around a taxonomy node (depth and node count clamped).

    Returns JSON: {root_iri, nodes:[{iri,label}], edges:[[parent,child]]}.
    """
    sg = get_store().get_ontology_neighbourhood(
        params.node_iri, max_depth=params.max_depth, max_nodes=params.max_nodes
    )
    return sg.model_dump_json()


@mcp.tool(name="map_vendor_product", annotations={"title": "Map a vendor product to CDAO", **_RO})
async def map_vendor_product_tool(params: MapInput) -> str:
    """Map one vendor product to a CDAO node, applying the scope gate and confidence floor.

    Scope-blocked products return status 'out_of_scope'. Below the 0.80 floor
    they return 'needs_review' for a human. Returns the full MappingResult as JSON:
    {vendor_product_iri, vendor, product_id, product_name, mapped_node_iri,
     mapped_node_label, confidence, status, candidates[], rationale}.
    """
    try:
        ref = _frame(params.vendor, params.product_id, params.name, params.description)
    except NotImplementedError as e:
        return _frame_error_envelope(e)
    return map_vendor_product(ref).model_dump_json()


if __name__ == "__main__":
    # Bind for containers / AWS; override with MCP_HOST / MCP_PORT.
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8000"))
    mcp.run(transport="streamable-http")
