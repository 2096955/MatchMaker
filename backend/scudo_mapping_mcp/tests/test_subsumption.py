"""P1 — RDFS subsumption closure and BM25 candidate expansion."""

from __future__ import annotations

from pathlib import Path


from scudo_mapping_mcp.loaders.dcat_loader import load_dcat_taxonomy
from scudo_mapping_mcp.loaders.subsumption import (
    build_child_index,
    direct_subsumption_neighbors,
    materialize_subsumption_closure,
)
from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.retrieval import _expand_subsumption, multi_path_retrieve
from scudo_mapping_mcp.tests.fake_store import FakeStore

FIXTURE_TTL = Path(__file__).parent / "fixtures" / "dcat_taxonomy.ttl"
EQUITIES = "https://example.com/taxonomy/Equities"
FINANCIAL = "https://example.com/taxonomy/FinancialData"
EQ_PRICES = "https://example.com/taxonomy/EquityPrices"
BONDS = "https://example.com/taxonomy/Bonds"


def test_subsumption_closure_expands_transitive_superclasses():
    nodes = [
        TaxonomyNode(iri="a", label="A", superclass_iris=["b"]),
        TaxonomyNode(iri="b", label="B", superclass_iris=["c"]),
        TaxonomyNode(iri="c", label="C"),
    ]
    closed = materialize_subsumption_closure(nodes, max_depth=3)
    by_iri = {n.iri: n for n in closed}
    assert set(by_iri["a"].superclass_iris) == {"b", "c"}


def test_subsumption_closure_skipped_when_depth_zero():
    nodes = [
        TaxonomyNode(iri="a", label="A", superclass_iris=["b"]),
        TaxonomyNode(iri="b", label="B", superclass_iris=["c"]),
    ]
    assert materialize_subsumption_closure(nodes, max_depth=0) == nodes


def test_dcat_loader_applies_closure_when_depth_set():
    nodes = load_dcat_taxonomy(FIXTURE_TTL, subsumption_depth=3)
    by_iri = {n.iri: n for n in nodes}
    equities = by_iri[EQUITIES]
    assert FINANCIAL in equities.superclass_iris
    if FINANCIAL in by_iri:
        financial = by_iri[FINANCIAL]
        for anc in financial.superclass_iris:
            assert anc in by_iri or anc == FINANCIAL


def test_direct_subsumption_neighbors_one_hop():
    parent = TaxonomyNode(iri="p", label="Parent")
    child = TaxonomyNode(
        iri="c",
        label="Child",
        parent_iri="p",
        superclass_iris=["p"],
        children_iris=[],
    )
    parent_with_child = parent.model_copy(update={"children_iris": ["c"]})
    nodes = [parent_with_child, child]
    idx = build_child_index(nodes)
    neighbors = direct_subsumption_neighbors(child, idx)
    assert "p" in neighbors


def test_expand_subsumption_adds_parent_candidate():
    parent = TaxonomyNode(iri="p", label="Parent equities")
    child = TaxonomyNode(
        iri="c",
        label="Child",
        parent_iri="p",
        superclass_iris=["p"],
    )
    nominated = [Candidate(node=child, similarity=0.0)]
    expanded = _expand_subsumption(nominated, [parent, child])
    iris = {c.node.iri for c in expanded}
    assert "p" in iris
    assert all(c.similarity == 0.0 for c in expanded)


def test_multi_path_subsumption_expand_flag(monkeypatch):
    monkeypatch.setenv("SCUDO_SUBSUMPTION_EXPAND", "on")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    parent = TaxonomyNode(iri="p", label="Parent")
    child = TaxonomyNode(iri="c", label="Child", parent_iri="p", superclass_iris=["p"])
    store = FakeStore(nodes=[parent, child])
    ref = VendorProductRef(vendor="LSEG", product_id="x", name="Child")

    def dense_scorer(_q, cands):
        return [Candidate(node=c.node, similarity=0.9) for c in cands]

    results = multi_path_retrieve(ref, store, dense_scorer=dense_scorer)
    iris = {c.node.iri for c in results}
    assert "p" in iris


def test_p1_flags_default_off(monkeypatch):
    for key in (
        "SCUDO_SUBSUMPTION_DEPTH",
        "SCUDO_SUBSUMPTION_EXPAND",
        "SCUDO_TAXONOMY_TEXT_SHADOW",
    ):
        monkeypatch.delenv(key, raising=False)
    from scudo_mapping_mcp import config as cfg

    s = cfg.Settings.from_env()
    assert s.subsumption_depth == 0
    assert s.subsumption_expand is False
    assert s.taxonomy_text_shadow is False
