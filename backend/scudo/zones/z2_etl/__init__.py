"""Zone 2 — Ingestion Processing (ETL).

Validate → normalise → land. The ETL sanity-check Lambda
(``etl_handler.handler``, SQS-backed) reads a raw S3 object, validates it, and
writes either clean/canonical or quarantine output plus an Aurora audit record.
The deterministic guards that sit in front of the matcher — scope check and
field normalisation — plus CSVW column aliasing and the DCAT projection models
live here too. (Field-level canonical normalisation currently also lives in the
matcher's ``default_field_rules``; converging it into the ETL Lambda is flagged
work, not done.)

AWS entrypoint: ``scudo.etl_handler.handler`` (Lambda).

Aurora/graph-store role: writes audit events to Aurora (``scudo.audit_events``)
and updates ETL job status; produces the canonical rows the Matching Engine then
retrieves candidates for. Aurora is the source of truth.

These are re-exports — the modules physically live in ``scudo`` /
``scudo_mapping_mcp``. See ``ZONES.md``.
"""

from scudo import etl_handler
from scudo_mapping_mcp import (
    csvw_aliases,
    frames,
    models_dcat,
    validations,
)

__all__ = [
    "etl_handler",
    "frames",
    "validations",
    "csvw_aliases",
    "models_dcat",
]
