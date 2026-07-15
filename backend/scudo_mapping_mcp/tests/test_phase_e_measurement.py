"""Phase E step 2 — fix the measurement gaps before any rollout.

Verified seam facts (spec): the shadow logger was wired ONLY into the
multi-path route (default off) so shadow mode produced zero signal in
default environments; ``make_opus_dense_scorer`` hardcoded
``candidate_desc=""`` so definitions never reached the dense prompt; and
the shadow diff was a DEBUG log nobody counts. Pinned here:

  * ``maybe_log_taxonomy_text_shadow`` fires on the DEFAULT legacy store
    BM25 sidecar (memory_store + falkordb_store call sites).
  * ``make_opus_dense_scorer`` passes ``taxonomy_candidate_desc(c.node)``
    — flag-gated, so behaviour is unchanged while SCUDO_TAXONOMY_TEXT is
    off, and enriched text reaches prompt assembly when it is on.
  * The shadow diff is a counted CloudWatch EMF metric (pattern of
    ``scudo.metrics.emit``, commit 778b47a), not only a DEBUG log.
"""

from __future__ import annotations

import json

from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.opus_dense import make_opus_dense_scorer
from scudo_mapping_mcp.store.memory_store import MemoryStore
from scudo_mapping_mcp.tests.fake_store import FakeStore


def _set_flags(monkeypatch, **flags):
    for key in (
        "SCUDO_TAXONOMY_TEXT",
        "SCUDO_TAXONOMY_TEXT_SHADOW",
        "SCUDO_TAXONOMY_UML_TEXT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, val in flags.items():
        monkeypatch.setenv(key, val)
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())


def _shadow_nodes() -> list[TaxonomyNode]:
    # Alt-label winner vs label winner: text-on BM25 nominates differently
    # from text-off, so the shadow diff is guaranteed non-empty.
    return [
        TaxonomyNode(iri="a", label="zzz", alt_labels=["RIC"]),
        TaxonomyNode(iri="b", label="RIC feed"),
    ]


# ── shadow logger fires on the DEFAULT legacy store path ──────────────────


def test_shadow_logger_fires_on_default_memory_store_path(monkeypatch):
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT_SHADOW="on")
    monkeypatch.delenv("SCUDO_DENSE_BACKEND", raising=False)
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)

    calls: list[dict] = []
    from scudo_mapping_mcp import taxonomy_text as tt

    real = tt.maybe_log_taxonomy_text_shadow

    def spy(query_text, universe, store, nominated, *, top_n):
        diff = real(query_text, universe, store, nominated, top_n=top_n)
        calls.append({"query": query_text, "diff": diff, "top_n": top_n})
        return diff

    monkeypatch.setattr(
        "scudo_mapping_mcp.store.memory_store.maybe_log_taxonomy_text_shadow", spy
    )

    mem = MemoryStore(nodes=_shadow_nodes())
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="RIC")
    results = mem.find_similar_products(ref, max_results=1)

    assert calls, "shadow logger must fire on the DEFAULT legacy store path"
    assert calls[0]["diff"] is not None
    # And the live results are untouched by shadow (nomination unchanged).
    assert [c.node.iri for c in results] == ["b"]


def test_shadow_logger_fires_on_falkordb_legacy_path(monkeypatch):
    """Same wiring on the FalkorDB legacy branch, driven through a probe
    subclass that stubs the graph IO (same pattern as the smoke suite's
    _DenseProbeStore)."""
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT_SHADOW="on")
    monkeypatch.delenv("SCUDO_DENSE_BACKEND", raising=False)
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)

    from scudo_mapping_mcp.store import falkordb_store as fs_mod

    nodes = {n.iri: n for n in _shadow_nodes()}

    class _ProbeStore(fs_mod.FalkorDBStore):
        def __init__(self):  # noqa: D401 — skip FalkorDB connection
            pass

        def _ro(self, cypher, params=None):
            if "MATCH (t:TaxonomyNode)" in cypher:
                return [(n.iri, n.label, n.parent_iri) for n in nodes.values()]
            return []

        def get_taxonomy_node(self, node_iri):
            return nodes.get(node_iri)

        def get_negative_precedents(self, vendor, product_id):
            return []

        def rank_signals_for(self, vendor_signature):
            return {}

    calls: list[dict] = []
    from scudo_mapping_mcp import taxonomy_text as tt

    real = tt.maybe_log_taxonomy_text_shadow

    def spy(query_text, universe, store, nominated, *, top_n):
        diff = real(query_text, universe, store, nominated, top_n=top_n)
        calls.append({"diff": diff})
        return diff

    monkeypatch.setattr(tt, "maybe_log_taxonomy_text_shadow", spy)

    probe = _ProbeStore()
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="RIC")
    probe.find_similar_products(ref, max_results=1)
    assert calls, "shadow logger must fire on the FalkorDB legacy sidecar"
    assert calls[0]["diff"] is not None


