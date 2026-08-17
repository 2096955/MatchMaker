"""Zone-2 ETL Lambda — validate S3 object, lineage trail, quarantine bad files."""

from __future__ import annotations

import hashlib
import json
import logging

from . import aurora_store, aws_resources, metrics

log = logging.getLogger("scudo.etl")


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "body": body}


def _extract_s3_refs(event: dict) -> list[tuple[str, str]]:
    refs = []
    for rec in event.get("Records") or []:
        s3 = rec.get("s3") or {}
        bucket = (s3.get("bucket") or {}).get("name")
        key = (s3.get("object") or {}).get("key")
        if bucket and key:
            refs.append((bucket, key))
    return refs


def _validate_payload(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    if not data.get("vendor") or not data.get("vendor_product_ref"):
        raise ValueError("vendor and vendor_product_ref required")
    return data


def _quarantine(bucket: str, key: str, reason: str) -> None:
    log.error("quarantine s3://%s/%s reason=%s", bucket, key, reason)
    metrics.emit("etl_quarantine", dims={"reason": reason[:64]})


def _process_object(bucket: str, key: str, raw: bytes) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = _validate_payload(raw)
    except Exception as exc:
        _quarantine(bucket, key, str(exc))
        return
    aurora_store.put_facts_record(
        source_bucket=bucket,
        source_key=key,
        content_hash=digest,
        payload=payload,
    )
    job_id = digest[:16]
    aurora_store.update_job_status(job_id=job_id, status="ingested", fields=payload)
    aws_resources.put_audit_record(
        item_id=job_id, event_type="etl_ingested", payload=payload
    )
    metrics.emit("etl_ingested")


def handler(event: dict, context=None) -> dict:
    # Local/dev: body may carry inline payload
    if local_inline := (event or {}).get("inline"):
        raw = json.dumps(local_inline).encode()
        _process_object("local", "inline.json", raw)
        return _resp(200, {"ok": True, "mode": "inline"})

    refs = _extract_s3_refs(event or {})
    if not refs:
        return _resp(400, {"error": "no s3 records"})
    # Production reads S3 via boto3; local tests pass preloaded content.
    for bucket, key in refs:
        content = (event.get("contents") or {}).get(f"{bucket}/{key}")
        if content is None:
            raise RuntimeError(f"missing content for s3://{bucket}/{key}")
        raw = content if isinstance(content, bytes) else str(content).encode()
        _process_object(bucket, key, raw)
    return _resp(200, {"ok": True, "processed": len(refs)})
