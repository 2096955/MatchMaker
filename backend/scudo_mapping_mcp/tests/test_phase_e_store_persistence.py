"""Phase E — the structured matching signals survive the FalkorDB store
round-trip.

Without persistence, the loaded ``asset_class`` never reaches
``run_validations`` in a FalkorDB deployment (seed_taxonomy →
upsert_taxonomy_node drops it; get_taxonomy_node never reads it) — making
step 4 silently inert on the DEFAULT deployed backend, the exact class of
zero-signal gap Phase E step 2 exists to close. Tested through a probe
subclass that stubs the graph IO (same pattern as the smoke suite's
_DenseProbeStore) — no live FalkorDB needed.
"""

from __future__ import annotations

from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.store import falkordb_store as fs_mod


class _RecordingGraph:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))


class _ProbeStore(fs_mod.FalkorDBStore):
    def __init__(self):  # noqa: D401 — skip the FalkorDB connection
        self._graph = _RecordingGraph()
        self._g = self._graph
        self._ro_rows: list = []

    def _ro(self, cypher, params=None):
        return self._ro_rows


def _signal_node() -> TaxonomyNode:
    return TaxonomyNode(
        iri="cdao:eq-prices",
        label="Equity Prices",
        definition="Customer definition.",
        alt_labels=["Stocks"],
        business_concept="Pricing",
        asset_class="Equity",
        super_asset_class="Securities",
    )


def test_upsert_taxonomy_node_persists_signal_fields():
    store = _ProbeStore()
    store.upsert_taxonomy_node(_signal_node())
    merge_calls = [
        (c, p) for c, p in store._graph.calls if "MERGE (t:TaxonomyNode" in c
    ]
    assert merge_calls, "upsert must MERGE the TaxonomyNode"
    cypher, params = merge_calls[0]
    assert "t.business_concept=$business_concept" in cypher
    assert "t.asset_class=$asset_class" in cypher
    assert "t.super_asset_class=$super_asset_class" in cypher
    assert params["business_concept"] == "Pricing"
    assert params["asset_class"] == "Equity"
    assert params["super_asset_class"] == "Securities"


def test_get_taxonomy_node_reads_signal_fields_back():
    store = _ProbeStore()
    store._ro_rows = [
        (
            "cdao:eq-prices",
            "Equity Prices",
            "Customer definition.",
            "Stocks",
            "concept",
            None,
            [],
            [],
            [],
            "Pricing",
            "Equity",
            "Securities",
        )
    ]
    node = store.get_taxonomy_node("cdao:eq-prices")
    assert node is not None
    assert node.business_concept == "Pricing"
    assert node.asset_class == "Equity"
    assert node.super_asset_class == "Securities"


def test_get_taxonomy_node_absent_signals_read_as_none():
    store = _ProbeStore()
    store._ro_rows = [
        (
            "cdao:legacy",
            "Legacy Node",
            "",
            "",
            "concept",
            None,
            [],
            [],
            [],
            "",  # FalkorDB null-ish empties normalise to None
            None,
            "",
        )
    ]
    node = store.get_taxonomy_node("cdao:legacy")
    assert node is not None
    assert node.business_concept is None
    assert node.asset_class is None
    assert node.super_asset_class is None


def test_list_taxonomy_nodes_reads_signal_fields_back():
    store = _ProbeStore()
    store._ro_rows = [
        (
            "cdao:eq-prices",
            "Equity Prices",
            None,
            "Customer definition.",
            "Stocks",
            "concept",
            [],
            [],
            "Pricing",
            "Equity",
            "Securities",
        )
    ]
    nodes = store.list_taxonomy_nodes()
    assert len(nodes) == 1
    assert nodes[0].business_concept == "Pricing"
    assert nodes[0].asset_class == "Equity"
    assert nodes[0].super_asset_class == "Securities"