def test_shadow_wiring_inert_when_shadow_flag_off(monkeypatch):
    """Default environment (both flags off): no shadow computation at all —
    the legacy path must not pay the text-on BM25 rescore."""
    _set_flags(monkeypatch)
    calls: list = []
    from scudo_mapping_mcp import taxonomy_text as tt

    real = tt.shadow_bm25_top_iris
    monkeypatch.setattr(
        tt,
        "shadow_bm25_top_iris",
        lambda *a, **k: calls.append(1) or real(*a, **k),
    )

    mem = MemoryStore(nodes=_shadow_nodes())
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="RIC")
    mem.find_similar_products(ref, max_results=1)
    assert calls == []


# ── make_opus_dense_scorer candidate_desc flag gating ─────────────────────


def _capture_scorer_descs(monkeypatch) -> list[str]:
    captured: list[str] = []

    def fake_score(*, query_label, query_desc, candidate_label, candidate_desc):
        captured.append(candidate_desc)
        return 0.9

    monkeypatch.setattr("scudo_mapping_mcp.opus_dense.opus_dense_score", fake_score)
    return captured


def test_opus_scorer_candidate_desc_empty_when_flag_off(monkeypatch):
    _set_flags(monkeypatch)  # text off — behaviour unchanged vs today
    captured = _capture_scorer_descs(monkeypatch)
    from scudo_mapping_mcp.models import Candidate

    node = TaxonomyNode(
        iri="x", label="Equity Prices", definition="SKOS definition text."
    )
    scorer = make_opus_dense_scorer(query_desc="vendor desc")
    scorer("Equity RT", [Candidate(node=node, similarity=0.0)])
    assert captured == [""]


def test_opus_scorer_candidate_desc_enriched_when_flag_on(monkeypatch):
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on")
    captured = _capture_scorer_descs(monkeypatch)
    from scudo_mapping_mcp.models import Candidate

    node = TaxonomyNode(
        iri="x",
        label="Equity Prices",
        definition="SKOS definition text.",
        business_concept="Pricing",
        asset_class="Equity",
    )
    scorer = make_opus_dense_scorer(query_desc="vendor desc")
    scorer("Equity RT", [Candidate(node=node, similarity=0.0)])
    # Enriched text reaches prompt assembly — definition only. The Phase E
    # signal fields are BM25-only (floor neutrality) and must never reach
    # the dense prompt.
    assert captured == ["SKOS definition text."]


# ── shadow diff is a counted EMF metric ───────────────────────────────────


def test_shadow_diff_emits_counted_emf_metric(monkeypatch, capsys):
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT_SHADOW="on")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")

    from scudo_mapping_mcp.models import Candidate
    from scudo_mapping_mcp.taxonomy_text import maybe_log_taxonomy_text_shadow

    alt_winner, label_winner = _shadow_nodes()
    store = FakeStore(nodes=[alt_winner, label_winner])
    nominated = [Candidate(node=label_winner, similarity=0.0)]

    diff = maybe_log_taxonomy_text_shadow(
        "RIC", [alt_winner, label_winner], store, nominated, top_n=1
    )
    assert diff is not None
    expected = len(diff["added"]) + len(diff["removed"])
    assert expected >= 1

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    emf = [json.loads(ln) for ln in lines if "TaxonomyShadowDiff" in ln]
    assert emf, "shadow diff must emit a counted EMF metric line"
    line = emf[0]
    assert line["TaxonomyShadowDiff"] == expected
    cw = line["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "SCUDO"
    assert cw["Metrics"][0]["Name"] == "TaxonomyShadowDiff"


def test_shadow_no_diff_emits_no_metric(monkeypatch, capsys):
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT_SHADOW="on")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")

    from scudo_mapping_mcp.models import Candidate
    from scudo_mapping_mcp.taxonomy_text import maybe_log_taxonomy_text_shadow

    node = TaxonomyNode(iri="only", label="Solo")
    store = FakeStore(nodes=[node])
    nominated = [Candidate(node=node, similarity=0.0)]
    diff = maybe_log_taxonomy_text_shadow("Solo", [node], store, nominated, top_n=1)
    assert diff == {"added": set(), "removed": set()}
    assert "TaxonomyShadowDiff" not in capsys.readouterr().out
