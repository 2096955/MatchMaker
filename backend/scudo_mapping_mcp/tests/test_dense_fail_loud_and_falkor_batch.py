from __future__ import annotations

import pytest

from scudo_mapping_mcp import opus_dense, retrieval
from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.store.falkordb_store import FalkorDBStore
from scudo_mapping_mcp.store.memory_store import MemoryStore


@pytest.fixture(autouse=True)
def _isolate_breaker_state():
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
        TaxonomyNode(iri="cdao:equity", label="Equity Prices"),
        TaxonomyNode(iri="cdao:bond", label="Bond Reference"),
        TaxonomyNode(iri="cdao:curve", label="Commodity Curve"),
    ]


def _ref() -> VendorProductRef:
    return VendorProductRef(
        vendor="LSEG",
        product_id="EQ-1",
        name="Equity Prices",
        description="Historical stock prices",
    )


def _falkor_without_server(monkeypatch) -> FalkorDBStore:
    store = object.__new__(FalkorDBStore)
    nodes = _nodes()
    rows = [(node.iri, node.label, node.parent_iri) for node in nodes]
    by_iri = {node.iri: node for node in nodes}
    monkeypatch.setattr(store, "_ro", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(store, "get_taxonomy_node", by_iri.get)
    monkeypatch.setattr(store, "get_negative_precedents", lambda *_args: [])
    monkeypatch.setattr(store, "rank_signals_for", lambda *_args: {})
    return store


def test_multi_path_strict_model_failure_escapes_dense_rescore(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "1")
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    fallback_calls: list[str] = []

    def fail_model(*_args, **_kwargs):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", fail_model)
    monkeypatch.setattr(
        opus_dense,
        "_jaro_winkler_score",
        lambda *_args, **_kwargs: fallback_calls.append("fallback") or 0.4,
    )

    with pytest.raises(opus_dense.DenseScoringUnavailableError, match="AccessDenied"):
        MemoryStore(_nodes()).find_similar_products(_ref())

    assert fallback_calls == []


def test_multi_path_open_breaker_refusal_escapes_without_scoring(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "1")
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    opus_dense._breaker_failures = opus_dense._BREAKER_THRESHOLD
    opus_dense._breaker_opened_at = opus_dense.time.monotonic()
    model_calls: list[str] = []
    fallback_calls: list[str] = []
    monkeypatch.setattr(
        opus_dense,
        "_opus_invoke_score",
        lambda *_args, **_kwargs: model_calls.append("model") or 0.9,
    )
    monkeypatch.setattr(
        opus_dense,
        "_jaro_winkler_score",
        lambda *_args, **_kwargs: fallback_calls.append("fallback") or 0.4,
    )

    with pytest.raises(opus_dense.DenseCircuitOpenError, match="circuit.*open"):
        MemoryStore(_nodes()).find_similar_products(_ref())

    assert model_calls == []
    assert fallback_calls == []


def test_ordinary_injected_scorer_error_still_degrades():
    survivors = [Candidate(node=node, similarity=0.0) for node in _nodes()]

    def ordinary_failure(_query, _survivors):
        raise RuntimeError("ordinary injected scorer failure")

    result = retrieval._dense_rescore("query", survivors, ordinary_failure)

    assert [candidate.similarity for candidate in result] == [0.5, 0.5, 0.5]


def test_falkor_opus_failure_discards_all_model_values(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    store = _falkor_without_server(monkeypatch)
    model_value = 0.99

    def flaky(*, candidate_label, **_kwargs):
        if candidate_label == "Bond Reference":
            raise RuntimeError("model failed")
        return model_value

    monkeypatch.setattr(opus_dense, "opus_dense_score", flaky)

    results = store.find_similar_products(_ref())

    assert results
    assert all(candidate.similarity != model_value for candidate in results)


def test_falkor_opus_failure_is_loud_when_fallback_off(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    store = _falkor_without_server(monkeypatch)
    fallback_calls: list[str] = []

    def flaky(*, candidate_label, **_kwargs):
        if candidate_label == "Bond Reference":
            raise RuntimeError("strict Falkor model failure")
        return 0.99

    monkeypatch.setattr(opus_dense, "opus_dense_score", flaky)
    monkeypatch.setattr(
        opus_dense,
        "_jaro_winkler_score",
        lambda *_args, **_kwargs: fallback_calls.append("fallback") or 0.4,
    )

    with pytest.raises(
        opus_dense.DenseScoringUnavailableError,
        match="strict Falkor model failure",
    ):
        store.find_similar_products(_ref())

    assert fallback_calls == []
