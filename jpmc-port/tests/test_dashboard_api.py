"""Matching dashboard façade — Capone-shaped /api/mapping/* over jpmc-port."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SCUDO_LOCAL"] = "1"


def setup_function():
    from scudo import local_state

    local_state.reset()


def test_dashboard_dist_vendored():
    dist = ROOT / "dashboard-dist"
    assert (dist / "index.html").is_file()
    assert (dist / "matching-graph.json").is_file()
    assert (dist / "meta.json").is_file()
    graph = json.loads((dist / "matching-graph.json").read_text())
    assert graph.get("nodes") or graph.get("layers")


def test_vendors_list():
    from scudo.dashboard_api import list_vendors

    body = list_vendors()
    assert "LSEG" in body["vendors"]
    assert body["default"] == "LSEG"


def test_ingest_sse_parses_json_and_fills_working_set():
    from scudo import local_state
    from scudo.dashboard_api import iter_ingest_sse

    payload = json.dumps(
        [
            {
                "product_id": "LSEG-IBES-EST-001",
                "name": "equity research estimates",
                "description": "sell-side estimates",
            }
        ]
    ).encode()
    frames = list(iter_ingest_sse("LSEG", "products.json", payload))
    joined = "".join(frames)
    assert "etl-lambda" in joined
    assert "final_result" in joined
    assert "LSEG-IBES-EST-001" in joined
    assert ("lseg", "LSEG-IBES-EST-001") in local_state.WORKING_SET


def test_agent_run_sse_emits_final_mapping():
    from scudo.dashboard_api import ingest_products, iter_agent_run_sse

    ingest_products(
        "LSEG",
        [
            {
                "product_id": "LSEG-IBES-EST-001",
                "name": "equity research estimates",
                "description": "sell-side",
            }
        ],
    )
    frames = list(
        iter_agent_run_sse(
            {
                "vendor": "LSEG",
                "product_id": "LSEG-IBES-EST-001",
                "name": "equity research",
            }
        )
    )
    events = []
    for fr in frames:
        if fr.startswith("data: "):
            events.append(json.loads(fr[len("data: ") :].strip()))
    types = [e["type"] for e in events]
    assert "start" in types
    assert "tool_call" in types
    assert "final_result" in types
    final = next(e for e in events if e["type"] == "final_result")
    mapping = final["mapping"]
    assert mapping["mapped_node_iri"]
    assert mapping["status"] in {"auto_mapped", "needs_review", "out_of_scope"}
    assert isinstance(mapping["confidence"], float)


def test_dashboard_decision_learns():
    from scudo import aurora_memory
    from scudo.dashboard_api import record_dashboard_decision

    status, body = record_dashboard_decision(
        {
            "vendor": "LSEG",
            "product_id": "LSEG-DASH-1",
            "decision": "approve",
            "node_iri": "jpmorgan:data:cdao:EquityResearch",
            "suggested_confidence": 0.91,
        }
    )
    assert status == 200
    assert body["learned"] is True
    priors = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-DASH-1"
    )
    assert priors.precedent is not None


def test_flask_app_serves_demo_and_vendors():
    import pytest

    pytest.importorskip("flask")
    os.environ["SCUDO_SERVE_DASHBOARD_DIST"] = "1"
    from run_local import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/demo/")
    assert r.status_code == 200
    assert b"html" in r.data.lower() or b"<!doctype" in r.data.lower()
    g = client.get("/demo/matching-graph.json")
    assert g.status_code == 200
    v = client.get("/api/mapping/vendors")
    assert v.status_code == 200
    assert "LSEG" in v.get_json()["vendors"]
