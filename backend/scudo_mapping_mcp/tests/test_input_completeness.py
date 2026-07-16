"""Input-completeness validation — flag-gated thin-input guard.

Behind its OWN opt-in flag ``SCUDO_INPUT_COMPLETENESS_VALIDATION`` (default
off, call-time read — measured-rollout discipline like
``SCUDO_ASSET_CLASS_VALIDATION``): with the flag off, ``run_validations``
output is byte-identical to today (existing artifacts pin the 5-entry list).

Why this validation exists: the dense arm is Jaro-Winkler string distance by
default, so THIN input scores HIGHER than complete input (empirically: a
name-only record scored 0.913 vs 0.822 for the same record with its
description present; a record with NO name at all falls back to matching on
the raw product_id and scored 0.969). Flag on: thin input becomes a required
validation failure → hard FAIL band → NEEDS_REVIEW, specialist not consulted
(matching.py req_fails branch) — no matching.py change needed.
"""

from __future__ import annotations

import os

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")

import pytest

from scudo_mapping_mcp import config as cfg
from scudo_mapping_mcp.models import (
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
)
from scudo_mapping_mcp.validations import run_validations

FLAG = "SCUDO_INPUT_COMPLETENESS_VALIDATION"

# The pre-existing validation list, in stable order. Flag off MUST reproduce
# exactly this (existing artifacts and tests pin it).
BASELINE_NAMES = [
    "scope_compatible",
    "identifier_resolves",
    "data_class_match",
    "name_length",
    "description_length",
]


def _node() -> TaxonomyNode:
    return TaxonomyNode(iri="cdao:eq-prices", label="Equity Prices")


def _ref(name: str, description: str) -> VendorProductRef:
    return VendorProductRef(
        vendor="LSEG",
        product_id="EQ-RT-001",
        name=name,
        description=description,
    )


def _run(ref: VendorProductRef):
    return run_validations(ref, _node(), scope_allowed=True, has_store_node=True)


def _completeness(validations):
    return [v for v in validations if v.name == "input_completeness"]


# ── flag OFF (default): output byte-identical to today ─────────────────────


def test_flag_unset_emits_no_input_completeness_entry(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    results = _run(_ref("", ""))  # even maximally-thin input
    assert [v.name for v in results] == BASELINE_NAMES
    assert _completeness(results) == []


def test_flag_zero_emits_no_input_completeness_entry(monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    results = _run(_ref("", ""))
    assert [v.name for v in results] == BASELINE_NAMES
    assert _completeness(results) == []


def test_flag_defaults_off_in_config(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert cfg.env_input_completeness_validation_enabled() is False
    monkeypatch.setenv(FLAG, "on")
    assert cfg.env_input_completeness_validation_enabled() is True


# ── flag ON: unit behaviour ─────────────────────────────────────────────────


def test_complete_ref_passes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("Equity Prices Real Time", "Real-time equity pricing feed."))
    entries = _completeness(results)
    assert len(entries) == 1
    v = entries[0]
    assert v.status == "pass"
    assert v.required is True
    assert v.detail == ""
    # Appended last — stable order for diffable bundle exports.
    assert results[-1].name == "input_completeness"
    assert [r.name for r in results[:-1]] == BASELINE_NAMES


def test_empty_name_fails_with_product_id_fallback_detail(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("", "Real-time equity pricing feed."))
    (v,) = _completeness(results)
    assert v.status == "fail"
    assert v.required is True
    # Detail must say the query degrades to the raw product_id
    # (memory_store.py / falkordb_store.py query composition).
    assert "product_id" in v.detail


def test_two_char_name_fails(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("EQ", "Real-time equity pricing feed."))
    (v,) = _completeness(results)
    assert v.status == "fail"
    assert "short" in v.detail.lower()


def test_whitespace_only_name_counts_as_empty(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("   ", "Real-time equity pricing feed."))
    (v,) = _completeness(results)
    assert v.status == "fail"
    assert "product_id" in v.detail


@pytest.mark.parametrize("bare_id", ["EQUITY-PRICES", "EQP_RT_001", "X1REF"])
def test_bare_identifier_name_fails(monkeypatch, bare_id):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref(bare_id, "Real-time equity pricing feed."))
    (v,) = _completeness(results)
    assert v.status == "fail"
    assert "identifier" in v.detail.lower()


@pytest.mark.parametrize("ok_name", ["Prices", "FX", "Equity Prices Real Time"])
def test_ordinary_or_generic_word_name_not_flagged_as_bare_identifier(
    monkeypatch, ok_name
):
    """Generic-but-well-formed names (e.g. a bare "Prices") are NOT rejected
    here — near-tie ambiguity for a generic query is the margin gate's job,
    not input_completeness's. Only "FX" (2 chars) trips the separate
    min-length rule, not the bare-identifier rule."""
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref(ok_name, "Real-time equity pricing feed."))
    (v,) = _completeness(results)
    if len(ok_name) < 3:
        assert v.status == "fail"
        assert "identifier" not in v.detail.lower()
    else:
        assert v.status == "pass"


