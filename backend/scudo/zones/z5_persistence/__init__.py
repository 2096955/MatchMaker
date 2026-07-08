"""Zone 5 — Persistence & Human Review.

The system of record and the human loop. **Aurora PostgreSQL is the single
source of truth** — one cluster, four schemas (``public``, ``scudo``,
``console``, ``ingestion``). ``aurora_store`` is the durable persistence layer
(audit, decisions, outbox, catalogue, lineage, ETL jobs, taxonomy) via the RDS
Data API; ``aurora_memory`` backs CONSULT/DISTILL/SkillOpt agent memory;
``catalogue`` is the approved-catalogue API surface (the API consumes this,
never Aurora/Neptune directly); ``projection_handler`` drains the transactional
outbox to projections; ``feedback`` writes the HITL decision back and tilts
precedent rank; ``verdict`` is the HMAC seal integrity contract; ``rights_odrl``
is the fail-closed ODRL 2.2 rights evaluator; ``persistence_mcp`` is the sole
writer (trust-gradient tier 3, publish gate I5).

AWS entrypoints: ``scudo.projection_handler.handler`` (Lambda),
``scudo_mapping_mcp.persistence_mcp`` (ECS, port 8003). The Flask console Human
Review surface lives outside these packages (``backend/routes/mapping.py``,
``backend/db.py``) — see ``ZONES.md``.

Aurora/graph-store role: Aurora is authoritative. The DynamoDB tables the older
design used were consolidated into Aurora schemas (5-zone alignment) and removed.

These are re-exports — the modules physically live in ``scudo`` /
``scudo_mapping_mcp``. See ``ZONES.md``.
"""

from scudo import (
    aurora_memory,
    aurora_store,
    catalogue,
    projection_handler,
)
from scudo_mapping_mcp import (
    feedback,
    persistence_mcp,
    rights_odrl,
    verdict,
)

__all__ = [
    "aurora_store",
    "aurora_memory",
    "catalogue",
    "projection_handler",
    "feedback",
    "verdict",
    "rights_odrl",
    "persistence_mcp",
]
