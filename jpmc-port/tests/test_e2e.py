"""End-to-end: intake → map → verify → publish → outbox project."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SCUDO_LOCAL"] = "1"
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)


def test_run_publishes_and_projects():
    from scudo.handler import handle
    from scudo import local_state

    local_state.reset()

    resp = handle(
        {
            "path": "/run",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "name": "equity research estimates",
                "description": "sell-side equity research estimates",
            },
        }
    )
    assert resp["statusCode"] == 200
    body = resp["body"]
    assert body["outcome"] == "published"
    assert body["published_graph"]
    assert body["mapping_result"]["confidence"] >= 0.80
    assert body["verifier_report"]["total_score"] >= 16

    proj = handle({"path": "/project", "httpMethod": "POST", "headers": {}, "body": {}})
    assert proj["statusCode"] == 200
    assert proj["body"]["dispatched"] >= 1
    assert local_state.NEPTUNE_GRAPHS


def test_research_never_publishes():
    from scudo.handler import handle
    from scudo import local_state

    local_state.reset()
    resp = handle(
        {
            "path": "/run",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "vendor": "lseg",
                "vendor_product_ref": "GAP-001",
                "ontology_gap": True,
                "name": "unknown product",
            },
        }
    )
    assert resp["statusCode"] == 200
    assert resp["body"]["outcome"] == "research_queued"
    assert not local_state.NEPTUNE_GRAPHS


def test_health():
    from scudo.handler import handle

    resp = handle({"path": "/health", "httpMethod": "GET", "headers": {}, "body": {}})
    assert resp["statusCode"] == 200
    assert resp["body"]["ok"] is True
