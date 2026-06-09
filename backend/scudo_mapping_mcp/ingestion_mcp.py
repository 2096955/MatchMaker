"""
Ingestion MCP — tier 1 of the trust gradient.

ROLE
----
Reads UNTRUSTED vendor data. Normalises any vendor's catalogue into the
canonical ``VendorProductRef`` shape that Match & Verify can score against.
Vendor is a tool ARGUMENT here — adding Bloomberg or another vendor means a
new adapter, not a new server.

WRITE BOUNDARY
--------------
This module deliberately does NOT import the package's write surface
(``feedback``, ``bundle``, ``store.upsert_*``, ``store.bump_*``). The trust
test in ``tests/smoke.py`` (``TRUST_ingestion_mcp_imports_no_writers``)
asserts this statically via the AST. If a future change needs to mutate
canonical state from the ingestion path, that's a design discussion, not a
silent import.

SCOPE GATE — LAYER 1
--------------------
``frames.check_scope`` is called at this boundary so an out-of-scope vendor
never even reaches Match & Verify. Defense in depth: Match & Verify checks
it again (layer 2), Persistence checks it again (layer 3).

TOOLS
-----
  - ``ingest.list_frames``  — list what's currently in the working set,
                              optionally filtered by vendor.
  - ``ingest.get_frame``    — return one ``VendorProductRef`` by
                              (vendor, product_id). The single seam the
                              other MCPs (or the agent) call when they
                              need vendor data.

Both tools are ``readOnlyHint=true`` and ``destructiveHint=false`` —
honest annotations that match the actual behaviour. Run locally with:
    python -m scudo_mapping_mcp.ingestion_mcp
"""
from __future__ import annotations

import json
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .config import PRIORITY_VENDORS
from .frames import _read_vendor_frame, all_frames, check_scope
from .models import VendorProductRef

_RO = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

mcp = FastMCP("scudo_ingestion_mcp")


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ListInput(_Base):
    vendor: Optional[str] = Field(
        default=None,
        description=(
            f"Optional vendor filter. One of: {', '.join(PRIORITY_VENDORS)}."
        ),
    )


class GetInput(_Base):
    vendor: str = Field(
        ..., min_length=1,
        description=f"One of: {', '.join(PRIORITY_VENDORS)}",
    )
    product_id: str = Field(..., min_length=1)


@mcp.tool(
    name="ingest.list_frames",
    annotations={"title": "List vendor frames in the working set", **_RO},
)
async def list_frames(params: ListInput) -> str:
    """Return the (vendor, product_id, name) triples currently available.

    Bounded and read-only. The agent uses this to discover what's been
    ingested before asking Match & Verify to score anything.
    """
    refs = all_frames()
    if params.vendor:
        scope = check_scope(VendorProductRef(
            vendor=params.vendor, product_id="_probe",
        ))
        if not scope.allowed:
            return json.dumps({
                "vendor": params.vendor,
                "in_scope": False,
                "reason": scope.reason,
                "count": 0,
                "products": [],
            })
        refs = [r for r in refs if r.vendor == params.vendor]
    return json.dumps({
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
    })


@mcp.tool(
    name="ingest.get_frame",
    annotations={"title": "Get one vendor frame", **_RO},
)
async def get_frame(params: GetInput) -> str:
    """Return one ``VendorProductRef`` (vendor, product_id, name,
    description, raw) for the requested product.

    Scope gate fires here (layer 1). Out-of-scope vendor → refusal with
    reason; the Match & Verify and Persistence MCPs never see the request.
    """
    probe = VendorProductRef(vendor=params.vendor, product_id=params.product_id)
    scope = check_scope(probe)
    if not scope.allowed:
        return json.dumps({
            "error": "out_of_scope",
            "reason": scope.reason,
        })
    ref = _read_vendor_frame(params.vendor, params.product_id)
    if ref is None:
        return json.dumps({
            "error": "not_found",
            "vendor": params.vendor,
            "product_id": params.product_id,
        })
    # Drop the heavy raw blob from the wire by default — the matcher uses
    # name/description; raw is for downstream analytics.
    return json.dumps({
        "vendor": ref.vendor,
        "product_id": ref.product_id,
        "name": ref.name,
        "description": ref.description,
        "raw": ref.raw,
        "source_content_hash": ref.source_content_hash,
        "source_file_audit_id": ref.source_file_audit_id,
    })


if __name__ == "__main__":
    # Distinct default port so Ingestion / Match & Verify / Persistence can
    # co-run locally without a clash.
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8001"))
    mcp.run(transport="streamable_http")
