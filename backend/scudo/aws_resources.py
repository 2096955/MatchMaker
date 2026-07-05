"""Small AWS adapter layer for the SCUDO Lambda PoC.

The deployed template wires the target-state data-flow resources into
environment variables. These helpers keep boto3 imports lazy so local smoke
tests do not need AWS credentials or boto3 installed.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any, Mapping

log = logging.getLogger("scudo.aws_resources")


def _boto3():
    import boto3  # type: ignore

    return boto3


def _jsonable(value: Any) -> Any:
    """Return a DynamoDB-safe JSON-ish value."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def env_resource_summary() -> dict[str, str | None]:
    """Expose the provisioned resource contract in /health."""
    keys = [
        "SCUDO_RAW_BUCKET",
        "SCUDO_CLEAN_BUCKET",
        "SCUDO_QUARANTINE_BUCKET",
        "SCUDO_VENDOR_CATALOG_BUCKET",
        "SCUDO_CDAO_CATALOG_BUCKET",
        "SCUDO_ETL_QUEUE_URL",
        "SCUDO_EVENT_BUS_NAME",
        "SCUDO_PERSISTENCE_QUEUE_URL",
        "SCUDO_NEPTUNE_SPARQL_ENDPOINT",
        "SCUDO_NEPTUNE_ENDPOINT",
        "SCUDO_OPENSEARCH_ENDPOINT",
        "SCUDO_OPENSEARCH_INDEX",
        "SCUDO_AURORA_CLUSTER_ARN",
        "SCUDO_AURORA_SECRET_ARN",
        "SCUDO_AURORA_DATABASE_NAME",
        "SCUDO_EMBEDDINGS_MODEL_ID",
        "SCUDO_APPSYNC_API_URL",
    ]
    return {key: os.environ.get(key) for key in keys}


def put_audit_record(
    *, item_id: str, event_type: str, payload: Mapping[str, Any]
) -> None:
    # Persistence consolidated onto the single Aurora PostgreSQL cluster.
    # FAIL-LOUD: aurora_store raises on missing config / Data API error rather
    # than silently dropping the audit trail (the DynamoDB fail-soft no-op that
    # lived here is the behaviour the migration deliberately removes).
    from . import aurora_store

    aurora_store.put_audit_record(
        item_id=item_id, event_type=event_type, payload=payload
    )


def put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None:
    from . import aurora_store

    aurora_store.put_review_record(ticket=ticket, payload=payload)


def put_outbox_record(
    *, event_id: str, detail_type: str, detail: Mapping[str, Any]
) -> None:
    from . import aurora_store

    aurora_store.put_outbox_record(
        event_id=event_id, detail_type=detail_type, detail=detail
    )


def put_eventbridge_event(*, detail_type: str, detail: Mapping[str, Any]) -> None:
    bus_name = os.environ.get("SCUDO_EVENT_BUS_NAME")
    if not bus_name:
        return
    try:
        _boto3().client("events").put_events(
            Entries=[
                {
                    "Source": "scudo.matchmaker",
                    "DetailType": detail_type,
                    "EventBusName": bus_name,
                    "Detail": json.dumps(dict(detail), default=str),
                }
            ]
        )
    except Exception:
        log.exception("failed to publish event to %s", bus_name)
