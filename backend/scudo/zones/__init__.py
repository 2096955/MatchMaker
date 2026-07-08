"""SCUDO 5-zone architecture façade — a *logical* view over the physical code.

Nigel (JPM) approved the five-zone target design on 2026-07-03:

    Zone 1  Vendor Sources & Ingestion
    Zone 2  Ingestion Processing (ETL)
    Zone 3  Matching Engine
    Zone 4  Agentic Layer
    Zone 5  Persistence & Human Review   (Aurora PostgreSQL = single source of truth)

The zones are a layering that cuts **across** the two runtime packages
(``scudo`` and ``scudo_mapping_mcp``) — neither package equals one zone. Rather
than physically move ~85 modules (which would rewrite ~250-350 import/config
lines and break 7 Lambda/ECS entrypoints), this package makes the zones
*importable* by re-exporting the existing modules where they already live::

    from scudo.zones.z3_matching import matching, matcher_bridge
    from scudo.zones.z5_persistence import aurora_store

Nothing here moves or wraps code — each name is the real module object. This
package is a **leaf**: it is not imported by ``scudo/__init__.py`` or by any
runtime handler, so importing a zone does not amplify the known
``scudo`` ↔ ``scudo_mapping_mcp`` import cycle.

The authoritative module→zone map (including the Flask console surface and
cross-cutting modules) is ``ZONES.md`` at the repo root. The approved diagram is
``docs/architecture/scudo-5zone-architecture.png``.
"""

from . import (
    z1_ingestion,
    z2_etl,
    z3_matching,
    z4_agentic,
    z5_persistence,
)

__all__ = [
    "z1_ingestion",
    "z2_etl",
    "z3_matching",
    "z4_agentic",
    "z5_persistence",
]
