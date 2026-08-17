from __future__ import annotations

import threading

import pytest


from scudo_mapping_mcp import opus_dense
from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.store.falkordb_store import _jaro_winkler
from scudo_mapping_mcp.store.memory_store import MemoryStore
from scudo_mapping_mcp.store.retrieval_scoring import score_candidates
from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore
from scudo_mapping_mcp.taxonomy_text import taxonomy_dense_text


@pytest.fixture(autouse=True)
def _isolate_breaker_state():
    """The Bedrock breaker is process-global.

    Several tests here deliberately fail model calls, which trips it — and a
    tripped breaker changes the arm every LATER test runs on, in this file and
    others. That is the exact cross-test leak that previously made a store test
    fail only inside the full suite, so isolate it here rather than rediscover
    it in CI.
    """
    saved = (
        opus_dense._breaker_failures,
        opus_dense._breaker_opened_at,
        opus_dense._breaker_probe_inflight,
        opus_dense._breaker_probe_started_at,
        opus_dense._breaker_generation,
        opus_dense._breaker_probe_owner,
    )
    yield
    (
        opus_dense._breaker_failures,
        opus_dense._breaker_opened_at,
        opus_dense._breaker_probe_inflight,
        opus_dense._breaker_probe_started_at,
        opus_dense._breaker_generation,
        opus_dense._breaker_probe_owner,
    ) = saved


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


# ── All-or-nothing dense scoring (2026-08-15) ──────────────────────────────
#
# A candidate list must carry exactly ONE similarity scale. Before this
# contract, workers shared a process-global circuit breaker, so which
# candidates got an Opus score and which silently fell back to Jaro-Winkler
# depended on thread interleaving — and a review measured that flipping the
# published band (0.84/pass serial vs 0.77/borderline concurrent). Comparing
# scores from two different scales in one ranking is the real defect; the
# timing was only how it surfaced.


def _jaro_baseline(tmp_path_factory_dir, ref):
    """Expected shape when the whole batch is scored by Jaro-Winkler."""
    memory = MemoryStore(_nodes())
    sqlite = ScipySQLiteStore(tmp_path_factory_dir / "baseline.sqlite3")
    sqlite.replace_taxonomy(_nodes())
    return _shape(memory.find_similar_products(ref)), _shape(
        sqlite.find_similar_products(ref)
    )


def test_opus_batch_uses_model_scores_only_when_every_nominee_succeeds(
    tmp_path, monkeypatch
):
    """A fully successful batch keeps every model score."""
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    model_scores = {
        "Equity Prices": 0.71,
        "Bond Reference": 0.62,
        "Commodity Curve": 0.93,
    }

    def strict(*, candidate_label, **_kwargs):
        return model_scores[candidate_label]

    for module in ("memory_store", "scipy_sqlite_store"):
        monkeypatch.setattr(
            f"scudo_mapping_mcp.store.{module}.opus_dense.opus_dense_score",
            strict,
        )
    memory, sqlite = _stores(tmp_path)
    ref = VendorProductRef(vendor="ICE", product_id="O-2", name="Anything")

    memory_shape = _shape(memory.find_similar_products(ref))
    assert memory_shape == _shape(sqlite.find_similar_products(ref))
    assert {score for _iri, score in memory_shape} <= set(model_scores.values())


def test_one_opus_failure_discards_the_whole_model_batch(tmp_path, monkeypatch):
    """One failure must discard the successes from the SAME batch.

    This is the regression that matters: previously the failing candidate fell
    back to Jaro-Winkler on its own while its siblings kept their model scores,
    so one list held two incomparable scales.
    """
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    ref = VendorProductRef(vendor="ICE", product_id="O-3", name="Anything")

    # Baseline: what a full Jaro-Winkler batch produces for this fixture.
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    baseline_memory, baseline_sqlite = _jaro_baseline(tmp_path, ref)

    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    succeeded: set[float] = set()

    def flaky(*, candidate_label, **_kwargs):
        if candidate_label == "Bond Reference":
            raise RuntimeError("bedrock refused this candidate")
        value = 0.99 if candidate_label == "Commodity Curve" else 0.88
        succeeded.add(value)
        return value

    for module in ("memory_store", "scipy_sqlite_store"):
        monkeypatch.setattr(
            f"scudo_mapping_mcp.store.{module}.opus_dense.opus_dense_score",
            flaky,
        )
    memory, sqlite = _stores(tmp_path / "attempt")
    memory_shape = _shape(memory.find_similar_products(ref))
    sqlite_shape = _shape(sqlite.find_similar_products(ref))

    assert memory_shape == baseline_memory
    assert sqlite_shape == baseline_sqlite
    assert all(score not in succeeded for _iri, score in memory_shape)


def _assert_zero_model_calls(monkeypatch, product_id: str) -> None:
    calls: list[str] = []

    def counting(*, candidate_label, **_kwargs):
        calls.append(candidate_label)
        return 0.9

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.opus_dense.opus_dense_score",
        counting,
    )
    store = MemoryStore(_nodes())
    store.find_similar_products(
        VendorProductRef(vendor="ICE", product_id=product_id, name="Anything")
    )
    assert calls == []


