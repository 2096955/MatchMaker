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

from .aws_resources import _boto3, _jsonable, put_audit_record, put_eventbridge_event

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


def _table(name: str):
    table_name = os.environ.get(name)
    if not table_name:
        return None
    return _boto3().resource("dynamodb").Table(table_name)


def _process_object(bucket: str, key: str) -> dict:
    clean_bucket = os.environ["SCUDO_CLEAN_BUCKET"]
    quarantine_bucket = os.environ["SCUDO_QUARANTINE_BUCKET"]
    s3 = _boto3().client("s3")
    job_id = hashlib.sha256(f"{bucket}/{key}".encode("utf-8")).hexdigest()[:24]
    now_ms = int(time.time() * 1000)

    job_table = _table("SCUDO_JOB_TABLE")
    if job_table is not None:
        job_table.put_item(
            Item={
                "job_id": job_id,
                "created_at_ms": now_ms,
                "status": "PROCESSING",
                "source_bucket": bucket,
                "source_key": key,
            }
        )

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
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
        s3.put_object(
            Bucket=clean_bucket,
            Key=canonical_key,
            Body=json.dumps(canonical).encode("utf-8"),
            ContentType="application/json",
        )

        facts_table = _table("SCUDO_FACTS_TABLE")
        if facts_table is not None:
            facts_table.put_item(
                Item={
                    "pk": f"SOURCE#{bucket}",
                    "sk": f"{key}#{content_hash}",
                    "job_id": job_id,
                    "canonical_bucket": clean_bucket,
                    "canonical_key": canonical_key,
                    "payload": _jsonable(canonical),
                }
            )
        if job_table is not None:
            job_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s=:s, canonical_bucket=:b, canonical_key=:k, completed_at_ms=:t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "PASSED",
                    ":b": clean_bucket,
                    ":k": canonical_key,
                    ":t": int(time.time() * 1000),
                },
            )
        put_audit_record(
            item_id=job_id,
            event_type="ETL_PASSED",
            payload=canonical,
        )
        put_eventbridge_event(detail_type="CanonicalMetadataReady", detail=canonical)
        return {"job_id": job_id, "status": "PASSED", "canonical_key": canonical_key}
    except Exception as exc:
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
        if job_table is not None:
            job_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s=:s, quarantine_bucket=:b, quarantine_key=:k, error=:e, completed_at_ms=:t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "FAILED",
                    ":b": quarantine_bucket,
                    ":k": quarantine_key,
                    ":e": str(exc),
                    ":t": int(time.time() * 1000),
                },
            )
        put_audit_record(item_id=job_id, event_type="ETL_FAILED", payload=error_doc)
        log.exception("ETL failed for s3://%s/%s", bucket, key)
        return {"job_id": job_id, "status": "FAILED", "quarantine_key": quarantine_key}


def handler(event: dict, context: Any) -> dict:
    refs = _extract_s3_refs(event)
    results = [_process_object(bucket, key) for bucket, key in refs]
    return _resp(200, {"processed": len(results), "results": results})
