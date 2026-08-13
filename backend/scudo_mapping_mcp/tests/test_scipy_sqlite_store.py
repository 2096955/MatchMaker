from __future__ import annotations

import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from scudo_mapping_mcp import matching
from scudo_mapping_mcp import ingest as ingest_module
from scudo_mapping_mcp.store.scipy_sqlite_store import (
    ScipySQLiteStore,
)
from scudo_mapping_mcp.models import (
    ConceptualEdge,
    ConceptualEdgeKind,
    ConceptualNode,
    ConceptualNodeKind,
    ContractTerms,
    PartyProfile,
    TaxonomyNode,
    VendorProductRef,
)
from scudo_mapping_mcp.store import scipy_sqlite_store as store_module
from scudo_mapping_mcp.store.scipy_sqlite_schema import connect


def _nodes() -> list[TaxonomyNode]:
    return [
        TaxonomyNode(
            iri="cdao:root",
            label="Market Data",
            children_iris=["cdao:eq"],
            definition="Root definition",
            alt_labels=["MD"],
            node_kind="class",
            business_concept="Markets",
            asset_class="All",
            super_asset_class="Data",
            temporal_coverage="P10Y",
        ),
        TaxonomyNode(
            iri="cdao:eq",
            label="Equity Prices",
            parent_iri="cdao:root",
            superclass_iris=["cdao:root"],
            definition="Historical equity prices",
            alt_labels=["Stock prices"],
            node_kind="concept",
        ),
        TaxonomyNode(
            iri="cdao:px",
            label="Price Field",
            node_kind="property",
            superproperty_iris=["cdao:base-property"],
        ),
        TaxonomyNode(
            iri="cdao:base-property",
            label="Base Property",
            node_kind="property",
        ),
    ]


def test_taxonomy_roundtrip_replace_and_neighbourhood(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())

    assert store.health()
    assert [n.iri for n in store.list_taxonomy_nodes()] == sorted(
        n.iri for n in _nodes()
    )
    assert store.get_taxonomy_node("cdao:root") == _nodes()[0]
    graph = store.get_ontology_neighbourhood("cdao:eq", max_depth=2, max_nodes=10)
    assert [n.iri for n in graph.nodes] == ["cdao:eq"]
    assert graph.edges == []

    store.replace_taxonomy([TaxonomyNode(iri="cdao:eq", label="Equity Prices")])
    assert [n.iri for n in store.list_taxonomy_nodes()] == ["cdao:eq"]


