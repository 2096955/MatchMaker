"""Authoritative RDF graph — the system of record (neptune-io skill).

This subpackage holds everything that touches the AUTHORITATIVE Neptune
cluster: parameterised openCypher templates, the Neptune MCP Server launcher,
and the in-memory mock used for local dev. Per the neptune-io SKILL.md, this
is the *only* graph that backs facts and the *only* graph that gets written to.

Selection rule: if `NEPTUNE_ENDPOINT` is unset → mock; else → Neptune MCP path.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("scudo.authoritative")


def _is_real_mode() -> bool:
    """True when env says the authoritative Neptune cluster is reachable."""
    return bool(os.environ.get("NEPTUNE_ENDPOINT"))


# ────────────────────────────────────────────────────────────────────────────
# Facade — what scudo.tools delegates to for the authoritative reads.
# Publishing is orchestrator-only and lives in scudo.orchestrator (not here).
# ────────────────────────────────────────────────────────────────────────────
def node_by_iri(iri: str) -> Optional[dict]:
    """Authoritative single-node lookup. Used to CONFIRM a sidecar candidate."""
    if _is_real_mode():
        from . import mcp as neptune_mcp
        return neptune_mcp.node_by_iri(iri=iri)
    from . import mock
    return mock.node_by_iri(iri=iri)


def existing_mapping(vendor: str, vendor_product_ref: str) -> Optional[dict]:
    """Prior authoritative mapping (drives NEW vs EXTEND)."""
    if _is_real_mode():
        from . import mcp as neptune_mcp
        return neptune_mcp.existing_mapping(vendor=vendor, vendor_product_ref=vendor_product_ref)
    from . import mock
    return mock.existing_mapping(vendor=vendor, vendor_product_ref=vendor_product_ref)


def conflicts(vendor_product_ref: str) -> list[dict]:
    """Equivalent products at other vendors mapped elsewhere (drives RECONCILE)."""
    if _is_real_mode():
        from . import mcp as neptune_mcp
        return neptune_mcp.conflicts(vendor_product_ref=vendor_product_ref)
    from . import mock
    return mock.conflicts(vendor_product_ref=vendor_product_ref)


__all__ = ["node_by_iri", "existing_mapping", "conflicts"]
