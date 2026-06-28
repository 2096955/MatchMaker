"""GET /api/mapping/graph returns the MatchPayload contract."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}


def test_mapping_graph_returns_200():
    r = client.get("/api/mapping/graph", headers=AUTH)
    assert r.status_code == 200
    body = r.get_json()
    assert body["meta"]["dataProvenance"] == "synthetic"
    assert "nodes" in body


def test_mapping_graph_vendor_drilldown():
    r = client.get(
        "/api/mapping/graph?vendor=ICE&ref=ICE-CA",
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.get_json()
    vendor_nodes = [n for n in body["nodes"] if n["kind"] == "vendor-product"]
    assert vendor_nodes, "drilldown returned no vendor-product node"
    # Node ids use the canonical mds.<vendor>:<uuid5> scheme, NOT human refs.
    assert all(n["id"].startswith("mds.ice:") for n in vendor_nodes), (
        f"vendor node ids not canonical: {[n['id'] for n in vendor_nodes]}"
    )
    assert not any("ICE-CA" in n["id"] for n in vendor_nodes), (
        "human ref leaked into node id"
    )
    # The human-readable ref stays discoverable in the caption.
    assert any("ICE-CA" in (n.get("caption") or "") for n in vendor_nodes), (
        "ICE-CA ref missing from caption"
    )
