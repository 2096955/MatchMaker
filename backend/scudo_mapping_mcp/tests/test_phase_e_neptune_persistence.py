"""Phase E — signal-field persistence on the Neptune (SPARQL) backend.

Same rationale as ``test_phase_e_store_persistence.py``: without the
mds:businessConcept / mds:assetClass / mds:superAssetClass triples the
deterministic assetClass validation (step 4) is silently inert in an Atlas
cutover. Driven through a stubbed SPARQL client — no live Neptune."""

from __future__ import annotations

from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.store import neptune_store as ns_mod


class _RecordingClient:
    def __init__(self):
        self.updates: list[str] = []
        self.select_rows: list[dict] = []

    def query_update(self, sparql):
        self.updates.append(sparql)

    def query_select(self, sparql):
        return self.select_rows


def _probe_store():
    store = ns_mod.NeptuneStore.__new__(ns_mod.NeptuneStore)
    client = _RecordingClient()
    store._client = client
    store._graph_iri = "https://jpmc.example/graph/scudo"
    return store, client


def _signal_node() -> TaxonomyNode:
    return TaxonomyNode(
        iri="https://example.com/cdao/eq-prices",
        label="Equity Prices",
        definition="Customer definition.",
        business_concept="Pricing",
        asset_class="Equity",
        super_asset_class="Securities",
    )


def test_neptune_upsert_writes_signal_triples():
    store, client = _probe_store()
    store.upsert_taxonomy_node(_signal_node())
    assert client.updates
    sparql = client.updates[0]
    assert 'mds:businessConcept "Pricing"' in sparql
    assert 'mds:assetClass "Equity"' in sparql
    assert 'mds:superAssetClass "Securities"' in sparql
    # And the DELETE half clears stale values on re-upsert (idempotence).
    assert "mds:businessConcept ?bc" in sparql
    assert "mds:assetClass ?ac" in sparql
    assert "mds:superAssetClass ?sac" in sparql


def test_neptune_upsert_omits_absent_signal_triples():
    store, client = _probe_store()
    store.upsert_taxonomy_node(TaxonomyNode(iri="https://x.example/n", label="N"))
    sparql = client.updates[0]
    assert 'mds:businessConcept "' not in sparql
    assert 'mds:assetClass "' not in sparql
    assert 'mds:superAssetClass "' not in sparql


def test_neptune_get_taxonomy_node_reads_signals_back():
    store, client = _probe_store()
    client.select_rows = [
        {
            "label": {"value": "Equity Prices"},
            "definition": {"value": "Customer definition."},
            "kind": {"value": "concept"},
            "businessConcept": {"value": "Pricing"},
            "assetClass": {"value": "Equity"},
            "superAssetClass": {"value": "Securities"},
        }
    ]
    node = store.get_taxonomy_node("https://example.com/cdao/eq-prices")
    assert node is not None
    assert node.business_concept == "Pricing"
    assert node.asset_class == "Equity"
    assert node.super_asset_class == "Securities"


def test_neptune_get_taxonomy_node_absent_signals_are_none():
    store, client = _probe_store()
    client.select_rows = [{"label": {"value": "Legacy"}, "kind": {"value": "concept"}}]
    node = store.get_taxonomy_node("https://example.com/cdao/legacy")
    assert node is not None
    assert node.business_concept is None
    assert node.asset_class is None
    assert node.super_asset_class is None


# ── LIST path (SPARQL_LIST_ALL_TAXONOMY → list_taxonomy_nodes) ─────────────
# The BM25/rescore paths iterate the FULL store via list_taxonomy_nodes; if
# only the single-node query carries the signals, a Neptune round-trip
# silently drops them for every consumer of the list path.


def test_neptune_list_template_selects_signal_fields():
    sparql = ns_mod.SPARQL_LIST_ALL_TAXONOMY
    assert "mds:businessConcept ?businessConcept" in sparql
    assert "mds:assetClass ?assetClass" in sparql
    assert "mds:superAssetClass ?superAssetClass" in sparql


def test_neptune_list_taxonomy_nodes_reads_signals_back():
    store, client = _probe_store()
    client.select_rows = [
        {
            "iri": {"value": "https://example.com/cdao/eq-prices"},
            "label": {"value": "Equity Prices"},
            "definition": {"value": "Customer definition."},
            "kind": {"value": "concept"},
            "businessConcept": {"value": "Pricing"},
            "assetClass": {"value": "Equity"},
            "superAssetClass": {"value": "Securities"},
        }
    ]
    nodes = store.list_taxonomy_nodes()
    assert len(nodes) == 1
    assert nodes[0].business_concept == "Pricing"
    assert nodes[0].asset_class == "Equity"
    assert nodes[0].super_asset_class == "Securities"


def test_neptune_list_taxonomy_nodes_absent_signals_are_none():
    store, client = _probe_store()
    client.select_rows = [
        {
            "iri": {"value": "https://example.com/cdao/legacy"},
            "label": {"value": "Legacy"},
            "kind": {"value": "concept"},
        }
    ]
    nodes = store.list_taxonomy_nodes()
    assert len(nodes) == 1
    assert nodes[0].business_concept is None
    assert nodes[0].asset_class is None
    assert nodes[0].super_asset_class is None


# ── clause-coverage guard (Phase B pattern) ────────────────────────────────


def test_every_full_node_taxonomy_select_carries_the_signal_predicates():
    """Future-proofing guard: every module-level SPARQL SELECT template that
    reads a taxonomy node's TEXT surface (skos:definition is the marker for
    "constructs a full TaxonomyNode") must also read the three Phase E
    signal predicates — a new field can't silently miss one read query.
    The iri/label/parent-only placeholder (SPARQL_LIST_TAXONOMY_NODES,
    unused) intentionally stays out of scope."""
    import re

    offenders: list[str] = []
    for name in dir(ns_mod):
        if not name.startswith("SPARQL_"):
            continue
        value = getattr(ns_mod, name)
        if not isinstance(value, str):
            continue
        if not re.search(r"^\s*SELECT\b", value, flags=re.MULTILINE):
            continue
        if "skos:definition" not in value:
            continue  # not a full-TaxonomyNode read
        for predicate in (
            "mds:businessConcept",
            "mds:assetClass",
            "mds:superAssetClass",
        ):
            if predicate not in value:
                offenders.append(f"{name} missing {predicate}")
    assert not offenders, offenders
