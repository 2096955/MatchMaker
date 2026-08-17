"""Zone-1 vendor poller — Secrets Manager keys; truncate-loud max_pages."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.request import Request, urlopen

from . import metrics

log = logging.getLogger("scudo.poller")
_DEFAULT_MAX_PAGES = 10


def _secrets(secret_id: str) -> dict:
    if os.environ.get("SCUDO_LOCAL"):
        return json.loads(os.environ.get("SCUDO_POLL_SECRET_JSON", "{}"))
    import boto3

    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)[
        "SecretString"
    ]
    return json.loads(raw)


def _s3_put(bucket: str, key: str, body: bytes) -> None:
    if os.environ.get("SCUDO_LOCAL"):
        log.info("local s3 put s3://%s/%s bytes=%d", bucket, key, len(body))
        return
    import boto3

    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body)


def _http(url: str, headers: dict) -> bytes:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def load_config(event: dict) -> dict:
    cfg = dict(event.get("config") or {})
    secret_id = cfg.get("secret_id") or os.environ.get("SCUDO_POLL_SECRET_ID")
    if secret_id:
        cfg["secrets"] = _secrets(secret_id)
    cfg.setdefault("max_pages", _DEFAULT_MAX_PAGES)
    return cfg


def poll_vendor(cfg: dict) -> dict[str, Any]:
    base = cfg.get("base_url") or ""
    if not base and os.environ.get("SCUDO_LOCAL"):
        metrics.emit("poll_pages", value=0)
        return {"pages": 0, "mode": "local-noop"}
    api_key = (cfg.get("secrets") or {}).get("api_key") or ""
    if not api_key:
        raise RuntimeError("poller api_key missing from Secrets Manager payload")
    max_pages = int(cfg["max_pages"])
    bucket = cfg["landing_bucket"]
    pages = 0
    for page in range(1, max_pages + 1):
        url = f"{base.rstrip('/')}/products?page={page}"
        raw = _http(url, {"x-api-key": api_key})
        _s3_put(bucket, f"poll/{cfg.get('vendor', 'vendor')}/page-{page}.json", raw)
        pages += 1
        if len(raw) < 10:
            break
    else:
        raise RuntimeError(
            f"max_pages={max_pages} truncated — raise limit or page size"
        )
    metrics.emit("poll_pages", value=pages)
    return {"pages": pages}


def handler(event: dict, context=None) -> dict:
    cfg = load_config(event or {})
    result = poll_vendor(cfg)
    return {"statusCode": 200, "body": result}
