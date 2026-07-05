"""Catalogue MCP client wrapper.

By default points at the in-tree vendor_catalogue_mcp.server via stdio (the
local-dev transport from the contract). On JPMC AWS, swap the
`catalogue_mcp_client()` factory to use streamable HTTP — the call-site code
above this line does not change.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping, Optional

from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient

# Launch the vendor_catalogue_mcp server module via stdio. Using the running
# Python interpreter so the in-tree code is the server. Override via
# SCUDO_CATALOGUE_MCP_CMD if you point at a different server.
_DEFAULT_SERVER_CMD = [sys.executable, "-m", "vendor_catalogue_mcp.server"]


def catalogue_mcp_client(server_cmd: Optional[list[str]] = None) -> MCPClient:
    """Construct the catalogue MCP client. Open it in a context manager:

    with catalogue_mcp_client() as mcp:
        tools = mcp.list_tools_sync()
        ...
    """
    cmd = server_cmd or _DEFAULT_SERVER_CMD
    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command=cmd[0], args=cmd[1:]))
    )


def get_product_via_mcp(mcp: MCPClient, vendor: str, vendor_product_ref: str) -> dict:
    """Convenience: call catalogue_get_product synchronously and return the
    NormalisedProduct dict. Raises if the tool errors."""
    result = mcp.call_tool_sync(
        tool_use_id=f"scudo-{vendor}-{vendor_product_ref}",
        name="catalogue_get_product",
        arguments={
            "params": {"vendor": vendor, "vendor_product_ref": vendor_product_ref}
        },
    )
    # MCP ToolResult contains a list of content blocks; the NormalisedProduct
    # payload comes back as JSON text.
    for block in result.get("content", []):
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            return json.loads(text)
    raise RuntimeError("catalogue_get_product returned no parseable content")


# ── Approved-catalogue reads/writes on the single Aurora cluster ──────────────
# The SCUDO API endpoints consume THESE (never aurora_store or Neptune directly),
# so the storage/data-model choice stays behind scudo.catalogue.
def _row_to_record(row: list[dict]) -> dict:
    """Map an RDS Data API row ``[iri, payload_json]`` to ``{iri, payload}``."""
    iri = (row[0] or {}).get("stringValue", "") if row else ""
    raw = (row[1] or {}).get("stringValue") if len(row) > 1 else None
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        payload = {}
    return {"iri": iri, "payload": payload}


def list_approved(limit: int = 100) -> list[dict]:
    """List approved catalogue records (``{iri, payload}``) from
    ``scudo.catalogue_products``, ordered by IRI."""
    from . import aurora_store

    result = aurora_store._execute(
        "select iri, payload from scudo.catalogue_products order by iri limit :limit",
        [{"name": "limit", "value": {"longValue": int(limit)}}],
    )
    return [_row_to_record(row) for row in result.get("records", [])]


def get_record(iri: str) -> Optional[dict]:
    """One approved catalogue record by IRI (canonical payload), or None."""
    from . import aurora_store

    result = aurora_store._execute(
        "select iri, payload from scudo.catalogue_products where iri = :iri",
        [{"name": "iri", "value": {"stringValue": iri}}],
    )
    rows = result.get("records", [])
    return _row_to_record(rows[0]) if rows else None


def upsert_record(iri: str, payload: Mapping[str, Any]) -> None:
    """Insert/replace an approved catalogue record — the HITL-approve write path
    that populates what ``list_approved`` / ``get_record`` read back."""
    from . import aurora_store

    aurora_store._execute(
        "insert into scudo.catalogue_products (iri, payload) "
        "values (:iri, :payload::jsonb) "
        "on conflict (iri) do update set payload = excluded.payload",
        [
            {"name": "iri", "value": {"stringValue": iri}},
            {
                "name": "payload",
                "value": {"stringValue": json.dumps(dict(payload), default=str)},
                "typeHint": "JSON",
            },
        ],
    )


__all__ = [
    "catalogue_mcp_client",
    "get_product_via_mcp",
    "list_approved",
    "get_record",
    "upsert_record",
]
