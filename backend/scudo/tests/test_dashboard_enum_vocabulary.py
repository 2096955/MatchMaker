"""Dashboard z.enum vocabulary gate for the matching graph fixture.

The understand-anything dashboard validates the KnowledgeGraph with a strict
closed vocabulary (zod z.enum in @understand-anything/core). Any node or edge
whose ``type`` falls outside these sets is silently DROPPED at load — the
"30 dropped items" banner regression. This test pins the full generated graph
(including the M10 conceptual-enrichment fold-in) to that vocabulary so the
builder can never again emit types the dashboard throws away.
"""

from __future__ import annotations

import json
import os

# Mirrors NodeType / EdgeType z.enum in
# understand-anything-plugin/packages/core (schema version 1.0.0).
DASHBOARD_NODE_TYPES = {
    "file",
    "function",
    "class",
    "module",
    "concept",
    "config",
    "document",
    "service",
    "table",
    "endpoint",
    "pipeline",
    "schema",
    "resource",
    "domain",
    "flow",
    "step",
    "article",
    "entity",
    "topic",
    "claim",
    "source",
}

DASHBOARD_EDGE_TYPES = {
    "imports",
    "exports",
    "contains",
    "inherits",
    "implements",
    "calls",
    "subscribes",
    "publishes",
    "middleware",
    "reads_from",
    "writes_to",
    "transforms",
    "validates",
    "depends_on",
    "tested_by",
    "configures",
    "related",
    "similar_to",
    "deploys",
    "serves",
    "provisions",
    "triggers",
    "migrates",
    "documents",
    "routes",
    "defines_schema",
    "contains_flow",
    "flow_step",
    "cross_domain",
    "cites",
    "contradicts",
    "builds_on",
    "exemplifies",
    "categorized_under",
    "authored_by",
}


def _run_main_and_load() -> dict:
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")

    from scudo.build_matching_graph import main

    main()
    fixture = os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "matching-graph.json"
    )
    with open(fixture, encoding="utf-8") as fh:
        return json.load(fh)


def test_all_node_types_within_dashboard_enum():
    g = _run_main_and_load()
    bad = {n["type"] for n in g["nodes"]} - DASHBOARD_NODE_TYPES
    assert not bad, f"node types the dashboard would drop: {sorted(bad)}"


def test_all_edge_types_within_dashboard_enum():
    g = _run_main_and_load()
    bad = {e["type"] for e in g["edges"]} - DASHBOARD_EDGE_TYPES
    assert not bad, f"edge types the dashboard would drop: {sorted(bad)}"


def test_all_edge_endpoints_exist():
    """Edges referencing dropped/missing nodes also drop in the dashboard."""
    g = _run_main_and_load()
    node_ids = {n["id"] for n in g["nodes"]}
    dangling = [
        (e["source"], e["target"])
        for e in g["edges"]
        if e["source"] not in node_ids or e["target"] not in node_ids
    ]
    assert not dangling, f"edges with missing endpoints: {dangling}"


def test_m10_conceptual_layer_survives_validation():
    """The M10 layer must reference only nodes that pass the enum gate —
    a 0-component layer card is the visible symptom of the drop."""
    g = _run_main_and_load()
    layers = {lyr["id"]: lyr for lyr in g["layers"]}
    m10 = layers.get("layer:m10-conceptual-enrichment")
    assert m10 is not None, "M10 conceptual enrichment layer missing"
    node_ids = {n["id"] for n in g["nodes"]}
    missing = [nid for nid in m10["nodeIds"] if nid not in node_ids]
    assert not missing, f"M10 layer references missing nodes: {missing}"
    assert len(m10["nodeIds"]) >= 15, (
        f"expected the full 15-node conceptual layer, got {len(m10['nodeIds'])}"
    )
