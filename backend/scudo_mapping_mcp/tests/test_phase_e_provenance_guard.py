"""Phase E step 6 — I5 provenance boundary.

Only customer-curated catalogue text may feed matching text. SCUDO-inferred
conceptual metadata (``classify_business_concept`` output) must NEVER be
written back into ``TaxonomyNode`` text fields — that would close an LLM
feedback loop into the confidence gate.

Verified call-site sweep (2026-07-13): ``classify_business_concept`` is
called from ``routes/mapping.py::enrich_mapped_product`` only (plus tests /
smoke). Its output is upserted exclusively via ``upsert_conceptual_node``;
no seam exists today where classify output reaches TaxonomyNode text. The
enforcement test below pins that the enrichment write path only touches
ConceptualNode, never TaxonomyNode.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import DCTERMS, RDF

from scudo_mapping_mcp.csvw_aliases import CAT_ONTOLOGY_NS
from scudo_mapping_mcp.loaders.dcat_loader import (
    _is_taxonomy_entity,
    load_dcat_taxonomy_from_graph,
)
from scudo_mapping_mcp.models import (
    ConceptualGraph,
    ConceptualNode,
    ConceptualNodeKind,
    TaxonomyNode,
    VendorProductRef,
    conceptual_iri,
)
from scudo_mapping_mcp.tests.fake_store import FakeStore

CAT = Namespace(CAT_ONTOLOGY_NS)
EX = Namespace("https://example.com/cat/")


# ── I5 comment states the boundary explicitly ──────────────────────────────


def test_models_i5_comment_states_provenance_boundary():
    import scudo_mapping_mcp.models as models_mod

    source = Path(models_mod.__file__).read_text()
    assert "classify_business_concept" in source, (
        "models.py I5 comment must name classify_business_concept as the "
        "SCUDO-inferred metadata that may never feed TaxonomyNode text"
    )
    assert "NEVER be written back into" in source
    assert "TaxonomyNode text fields" in source


# ── enforcement: enrichment write path never touches TaxonomyNode ──────────


def test_enrichment_write_path_only_touches_conceptual_nodes(monkeypatch):
    """Drive /mapping/enrich with a classify stub that returns a pick;
    assert no TaxonomyNode write happens and the mapped node's text
    fields are byte-identical before/after."""
    import os

    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    monkeypatch.setenv("SCUDO_AUTH_ALLOW_DEV", "1")
    monkeypatch.setenv("SCUDO_AUTH_DEV_PRINCIPAL", "test@local")

    concept = "jpmorgan:data:cdao:EquityPrices"
    store = FakeStore()
    taxonomy_node = TaxonomyNode(
        iri=concept,
        label="Equity Prices",
        definition="Customer-curated definition.",
        alt_labels=["Stocks"],
        business_concept="Pricing",
        asset_class="Equity",
    )
    store.upsert_taxonomy_node(taxonomy_node)
    ref = VendorProductRef(vendor="LSEG", product_id="EQ-1", name="EQ Product")
    store.upsert_precedent(
        ref=ref,
        node=taxonomy_node,
        decision="approve",
        decided_by="test",
        confidence=0.95,
    )
    candidate = ConceptualNode(
        iri=conceptual_iri(concept, ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT, "bce"),
        kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
        label="LLM-inferred business concept",
        attaches_to_concept_iri=concept,
    )
    store.upsert_conceptual_node(candidate)
    before = store.get_taxonomy_node(concept).model_dump()

    taxonomy_writes: list = []
    real_upsert = store.upsert_taxonomy_node

    def record_taxonomy_write(node):
        taxonomy_writes.append(node)
        return real_upsert(node)

    monkeypatch.setattr(store, "upsert_taxonomy_node", record_taxonomy_write)

    def _fake_classify(ref, concept_iri, candidates):
        return candidate  # the LLM "picked" — output is ConceptualNode only

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

    # The LLM pick landed on the conceptual layer...
    assert store.get_conceptual_graph(concept).nodes
    # ...and NEVER on TaxonomyNode: no write call, no text drift.
    assert taxonomy_writes == []
    assert store.get_taxonomy_node(concept).model_dump() == before


# ── characterisation: cat:MarketingDataset subjects are dropped today ──────


def test_cat_marketing_dataset_subject_is_dropped_by_loader_today():
    """CHARACTERISATION, not endorsement (flagged for the ontology owner):
    ``_is_taxonomy_entity`` accepts SKOS/OWL/RDFS classes+properties and
    dcat Dataset/Distribution/DataService ONLY. A subject typed
    ``cat:MarketingDataset`` (no dcat:Dataset co-typing) is silently
    dropped, so Phase E enrichment would be moot for such exports. Do not
    change this behaviour without an ontology-owner decision on whether
    the customer's UML→RDF export co-types datasets as dcat:Dataset."""
    g = Graph()
    md = EX["marketing-pack"]
    g.add((md, RDF.type, CAT.MarketingDataset))
    g.add((md, DCTERMS.title, Literal("Equity Marketing Pack")))
    g.add((md, CAT.assetClass, Literal("Equity")))

    assert _is_taxonomy_entity(g, md) is False
    assert load_dcat_taxonomy_from_graph(g) == []

    # Co-typed dcat:Dataset IS loaded — the drop is purely the missing type.
    from rdflib.namespace import DCAT

    g.add((md, RDF.type, DCAT.Dataset))
    nodes = load_dcat_taxonomy_from_graph(g)
    assert [n.iri for n in nodes] == [str(md)]
