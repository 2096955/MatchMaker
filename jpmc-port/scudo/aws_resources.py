"""Thin AWS seam — Aurora writers fail-loud; EventBridge soft."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from . import aurora_store

log = logging.getLogger("scudo.aws_resources")


def _boto3():
    import boto3

    return boto3


def env_resource_summary() -> dict[str, str]:
    import os

    keys = (
        "SCUDO_AURORA_CLUSTER_ARN",
        "SCUDO_AURORA_SECRET_ARN",
        "SCUDO_AURORA_DATABASE_NAME",
        "SCUDO_EVENT_BUS_NAME",
        "NEPTUNE_ENDPOINT",
    )
    return {k: ("set" if os.environ.get(k) else "unset") for k in keys}


def put_audit_record(
    *, item_id: str, event_type: str, payload: Mapping[str, Any]
) -> None:
    aurora_store.put_audit_record(
        item_id=item_id, event_type=event_type, payload=payload
    )


def put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None:
    aurora_store.put_review_record(ticket=ticket, payload=payload)


def put_outbox_record(
    *, event_id: str, detail_type: str, detail: Mapping[str, Any]
) -> None:
    aurora_store.put_outbox_record(
        event_id=event_id, detail_type=detail_type, detail=detail
    )


def put_eventbridge_event(*, detail_type: str, detail: Mapping[str, Any]) -> None:
    import os

    bus = os.environ.get("SCUDO_EVENT_BUS_NAME")
    if not bus:
        return
    try:
        _boto3().client("events").put_events(
            Entries=[
                {
                    "Source": "scudo.mapping",
                    "DetailType": detail_type,
                    "Detail": __import__("json").dumps(dict(detail), default=str),
                    "EventBusName": bus,
                }
            ]
        )
    except Exception:
        log.exception("EventBridge put_events failed soft")
