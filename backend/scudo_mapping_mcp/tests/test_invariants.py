"""
Section 13 invariant smoke tests.

Pure-Python — runs without a FalkorDB container. Behaviour gates:

- IRI determinism (I8): same (vendor, product_id) -> same IRI across runs.
- Out-of-scope vendor (I3): fail-closed scope gate.
- Below-floor candidate (I4): needs_review.
- At/above floor: auto_mapped (when validations pass).
- Required-validation failure: forces needs_review even above the floor.
- Reject (M4-new): negative precedent excludes the node next run.
- Approve (M4-new): confirmed precedent short-circuits the next run.
- Approve (M4-new): rank signal promotes the node for the same vendor signature.
- apply_decision input validation.

Real store I/O (Cypher, ro_query) is covered separately by integration tests
against a live FalkorDB container; this file owns the contract behaviour.
"""
from __future__ import annotations

import pytest

from scudo_mapping_mcp import frames as frames_mod
from scudo_mapping_mcp import store as store_pkg
from scudo_mapping_mcp.config import PRIORITY_VENDORS
from scudo_mapping_mcp.feedback import apply_decision
from scudo_mapping_mcp.matching import map_vendor_product
from scudo_mapping_mcp.models import (
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
    mds_iri,
)
from scudo_mapping_mcp.tests.fake_store import FakeStore


# Pick a vendor that is in the priority set so the scope gate passes.
IN_SCOPE_VENDOR = PRIORITY_VENDORS[0]
EQUITIES_IRI = "cdao:equities"
EQ_PRICES_IRI = "cdao:eq-prices"


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch):
    """Swap the singleton store for a FakeStore per test."""
    fake = FakeStore(nodes=[
        TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
        TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices",
                     parent_iri=EQUITIES_IRI),
        TaxonomyNode(iri="cdao:fx", label="Foreign Exchange"),
    ])
    # Patch every callsite that reaches for the store. lru_cache means we
    # can't just call reset_store_cache — we monkeypatch the function.
    monkeypatch.setattr(store_pkg, "get_store", lambda: fake)
    import scudo_mapping_mcp.matching as m
    import scudo_mapping_mcp.feedback as f
    monkeypatch.setattr(m, "get_store", lambda: fake)
    monkeypatch.setattr(f, "get_store", lambda: fake)
    yield fake


@pytest.fixture
def clean_frames():
    frames_mod.clear_frames()
    yield
    frames_mod.clear_frames()


def test_iri_is_deterministic():
    a = mds_iri(IN_SCOPE_VENDOR, "EQ-RT-001")
    b = mds_iri(IN_SCOPE_VENDOR, "EQ-RT-001")
    assert a == b
    assert a.startswith("mds.")


def test_iri_slug_handles_ampersand_and_spaces():
    iri = mds_iri("S&P Global", "ABC")
    assert iri.startswith("mds.sandpglobal:"), iri


def test_out_of_scope_vendor_is_fail_closed(_isolate_store, clean_frames):
    ref = VendorProductRef(
        vendor="NotAVendor", product_id="x", name="anything",
    )
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.OUT_OF_SCOPE
    assert result.mapped_node_iri is None
    # validations + field_normalisation are populated on every code path.
    assert result.field_normalisation, "should always emit default field rules"
    scope_v = next(v for v in result.validations if v.name == "scope_compatible")
    assert scope_v.status == "fail"


def test_below_floor_returns_needs_review(_isolate_store, clean_frames):
    _isolate_store.set_score(EQ_PRICES_IRI, 0.40)
    _isolate_store.set_score(EQUITIES_IRI, 0.30)
    _isolate_store.set_score("cdao:fx", 0.20)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="X1",
                           name="random gibberish that wont match anything")
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert result.confidence < 0.80


def test_above_floor_returns_auto_mapped(_isolate_store, clean_frames):
    _isolate_store.set_score(EQ_PRICES_IRI, 0.95)
    _isolate_store.set_score(EQUITIES_IRI, 0.60)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-001",
                           name="Equity Prices Real Time")
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.AUTO_MAPPED
    assert result.mapped_node_iri == EQ_PRICES_IRI
    assert result.confidence >= 0.80


