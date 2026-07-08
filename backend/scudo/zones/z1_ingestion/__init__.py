"""Zone 1 — Vendor Sources & Ingestion.

Where vendor product metadata enters SCUDO. On AWS this is the config-driven
vendor-API poller (``poller_handler.handler``, an EventBridge-scheduled Lambda —
onboarding a vendor is a config change, not a new Lambda) plus the ingestion MCP
(trust-gradient tier 1: sees untrusted vendor data, holds no signing key). The
demo/console ingress adds the SSRF-guarded single-URL fetch and Turtle taxonomy
upload. The MFT→FTP gateway and vendor-S3/DMS boxes on the approved diagram are
JPM-owned and deliberately not in this repo.

AWS entrypoints: ``scudo.poller_handler.handler`` (Lambda),
``scudo_mapping_mcp.ingestion_mcp`` (ECS, port 8001).

Aurora/graph-store role: writes ETL job status to Aurora ``scudo.etl_jobs`` on
kickoff; seeds the graph store (retrieval index) with CDAO taxonomy. Aurora is
the source of truth; the graph store is a retrieval index only.

These are re-exports — the modules physically live in ``scudo`` /
``scudo_mapping_mcp``. See ``ZONES.md``.
"""

from scudo import poller_handler
from scudo_mapping_mcp import (
    ingest,
    ingestion_mcp,
    turtle_ingester,
    url_ingest,
)

__all__ = [
    "poller_handler",
    "ingest",
    "ingestion_mcp",
    "turtle_ingester",
    "url_ingest",
]
