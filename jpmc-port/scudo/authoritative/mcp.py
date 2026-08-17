"""Neptune MCP Server wiring + parameterised openCypher helpers.

Pattern from aws-samples/amazon-neptune-samples/neptune-prototyping-with-agents:
the Neptune MCP Server is launched as a subprocess (via `uvx`) and exposes
the Neptune Database via the MCP protocol. The agent never composes Cypher
text — the templated queries below carry only bound parameters.

The same MCPClient construction used for the catalogue MCP applies here, so
when an agent wants Neptune access via Strands it gets one tool registry per
client context. For SCUDO's @tool functions we don't need the agent loop —
the orchestrator calls these helpers directly, which is faster and keeps
specialists' tool surface bounded (per the hooks.md rediscovery cap).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Iterable, Optional

# Parameterised openCypher templates. NEVER string-concatenated with input.
# All parameters bind via `parameters={...}` on the MCP tool call.

CANDIDATE_BY_TERM = """
MATCH (n:CdaoNode)
WHERE toLower(n.label) CONTAINS toLower($term)
   OR any(syn IN n.synonyms WHERE toLower(syn) CONTAINS toLower($term))
RETURN n.iri AS iri, n.label AS label, 1.0 AS score
ORDER BY size(n.label) ASC
LIMIT $limit
"""

# Authoritative single-node confirmation: pulls the node, its definition, and
# its immediate SKOS-style edges. Used after graphrag_retrieve narrows the field
# but before the specialist commits to a target.
NODE_BY_IRI = """
MATCH (n:CdaoNode {iri: $iri})
OPTIONAL MATCH (n)-[r]->(m:CdaoNode)
RETURN n.iri AS iri,
       n.label AS label,
       n.definition AS definition,
       n.synonyms AS synonyms,
       collect(DISTINCT {predicate: type(r), object_iri: m.iri, object_label: m.label}) AS edges
"""

EXISTING_MAPPING_BY_VENDOR_PRODUCT = """
MATCH (v:VendorProduct {vendor: $vendor, vendor_product_ref: $vendor_product_ref})
      -[r:MAPPED_TO]->(t:CdaoNode)
RETURN v.iri AS source_iri,
       t.iri AS target_iri,
       r.rationale AS rationale,
       coalesce(r.confidence, 0.0) AS confidence
ORDER BY r.asserted_at DESC
LIMIT 1
"""

CONFLICTS_BY_VENDOR_PRODUCT_REF = """
MATCH (this:VendorProduct {vendor_product_ref: $vendor_product_ref})-[:MAPPED_TO]->(t1:CdaoNode)
MATCH (other:VendorProduct)-[:MAPPED_TO]->(t2:CdaoNode)
WHERE other.vendor_product_ref = $vendor_product_ref
  AND other.vendor <> this.vendor
  AND t2.iri <> t1.iri
RETURN other.vendor AS other_vendor,
       other.vendor_product_ref AS other_vendor_product_ref,
       t2.iri AS other_target_iri,
       'differing target across vendors' AS note
"""


# ────────────────────────────────────────────────────────────────────────────
# Neptune MCP Server launcher (subprocess via uvx — same launch shape as the
# AWS sample). Override the command via NEPTUNE_MCP_CMD for alternate installs.
# ────────────────────────────────────────────────────────────────────────────
def _neptune_mcp_cmd() -> list[str]:
    override = os.environ.get("NEPTUNE_MCP_CMD")
    if override:
        return override.split()
    # Default per the AWS sample's README: uvx pulls and runs the MCP server.
    # The exact server package name is configurable; the sample uses
    # `awslabs.amazon-neptune-mcp-server`.
    return ["uvx", "awslabs.amazon-neptune-mcp-server@latest"]


def _neptune_mcp_env() -> dict[str, str]:
    """Env the Neptune MCP server reads — passed through from the parent process."""
    env = dict(os.environ)
    # The MCP server picks these up; they must be set on the parent if the
    # caller didn't already export them.
    from . import client
    from ..shared import aws_region
    env.setdefault("NEPTUNE_WRITER_ENDPOINT", client.neptune_endpoint())
    env.setdefault("AWS_REGION", aws_region())
    env.setdefault("NEPTUNE_USE_IAM_AUTH", "true" if client.use_iam_auth() else "false")
    return env


def neptune_mcp_client():
    """Returns an MCPClient context manager wrapping the Neptune MCP Server.

        with neptune_mcp_client() as mcp:
            tools = mcp.list_tools_sync()
    """
    from mcp import StdioServerParameters, stdio_client
    from strands.tools.mcp import MCPClient

    cmd = _neptune_mcp_cmd()
    env = _neptune_mcp_env()
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
        )
    )


# ────────────────────────────────────────────────────────────────────────────
# Parameterised query helpers — the @tool delegates call these directly.
# They open a short-lived MCP context and invoke the Neptune MCP Server's
# query-execution tool. The exact tool name on the server is configurable.
# ────────────────────────────────────────────────────────────────────────────
_OPENCYPHER_TOOL_NAME = os.environ.get(
    "NEPTUNE_MCP_QUERY_TOOL", "execute_opencypher_query"
)


def _run_opencypher(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Send a parameterised openCypher query through the Neptune MCP Server.

    Raises if MCP isn't reachable or the server rejects the call. Returns the
    list of row-dicts (the MCP server already deserialises Neptune's JSON).
    """
    with neptune_mcp_client() as mcp:
        result = mcp.call_tool_sync(
            tool_use_id=f"scudo-neptune-{hash(query) & 0xfffffff:x}",
            name=_OPENCYPHER_TOOL_NAME,
            arguments={"query": query, "parameters": parameters},
        )
    # MCP result content is a list of blocks; flatten any JSON text blocks.
    rows: list[dict[str, Any]] = []
    for block in result.get("content", []):
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        # Accept either a list of row-dicts or a wrapper {results: [...]}.
        if isinstance(parsed, list):
            rows.extend(parsed)
        elif isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            rows.extend(parsed["results"])
    return rows


# ────────────────────────────────────────────────────────────────────────────
# Public helpers used by scudo.neptune.__init__ in real mode
# ────────────────────────────────────────────────────────────────────────────
def node_by_iri(*, iri: str) -> Optional[dict]:
    rows = _run_opencypher(NODE_BY_IRI, {"iri": iri})
    return rows[0] if rows else None


def existing_mapping(*, vendor: str, vendor_product_ref: str) -> Optional[dict]:
    rows = _run_opencypher(
        EXISTING_MAPPING_BY_VENDOR_PRODUCT,
        {"vendor": vendor, "vendor_product_ref": vendor_product_ref},
    )
    return rows[0] if rows else None


def conflicts(*, vendor_product_ref: str) -> list[dict]:
    return _run_opencypher(
        CONFLICTS_BY_VENDOR_PRODUCT_REF,
        {"vendor_product_ref": vendor_product_ref},
    )


def candidate_nodes_via_cypher(*, term: str, limit: int) -> list[dict]:
    """Lexical-fallback candidate retrieval via openCypher — used when the
    LlamaIndex PropertyGraphIndex isn't built yet (cold start) or as a sanity
    check against the embedding path.
    """
    return _run_opencypher(CANDIDATE_BY_TERM, {"term": term, "limit": limit})


__all__ = [
    "CANDIDATE_BY_TERM", "NODE_BY_IRI",
    "EXISTING_MAPPING_BY_VENDOR_PRODUCT", "CONFLICTS_BY_VENDOR_PRODUCT_REF",
    "neptune_mcp_client",
    "node_by_iri", "existing_mapping", "conflicts", "candidate_nodes_via_cypher",
]
