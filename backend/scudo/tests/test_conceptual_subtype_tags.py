"""Subtype surfaces in dashboard KnowledgeGraph fold-in tags (D1 carrier)."""

from __future__ import annotations

from scudo.build_matching_graph import _fold_conceptual_knowledge_graph
from scudo_mapping_mcp.models import ConceptualNode, ConceptualNodeKind, conceptual_iri
from scudo_mapping_mcp.tests.fake_store import FakeStore


def test_subtype_appears_in_dashboard_fold_in_tags():
    concept = "jpmorgan:data:cdao:EquityPrices"
    store = FakeStore()
    store.upsert_conceptual_node(
        ConceptualNode(
            iri=conceptual_iri(concept, ConceptualNodeKind.DOCUMENT, "order"),
            kind=ConceptualNodeKind.DOCUMENT,
            label="Order Form",
            attaches_to_concept_iri=concept,
            subtype="order_form",
        )
    )
    captured: list[dict] = []

    def add_node(payload: dict) -> None:
        captured.append(payload)

    edges: list[dict] = []
    _fold_conceptual_knowledge_graph(store, concept, add_node, edges)
    assert len(captured) == 1
    assert "conceptual" in captured[0]["tags"]
    assert "document" in captured[0]["tags"]
    assert "order_form" in captured[0]["tags"]
