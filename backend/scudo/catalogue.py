"""Catalogue MCP client wrapper.

By default points at the in-tree vendor_catalogue_mcp.server via stdio (the
local-dev transport from the contract). On JPMC AWS, swap the
`catalogue_mcp_client()` factory to use streamable HTTP — the call-site code
above this line does not change.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Optional

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
        lambda: stdio_client(
            StdioServerParameters(command=cmd[0], args=cmd[1:])
        )
    )


def get_product_via_mcp(mcp: MCPClient, vendor: str, vendor_product_ref: str) -> dict:
    """Convenience: call catalogue_get_product synchronously and return the
    NormalisedProduct dict. Raises if the tool errors."""
    result = mcp.call_tool_sync(
        tool_use_id=f"scudo-{vendor}-{vendor_product_ref}",
        name="catalogue_get_product",
        arguments={"params": {"vendor": vendor, "vendor_product_ref": vendor_product_ref}},
    )
    # MCP ToolResult contains a list of content blocks; the NormalisedProduct
    # payload comes back as JSON text.
    for block in result.get("content", []):
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            return json.loads(text)
    raise RuntimeError("catalogue_get_product returned no parseable content")


__all__ = ["catalogue_mcp_client", "get_product_via_mcp"]
