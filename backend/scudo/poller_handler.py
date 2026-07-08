"""Config-driven vendor-API poller — ONE Lambda for all vendors.

Onboarding a new vendor API is a config change (an entry in the JSON document
at SCUDO_POLLER_CONFIG), never a new Lambda. API keys are fetched at runtime
from Secrets Manager and never live in code or config. Pages stream straight to
the raw S3 bucket to keep memory flat regardless of catalogue size. A vendor
whose full pull cannot finish inside the Lambda window has a documented reserve
path (Step Functions fan-out or a Fargate task) — NOT built here; the max_pages
cap below truncates loudly rather than looping or blowing the window silently.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from . import metrics

log = logging.getLogger("scudo.poller")
log.setLevel(logging.INFO)

# Safety cap so a mispaginated vendor API can never loop forever or exhaust the
# Lambda window unnoticed. Overridable per vendor via config `max_pages`.
_DEFAULT_MAX_PAGES = 10_000


def _s3():
    import boto3  # type: ignore

    return boto3.client("s3")


def _secrets():
    import boto3  # type: ignore

    return boto3.client("secretsmanager")


def _http():
    import urllib3  # type: ignore

    pool = urllib3.PoolManager()

    class _Client:
        def get(self, url, headers=None):
            resp = pool.request("GET", url, headers=headers or {})
            return json.loads(resp.data.decode("utf-8"))

    return _Client()


def load_config(s3=None) -> list[dict]:
    """Load the per-vendor poller config from the S3 URI in SCUDO_POLLER_CONFIG."""
    ref = os.environ["SCUDO_POLLER_CONFIG"]
    if not ref.startswith("s3://"):
        raise RuntimeError("SCUDO_POLLER_CONFIG must be an s3:// URI")
    bucket, _, key = ref[len("s3://") :].partition("/")
    s3 = s3 or _s3()
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    doc = json.loads(body)
    return doc["vendors"] if isinstance(doc, dict) else doc


def _api_key(secrets, secret_id: str) -> str:
    raw = secrets.get_secret_value(SecretId=secret_id)["SecretString"]
    return json.loads(raw)["api_key"]


def poll_vendor(vendor_cfg: dict, *, s3, secrets, http) -> dict:
    """Pull one vendor's catalogue, streaming each page straight to the raw
    bucket under ``api/<vendor>/<date>/page-<n>.json``. Flat memory: a page is
    written and dropped before the next is fetched. Returns a per-vendor summary.
    """
    vendor = vendor_cfg["vendor"]
    if not vendor_cfg.get("enabled", False):
        return {"vendor": vendor, "pages": 0, "skipped": True, "truncated": False}

    raw_bucket = os.environ["SCUDO_RAW_BUCKET"]
    date = os.environ.get("SCUDO_POLLER_DATE", "run")
    api_key = _api_key(secrets, vendor_cfg["secret_id"])
    max_pages = int(vendor_cfg.get("max_pages", _DEFAULT_MAX_PAGES))

    page_n = 0
    truncated = False
    url = vendor_cfg["endpoint"]
    while url:
        if page_n >= max_pages:
            truncated = True
            log.warning(
                "poller: vendor %s hit max_pages=%d; pull truncated (reserve "
                "path: Step Functions / Fargate)",
                vendor,
                max_pages,
            )
            break
        page = http.get(url, headers={"x-api-key": api_key})
        page_n += 1
        s3.put_object(
            Bucket=raw_bucket,
            Key=f"api/{vendor}/{date}/page-{page_n}.json",
            Body=json.dumps(page).encode("utf-8"),
            ContentType="application/json",
        )
        nxt = page.get("next")
        # A string `next` is the cursor / next-page URL; a truthy non-string
        # re-polls the same endpoint (simple stub pagination).
        url = nxt if isinstance(nxt, str) else (vendor_cfg["endpoint"] if nxt else None)

    metrics.emit("PollerPages", page_n, dims={"vendor": vendor})
    return {
        "vendor": vendor,
        "pages": page_n,
        "skipped": False,
        "truncated": truncated,
    }


def handler(event: dict, context: Any) -> dict:
    """EventBridge-scheduled entry: poll every enabled vendor in the config.

    A no-op when SCUDO_POLLER_CONFIG is unset, so the schedule can ship before
    any vendor API is onboarded without failing every tick."""
    if not os.environ.get("SCUDO_POLLER_CONFIG"):
        log.info("poller: SCUDO_POLLER_CONFIG unset; no vendors to poll")
        return {"polled": 0, "results": []}
    s3, secrets, http = _s3(), _secrets(), _http()
    results = [
        poll_vendor(cfg, s3=s3, secrets=secrets, http=http) for cfg in load_config(s3)
    ]
    return {
        "polled": len([r for r in results if not r["skipped"]]),
        "results": results,
    }
