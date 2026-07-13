"""Dashboard z.enum vocabulary gate for the matching graph fixture.

The understand-anything dashboard validates the KnowledgeGraph with a strict
closed vocabulary (zod z.enum in @understand-anything/core). Any node or edge
whose ``type`` falls outside these sets is silently DROPPED at load — the
"30 dropped items" banner regression. This test pins the full generated graph
(including the M10 conceptual-enrichment fold-in) to that vocabulary so the
builder can never again emit types the dashboard throws away.
"""

from __future__ import annotations

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


def test_rights_contract_node_kinds_map_into_dashboard_enum():
    """Every ConceptualNodeKind (incl. rights half) must map to a
    dashboard-closed type — iterate the FULL enum so forgotten map entries
    cannot hide behind the ``.get(kind, "entity")`` fallback."""
    from scudo.build_matching_graph import _CONCEPTUAL_NODE_TYPE
    from scudo_mapping_mcp.models import ConceptualNodeKind

    for kind in ConceptualNodeKind:
        assert kind.value in _CONCEPTUAL_NODE_TYPE, (
            f"{kind.value} has no dashboard mapping"
        )
        assert _CONCEPTUAL_NODE_TYPE[kind.value] in DASHBOARD_NODE_TYPES


def test_rights_contract_edge_kinds_map_into_dashboard_enum():
    """Every ConceptualEdgeKind must map to a dashboard-closed type —
    full-enum iteration (same forgotten-entry hardening as nodes)."""
    from scudo.build_matching_graph import _CONCEPTUAL_EDGE_TYPE
    from scudo_mapping_mcp.models import ConceptualEdgeKind

    for kind in ConceptualEdgeKind:
        assert kind.value in _CONCEPTUAL_EDGE_TYPE, (
            f"{kind.value} has no dashboard mapping"
        )
        assert _CONCEPTUAL_EDGE_TYPE[kind.value] in DASHBOARD_EDGE_TYPES


def test_all_node_types_within_dashboard_enum(built_matching_graph):
    g = built_matching_graph["graph"]
    bad = {n["type"] for n in g["nodes"]} - DASHBOARD_NODE_TYPES
    assert not bad, f"node types the dashboard would drop: {sorted(bad)}"


def test_all_edge_types_within_dashboard_enum(built_matching_graph):
    g = built_matching_graph["graph"]
    bad = {e["type"] for e in g["edges"]} - DASHBOARD_EDGE_TYPES
    assert not bad, f"edge types the dashboard would drop: {sorted(bad)}"


def test_all_edge_endpoints_exist(built_matching_graph):
    """Edges referencing dropped/missing nodes also drop in the dashboard."""
    g = built_matching_graph["graph"]
    node_ids = {n["id"] for n in g["nodes"]}
    dangling = [
        (e["source"], e["target"])
        for e in g["edges"]
        if e["source"] not in node_ids or e["target"] not in node_ids
    ]
    assert not dangling, f"edges with missing endpoints: {dangling}"


def test_m10_conceptual_layer_survives_validation(built_matching_graph):
    """The M10 layer must reference only nodes that pass the enum gate —
    a 0-component layer card is the visible symptom of the drop."""
    g = built_matching_graph["graph"]
    layers = {lyr["id"]: lyr for lyr in g["layers"]}
    m10 = layers.get("layer:m10-conceptual-enrichment")
    assert m10 is not None, "M10 conceptual enrichment layer missing"
    node_ids = {n["id"] for n in g["nodes"]}
    missing = [nid for nid in m10["nodeIds"] if nid not in node_ids]
    assert not missing, f"M10 layer references missing nodes: {missing}"
    assert len(m10["nodeIds"]) >= 15, (
        f"expected the full 15-node conceptual layer, got {len(m10['nodeIds'])}"
    )
