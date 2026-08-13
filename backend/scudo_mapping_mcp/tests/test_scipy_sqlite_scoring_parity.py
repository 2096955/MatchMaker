from __future__ import annotations


from scudo_mapping_mcp import opus_dense
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.store.falkordb_store import _jaro_winkler
from scudo_mapping_mcp.store.memory_store import MemoryStore
from scudo_mapping_mcp.store.retrieval_scoring import score_candidates
from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore
from scudo_mapping_mcp.taxonomy_text import taxonomy_dense_text


def _nodes() -> list[TaxonomyNode]:
    return [
        TaxonomyNode(
            iri="cdao:a",
            label="Equity Prices",
            definition="Historical stock prices",
            alt_labels=["Stocks"],
        ),
        TaxonomyNode(
            iri="cdao:b",
            label="Equity Prices",
            definition="Historical stock prices",
            alt_labels=["Stocks"],
        ),
        TaxonomyNode(
            iri="cdao:c",
            label="Bond Reference",
            definition="Fixed income identifiers",
        ),
        TaxonomyNode(
            iri="cdao:d",
            label="Commodity Curve",
            definition="Energy forward curve",
        ),
    ]


def _stores(tmp_path):
    memory = MemoryStore(_nodes())
    sqlite = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    sqlite.replace_taxonomy(_nodes())
    return memory, sqlite


def _shape(candidates):
    return [(candidate.node.iri, candidate.similarity) for candidate in candidates]


def test_jaro_scoring_is_exactly_equal_for_ties_and_negative_scores(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    memory, sqlite = _stores(tmp_path)
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="EQ-1",
        name="Equity Prices",
        description="Historical stock prices",
    )

    assert _shape(memory.find_similar_products(ref)) == _shape(
        sqlite.find_similar_products(ref)
    )


def test_shared_scoring_matches_independent_golden_and_falkor_dense_helper(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    memory, sqlite = _stores(tmp_path)
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="GOLDEN",
        name="Equity Prices",
        description="Historical stock prices",
    )
    expected = [
        ("cdao:b", 0.8703),
        ("cdao:a", 0.8703),
        ("cdao:d", 0.5456),
    ]
    kwargs = {
        "ref": ref,
        "max_results": 10,
        "min_similarity": 0.5,
        "candidate_filter": lambda candidate: candidate.node.iri != "cdao:c",
    }
    direct = score_candidates(
        store=memory,
        nodes=_nodes(),
        rejected_iris=set(),
        boosts={"cdao:b": 2},
        **kwargs,
    )
    assert _shape(direct) == expected
    assert (
        round(
            _jaro_winkler(
                f"{ref.name} {ref.description}",
                taxonomy_dense_text(_nodes()[0]),
            ),
            4,
        )
        == direct[0].similarity
    )

    for store in (memory, sqlite):
        for index in range(2):
            approved = VendorProductRef(
                vendor="LSEG",
                product_id=f"APPROVED-{index}",
                name=ref.name,
                description=ref.description,
            )
            store.upsert_precedent(
                ref=approved,
                node=store.get_taxonomy_node("cdao:b"),
                decision="approve",
                decided_by="reviewer",
                confidence=1.0,
            )
        assert _shape(store.find_similar_products(**kwargs)) == expected


def test_filters_threshold_and_caps_are_exactly_equal(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    memory, sqlite = _stores(tmp_path)
    ref = VendorProductRef(vendor="LSEG", product_id="EQ-2", name="Equity")
    keep = lambda candidate: candidate.node.iri not in {"cdao:a", "cdao:d"}

    assert _shape(
        memory.find_similar_products(
            ref,
            max_results=2,
            min_similarity=0.2,
            candidate_filter=keep,
        )
    ) == _shape(
        sqlite.find_similar_products(
            ref,
            max_results=2,
            min_similarity=0.2,
            candidate_filter=keep,
        )
    )


def test_negative_precedents_and_rank_boosts_preserve_raw_similarity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    memory, sqlite = _stores(tmp_path)
    approved = VendorProductRef(
        vendor="LSEG",
        product_id="APPROVED",
        name="Equity Prices",
    )
    rejected = VendorProductRef(
        vendor="LSEG",
        product_id="QUERY",
        name="Equity Prices",
        description="Historical stock prices",
    )
    for store in (memory, sqlite):
        store.upsert_precedent(
            ref=approved,
            node=store.get_taxonomy_node("cdao:b"),
            decision="approve",
            decided_by="reviewer",
            confidence=1.0,
        )
        store.upsert_precedent(
            ref=rejected,
            node=store.get_taxonomy_node("cdao:c"),
            decision="reject",
            decided_by="reviewer",
            confidence=0.0,
        )
        store.upsert_vendor_product(
            VendorProductRef(
                vendor="LSEG",
                product_id="APPROVED",
                name="Equity Prices",
            )
        )

    memory_result = memory.find_similar_products(rejected)
    sqlite_result = sqlite.find_similar_products(rejected)
    assert _shape(memory_result) == _shape(sqlite_result)
    assert all(candidate.node.iri != "cdao:c" for candidate in memory_result)
    assert memory_result[0].similarity == 0.8703


def test_opus_injected_scores_use_same_path_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    scores = {
        "Equity Prices": 0.75,
        "Bond Reference": 0.1,
        "Commodity Curve": 0.9,
    }

    def stub_score(*, candidate_label, **_kwargs):
        return scores[candidate_label]

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.opus_dense.opus_dense_score",
        stub_score,
    )
    monkeypatch.setattr(
        "scudo_mapping_mcp.store.scipy_sqlite_store.opus_dense.opus_dense_score",
        stub_score,
    )
    memory, sqlite = _stores(tmp_path)
    ref = VendorProductRef(vendor="ICE", product_id="O-1", name="Anything")

    assert _shape(memory.find_similar_products(ref)) == _shape(
        sqlite.find_similar_products(ref)
    )
    assert _shape(memory.find_similar_products(ref)) == [
        ("cdao:d", 0.9),
        ("cdao:a", 0.75),
        ("cdao:b", 0.75),
        ("cdao:c", 0.1),
    ]


