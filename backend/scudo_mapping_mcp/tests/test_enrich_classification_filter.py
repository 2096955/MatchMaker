"""Enrich route filters classification candidates to catalogue-eligible kinds.

Regression: a mixed-kind conceptual graph (catalogue + rights) must not pass
CONTRACT/PARTY nodes into classify_business_concept.
"""

from __future__ import annotations

import os

from scudo_mapping_mcp.models import (
    ConceptualGraph,
    ConceptualNode,
    ConceptualNodeKind,
    MappingStatus,
    RIGHTS_HALF_NODE_KINDS,
    TaxonomyNode,
    VendorProductRef,
    conceptual_iri,
)
from scudo_mapping_mcp.tests.fake_store import FakeStore


def test_rights_half_kinds_are_excluded_from_classification_candidates():
    concept = "jpmorgan:data:cdao:EquityPrices"
    catalogue = ConceptualNode(
        iri=conceptual_iri(concept, ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT, "bce"),
        kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
        label="Equity Concept",
        attaches_to_concept_iri=concept,
    )
    rights = ConceptualNode(
        iri=conceptual_iri(concept, ConceptualNodeKind.CONTRACT, "ctr"),
        kind=ConceptualNodeKind.CONTRACT,
        label="Master Contract",
        attaches_to_concept_iri=concept,
    )
    party = ConceptualNode(
        iri=conceptual_iri(concept, ConceptualNodeKind.PARTY, "vendor"),
        kind=ConceptualNodeKind.PARTY,
        label="Vendor Party",
        attaches_to_concept_iri=concept,
        party_scope="external",
    )
    mixed = [catalogue, rights, party]
    eligible = [n for n in mixed if n.kind not in RIGHTS_HALF_NODE_KINDS]
    assert eligible == [catalogue]
    assert rights.kind in RIGHTS_HALF_NODE_KINDS
    assert party.kind in RIGHTS_HALF_NODE_KINDS


def test_enrich_route_filters_classification_candidates(monkeypatch):
    """Drive /mapping/enrich with a mixed graph; assert classify sees only
    catalogue-eligible nodes (SSE heartbeat helpers remain untouched)."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    os.environ.setdefault("SCUDO_AUTH_ALLOW_DEV", "1")
    os.environ.setdefault("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")
    monkeypatch.setenv("SCUDO_AUTH_ALLOW_DEV", "1")
    monkeypatch.setenv("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")

    concept = "jpmorgan:data:cdao:EquityPrices"
    store = FakeStore()
    ref = VendorProductRef(vendor="LSEG", product_id="EQ-1", name="EQ Product")
    store.upsert_precedent(
        ref=ref,
        node=TaxonomyNode(iri=concept, label="Equity Prices"),
        decision="approve",
        decided_by="test",
        confidence=0.95,
    )
    store.upsert_conceptual_node(
        ConceptualNode(
            iri=conceptual_iri(concept, ConceptualNodeKind.CONTRACT, "ctr"),
            kind=ConceptualNodeKind.CONTRACT,
            label="Should Not Classify",
            attaches_to_concept_iri=concept,
        )
    )
    store.upsert_conceptual_node(
        ConceptualNode(
            iri=conceptual_iri(
                concept, ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT, "bce"
            ),
            kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
            label="Eligible Concept",
            attaches_to_concept_iri=concept,
        )
    )

    seen_kinds: list[str] = []

    def _fake_classify(ref, concept_iri, candidates):
        seen_kinds.extend([c.kind.value for c in candidates])
        return None

    def _fake_extract(ref, concept_iri):
        return ConceptualGraph(root_concept_iri=concept_iri, nodes=[], edges=[])

    monkeypatch.setattr("routes.mapping.get_store", lambda: store)
    monkeypatch.setattr(
        "routes.mapping._read_vendor_frame",
        lambda vendor, product_id: VendorProductRef(
            vendor=vendor, product_id=product_id, name="EQ Product"
        ),
    )
    monkeypatch.setattr("routes.mapping.extract_field_structure", _fake_extract)
    monkeypatch.setattr("routes.mapping.classify_business_concept", _fake_classify)

    from app import app

    client = app.test_client()
    resp = client.post(
        "/api/mapping/enrich",
        json={"vendor": "LSEG", "product_id": "EQ-1"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert "contract" not in seen_kinds
    assert "party" not in seen_kinds
    assert "business_concept_element" in seen_kinds
    mapping = store.get_precedent_mapping("LSEG", "EQ-1")
    assert mapping is not None
    assert mapping.status in {
        MappingStatus.APPROVED,
        MappingStatus.AUTO_MAPPED,
        MappingStatus.OVERRIDDEN,
    }
