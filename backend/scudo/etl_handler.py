"""SCUDO ETL Lambda entrypoint.

The target-state architecture has an event-driven left column:
S3 raw object -> EventBridge -> SQS -> Lambda -> clean canonical S3 or
quarantine, with DynamoDB job/fact tracking. This handler implements that
thin control plane without pulling the heavier local ingestion framework into
the Lambda image.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote_plus

from .aurora_store import put_facts_record, update_job_status
from .aws_resources import _boto3, put_audit_record, put_eventbridge_event

log = logging.getLogger("scudo.etl")
log.setLevel(logging.INFO)


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _extract_s3_refs(event: dict) -> list[tuple[str, str]]:
    """Extract (bucket, key) from SQS-wrapped EventBridge or S3 records."""
    refs: list[tuple[str, str]] = []
    records = event.get("Records")
    if records and any("body" in record for record in records):
        for record in records:
            body = record.get("body")
            payload = json.loads(body) if isinstance(body, str) else record
            refs.extend(_extract_s3_refs(payload))
        return refs

    # EventBridge S3 object-created event.
    detail = event.get("detail") or {}
    bucket = (detail.get("bucket") or {}).get("name")
    key = (detail.get("object") or {}).get("key")
    if bucket and key:
        return [(bucket, unquote_plus(key))]

    # Native S3 notification shape.
    for record in event.get("Records", []):
        s3 = record.get("s3") or {}
        b = (s3.get("bucket") or {}).get("name")
        k = (s3.get("object") or {}).get("key")
        if b and k:
            refs.append((b, unquote_plus(k)))
    return refs


def _validate_payload(key: str, body: bytes) -> tuple[bool, str]:
    """Deterministic sanity check before canonicalisation. A bad vendor file is
    data, not an outage — a False result quarantines it with a machine-readable
    reason (fail-soft on the FILE). The audit/job trail, by contrast, is
    fail-loud (see aurora_store)."""
    if not body:
        return False, "empty object"
    suffix = PurePosixPath(key).suffix.lower()
    if suffix == ".json" or key.startswith("api/"):
        try:
            json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return False, f"json parse error: {exc}"
    if len(body) > 512 * 1024 * 1024:
        return False, "object exceeds 512MiB sanity ceiling"
    return True, ""


def _process_object(bucket: str, key: str) -> dict:
    clean_bucket = os.environ["SCUDO_CLEAN_BUCKET"]
    quarantine_bucket = os.environ["SCUDO_QUARANTINE_BUCKET"]
    s3 = _boto3().client("s3")
    job_id = hashlib.sha256(f"{bucket}/{key}".encode("utf-8")).hexdigest()[:24]
    now_ms = int(time.time() * 1000)

    # The audit/job/facts trail persists to the single Aurora cluster and is
    # FAIL-LOUD everywhere: a failed write raises so the SQS message retries
    # (DLQ) rather than silently losing the trail. ONLY the read/validate/
    # canonicalise step is fail-soft — a bad vendor file is data, not an outage,
    # so it is quarantined. An Aurora/audit/EventBridge failure must never be
    # mistaken for a bad file, so every write below lives OUTSIDE the quarantine
    # try.
    update_job_status(
        job_id=job_id,
        status="PROCESSING",
        fields={"source_bucket": bucket, "source_key": key, "created_at_ms": now_ms},
    )

    # ── Fail-soft: read + sanity-check + canonicalise in memory. Only bad-file
    #    conditions are caught here and routed to quarantine. ──
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        ok, reason = _validate_payload(key, body)
        if not ok:
            raise ValueError(f"sanity check failed: {reason}")
        content_hash = hashlib.sha256(body).hexdigest()
        basename = PurePosixPath(key).name or "object"
        canonical_key = f"clean/{job_id}/{basename}.json"
        canonical = {
            "job_id": job_id,
            "source_bucket": bucket,
            "source_key": key,
            "source_content_hash": content_hash,
            "content_type": obj.get("ContentType"),
            "size": len(body),
            "loaded_at_ms": now_ms,
        }
    except Exception as exc:
        return _quarantine(s3, bucket, key, job_id, quarantine_bucket, exc)

    # ── Fail-loud: the file is good. Write the canonical output, the lineage
    #    fact, the PASSED job status, the audit record, and the projection
    #    event. A failure here RAISES (SQS retry / DLQ) — a good file is never
    #    quarantined for an infra/persistence outage. ──
    s3.put_object(
        Bucket=clean_bucket,
        Key=canonical_key,
        Body=json.dumps(canonical).encode("utf-8"),
        ContentType="application/json",
    )
    put_facts_record(
        source_bucket=bucket,
        source_key=key,
        content_hash=content_hash,
        payload=canonical,
    )
    update_job_status(
        job_id=job_id,
        status="PASSED",
        fields={"canonical_bucket": clean_bucket, "canonical_key": canonical_key},
    )
    put_audit_record(item_id=job_id, event_type="ETL_PASSED", payload=canonical)
    put_eventbridge_event(detail_type="CanonicalMetadataReady", detail=canonical)
    return {"job_id": job_id, "status": "PASSED", "canonical_key": canonical_key}


def _quarantine(
    s3, bucket: str, key: str, job_id: str, quarantine_bucket: str, exc: Exception
) -> dict:
    """Route a BAD FILE to the rejected bucket (fail-soft on the FILE). The
    rejection trail (job status + audit) is still FAIL-LOUD — but the file is
    quarantined idempotently, so an SQS retry re-quarantines and re-writes the
    trail rather than losing it."""
    quarantine_key = f"quarantine/{job_id}/{PurePosixPath(key).name or 'object'}.json"
    error_doc = {
        "job_id": job_id,
        "source_bucket": bucket,
        "source_key": key,
        "error": str(exc),
        "failed_at_ms": int(time.time() * 1000),
    }
    s3.put_object(
        Bucket=quarantine_bucket,
        Key=quarantine_key,
        Body=json.dumps(error_doc).encode("utf-8"),
        ContentType="application/json",
    )
    update_job_status(
        job_id=job_id,
        status="FAILED",
        fields={
            "quarantine_bucket": quarantine_bucket,
            "quarantine_key": quarantine_key,
            "error": str(exc),
        },
    )
    put_audit_record(item_id=job_id, event_type="ETL_FAILED", payload=error_doc)
    log.exception("ETL quarantined s3://%s/%s: %s", bucket, key, exc)
    return {"job_id": job_id, "status": "FAILED", "quarantine_key": quarantine_key}


def handler(event: dict, context: Any) -> dict:
    refs = _extract_s3_refs(event)
    results = [_process_object(bucket, key) for bucket, key in refs]
    return _resp(200, {"processed": len(results), "results": results})