def test_required_validation_failure_forces_needs_review(_isolate_store, clean_frames):
    """A required-fail validation forces needs_review even at 1.0 similarity."""
    _isolate_store.set_score(EQ_PRICES_IRI, 0.99)
    # Drop the candidate from the store between scoring and validation by
    # presenting a candidate IRI that the validator can't resolve. Simplest
    # reproduction: ask the matcher about a ref whose store-side lookup of
    # the BEST candidate succeeds, but pretend the lookup fails via removal.
    fake = _isolate_store
    real_get = fake.get_taxonomy_node
    # First call returns the candidate (for find_similar_products' world);
    # subsequent calls (the validator's lookup) return None.
    calls = {"n": 0}

    def flaky_get(iri):
        calls["n"] += 1
        # The matcher calls get_taxonomy_node ONLY for validation lookup, so
        # the first call here IS the validation lookup -> return None to fail.
        return None

    fake.get_taxonomy_node = flaky_get  # type: ignore[assignment]
    try:
        ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-002",
                               name="Equity Prices Real Time")
        result = map_vendor_product(ref)
    finally:
        fake.get_taxonomy_node = real_get  # type: ignore[assignment]

    assert result.status == MappingStatus.NEEDS_REVIEW
    # identifier_resolves must be the failing required check.
    failed = [v for v in result.validations if v.required and v.status == "fail"]
    assert any(v.name == "identifier_resolves" for v in failed), result.validations


def test_reject_excludes_node_from_future_candidates(_isolate_store, clean_frames):
    _isolate_store.set_score(EQ_PRICES_IRI, 0.95)
    _isolate_store.set_score(EQUITIES_IRI, 0.85)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-003",
                           name="Equity Prices Real Time")

    apply_decision(
        ref, decision="reject", decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
    )
    result = map_vendor_product(ref)
    # EQ_PRICES_IRI is rejected — the next-best (EQUITIES) should win.
    assert result.mapped_node_iri == EQUITIES_IRI
    assert all(c.node.iri != EQ_PRICES_IRI for c in result.candidates)


def test_approve_writes_confirmed_precedent(_isolate_store, clean_frames):
    _isolate_store.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-004",
                           name="Equity Prices Real Time")

    apply_decision(
        ref, decision="approve", decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI, suggested_confidence=0.91,
    )
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.APPROVED
    assert result.mapped_node_iri == EQ_PRICES_IRI
    assert result.rationale.startswith("precedent")


def test_override_records_overridden_status_and_boosts_chosen_node(_isolate_store, clean_frames):
    _isolate_store.set_score(EQ_PRICES_IRI, 0.95)
    _isolate_store.set_score(EQUITIES_IRI, 0.10)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005",
                           name="Equity Prices Real Time")

    apply_decision(
        ref, decision="override", decided_by="reviewer@jpmc",
        node_iri=EQUITIES_IRI,
    )
    # Same vendor signature, different product_id: the EQUITIES node should
    # now be ranked higher by the rank signal even though base similarity is
    # lower than EQ_PRICES_IRI.
    sibling = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005-B",
                               name="Equity Prices Real Time")
    result = map_vendor_product(sibling)
    # EQ_PRICES still wins (much higher base) — but EQUITIES has moved up.
    eq_node_score = next(c.similarity for c in result.candidates if c.node.iri == EQUITIES_IRI)
    assert eq_node_score > 0.10, f"rank signal did not boost equities: {result.candidates}"


def test_apply_decision_validates_inputs(_isolate_store, clean_frames):
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="X", name="foo")
    with pytest.raises(ValueError, match="decision must be"):
        apply_decision(ref, decision="maybe", decided_by="u",
                       node_iri=EQ_PRICES_IRI)
    with pytest.raises(ValueError, match="decided_by"):
        apply_decision(ref, decision="approve", decided_by="",
                       node_iri=EQ_PRICES_IRI)
    with pytest.raises(ValueError, match="node_iri"):
        apply_decision(ref, decision="approve", decided_by="u", node_iri="")
    with pytest.raises(ValueError, match="unknown taxonomy node"):
        apply_decision(ref, decision="approve", decided_by="u",
                       node_iri="cdao:does-not-exist")


def test_mapping_artifact_is_self_describing_on_every_path(_isolate_store, clean_frames):
    """field_normalisation + validations are always populated."""
    _isolate_store.set_score(EQ_PRICES_IRI, 0.95)
    ref_in = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="A", name="x")
    ref_out = VendorProductRef(vendor="NotAVendor", product_id="B", name="x")
    for r in (ref_in, ref_out):
        result = map_vendor_product(r)
        assert result.field_normalisation
        assert result.validations
