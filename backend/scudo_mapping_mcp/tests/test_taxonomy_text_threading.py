"""P0 — taxonomy definition/alt-label threading into dense + BM25 paths."""

from __future__ import annotations

from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.retrieval import _bm25_prefilter, multi_path_retrieve
from scudo_mapping_mcp.store.memory_store import MemoryStore
from scudo_mapping_mcp.taxonomy_text import taxonomy_bm25_doc, taxonomy_dense_text
from scudo_mapping_mcp.tests.fake_store import FakeStore


def test_p0_flags_default_off(monkeypatch):
    """New ontology behaviour is opt-in; legacy paths remain default."""
    for key in (
        "SCUDO_TAXONOMY_TEXT",
        "SCUDO_USE_OPUS_DENSE",
        "SCUDO_TAXONOMY_LOADER",
        "SCUDO_DENSE_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    from scudo_mapping_mcp import config as cfg

    s = cfg.Settings.from_env()
    assert s.taxonomy_text_enabled is False
    assert s.use_opus_dense is False
    assert s.taxonomy_loader == "cdao"
    assert s.dense_backend == "jaro_winkler"


def test_stored_definition_ignored_when_text_flag_off(monkeypatch):
    """Extended node fields are inert for matching without SCUDO_TAXONOMY_TEXT."""
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())
    node = TaxonomyNode(
        iri="x",
        label="Equities",
        definition="SecretDefinitionToken",
        alt_labels=["HiddenAlt"],
    )
    assert taxonomy_bm25_doc(node) == "Equities"
    assert taxonomy_dense_text(node) == "Equities"


def test_bm25_prefilter_never_populates_similarity():
    """BM25 nominates only — similarity stays 0.0 until dense rescore."""
    store = FakeStore(
        nodes=[
            TaxonomyNode(iri="a", label="Alpha"),
            TaxonomyNode(iri="b", label="Beta"),
        ]
    )
    nominated = _bm25_prefilter("Alpha", list(store.list_taxonomy_nodes()), store)
    assert nominated
    assert all(c.similarity == 0.0 for c in nominated)


def test_multi_path_similarity_is_dense_not_bm25(monkeypatch):
    """BM25 may change who gets nominated; similarity is always from dense."""
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    # Alt label wins BM25; label match wins dense.
    node_bm25 = TaxonomyNode(
        iri="a",
        label="Unrelated",
        alt_labels=["RIC"],
        definition="",
    )
    node_dense = TaxonomyNode(iri="b", label="RIC prices", definition="")
    store = FakeStore(nodes=[node_bm25, node_dense])
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="RIC")

    def dense_scorer(_query: str, cands: list[Candidate]) -> list[Candidate]:
        return [
            Candidate(
                node=c.node,
                similarity=0.91 if c.node.iri == "b" else 0.12,
            )
            for c in cands
        ]

    results = multi_path_retrieve(ref, store, dense_scorer=dense_scorer)
    by_iri = {c.node.iri: c.similarity for c in results}
    assert by_iri["b"] == 0.91
    assert by_iri["a"] == 0.12
    assert results[0].node.iri == "b"


def test_memory_store_similarity_is_dense_not_rrf(monkeypatch):
    """Legacy path: RRF/BM25 reorder only; Candidate.similarity stays dense."""
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    monkeypatch.setenv("SCUDO_USE_OPUS_DENSE", "")
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "jaro_winkler")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    node_bm25 = TaxonomyNode(
        iri="a",
        label="zzz",
        alt_labels=["TICKER"],
        definition="",
    )
    node_dense = TaxonomyNode(
        iri="b",
        label="TICKER feed",
        definition="",
    )
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="TICKER")
    mem = MemoryStore(nodes=[node_bm25, node_dense])
    results = mem.find_similar_products(ref, max_results=2, min_similarity=0.0)
    by_iri = {c.node.iri: c.similarity for c in results}
    assert by_iri["b"] > by_iri["a"]
    # Similarity is Jaro on dense text, not a fused rank quantity in [0, 0.1].
    assert by_iri["b"] > 0.5


def test_memory_store_opus_branch_calls_taxonomy_candidate_desc(monkeypatch):
    """MemoryStore legacy opus path threads taxonomy_candidate_desc."""
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)
    from scudo_mapping_mcp import config as cfg

    assert cfg.env_dense_backend() == "opus"

    node = TaxonomyNode(
        iri="cdao:eq-prices",
        label="Equity Prices",
        definition="SKOS definition text.",
        alt_labels=["Stocks"],
    )
    ref = VendorProductRef(vendor="LSEG", product_id="X", name="Equity RT")

    calls: list[str] = []
    from scudo_mapping_mcp.taxonomy_text import taxonomy_candidate_desc as real_desc

    def spy(n):
        calls.append(real_desc(n))
        return real_desc(n)

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.taxonomy_candidate_desc",
        spy,
    )
    monkeypatch.setattr(
        "scudo_mapping_mcp.opus_dense.opus_dense_score",
        lambda *a, **k: 0.9,
    )

    mem = MemoryStore(nodes=[node])
    mem.find_similar_products(ref, max_results=5)
    assert calls == ["SKOS definition text."]


def test_bm25_doc_includes_alt_labels_when_flag_on(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())
    node = TaxonomyNode(
        iri="x",
        label="Equities",
        definition="Listed stocks.",
        alt_labels=["Stocks", "EQ"],
    )
    doc = taxonomy_bm25_doc(node)
    assert "Equities" in doc
    assert "Stocks" in doc
    assert "Listed stocks." in doc


def test_retrieval_bm25_uses_full_node_text(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    node = TaxonomyNode(
        iri="cdao:eq-prices",
        label="Equity Prices",
        definition="UniqueDefinitionToken",
        alt_labels=["EP"],
    )
    store = FakeStore(nodes=[node])
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="EP feed")

    docs_seen: list[str] = []

    def capture_bm25(query, docs, **kwargs):
        docs_seen.extend(text for _, text in docs)
        return {node.iri: 1.0}

    monkeypatch.setattr(store, "bm25_scores", capture_bm25)

    multi_path_retrieve(
        ref,
        store,
        dense_scorer=lambda q, cands: cands,
    )
    assert any("UniqueDefinitionToken" in d for d in docs_seen)
    assert any("EP" in d for d in docs_seen)