def test_empty_description_fails_citing_jaro_winkler_thinness(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("Equity Prices Real Time", ""))
    (v,) = _completeness(results)
    assert v.status == "fail"
    assert "description" in v.detail.lower()
    # Cites the empirical finding: thin input scores HIGHER on the
    # Jaro-Winkler arm.
    assert "jaro" in v.detail.lower()
    assert "higher" in v.detail.lower()


def test_multiple_failures_combine_into_one_semicolon_joined_entry(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref("", ""))
    entries = _completeness(results)
    assert len(entries) == 1  # ONE validation entry, not one per failure
    v = entries[0]
    assert v.status == "fail"
    assert ";" in v.detail
    assert "product_id" in v.detail
    assert "description" in v.detail.lower()


# ── flag ON: end-to-end through the matcher (memory store) ─────────────────


@pytest.fixture
def memory_store_seeded(monkeypatch):
    """Fresh MemoryStore seeded from the cdao fixture, restored afterwards.

    STORE_BACKEND=memory must be live in the settings the store factory
    reads. The module-level ``os.environ.setdefault`` above handles the
    standalone run; re-snapshotting settings here makes the test robust to
    import order when run inside the wider suite (the config singleton is
    frozen at first import).
    """
    monkeypatch.setenv("STORE_BACKEND", "memory")
    import scudo_mapping_mcp.store.factory as factory
    from scudo_mapping_mcp.store import reset_store_cache

    fresh = cfg.Settings.from_env()
    assert fresh.store_backend == "memory"
    monkeypatch.setattr(cfg, "settings", fresh)
    monkeypatch.setattr(factory, "settings", fresh)
    reset_store_cache()

    from scudo_mapping_mcp.ingest import seed_taxonomy

    assert seed_taxonomy() > 0
    yield
    reset_store_cache()


def test_e2e_name_only_ref_forced_to_needs_review_when_flag_on(
    memory_store_seeded, monkeypatch
):
    monkeypatch.setenv(FLAG, "1")
    from scudo_mapping_mcp.matching import map_vendor_product

    ref = _ref("Equity Prices Real Time", "")
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert result.band == "fail"
    assert "input_completeness" in result.rationale


def test_e2e_name_only_ref_auto_maps_when_flag_off(memory_store_seeded, monkeypatch):
    """Regression baseline — pins the audit's finding that a name-only ref
    scores ABOVE the 0.80 auto-map edge on the Jaro-Winkler arm (0.913
    empirically), i.e. nothing flags thin input with the flag off."""
    monkeypatch.delenv(FLAG, raising=False)
    from scudo_mapping_mcp.matching import map_vendor_product

    ref = _ref("Equity Prices Real Time", "")
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.AUTO_MAPPED
    assert result.band == "pass"
    assert result.confidence >= 0.80