def test_opus_calls_are_bounded_to_top_25_lexical_nominees(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    nodes = [
        TaxonomyNode(
            iri=f"cdao:{index:03d}",
            label=f"Generic Node {index}",
            definition="unrelated",
        )
        for index in range(151)
    ]
    nodes[-1] = TaxonomyNode(
        iri="cdao:lexical",
        label="Equity Prices",
        definition="Historical stock prices",
    )
    calls = []

    def stub_score(*, candidate_label, **_kwargs):
        calls.append(candidate_label)
        return 0.91 if candidate_label == "Equity Prices" else 0.1

    memory = MemoryStore(nodes)
    sqlite = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    sqlite.replace_taxonomy(nodes)
    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.opus_dense.opus_dense_score",
        stub_score,
    )
    monkeypatch.setattr(
        "scudo_mapping_mcp.store.scipy_sqlite_store.opus_dense.opus_dense_score",
        stub_score,
    )
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="OPUS",
        name="Equity Prices",
        description="Historical stock prices",
    )

    for store in (memory, sqlite):
        calls.clear()
        result = store.find_similar_products(ref)
        assert len(calls) <= 25
        assert "Equity Prices" in calls
        assert result[0].node.iri == "cdao:lexical"
        assert result[0].similarity == 0.91


def test_opus_multi_path_flag_delegates_with_negatives_filter_and_raw_score(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "1")
    nodes = [
        TaxonomyNode(iri=f"cdao:{index:03d}", label=f"Node {index}")
        for index in range(40)
    ]
    nodes.append(TaxonomyNode(iri="cdao:best", label="Equity Prices"))
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(nodes)
    ref = VendorProductRef(vendor="LSEG", product_id="QUERY", name="Equity Prices")
    store.upsert_precedent(
        ref=ref,
        node=store.get_taxonomy_node("cdao:000"),
        decision="reject",
        decided_by="reviewer",
        confidence=0.0,
    )
    calls = []

    def scorer(_query, survivors):
        calls.extend(candidate.node.iri for candidate in survivors)
        return [
            candidate.model_copy(
                update={
                    "similarity": (0.94 if candidate.node.iri == "cdao:best" else 0.4)
                }
            )
            for candidate in survivors
        ]

    monkeypatch.setattr(opus_dense, "make_opus_dense_scorer", lambda **_kwargs: scorer)
    results = store.find_similar_products(
        ref,
        candidate_filter=lambda candidate: candidate.node.iri != "cdao:001",
    )

    assert 0 < len(calls) <= 25
    assert "cdao:000" not in calls
    assert "cdao:001" not in calls
    assert results[0].node.iri == "cdao:best"
    assert results[0].similarity == 0.94


def test_scoring_reads_one_snapshot_even_when_revision_changes_mid_score(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    store = ScipySQLiteStore(tmp_path / "matching.sqlite3")
    store.replace_taxonomy(_nodes())
    captured = store._snapshot_manager.capture()
    original_capture = store._snapshot_manager.capture
    changed = False

    def replace_after_capture():
        nonlocal changed
        if not changed:
            changed = True
            monkeypatch.setattr(
                store._snapshot_manager,
                "capture",
                original_capture,
            )
            store.replace_taxonomy([TaxonomyNode(iri="cdao:new", label="New")])
        return captured

    monkeypatch.setattr(store._snapshot_manager, "capture", replace_after_capture)
    result = store.find_similar_products(
        VendorProductRef(vendor="LSEG", product_id="S", name="Equity Prices")
    )

    assert {candidate.node.iri for candidate in result} == {
        "cdao:a",
        "cdao:b",
        "cdao:c",
        "cdao:d",
    }
