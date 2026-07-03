"""P1 — SCUDO_TAXONOMY_TEXT shadow rollout."""

from __future__ import annotations

from scudo_mapping_mcp.models import Candidate, TaxonomyNode
from scudo_mapping_mcp.taxonomy_text import (
    maybe_log_taxonomy_text_shadow,
    shadow_bm25_top_iris,
    taxonomy_bm25_doc,
)
from scudo_mapping_mcp.tests.fake_store import FakeStore


def test_shadow_inactive_when_flag_off(monkeypatch):
    monkeypatch.delenv("SCUDO_TAXONOMY_TEXT_SHADOW", raising=False)
    monkeypatch.delenv("SCUDO_TAXONOMY_TEXT", raising=False)
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    node = TaxonomyNode(iri="a", label="Alpha", alt_labels=["ALT"])
    store = FakeStore(nodes=[node])
    nominated = [Candidate(node=node, similarity=0.0)]
    assert (
        maybe_log_taxonomy_text_shadow("ALT", [node], store, nominated, top_n=5) is None
    )


def test_shadow_diff_when_text_off_shadow_on(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT_SHADOW", "on")
    monkeypatch.delenv("SCUDO_TAXONOMY_TEXT", raising=False)
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    alt_winner = TaxonomyNode(iri="a", label="zzz", alt_labels=["RIC"])
    label_winner = TaxonomyNode(iri="b", label="RIC feed")
    store = FakeStore(nodes=[alt_winner, label_winner])

    production_iris = shadow_bm25_top_iris(
        "RIC", [alt_winner, label_winner], store, top_n=2
    )
    assert "a" in production_iris

    nominated = [Candidate(node=label_winner, similarity=0.0)]
    diff = maybe_log_taxonomy_text_shadow(
        "RIC", [alt_winner, label_winner], store, nominated, top_n=2
    )
    assert diff is not None
    assert "a" in diff["added"] or "b" in diff["removed"]


def test_shadow_does_not_change_live_bm25_doc(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT_SHADOW", "on")
    monkeypatch.delenv("SCUDO_TAXONOMY_TEXT", raising=False)
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())

    node = TaxonomyNode(iri="x", label="L", definition="def", alt_labels=["A"])
    assert taxonomy_bm25_doc(node) == "L"