def test_result_is_identical_under_opposite_completion_orders(tmp_path, monkeypatch):
    """The published shape must not depend on which worker finishes first.

    This is the defect the all-or-nothing contract exists to kill: a reviewer
    measured 0.84/pass serially and 0.77/borderline concurrently on identical
    inputs, purely from thread interleaving.
    """
    ref = VendorProductRef(vendor="ICE", product_id="O-4", name="Anything")

    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    baseline, _ = _jaro_baseline(tmp_path / "base", ref)

    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")

    def _run(fail_first: bool):
        gate = threading.Event()

        def ordered(*, candidate_label, **_kwargs):
            if candidate_label == "Bond Reference":
                if not fail_first:
                    gate.wait(timeout=2)
                raise RuntimeError("model refused")
            if fail_first:
                gate.wait(timeout=2)
            gate.set()
            return 0.97

        for module in ("memory_store", "scipy_sqlite_store"):
            monkeypatch.setattr(
                f"scudo_mapping_mcp.store.{module}.opus_dense.opus_dense_score",
                ordered,
            )
        store = MemoryStore(_nodes())
        return _shape(store.find_similar_products(ref))

    run_a = _run(fail_first=True)
    run_b = _run(fail_first=False)
    assert run_a == run_b == baseline


def test_open_breaker_makes_zero_model_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    # Breaker state is restored by the autouse _isolate_breaker_state fixture,
    # so this can trip it freely. (An earlier version restored it by hand AND
    # carried a no-op monkeypatch.setattr that set the value to itself.)
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    _assert_zero_model_calls(monkeypatch, "O-5")


def test_fallback_off_open_breaker_raises_before_model_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    calls: list[str] = []

    def counting(*, candidate_label, **_kwargs):
        calls.append(candidate_label)
        return 0.9

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.opus_dense.opus_dense_score",
        counting,
    )
    store = MemoryStore(_nodes())

    with pytest.raises(RuntimeError, match="circuit.*open"):
        store.find_similar_products(
            VendorProductRef(vendor="ICE", product_id="O-5-loud", name="Anything")
        )
    assert calls == []


def test_fallback_off_open_breaker_raises_from_multi_path_scorer_before_model_calls(
    monkeypatch,
):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "1")
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    calls: list[str] = []

    def counting(*_args, **_kwargs):
        calls.append("called")
        return 0.9

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", counting)
    scorer = opus_dense.make_opus_dense_scorer()
    survivors = [Candidate(node=node, similarity=0.0) for node in _nodes()]

    with pytest.raises(RuntimeError, match="circuit.*open"):
        scorer("Anything", survivors)
    assert calls == []


def test_jaro_configured_mode_makes_zero_model_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    _assert_zero_model_calls(monkeypatch, "O-6")

    def counting(*, candidate_label, **_kwargs):
        calls.append(candidate_label)
        return 0.9


def test_network_level_failure_cannot_produce_mixed_scales(tmp_path, monkeypatch):
    """Fail at the NETWORK seam, which is what a dead key actually does.

    Regression for a real defect in the first cut of all-or-nothing scoring:
    the batch called `opus_dense_score`, which makes its OWN per-candidate
    fallback decision, so half the candidates kept model scores and half got
    Jaro-Winkler — measured [1.0, 0.9333, 0.91, 0.91] with 0.91 the model
    value. Scripting the injected scorer hid this; only failing the real
    network seam exposes it.
    """
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    model_value = 0.91
    calls = {"n": 0}

    def half_dead(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise RuntimeError("AccessDenied")
        return model_value

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", half_dead)
    store = MemoryStore(_nodes())
    ref = VendorProductRef(vendor="ICE", product_id="O-7", name="Equity Prices")
    similarities = [c.similarity for c in store.find_similar_products(ref)]

    assert model_value not in similarities


def test_multi_path_opus_route_is_also_all_or_nothing(tmp_path, monkeypatch):
    """SCUDO_USE_OPUS_DENSE=1 bypasses score_candidates() entirely.

    The stores return `multi_path_retrieve` early on that flag, so the batch
    guarantee in score_candidates() does not cover it. Measured before the
    fix: one failed call at the network seam returned
    [0.93, 0.93, 0.93, 0.4473] — three model scores ranked against one
    Jaro-Winkler score, on a route the demo runbook tells operators to enable.
    """
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "1")
    model_value = 0.93
    calls = {"n": 0}

    def half_dead(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("AccessDenied")
        return model_value

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", half_dead)
    store = ScipySQLiteStore(tmp_path / "multipath.sqlite3")
    store.replace_taxonomy(_nodes())
    ref = VendorProductRef(vendor="ICE", product_id="O-8", name="Equity Prices")
    similarities = [c.similarity for c in store.find_similar_products(ref)]

    assert similarities, "expected candidates"
    assert model_value not in similarities