def test_public_taxonomy_nodes_are_list_backed_warning_free_defensive_copies(
    tmp_path,
):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="EQ-1",
        name="Equity Prices",
    )

    public_nodes = [
        store.get_taxonomy_node("cdao:root"),
        store.list_taxonomy_nodes()[0],
        store.find_similar_products(ref)[0].node,
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for node in public_nodes:
            assert node is not None
            assert isinstance(node.children_iris, list)
            assert isinstance(node.alt_labels, list)
            assert isinstance(node.superclass_iris, list)
            assert isinstance(node.superproperty_iris, list)
            node.model_dump()

    assert not [
        warning
        for warning in caught
        if "Pydantic serializer warnings" in str(warning.message)
        or "Expected `list" in str(warning.message)
    ]

    public_nodes[0].children_iris.append("cdao:forged")
    public_nodes[0].alt_labels.append("forged")

    reread = store.get_taxonomy_node("cdao:root")
    snapshot_node = store._snapshot_manager.capture().nodes["cdao:root"]
    assert "cdao:forged" not in reread.children_iris
    assert "forged" not in reread.alt_labels
    assert "cdao:forged" not in snapshot_node.children_iris
    assert "forged" not in snapshot_node.alt_labels


def test_empty_taxonomy_replacement_is_rejected_without_changing_revision(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    before = store._snapshot_manager.capture()

    with pytest.raises(ValueError, match="empty taxonomy"):
        store.replace_taxonomy([])

    after = store._snapshot_manager.capture()
    assert after.revision == before.revision
    assert after.iris == before.iris
    store.close()
    restarted = ScipySQLiteStore(path)
    assert restarted.health()
    assert restarted._snapshot_manager.capture().iris == before.iris


def test_empty_initial_taxonomy_is_not_ready(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")

    assert not store.health()
    with pytest.raises(ValueError, match="empty taxonomy"):
        store.replace_taxonomy([])


@pytest.mark.parametrize("seed_source", ["loader", "override"])
def test_empty_seed_preserves_populated_taxonomy_and_revision(
    tmp_path, monkeypatch, seed_source
):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    before = store._snapshot_manager.capture()
    monkeypatch.setattr(ingest_module, "get_store", lambda: store)
    if seed_source == "loader":
        monkeypatch.delenv("SCUDO_TAXONOMY_SEED", raising=False)
        monkeypatch.setattr(ingest_module, "load_taxonomy_nodes", lambda _settings: [])
    else:
        empty_fixture = tmp_path / "empty-taxonomy.json"
        empty_fixture.write_text("[]", encoding="utf-8")
        monkeypatch.setenv("SCUDO_TAXONOMY_SEED", str(empty_fixture))

    with pytest.raises(RuntimeError, match="empty taxonomy"):
        ingest_module.seed_taxonomy()

    after = store._snapshot_manager.capture()
    assert after.revision == before.revision
    assert after.iris == before.iris
    store.close()
    restarted = ScipySQLiteStore(path)
    assert restarted.health()
    assert restarted._snapshot_manager.capture().iris == before.iris


def test_scoring_negatives_precedents_rank_and_restart(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="EQ-1",
        name="Equity Prices",
        description="Historical stock prices",
        raw={"ticker": "EQ"},
        source_content_hash="abc",
        source_file_audit_id="audit-1",
        temporal_coverage="2019-2021",
    )
    store.upsert_vendor_product(ref)
    candidates = store.find_similar_products(ref)
    assert candidates[0].node.iri == "cdao:eq"
    raw_similarity = candidates[0].similarity

    store.upsert_precedent(
        ref=ref,
        node=store.get_taxonomy_node("cdao:eq"),
        decision="approve",
        decided_by="reviewer",
        confidence=raw_similarity,
        decided_at_ms=1_700_000_000_001,
    )
    assert store.rank_signals_for("lseg::equity prices") == {"cdao:eq": 1}
    assert store.get_precedent_mapping("LSEG", "EQ-1").source_content_hash == "abc"

    store.close()
    restarted = ScipySQLiteStore(path)
    assert restarted.get_precedent_mapping("LSEG", "EQ-1").mapped_node_iri == "cdao:eq"
    rows = restarted.list_confirmed_precedents()
    assert rows[0]["source_file_audit_id"] == "audit-1"
    assert rows[0]["decided_at_ms"] == 1_700_000_000_001

    restarted.upsert_precedent(
        ref=ref,
        node=restarted.get_taxonomy_node("cdao:eq"),
        decision="reject",
        decided_by="reviewer",
        confidence=0.0,
    )
    assert restarted.get_precedent_mapping("LSEG", "EQ-1") is None
    assert restarted.get_negative_precedents("LSEG", "EQ-1") == ["cdao:eq"]
    assert all(c.node.iri != "cdao:eq" for c in restarted.find_similar_products(ref))


def test_taxonomy_replacement_retires_nodes_without_deleting_decision_audit(
    tmp_path,
):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    approved_ref = VendorProductRef(
        vendor="LSEG",
        product_id="APPROVED",
        name="Equity Prices",
        source_content_hash="approved-hash",
        source_file_audit_id="approved-audit",
    )
    rejected_ref = VendorProductRef(
        vendor="LSEG",
        product_id="REJECTED",
        name="Equity Prices",
        source_content_hash="rejected-hash",
        source_file_audit_id="rejected-audit",
    )
    target = store.get_taxonomy_node("cdao:eq")
    store.upsert_precedent(
        ref=approved_ref,
        node=target,
        decision="approve",
        decided_by="reviewer",
        confidence=0.93,
        decided_at_ms=1_700_000_000_001,
    )
    store.upsert_precedent(
        ref=rejected_ref,
        node=target,
        decision="reject",
        decided_by="reviewer",
        confidence=0.0,
        decided_at_ms=1_700_000_000_002,
    )

    replacement = [
        node.model_copy(update={"children_iris": []})
        if node.iri == "cdao:root"
        else node
        for node in _nodes()
        if node.iri != "cdao:eq"
    ]
    store.replace_taxonomy(replacement)
    store.close()
    restarted = ScipySQLiteStore(path)

    assert restarted.get_taxonomy_node("cdao:eq") is None
    assert "cdao:eq" not in {
        candidate.node.iri
        for candidate in restarted.find_similar_products(approved_ref)
    }
    assert restarted.get_precedent_mapping("LSEG", "APPROVED") is None
    assert restarted.get_negative_precedents("LSEG", "REJECTED") == ["cdao:eq"]
    assert restarted.list_confirmed_precedents() == [
        {
            "vendor": "LSEG",
            "product_id": "APPROVED",
            "product_name": "Equity Prices",
            "description": "",
            "mapped_node_iri": "cdao:eq",
            "mapped_node_label": "Equity Prices",
            "decision": "approve",
            "decided_by": "reviewer",
            "decided_at_ms": 1_700_000_000_001,
            "confidence": 0.93,
            "source_content_hash": "approved-hash",
            "source_file_audit_id": "approved-audit",
        }
    ]
    result = matching.map_vendor_product(approved_ref, store=restarted)
    assert result.status.value == "needs_review"
    assert result.rationale != "precedent"


def test_reintroducing_retired_node_restores_candidate_and_precedent_reuse(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(vendor="LSEG", product_id="P", name="Equity Prices")
    target = store.get_taxonomy_node("cdao:eq")
    store.upsert_precedent(
        ref=ref,
        node=target,
        decision="approve",
        decided_by="reviewer",
        confidence=0.91,
    )
    replacement = [
        node.model_copy(update={"children_iris": []})
        if node.iri == "cdao:root"
        else node
        for node in _nodes()
        if node.iri != target.iri
    ]
    store.replace_taxonomy(replacement)
    assert store.get_precedent_mapping("LSEG", "P") is None

    store.replace_taxonomy(_nodes())
    store.close()
    restarted = ScipySQLiteStore(path)

    assert restarted.get_taxonomy_node("cdao:eq").label == "Equity Prices"
    assert restarted.get_precedent_mapping("LSEG", "P").mapped_node_iri == "cdao:eq"


def test_provisional_precedent_is_excluded_everywhere(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(vendor="LSEG", product_id="P", name="Equity Prices")
    store.upsert_precedent(
        ref=ref,
        node=store.get_taxonomy_node("cdao:eq"),
        decision="approve",
        decided_by="auto",
        confidence=0.95,
        provisional=True,
    )
    assert store.get_precedent_mapping("LSEG", "P") is None
    assert store.list_confirmed_precedents() == []
    assert store.rank_signals_for("lseg::equity prices") == {}


def test_conceptual_full_model_and_edges_roundtrip(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    first = ConceptualNode(
        iri="enrich:policy",
        kind=ConceptualNodeKind.POLICY,
        label="Policy",
        attaches_to_concept_iri="cdao:eq",
        sequence_number=1,
        notation="POL",
        description="rights",
        contract_terms=ContractTerms(status="active", term="P1Y"),
        party_profile=PartyProfile(perm_id="P-1", organization_type="vendor"),
    )
    second = ConceptualNode(
        iri="enrich:party",
        kind=ConceptualNodeKind.PARTY,
        label="Party",
        attaches_to_concept_iri="cdao:eq",
    )
    edge = ConceptualEdge(
        from_iri=first.iri,
        to_iri=second.iri,
        kind=ConceptualEdgeKind.RULE_OBJECT,
        label="object",
    )
    store.upsert_conceptual_node(first)
    store.upsert_conceptual_node(second)
    store.upsert_conceptual_edge(edge)
    graph = store.get_conceptual_graph("cdao:eq")
    assert [n.model_dump() for n in graph.nodes] == [
        second.model_dump(),
        first.model_dump(),
    ]
    assert graph.edges == [edge]


def test_two_instances_and_threads_observe_committed_writes(tmp_path):
    path = tmp_path / "matching.sqlite3"
    first = ScipySQLiteStore(path)
    second = ScipySQLiteStore(path)
    first.replace_taxonomy(_nodes())
    assert second.get_taxonomy_node("cdao:eq") is not None

    refs = [
        VendorProductRef(vendor="LSEG", product_id=f"P-{i}", raw={"i": i})
        for i in range(20)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(first.upsert_vendor_product, refs))
    assert second.health()


def test_concurrent_upserts_merge_after_writer_lock_without_lost_update(
    tmp_path, monkeypatch
):
    path = tmp_path / "matching.sqlite3"
    first = ScipySQLiteStore(path)
    second = ScipySQLiteStore(path)
    first.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    barrier = threading.Barrier(2)
    original_transaction = store_module.write_transaction

    @contextmanager
    def synchronized_transaction(db_path):
        barrier.wait(timeout=5)
        with original_transaction(db_path) as conn:
            yield conn

    monkeypatch.setattr(
        store_module,
        "write_transaction",
        synchronized_transaction,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(
            first.upsert_taxonomy_node,
            TaxonomyNode(iri="cdao:a", label="A"),
        )
        right = pool.submit(
            second.upsert_taxonomy_node,
            TaxonomyNode(iri="cdao:b", label="B"),
        )
        left.result(timeout=10)
        right.result(timeout=10)

    with connect(path) as conn:
        revision = int(
            conn.execute(
                "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
            ).fetchone()[0]
        )
    assert revision == 3
    assert [node.iri for node in first.list_taxonomy_nodes()] == [
        "cdao:a",
        "cdao:b",
        "cdao:root",
    ]
    assert first._snapshot_manager.capture().revision == revision


def test_stale_post_commit_publication_refreshes_and_returns_success(
    tmp_path, monkeypatch
):
    path = tmp_path / "matching.sqlite3"
    first = ScipySQLiteStore(path)
    second = ScipySQLiteStore(path)
    first.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    original_publish = first._snapshot_manager.publish
    advanced = False

    def advance_before_publish(snapshot):
        nonlocal advanced
        if not advanced:
            advanced = True
            second.upsert_taxonomy_node(TaxonomyNode(iri="cdao:b", label="B"))
        original_publish(snapshot)

    monkeypatch.setattr(first._snapshot_manager, "publish", advance_before_publish)
    first.upsert_taxonomy_node(TaxonomyNode(iri="cdao:a", label="A"))

    snapshot = first._snapshot_manager.capture()
    assert snapshot.revision == 3
    assert set(snapshot.iris) == {"cdao:root", "cdao:a", "cdao:b"}


def test_same_store_taxonomy_writes_serialize_through_publication(
    tmp_path, monkeypatch
):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    first_publish_started = threading.Event()
    allow_first_publish = threading.Event()
    second_transaction_started = threading.Event()
    original_publish_or_refresh = store._publish_or_refresh
    original_transaction = store_module.write_transaction
    transaction_count = 0
    count_lock = threading.Lock()

    def blocked_publish(snapshot, *, required_iri):
        if required_iri == "cdao:a":
            first_publish_started.set()
            assert allow_first_publish.wait(timeout=5)
        return original_publish_or_refresh(snapshot, required_iri=required_iri)

    @contextmanager
    def observed_transaction(db_path):
        nonlocal transaction_count
        with count_lock:
            transaction_count += 1
            if transaction_count == 2:
                second_transaction_started.set()
        with original_transaction(db_path) as conn:
            yield conn

    monkeypatch.setattr(store, "_publish_or_refresh", blocked_publish)
    monkeypatch.setattr(store_module, "write_transaction", observed_transaction)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            store.upsert_taxonomy_node,
            TaxonomyNode(iri="cdao:a", label="A"),
        )
        assert first_publish_started.wait(timeout=5)
        second = pool.submit(
            store.upsert_taxonomy_node,
            TaxonomyNode(iri="cdao:b", label="B"),
        )
        assert not second_transaction_started.wait(timeout=0.2)
        allow_first_publish.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert second_transaction_started.is_set()
    assert set(store._snapshot_manager.capture().iris) == {
        "cdao:root",
        "cdao:a",
        "cdao:b",
    }


def test_commit_failure_never_publishes_prospective_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    before = store._snapshot_manager.capture()

    def fail_rebuild(_conn):
        raise RuntimeError("forced write failure")

    monkeypatch.setattr(store, "_rebuild_taxonomy_edges", fail_rebuild)
    with pytest.raises(RuntimeError, match="forced write failure"):
        store.upsert_taxonomy_node(TaxonomyNode(iri="cdao:new", label="New"))

    after = store._snapshot_manager.capture()
    assert after is before
    assert after.revision == 1
    assert "cdao:new" not in after.nodes


def test_bulk_taxonomy_load_executes_constant_query_count(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(
        [
            TaxonomyNode(
                iri=f"cdao:{index}",
                label=str(index),
                parent_iri=f"cdao:{index - 1}" if index else None,
            )
            for index in range(151)
        ]
    )
    with connect(path) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        nodes = store._nodes_from_connection(conn)

    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(nodes) == 151
    assert len(selects) == 2


def test_close_changes_lifecycle_and_blocks_operations(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    assert store.health()
    store.close()
    assert not store.health()
    with pytest.raises(RuntimeError, match="closed"):
        store.list_taxonomy_nodes()


def test_health_detects_missing_table_and_building_revision(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO store_metadata(key, value) VALUES "
            "('taxonomy_building_revision', '1')"
        )
    assert not store.health()
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM store_metadata WHERE key='taxonomy_building_revision'"
        )
        conn.execute("DROP TABLE conceptual_edges")
    assert not store.health()


def test_health_detects_corrupt_integrity_result(tmp_path, monkeypatch):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")

    class _CorruptConnection:
        def execute(self, _statement):
            return self

        def fetchone(self):
            return ("corrupt",)

    class _Context:
        def __enter__(self):
            return _CorruptConnection()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.scipy_sqlite_store.read_connection",
        lambda _path: _Context(),
    )
    assert not store.health()


def test_health_refreshes_snapshot_and_reuses_matching_revision(tmp_path, monkeypatch):
    path = tmp_path / "matching.sqlite3"
    first = ScipySQLiteStore(path)
    second = ScipySQLiteStore(path)
    first.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root")])
    assert second.health()
    current = second._snapshot_manager.current

    def fail_load():
        raise AssertionError("matching revision must not rebuild")

    monkeypatch.setattr(second._snapshot_manager, "_load_taxonomy", fail_load)
    assert second.health()
    assert second._snapshot_manager.current is current

    monkeypatch.undo()
    first.upsert_taxonomy_node(TaxonomyNode(iri="cdao:new", label="New"))
    assert second.health()
    assert second._snapshot_manager.current.revision == 2
    assert "cdao:new" in second._snapshot_manager.current.nodes


def test_health_is_false_when_durable_taxonomy_topology_is_invalid(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(
        [
            TaxonomyNode(iri="cdao:root", label="Root"),
            TaxonomyNode(
                iri="cdao:property",
                label="Property",
                node_kind="property",
            ),
        ]
    )
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO taxonomy_edges(from_iri, to_iri, edge_kind) "
            "VALUES ('cdao:property', 'cdao:root', 'parent')"
        )
        conn.execute(
            "UPDATE store_metadata SET value=CAST(value AS INTEGER)+1 "
            "WHERE key='taxonomy_revision'"
        )
    assert not store.health()


def test_invalid_replacement_preserves_taxonomy_and_revision(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    with connect(path) as conn:
        before = conn.execute(
            "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
        ).fetchone()[0]

    invalid = [
        TaxonomyNode(
            iri="cdao:bad",
            label="Bad",
            parent_iri="cdao:missing",
        )
    ]
    with pytest.raises(ValueError, match="missing"):
        store.replace_taxonomy(invalid)

    assert [node.iri for node in store.list_taxonomy_nodes()] == sorted(
        node.iri for node in _nodes()
    )
    with connect(path) as conn:
        after = conn.execute(
            "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
        ).fetchone()[0]
    assert after == before


@pytest.mark.parametrize(
    "nodes, message",
    [
        (
            [TaxonomyNode(iri="cdao:self", label="Self", parent_iri="cdao:self")],
            "self-loop",
        ),
        (
            [
                TaxonomyNode(iri="cdao:a", label="A", parent_iri="cdao:b"),
                TaxonomyNode(iri="cdao:b", label="B", parent_iri="cdao:a"),
            ],
            "cycle",
        ),
        (
            [
                TaxonomyNode(
                    iri="cdao:concept",
                    label="Concept",
                    parent_iri="cdao:property",
                ),
                TaxonomyNode(
                    iri="cdao:property", label="Property", node_kind="property"
                ),
            ],
            "typed",
        ),
    ],
)
def test_topology_validation_rejects_invalid_relations(tmp_path, nodes, message):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    with pytest.raises(ValueError, match=message):
        store.replace_taxonomy(nodes)


def test_relation_cardinality_is_bounded(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    parents = [
        TaxonomyNode(iri=f"cdao:p{index}", label=f"P{index}") for index in range(26)
    ]
    child = TaxonomyNode(
        iri="cdao:child",
        label="Child",
        superclass_iris=[node.iri for node in parents],
    )
    with pytest.raises(ValueError, match="cardinality"):
        store.replace_taxonomy([child, *parents])


@pytest.mark.parametrize("child_count", [26, 64])
def test_high_degree_parent_snapshot_is_exact_after_restart(tmp_path, child_count):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    children = [
        TaxonomyNode(
            iri=f"cdao:child-{index:02d}",
            label=f"Child {index}",
            parent_iri="cdao:root",
        )
        for index in range(child_count)
    ]
    expected_iris = tuple(sorted(node.iri for node in children))

    store.replace_taxonomy([TaxonomyNode(iri="cdao:root", label="Root"), *children])
    before = store._snapshot_manager.capture()
    assert store.health()
    assert before.nodes["cdao:root"].children_iris == expected_iris
    assert (
        before.class_concept_parent.neighbors(
            "cdao:root",
            direction="downward",
        )
        == expected_iris
    )
    store.close()

    restarted = ScipySQLiteStore(path)
    after = restarted._snapshot_manager.capture()
    assert restarted.health()
    assert after.iris == before.iris
    assert after.nodes["cdao:root"].children_iris == expected_iris
    assert (
        after.class_concept_parent.neighbors(
            "cdao:root",
            direction="downward",
        )
        == expected_iris
    )


def test_upsert_validates_complete_result_before_mutation(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    with pytest.raises(ValueError, match="missing"):
        store.upsert_taxonomy_node(
            TaxonomyNode(
                iri="cdao:new",
                label="New",
                parent_iri="cdao:not-there",
            )
        )
    assert store.get_taxonomy_node("cdao:new") is None


def test_upsert_reparents_node_and_removes_old_derived_child_after_restart(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(
        [
            TaxonomyNode(iri="cdao:a", label="A", children_iris=["cdao:c"]),
            TaxonomyNode(iri="cdao:b", label="B"),
            TaxonomyNode(iri="cdao:c", label="C", parent_iri="cdao:a"),
        ]
    )

    store.upsert_taxonomy_node(
        TaxonomyNode(iri="cdao:c", label="C", parent_iri="cdao:b")
    )

    snapshot = store._snapshot_manager.capture()
    assert snapshot.nodes["cdao:a"].children_iris == ()
    assert snapshot.nodes["cdao:b"].children_iris == ("cdao:c",)
    assert snapshot.class_concept_parent.neighbors(
        "cdao:c",
        direction="upward",
    ) == ("cdao:b",)
    store.close()

    restarted = ScipySQLiteStore(path)
    assert restarted.get_taxonomy_node("cdao:a").children_iris == []
    assert restarted.get_taxonomy_node("cdao:b").children_iris == ["cdao:c"]
    assert restarted.get_taxonomy_node("cdao:c").parent_iri == "cdao:b"


def test_taxonomy_reads_are_derived_from_normalized_edges(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    with connect(path) as conn:
        conn.execute(
            "UPDATE taxonomy_nodes SET payload_json=? WHERE iri='cdao:root'",
            (TaxonomyNode(iri="cdao:root", label="Market Data").model_dump_json(),),
        )

    root = store.get_taxonomy_node("cdao:root")
    child = store.get_taxonomy_node("cdao:eq")
    prop = store.get_taxonomy_node("cdao:px")
    assert root.children_iris == ["cdao:eq"]
    assert child.parent_iri == "cdao:root"
    assert child.superclass_iris == ["cdao:root"]
    assert prop.superproperty_iris == ["cdao:base-property"]


def test_override_replaces_positive_and_approve_clears_negative(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(vendor="LSEG", product_id="O", name="Same")
    eq = store.get_taxonomy_node("cdao:eq")
    root = store.get_taxonomy_node("cdao:root")
    store.upsert_precedent(
        ref=ref,
        node=eq,
        decision="approve",
        decided_by="u",
        confidence=0.9,
    )
    store.upsert_precedent(
        ref=ref,
        node=root,
        decision="override",
        decided_by="u",
        confidence=0.8,
    )
    assert store.get_precedent_mapping("lseg", "O").mapped_node_iri == "cdao:root"
    assert len(store.list_confirmed_precedents()) == 1
    store.upsert_precedent(
        ref=ref,
        node=eq,
        decision="reject",
        decided_by="u",
        confidence=0.0,
    )
    store.upsert_precedent(
        ref=ref,
        node=eq,
        decision="approve",
        decided_by="u",
        confidence=0.9,
    )
    assert store.get_negative_precedents("LSEG", "O") == []


@pytest.mark.parametrize(
    ("decision", "provisional"),
    [("approve", False), ("reject", False), ("approve", True)],
)
def test_new_decision_rejects_inactive_target_without_audit_writes(
    tmp_path, decision, provisional
):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    target = store.get_taxonomy_node("cdao:eq")
    store.replace_taxonomy(
        [
            node.model_copy(update={"children_iris": []})
            if node.iri == "cdao:root"
            else node
            for node in _nodes()
            if node.iri != target.iri
        ]
    )
    ref = VendorProductRef(vendor="LSEG", product_id="INACTIVE", name="Inactive")

    with pytest.raises(ValueError, match="active taxonomy"):
        store.upsert_precedent(
            ref=ref,
            node=target,
            decision=decision,
            decided_by="reviewer",
            confidence=0.5,
            provisional=provisional,
        )

    with connect(path) as conn:
        vendor_count = conn.execute(
            "SELECT COUNT(*) FROM vendor_products WHERE product_id='INACTIVE'"
        ).fetchone()[0]
        positive_count = conn.execute(
            "SELECT COUNT(*) FROM positive_precedents WHERE product_id='INACTIVE'"
        ).fetchone()[0]
        negative_count = conn.execute(
            "SELECT COUNT(*) FROM negative_precedents WHERE product_id='INACTIVE'"
        ).fetchone()[0]
    assert (vendor_count, positive_count, negative_count) == (0, 0, 0)


def test_decision_waits_for_retirement_then_rejects_and_reintroduction_succeeds(
    tmp_path, monkeypatch
):
    path = tmp_path / "matching.sqlite3"
    taxonomy_store = ScipySQLiteStore(path)
    decision_store = ScipySQLiteStore(path)
    taxonomy_store.replace_taxonomy(_nodes())
    target = taxonomy_store.get_taxonomy_node("cdao:eq")
    replacement = [
        node.model_copy(update={"children_iris": []})
        if node.iri == "cdao:root"
        else node
        for node in _nodes()
        if node.iri != target.iri
    ]
    transaction_started = threading.Event()
    release_replacement = threading.Event()
    original_write = taxonomy_store._write_taxonomy_node
    blocked = False

    def block_after_lock(conn, node):
        nonlocal blocked
        original_write(conn, node)
        if not blocked:
            blocked = True
            transaction_started.set()
            assert release_replacement.wait(timeout=5)

    monkeypatch.setattr(taxonomy_store, "_write_taxonomy_node", block_after_lock)
    replacement_thread = threading.Thread(
        target=lambda: taxonomy_store.replace_taxonomy(replacement)
    )
    replacement_thread.start()
    assert transaction_started.wait(timeout=5)
    ref = VendorProductRef(vendor="LSEG", product_id="RACE", name="Race")
    decision_result = {}

    def decide():
        try:
            decision_store.upsert_precedent(
                ref=ref,
                node=target,
                decision="approve",
                decided_by="reviewer",
                confidence=0.9,
            )
        except Exception as exc:
            decision_result["error"] = exc

    decision_thread = threading.Thread(target=decide)
    decision_thread.start()
    assert decision_thread.is_alive()
    release_replacement.set()
    replacement_thread.join(timeout=5)
    decision_thread.join(timeout=5)

    assert isinstance(decision_result.get("error"), ValueError)
    assert "active taxonomy" in str(decision_result["error"])
    assert decision_store.get_precedent_mapping("LSEG", "RACE") is None

    monkeypatch.undo()
    taxonomy_store.replace_taxonomy(_nodes())
    decision_store.upsert_precedent(
        ref=ref,
        node=decision_store.get_taxonomy_node("cdao:eq"),
        decision="approve",
        decided_by="reviewer",
        confidence=0.9,
    )
    assert decision_store.get_precedent_mapping("LSEG", "RACE") is not None


def test_conceptual_node_rejects_inactive_attachment_without_write(tmp_path):
    path = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(path)
    store.replace_taxonomy(_nodes())
    store.replace_taxonomy(
        [
            node.model_copy(update={"children_iris": []})
            if node.iri == "cdao:root"
            else node
            for node in _nodes()
            if node.iri != "cdao:eq"
        ]
    )
    conceptual = ConceptualNode(
        iri="enrich:inactive",
        kind=ConceptualNodeKind.POLICY,
        label="Inactive policy",
        attaches_to_concept_iri="cdao:eq",
    )

    with pytest.raises(ValueError, match="active taxonomy"):
        store.upsert_conceptual_node(conceptual)

    with connect(path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM conceptual_nodes WHERE iri='enrich:inactive'"
            ).fetchone()[0]
            == 0
        )


def test_conceptual_write_waits_for_retirement_then_rejects(tmp_path, monkeypatch):
    path = tmp_path / "matching.sqlite3"
    taxonomy_store = ScipySQLiteStore(path)
    conceptual_store = ScipySQLiteStore(path)
    taxonomy_store.replace_taxonomy(_nodes())
    replacement = [
        node.model_copy(update={"children_iris": []})
        if node.iri == "cdao:root"
        else node
        for node in _nodes()
        if node.iri != "cdao:eq"
    ]
    transaction_started = threading.Event()
    release_replacement = threading.Event()
    original_write = taxonomy_store._write_taxonomy_node
    blocked = False

    def block_after_lock(conn, node):
        nonlocal blocked
        original_write(conn, node)
        if not blocked:
            blocked = True
            transaction_started.set()
            assert release_replacement.wait(timeout=5)

    monkeypatch.setattr(taxonomy_store, "_write_taxonomy_node", block_after_lock)
    replacement_thread = threading.Thread(
        target=lambda: taxonomy_store.replace_taxonomy(replacement)
    )
    replacement_thread.start()
    assert transaction_started.wait(timeout=5)
    result = {}

    def write_conceptual():
        try:
            conceptual_store.upsert_conceptual_node(
                ConceptualNode(
                    iri="enrich:race",
                    kind=ConceptualNodeKind.POLICY,
                    label="Race policy",
                    attaches_to_concept_iri="cdao:eq",
                )
            )
        except Exception as exc:
            result["error"] = exc

    conceptual_thread = threading.Thread(target=write_conceptual)
    conceptual_thread.start()
    assert conceptual_thread.is_alive()
    release_replacement.set()
    replacement_thread.join(timeout=5)
    conceptual_thread.join(timeout=5)

    assert isinstance(result.get("error"), ValueError)
    assert "active taxonomy" in str(result["error"])
    with connect(path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM conceptual_nodes WHERE iri='enrich:race'"
            ).fetchone()[0]
            == 0
        )


def test_candidate_filter_clamps_and_preserves_raw_similarity(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    nodes = [TaxonomyNode(iri=f"cdao:{index:02d}", label="Same") for index in range(30)]
    store.replace_taxonomy(nodes)
    ref = VendorProductRef(vendor="LSEG", product_id="F", name="Same")
    candidates = store.find_similar_products(
        ref,
        max_results=999,
        candidate_filter=lambda candidate: candidate.node.iri != "cdao:00",
    )
    assert len(candidates) == 25
    assert candidates[0].node.iri == "cdao:01"
    assert all(candidate.similarity == 1.0 for candidate in candidates)


def test_rank_boost_changes_order_not_raw_similarity(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(
        [
            TaxonomyNode(iri="cdao:a", label="Same"),
            TaxonomyNode(iri="cdao:z", label="Same"),
        ]
    )
    approved = VendorProductRef(vendor="LSEG", product_id="A", name="Same")
    store.upsert_precedent(
        ref=approved,
        node=store.get_taxonomy_node("cdao:z"),
        decision="approve",
        decided_by="u",
        confidence=1.0,
    )
    store.upsert_precedent(
        ref=VendorProductRef(vendor="LSEG", product_id="A2", name="Same"),
        node=store.get_taxonomy_node("cdao:z"),
        decision="approve",
        decided_by="u",
        confidence=1.0,
    )
    sibling = VendorProductRef(vendor="lseg", product_id="B", name="Same")
    candidates = store.find_similar_products(sibling)
    assert [candidate.node.iri for candidate in candidates] == [
        "cdao:z",
        "cdao:a",
    ]
    assert [candidate.similarity for candidate in candidates] == [1.0, 1.0]


def test_unboosted_scoring_ties_are_deterministic_by_iri(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(
        [
            TaxonomyNode(iri="cdao:z", label="Same"),
            TaxonomyNode(iri="cdao:a", label="Same"),
        ]
    )
    ref = VendorProductRef(vendor="LSEG", product_id="T", name="Same")
    assert [candidate.node.iri for candidate in store.find_similar_products(ref)] == [
        "cdao:a",
        "cdao:z",
    ]


def test_neighbourhood_depth_and_node_caps_are_deterministic(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    nodes = [
        TaxonomyNode(
            iri=f"cdao:{index}",
            label=str(index),
            parent_iri=f"cdao:{index - 1}" if index else None,
        )
        for index in range(6)
    ]
    store.replace_taxonomy(nodes)
    graph = store.get_ontology_neighbourhood("cdao:0", max_depth=1, max_nodes=1000)
    assert [node.iri for node in graph.nodes] == ["cdao:0", "cdao:1"]
    assert graph.edges == [("cdao:0", "cdao:1")]


def test_neighbourhood_uses_only_canonical_parent_child_edges(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(
        [
            TaxonomyNode(
                iri="cdao:child",
                label="Child",
                parent_iri="cdao:root",
                superclass_iris=["cdao:advisory"],
            ),
            TaxonomyNode(iri="cdao:root", label="Root"),
            TaxonomyNode(iri="cdao:advisory", label="Advisory"),
        ]
    )

    graph = store.get_ontology_neighbourhood("cdao:advisory", max_depth=2)
    assert [node.iri for node in graph.nodes] == ["cdao:advisory"]
    assert graph.edges == []


def test_conceptual_upserts_update_and_clamp_deterministically(tmp_path):
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    for index in range(4):
        node = ConceptualNode(
            iri=f"enrich:{index}",
            kind=ConceptualNodeKind.PARTY,
            label=f"Party {index}",
            attaches_to_concept_iri="cdao:eq",
        )
        store.upsert_conceptual_node(node)
    store.upsert_conceptual_node(
        ConceptualNode(
            iri="enrich:0",
            kind=ConceptualNodeKind.PARTY,
            label="Updated",
            attaches_to_concept_iri="cdao:eq",
        )
    )
    graph = store.get_conceptual_graph("cdao:eq", max_nodes=2)
    assert [(node.iri, node.label) for node in graph.nodes] == [
        ("enrich:0", "Updated"),
        ("enrich:1", "Party 1"),
    ]
