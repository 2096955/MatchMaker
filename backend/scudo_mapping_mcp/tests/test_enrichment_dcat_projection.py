"""P1 — enrichment → matcher DCAT projection (WS4)."""

from __future__ import annotations

from scudo_mapping_mcp.enrichment_dcat_projection import (
    project_conceptual_graph,
    project_conceptual_node,
)
from scudo_mapping_mcp.models import (
    ConceptualGraph,
    ConceptualNode,
    ConceptualNodeKind,
    TaxonomyNode,
    conceptual_iri,
)

EQ = "https://example.com/taxonomy/EquityPrices"


def test_project_field_as_property():
    node = ConceptualNode(
        iri=conceptual_iri(EQ, ConceptualNodeKind.FIELD, "ticker"),
        kind=ConceptualNodeKind.FIELD,
        label="Ticker",
        attaches_to_concept_iri=EQ,
        vendor_field_name="RIC",
        data_type="string",
    )
    projected = project_conceptual_node(node)
    assert projected is not None
    assert isinstance(projected, TaxonomyNode)
    assert projected.node_kind == "property"
    assert projected.parent_iri == EQ
    assert "RIC" in projected.definition


def test_project_data_service_as_concept():
    node = ConceptualNode(
        iri=conceptual_iri(EQ, ConceptualNodeKind.DATA_SERVICE, "api"),
        kind=ConceptualNodeKind.DATA_SERVICE,
        label="Price API",
        attaches_to_concept_iri=EQ,
    )
    projected = project_conceptual_node(node)
    assert projected is not None
    assert projected.node_kind == "concept"
    assert EQ in projected.superclass_iris


def test_project_conceptual_graph_collects_projectable_nodes():
    field = ConceptualNode(
        iri=conceptual_iri(EQ, ConceptualNodeKind.FIELD, "px"),
        kind=ConceptualNodeKind.FIELD,
        label="Price",
        attaches_to_concept_iri=EQ,
    )
    graph = ConceptualGraph(root_concept_iri=EQ, nodes=[field])
    hints = project_conceptual_graph(graph)
    assert len(hints) == 1
    assert hints[0].label == "Price"
