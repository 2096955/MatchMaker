"""
Standalone Section-13 smoke runner (no pytest dependency).

Same gates as `test_invariants.py` but executable as a script:
    python -m scudo_mapping_mcp.tests.smoke
Exit code is non-zero on any failure, so this can sit behind the post-deploy
check the brief calls for ("smoke-test actual behaviour, not just startup").
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import sys
import traceback

from .. import agent as agent_mod
from .. import bundle as bundle_mod
from .. import config as config_mod
from .. import frames as frames_mod
from .. import hydrate as hydrate_mod
from .. import store as store_pkg
from ..agent import (
    AgentEvent,
    ScriptedMappingAgent,
    get_agent,
)
from ..config import PRIORITY_VENDORS
from ..feedback import apply_decision
from .. import matching as matching_mod
from .. import feedback as feedback_mod
from ..bundle import export_bundle, import_bundle
from ..frames import FrameDataError
from ..hydrate import HydrationError, hydrate
from ..matching import map_vendor_product
from ..models import (
    Candidate,
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
    mds_iri,
)
from ..store.base import RetrievalStore
from .fake_store import FakeStore


# --- Fake S3 client for M8 reader tests ------------------------------------
class S3NoSuchKey(Exception):
    """Mirrors botocore.exceptions.ClientError(Code=NoSuchKey) without
    pulling botocore into the test dependency surface. frames._is_no_such_key
    catches by class name."""


class FakeS3Client:
    """Minimal stand-in for boto3.client('s3').get_object — what the M8 reader
    actually calls. Stores per-(bucket, key) (body_bytes, metadata) tuples."""

    def __init__(self, objects=None):
        # key: (bucket, key) -> {"Body": bytes, "Metadata": dict}
        self._objects: dict[tuple[str, str], dict] = dict(objects or {})

    def put(self, bucket: str, key: str, body: bytes, metadata=None):
        self._objects[(bucket, key)] = {
            "Body": body,
            "Metadata": dict(metadata or {}),
        }

    def get_object(self, Bucket, Key):  # noqa: N803 — boto3 kwarg names
        try:
            obj = self._objects[(Bucket, Key)]
        except KeyError as e:
            raise S3NoSuchKey(f"s3://{Bucket}/{Key}") from e
        return {
            "Body": io.BytesIO(obj["Body"]),
            "Metadata": dict(obj["Metadata"]),
        }


def _swap_settings(**overrides):
    """Return (saved, replacement) — the test should restore saved on exit."""
    saved = frames_mod.settings
    new = dataclasses.replace(saved, **overrides)
    frames_mod.settings = new
    config_mod.settings = new
    hydrate_mod.settings = new
    return saved


def _restore_settings(saved):
    frames_mod.settings = saved
    config_mod.settings = saved
    hydrate_mod.settings = saved


IN_SCOPE_VENDOR = PRIORITY_VENDORS[0]
EQUITIES_IRI = "cdao:equities"
EQ_PRICES_IRI = "cdao:eq-prices"
FX_IRI = "cdao:fx"


def _fresh_store() -> FakeStore:
    fake = FakeStore(nodes=[
        TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
        TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices",
                     parent_iri=EQUITIES_IRI),
        TaxonomyNode(iri=FX_IRI, label="Foreign Exchange"),
    ])
    # Wire it through every module that resolves the store.
    store_pkg.get_store = lambda: fake  # type: ignore[assignment]
    matching_mod.get_store = lambda: fake  # type: ignore[assignment]
    feedback_mod.get_store = lambda: fake  # type: ignore[assignment]
    bundle_mod.get_store = lambda: fake  # type: ignore[assignment]
    agent_mod.get_store = lambda: fake  # type: ignore[assignment]
    frames_mod.clear_frames()
    return fake


_results: list[tuple[str, bool, str]] = []


def case(name: str):
    def deco(fn):
        try:
            fn()
            _results.append((name, True, ""))
        except AssertionError as e:
            _results.append((name, False, f"assertion: {e}"))
        except Exception as e:  # noqa: BLE001
            _results.append((name, False, f"{type(e).__name__}: {e}\n"
                                          f"{traceback.format_exc()}"))
        return fn
    return deco


@case("iri_is_deterministic")
def _():
    assert mds_iri(IN_SCOPE_VENDOR, "EQ-RT-001") == \
           mds_iri(IN_SCOPE_VENDOR, "EQ-RT-001")
    assert mds_iri("S&P Global", "X").startswith("mds.sandpglobal:")


@case("out_of_scope_vendor_is_fail_closed")
def _():
    _fresh_store()
    r = map_vendor_product(VendorProductRef(
        vendor="NotAVendor", product_id="x", name="anything",
    ))
    assert r.status == MappingStatus.OUT_OF_SCOPE, r.status
    assert r.field_normalisation
    scope_v = next(v for v in r.validations if v.name == "scope_compatible")
    assert scope_v.status == "fail"


@case("below_floor_returns_needs_review")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.40)
    fake.set_score(EQUITIES_IRI, 0.30)
    fake.set_score(FX_IRI, 0.20)
    r = map_vendor_product(VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="X1",
        name="random gibberish that wont match anything",
    ))
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert r.confidence < 0.80


@case("above_floor_returns_auto_mapped")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.60)
    r = map_vendor_product(VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-001",
        name="Equity Prices Real Time",
    ))
    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.mapped_node_iri == EQ_PRICES_IRI
    assert r.confidence >= 0.80


@case("required_validation_failure_forces_needs_review")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.99)
    # identifier_resolves fails when the validator can't look the node up.
    fake.get_taxonomy_node = lambda iri: None  # type: ignore[assignment]
    r = map_vendor_product(VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-002",
        name="Equity Prices Real Time",
    ))
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    failed = [v for v in r.validations if v.required and v.status == "fail"]
    names = [v.name for v in failed]
    assert "identifier_resolves" in names, names


@case("reject_excludes_node_from_future_candidates")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.85)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-003",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="reject", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI)
    r = map_vendor_product(ref)
    assert r.mapped_node_iri == EQUITIES_IRI, r.mapped_node_iri
    assert all(c.node.iri != EQ_PRICES_IRI for c in r.candidates)


@case("approve_writes_confirmed_precedent")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-004",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.APPROVED, r.status
    assert r.mapped_node_iri == EQ_PRICES_IRI
    assert r.rationale.startswith("precedent"), r.rationale


@case("override_tilts_sort_for_sibling_but_does_not_lift_similarity")
def _():
    """Rank signal tilts ORDER; Candidate.similarity stays the raw oracle score.

    The matcher's 0.80 floor sees the unmodified similarity, so a base-0.10
    candidate can never be auto_mapped just because it accumulated boosts
    (Section 10a CAVEAT / I5). This is the post-review fix.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.10)
    fake.set_score(FX_IRI, 0.05)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="override", decided_by="reviewer@jpmc",
                   node_iri=EQUITIES_IRI)
    sibling = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005-B",
                               name="Equity Prices Real Time")
    r = map_vendor_product(sibling)
    eq_score = next(c.similarity for c in r.candidates if c.node.iri == EQUITIES_IRI)
    # Boost no longer shows in Candidate.similarity; raw 0.10 stays raw.
    assert eq_score == 0.10, f"similarity must not absorb boost: {eq_score}"
    # EQ_PRICES still wins — it had a much higher base similarity.
    assert r.mapped_node_iri == EQ_PRICES_IRI


@case("rank_boost_cannot_lift_subfloor_candidate_above_floor")
def _():
    """N prior approvals at base 0.72 must NOT auto_map a sibling product.

    Drives rank up via real apply_decision calls — the rank signal is now
    DERIVED from precedent edges, so 5 separate products sharing the
    signature give a derived count of 5 and saturate the boost cap.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.72)
    # Real apply_decision approvals across 5 distinct products with the
    # same name (and so the same vendor_signature).
    from scudo_mapping_mcp.store.base import RetrievalStore
    n = RetrievalStore.max_useful_boost_approvals()  # tracks the seam's tuning
    for i in range(n):
        apply_decision(
            VendorProductRef(vendor=IN_SCOPE_VENDOR,
                             product_id=f"PRIOR-{i}",
                             name="Equity Prices Real Time"),
            decision="approve", decided_by=f"reviewer{i}@jpmc",
            node_iri=EQ_PRICES_IRI, suggested_confidence=0.91,
        )
    # New sibling product — different IRI, same signature.
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-NEW",
                           name="Equity Prices Real Time")
    r = map_vendor_product(ref)
    # Similarity stays at the raw oracle score; boost only tilts the sort.
    assert r.confidence == 0.72, r.confidence
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status


@case("override_after_approve_replaces_precedent_and_decrements_old_boost")
def _():
    """Approve A then override to B: precedent points to B; A's boost goes to 0."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.60)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-OV",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.95)
    apply_decision(ref, decision="override", decided_by="reviewer@jpmc",
                   node_iri=EQUITIES_IRI)

    # Precedent reuse now points to the OVERRIDE target, not the stale approve.
    r = map_vendor_product(ref)
    assert r.mapped_node_iri == EQUITIES_IRI, r.mapped_node_iri
    assert r.status == MappingStatus.OVERRIDDEN, r.status

    # Boost on the old node has been decremented back to 0; new node now at 1.
    sig = fake.vendor_signature(IN_SCOPE_VENDOR,
                                "Equity Prices Real Time", "EQ-RT-OV")
    boosts = fake.rank_signals_for(sig)
    assert boosts.get(EQ_PRICES_IRI, 0) == 0, boosts
    assert boosts.get(EQUITIES_IRI, 0) == 1, boosts


@case("rank_signal_survives_ingest_then_approve_path")
def _():
    """Production path: ingest writes the VendorProduct first, THEN HITL
    approves it. The signature on VendorProduct must be set by the ingest
    write (upsert_vendor_product) — the upsert_precedent path only sets
    signature ON CREATE, which would not fire on the already-existing iri.
    Reviewer r2 finding #1: without this, rank_signals_for returns 0 in
    every real deployment.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-ING",
                           name="Equity Prices Real Time",
                           description="vendor description")
    # Step 1: ingest writes the row (real flow goes through ingest.ingest_bytes
    # -> store.upsert_vendor_product per parsed row).
    fake.upsert_vendor_product(ref)
    # Step 2: HITL approves the previously-ingested product.
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)

    sig = fake.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time",
                                "EQ-ING")
    boosts = fake.rank_signals_for(sig)
    assert boosts.get(EQ_PRICES_IRI, 0) == 1, (
        f"ingest-then-approve must contribute to rank signal; "
        f"got {boosts!r}"
    )


@case("apply_decision_is_replay_idempotent_on_precedent_AND_signal")
def _():
    """Re-issuing the SAME approve must not change the graph on either axis.

    Previously the rank signal was a separate counter that would double on
    every replay (reviewer finding #1). With the signal derived from edges,
    re-issuing collapses to a no-op MERGE and the derived count is unchanged.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-IDEMP",
                           name="Equity Prices Real Time")

    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    sig = fake.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time",
                                "EQ-IDEMP")
    boosts_first = fake.rank_signals_for(sig)
    precedent_first = fake.get_precedent_mapping(IN_SCOPE_VENDOR, "EQ-IDEMP")

    # Same decision, again, three times.
    for _ in range(3):
        apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                       node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)

    boosts_after = fake.rank_signals_for(sig)
    precedent_after = fake.get_precedent_mapping(IN_SCOPE_VENDOR, "EQ-IDEMP")

    assert boosts_first == boosts_after, (boosts_first, boosts_after)
    assert boosts_after.get(EQ_PRICES_IRI, 0) == 1, boosts_after
    assert precedent_first.mapped_node_iri == precedent_after.mapped_node_iri


@case("scope_check_failure_is_treated_as_deny")
def _():
    """A scope check that raises must surface as allowed=False, not propagate."""
    from scudo_mapping_mcp import frames as fm

    class _Boom:
        vendor = property(lambda self: (_ for _ in ()).throw(
            RuntimeError("simulated odrl lookup failure")
        ))

    result = fm.check_scope(_Boom())  # type: ignore[arg-type]
    assert result.allowed is False
    assert "scope check failed" in result.reason


@case("reject_of_different_node_leaves_prior_approval_intact")
def _():
    """approve V->A, then reject V->B (a different node).
    The single positive precedent V->A must remain, and /map must still
    return APPROVED to A. Only the new rejection of B is added to negatives.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.60)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RA",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    apply_decision(ref, decision="reject", decided_by="reviewer@jpmc",
                   node_iri=EQUITIES_IRI)

    # Prior positive precedent to EQ_PRICES_IRI survives.
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.APPROVED, r.status
    assert r.mapped_node_iri == EQ_PRICES_IRI

    # And EQUITIES is now negatively recorded for this product specifically.
    assert EQUITIES_IRI in fake.get_negative_precedents(IN_SCOPE_VENDOR, "EQ-RA")


@case("negative_precedent_is_per_product_not_per_signature")
def _():
    """Reject on product X must NOT affect product Y, even when they share
    the same vendor signature (same vendor + same name)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref_x = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="X",
                             name="Equity Prices Real Time")
    ref_y = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="Y",
                             name="Equity Prices Real Time")
    apply_decision(ref_x, decision="reject", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI)
    # Product Y has no negative precedent; the matcher still considers
    # EQ_PRICES_IRI for Y.
    r = map_vendor_product(ref_y)
    assert r.mapped_node_iri == EQ_PRICES_IRI, r.mapped_node_iri


@case("out_of_scope_apply_decision_is_rejected_at_function_boundary")
def _():
    """Direct caller (not just HTTP) cannot write an out-of-scope precedent."""
    _fresh_store()
    ref = VendorProductRef(vendor="NotAVendor", product_id="X",
                           name="anything")
    try:
        apply_decision(ref, decision="approve", decided_by="u",
                       node_iri=EQ_PRICES_IRI, suggested_confidence=0.9)
    except ValueError as e:
        assert "out of scope" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for out-of-scope vendor")


@case("apply_decision_validates_inputs")
def _():
    fake = _fresh_store()
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="X", name="foo")
    for case_args, expected in [
        ({"decision": "maybe", "decided_by": "u", "node_iri": EQ_PRICES_IRI,
          "suggested_confidence": 0.9},
         "decision must be"),
        ({"decision": "approve", "decided_by": "", "node_iri": EQ_PRICES_IRI,
          "suggested_confidence": 0.9},
         "decided_by"),
        ({"decision": "approve", "decided_by": "u", "node_iri": "",
          "suggested_confidence": 0.9},
         "node_iri"),
        ({"decision": "approve", "decided_by": "u",
          "node_iri": EQ_PRICES_IRI},  # missing suggested_confidence
         "suggested_confidence is required"),
        ({"decision": "approve", "decided_by": "u",
          "node_iri": "cdao:nope", "suggested_confidence": 0.9},
         "unknown taxonomy node"),
    ]:
        try:
            apply_decision(ref, **case_args)
        except ValueError as e:
            assert expected in str(e), (case_args, str(e))
        else:
            raise AssertionError(f"expected ValueError for {case_args}")


@case("mapping_artifact_is_self_describing_on_every_path")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    for r in (
        VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="A", name="x"),
        VendorProductRef(vendor="NotAVendor", product_id="B", name="x"),
    ):
        result = map_vendor_product(r)
        assert result.field_normalisation, result
        assert result.validations, result


@case("provisional_edges_are_stored_but_excluded_from_precedent_reuse")
def _():
    """The provisional edge MUST be stored (so a regression in the filter is
    visible to this test), but get_precedent_mapping MUST hide it (I5).
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-PROV",
                           name="Equity Prices Real Time")
    fake.upsert_precedent(
        ref=ref,
        node=TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices"),
        decision="approve", decided_by="auto", confidence=0.95,
        provisional=True,
    )
    # The edge IS in the store (fidelity to FalkorDB store-and-filter).
    key = (IN_SCOPE_VENDOR, "EQ-RT-PROV")
    assert key in fake._provisional, "provisional precedent should be stored"
    # ...but get_precedent_mapping filters it out.
    assert fake.get_precedent_mapping(IN_SCOPE_VENDOR, "EQ-RT-PROV") is None
    # End-to-end: map_vendor_product does not short-circuit on a provisional.
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.rationale != "precedent", r.rationale


@case("precedent_reuse_does_not_emit_contradictory_validations")
def _():
    """Approve, then remove the node from the taxonomy seed. The precedent
    must still surface as APPROVED, and the validations payload must NOT
    contradict it (no required-fail identifier_resolves).
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-REUSE",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.95)
    # Pretend the node was retired from the taxonomy after the approval.
    del fake._nodes[EQ_PRICES_IRI]
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.APPROVED, r.status
    required_fails = [v for v in r.validations
                      if v.required and v.status == "fail"]
    assert not required_fails, (
        f"precedent reuse must not emit required-fail validations: "
        f"{r.validations}"
    )


@case("M6_export_bundle_carries_confirmed_precedent_with_rank")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-EX1",
                           name="Equity Prices Real Time",
                           description="Real-time equity pricing feed")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)

    bundle = export_bundle(source_env="test-A", created_at="2026-01-01T00:00:00.000Z")
    assert bundle.version == "1.0.0", bundle.version
    assert bundle.source_env == "test-A"
    assert len(bundle.patterns) == 1, bundle.patterns
    p = bundle.patterns[0]
    assert p.vendor == IN_SCOPE_VENDOR
    assert p.product_id == "EQ-RT-EX1"
    assert p.mapped_node_iri == EQ_PRICES_IRI
    assert p.confidence == 0.91
    # Approve bumped the rank signal once, so rank == 1 in the export.
    assert p.rank == 1, p.rank
    assert p.provenance.decision == "approve"
    assert p.provenance.decided_by == "reviewer@jpmc"
    # decided_at preserved as ISO-8601 UTC.
    assert p.provenance.decided_at.endswith("Z"), p.provenance.decided_at


@case("M6_export_excludes_provisional_and_negative")
def _():
    fake = _fresh_store()
    # One CONFIRMED, one PROVISIONAL, one REJECTED.
    ref_confirmed = VendorProductRef(vendor=IN_SCOPE_VENDOR,
                                     product_id="EQ-CONF",
                                     name="Equity Prices Real Time")
    ref_prov = VendorProductRef(vendor=IN_SCOPE_VENDOR,
                                product_id="EQ-PROV",
                                name="Equity Prices Real Time")
    ref_rej = VendorProductRef(vendor=IN_SCOPE_VENDOR,
                               product_id="EQ-REJ",
                               name="Equity Prices Real Time")
    fake.set_score(EQ_PRICES_IRI, 0.95)
    apply_decision(ref_confirmed, decision="approve",
                   decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    fake.upsert_precedent(
        ref=ref_prov,
        node=TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices"),
        decision="approve", decided_by="auto", confidence=0.95,
        provisional=True,
    )
    apply_decision(ref_rej, decision="reject", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI)

    bundle = export_bundle(source_env="test", created_at="2026-01-01T00:00:00.000Z")
    pids = [p.product_id for p in bundle.patterns]
    assert pids == ["EQ-CONF"], pids


@case("M6_round_trip_reproduces_confirmed_mappings_in_fresh_env")
def _():
    # Env A
    fake_a = _fresh_store()
    fake_a.set_score(EQ_PRICES_IRI, 0.95)
    refs = [
        VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id=pid,
                         name="Equity Prices Real Time")
        for pid in ("EQ-RT-A1", "EQ-RT-A2")
    ]
    for r in refs:
        apply_decision(r, decision="approve", decided_by="reviewer@jpmc",
                       node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    bundle = export_bundle(source_env="test-A",
                           created_at="2026-01-01T00:00:00.000Z")

    # Env B — fresh, same taxonomy.
    fake_b = _fresh_store()
    summary = import_bundle(bundle)
    assert summary.total == 2 and summary.applied == 2, summary
    assert summary.skipped_unknown_node == 0
    assert summary.skipped_out_of_scope == 0
    # Taxonomy fingerprints match (both envs seeded identically).
    assert summary.taxonomy_version_source == summary.taxonomy_version_local

    # Each precedent must replay through map_vendor_product on env B.
    for r in refs:
        result = map_vendor_product(r)
        assert result.status == MappingStatus.APPROVED, (r.product_id, result.status)
        assert result.mapped_node_iri == EQ_PRICES_IRI
        assert result.rationale == "precedent"


@case("M6_reimport_is_idempotent")
def _():
    fake_a = _fresh_store()
    fake_a.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-IDEM",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    bundle = export_bundle(source_env="test",
                           created_at="2026-01-01T00:00:00.000Z")

    fake_b = _fresh_store()
    import_bundle(bundle)
    rank_after_first = fake_b.rank_signals_for(
        fake_b.vendor_signature(IN_SCOPE_VENDOR,
                                "Equity Prices Real Time", "EQ-IDEM")
    ).get(EQ_PRICES_IRI, 0)
    # Second import on the SAME store — must not double the rank signal.
    import_bundle(bundle)
    rank_after_second = fake_b.rank_signals_for(
        fake_b.vendor_signature(IN_SCOPE_VENDOR,
                                "Equity Prices Real Time", "EQ-IDEM")
    ).get(EQ_PRICES_IRI, 0)
    assert rank_after_first == rank_after_second == 1, \
        (rank_after_first, rank_after_second)
    # Precedent still resolves to APPROVED.
    result = map_vendor_product(ref)
    assert result.status == MappingStatus.APPROVED


@case("M6_taxonomy_version_changes_when_taxonomy_changes")
def _():
    fake = _fresh_store()
    v1 = bundle_mod._taxonomy_hash(fake.list_taxonomy_nodes())
    fake.upsert_taxonomy_node(TaxonomyNode(iri="cdao:new", label="New Node"))
    v2 = bundle_mod._taxonomy_hash(fake.list_taxonomy_nodes())
    assert v1 != v2, (v1, v2)
    # Same store, same nodes, same hash — deterministic.
    v3 = bundle_mod._taxonomy_hash(fake.list_taxonomy_nodes())
    assert v2 == v3


@case("M6_import_skips_pattern_with_unknown_node_no_failure")
def _():
    # Env A has one confirmed mapping to cdao:eq-prices.
    fake_a = _fresh_store()
    fake_a.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-SKIP",
                           name="Equity Prices Real Time")
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.91)
    bundle = export_bundle(source_env="test",
                           created_at="2026-01-01T00:00:00.000Z")

    # Env B has a DIFFERENT taxonomy — missing the target node.
    fake_b = FakeStore(nodes=[TaxonomyNode(iri="cdao:fx", label="FX")])
    store_pkg.get_store = lambda: fake_b
    matching_mod.get_store = lambda: fake_b
    feedback_mod.get_store = lambda: fake_b
    bundle_mod.get_store = lambda: fake_b
    agent_mod.get_store = lambda: fake_b

    summary = import_bundle(bundle)
    assert summary.total == 1
    assert summary.applied == 0
    assert summary.skipped_unknown_node == 1
    # And the taxonomy fingerprints disagree, surfaced in the summary.
    assert summary.taxonomy_version_source != summary.taxonomy_version_local


@case("M6_import_skips_out_of_scope_vendor_no_failure")
def _():
    # Hand-build a bundle whose pattern is out of scope locally — bypassing
    # apply_decision's scope gate so the bundle is the only ingress.
    from ..models import (
        BundleProvenance, FieldRule, MappingBundle, MappingPattern,
    )
    fake = _fresh_store()
    pattern = MappingPattern(
        vendor="NotAVendor",
        product_id="X",
        product_name="anything",
        signature="NotAVendor::anything",
        mapped_node_iri=EQ_PRICES_IRI,
        mapped_node_label="Equity Prices",
        confidence=0.95,
        rank=1,
        field_normalisation=[FieldRule(vendor_field="name", cdao_field="prefLabel")],
        validations=[],
        provenance=BundleProvenance(
            decided_by="reviewer@jpmc",
            decided_at="2026-01-01T00:00:00.000Z",
            decision="approve",
        ),
    )
    bundle = MappingBundle(
        version="1.0.0",
        created_at="2026-01-01T00:00:00.000Z",
        source_env="adversarial",
        taxonomy_version="deadbeef",
        patterns=[pattern],
    )
    summary = import_bundle(bundle)
    assert summary.total == 1
    assert summary.applied == 0
    assert summary.skipped_out_of_scope == 1


@case("M6_bundle_rejects_incompatible_major_version")
def _():
    from ..models import MappingBundle
    _fresh_store()
    bundle = MappingBundle(
        version="2.0.0",  # major mismatch
        created_at="2026-01-01T00:00:00.000Z",
        source_env="future",
        taxonomy_version="x",
        patterns=[],
    )
    try:
        import_bundle(bundle)
    except ValueError as e:
        assert "incompatible" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError on major version mismatch")


# ──────────────────────────────────────────────────────────────────────────
# M8 — S3 frame source (federated audit fields + IRI parity)
# ──────────────────────────────────────────────────────────────────────────

def _m8_setup(objects=None, *, prefix=""):
    """Wire frames._read_vendor_frame to read from a FakeS3Client.

    Returns (fake, saved_settings) — caller restores via _restore_settings.
    """
    _fresh_store()  # reset module-level matcher state too
    fake = FakeS3Client(objects=objects)
    frames_mod._set_s3_client_for_test(fake)
    saved = _swap_settings(
        frame_source="s3",
        s3_bucket="test-bucket",
        s3_prefix=prefix,
    )
    return fake, saved


def _m8_teardown(saved):
    frames_mod._set_s3_client_for_test(None)
    _restore_settings(saved)


@case("M8_s3_reader_returns_VendorProductRef_when_key_present")
def _():
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR,
            "product_id": "EQ-RT-001",
            "name": "Equity Pricing Real Time",
            "description": "Real-time equity prices",
            "raw": {"permId": "EQ-RT-001"},
        }).encode("utf-8")
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/EQ-RT-001.json", body)
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-RT-001")
        assert ref is not None
        assert ref.vendor == IN_SCOPE_VENDOR
        assert ref.product_id == "EQ-RT-001"
        assert ref.name == "Equity Pricing Real Time"
        assert ref.raw == {"permId": "EQ-RT-001"}
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_returns_None_when_key_absent")
def _():
    """Mirrors the mock path's dict.get returning None — same shape, same
    contract. The matcher / route layer interpret None identically to the
    mock path."""
    fake, saved = _m8_setup()
    try:
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "NEVER-LANDED")
        assert ref is None
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_fails_closed_on_invalid_json")
def _():
    fake, saved = _m8_setup()
    try:
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/BAD-JSON.json", b"{not json")
        try:
            frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "BAD-JSON")
        except FrameDataError as e:
            assert "not valid JSON" in str(e), str(e)
        else:
            raise AssertionError("expected FrameDataError on invalid JSON")
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_fails_closed_on_missing_required_fields")
def _():
    """Pydantic validation at the boundary — upstream wrote a JSON object
    but it doesn't match VendorProductRef shape. The reader raises
    FrameDataError (distinguished from ValidationError so HTTP can map to
    502 'upstream bad data', not 400 'client bad body').
    """
    fake, saved = _m8_setup()
    try:
        # Missing vendor and product_id — required by Pydantic.
        body = json.dumps({"name": "incomplete"}).encode("utf-8")
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/INCOMPLETE.json", body)
        try:
            frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "INCOMPLETE")
        except FrameDataError as e:
            assert "VendorProductRef shape" in str(e), str(e)
        else:
            raise AssertionError("expected FrameDataError on schema mismatch")
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_populates_source_content_hash_deterministically")
def _():
    """sha256 of the body bytes; identical body → identical hash."""
    import hashlib
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-1",
            "name": "x",
        }, sort_keys=True).encode("utf-8")
        expected = hashlib.sha256(body).hexdigest()
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/EQ-1.json", body)
        ref1 = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-1")
        ref2 = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-1")
        assert ref1.source_content_hash == expected
        assert ref1.source_content_hash == ref2.source_content_hash
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_populates_source_file_audit_id_from_metadata")
def _():
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-2", "name": "x",
        }).encode("utf-8")
        fake.put(
            "test-bucket", f"{IN_SCOPE_VENDOR}/EQ-2.json", body,
            metadata={"file-audit-id": "audit-row-42"},
        )
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-2")
        assert ref.source_file_audit_id == "audit-row-42"
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_audit_id_None_when_metadata_absent")
def _():
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-3", "name": "x",
        }).encode("utf-8")
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/EQ-3.json", body)
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-3")
        assert ref.source_file_audit_id is None
        assert ref.source_content_hash is not None  # always populated
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_ignores_upstream_supplied_source_fields")
def _():
    """source_* are READER-COMPUTED audit fields. An upstream that tries to
    set them (deliberately or not) gets silently overwritten — this is the
    correct discipline for an audit value: the reader is the source of
    truth, not the payload."""
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-4", "name": "x",
            "source_content_hash": "ATTACKER-CONTROLLED",
            "source_file_audit_id": "ATTACKER-CONTROLLED",
        }).encode("utf-8")
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/EQ-4.json", body,
                 metadata={"file-audit-id": "real-audit-id"})
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-4")
        assert ref.source_content_hash != "ATTACKER-CONTROLLED"
        assert ref.source_file_audit_id == "real-audit-id"
    finally:
        _m8_teardown(saved)


@case("M8_s3_reader_requires_bucket_setting")
def _():
    """Misconfiguration trips a RuntimeError, not a silent empty read."""
    fake, saved = _m8_setup()
    # Override bucket to empty.
    saved2 = _swap_settings(frame_source="s3", s3_bucket="")
    try:
        try:
            frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "X")
        except RuntimeError as e:
            assert "S3_WORKING_SET_BUCKET" in str(e)
        else:
            raise AssertionError("expected RuntimeError when bucket unset")
    finally:
        _restore_settings(saved2)
        _m8_teardown(saved)


@case("M8_s3_reader_honours_s3_prefix")
def _():
    fake, saved = _m8_setup(prefix="env/uat/")
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-5", "name": "x",
        }).encode("utf-8")
        # Key MUST include the prefix.
        fake.put("test-bucket", f"env/uat/{IN_SCOPE_VENDOR}/EQ-5.json", body)
        ref = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-5")
        assert ref is not None
        # Same vendor/product_id but at top-of-bucket — must NOT be found.
        ref2 = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "OTHER")
        assert ref2 is None
    finally:
        _m8_teardown(saved)


@case("M8_iri_parity_mock_vs_s3_for_same_vendor_product")
def _():
    """The contract test. For the same (vendor, product_id), the mock path
    and the S3 path MUST produce identical IRIs. Catches I8 drift if the
    upstream pipeline ever normalises product_id differently from
    mds_iri — which silently re-forks every IRI in the system."""
    # Mock path — no S3 client wired.
    ref_mock = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-PARITY-001",
        name="Equity Pricing Real Time",
    )
    iri_mock = ref_mock.iri

    # S3 path — same (vendor, product_id) shape lands in the bucket.
    fake, saved = _m8_setup()
    try:
        body = json.dumps({
            "vendor": IN_SCOPE_VENDOR, "product_id": "EQ-PARITY-001",
            "name": "Equity Pricing Real Time",
        }).encode("utf-8")
        fake.put("test-bucket", f"{IN_SCOPE_VENDOR}/EQ-PARITY-001.json", body)
        ref_s3 = frames_mod._read_vendor_frame(IN_SCOPE_VENDOR, "EQ-PARITY-001")
        assert ref_s3.iri == iri_mock, (ref_s3.iri, iri_mock)
        # And the S3 path additionally provides federated-audit provenance.
        assert ref_s3.source_content_hash is not None
        # The mock path has no upstream provenance to record.
        assert ref_mock.source_content_hash is None
    finally:
        _m8_teardown(saved)


@case("M8_source_fields_propagate_through_matcher_on_every_path")
def _():
    """Every map_vendor_product return path copies source_content_hash and
    source_file_audit_id from the ref onto the MappingResult. Lets an
    auditor trace any decision to the exact landed file."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)

    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-PROP-1",
        name="Equity Prices Real Time",
        source_content_hash="hash-A",
        source_file_audit_id="audit-A",
    )
    # Path: above-floor → AUTO_MAPPED
    r = map_vendor_product(ref)
    assert r.source_content_hash == "hash-A"
    assert r.source_file_audit_id == "audit-A"

    # Path: OUT_OF_SCOPE
    ref_oos = VendorProductRef(
        vendor="NotAVendor", product_id="X", name="x",
        source_content_hash="hash-B",
        source_file_audit_id="audit-B",
    )
    r_oos = map_vendor_product(ref_oos)
    assert r_oos.status == MappingStatus.OUT_OF_SCOPE
    assert r_oos.source_content_hash == "hash-B"
    assert r_oos.source_file_audit_id == "audit-B"

    # Path: precedent reuse — now PREFERS the PERSISTED decision-time
    # source (from the edge) over the current call's ref. The decision was
    # approved against hash-A; even when a later call presents hash-C-newer,
    # the precedent reuse reports hash-A as the audit identity. This is the
    # federated audit chain: decisions are traceable to the frame they were
    # made against, not the frame the matcher happens to see on replay.
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.95)
    ref_next = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-PROP-1",
        name="Equity Prices Real Time",
        source_content_hash="hash-C-newer",
        source_file_audit_id="audit-C-newer",
    )
    r_prec = map_vendor_product(ref_next)
    assert r_prec.status == MappingStatus.APPROVED
    assert r_prec.source_content_hash == "hash-A", r_prec.source_content_hash
    assert r_prec.source_file_audit_id == "audit-A", r_prec.source_file_audit_id

    # Path: precedent reuse FALLBACK — when the persisted source is None
    # (e.g. an old edge written before this seam extension, or a HITL
    # approval made via the mock/inline path with no upstream provenance),
    # the current call's ref values are used. Tests by injecting a precedent
    # that lacks source fields and confirming the reuse path fills from ref.
    fake._precedent_meta[(IN_SCOPE_VENDOR, "EQ-PROP-1")][
        "source_content_hash"] = None
    fake._precedent_meta[(IN_SCOPE_VENDOR, "EQ-PROP-1")][
        "source_file_audit_id"] = None
    r_prec_fallback = map_vendor_product(ref_next)
    assert r_prec_fallback.source_content_hash == "hash-C-newer"
    assert r_prec_fallback.source_file_audit_id == "audit-C-newer"


@case("M8_source_fields_persist_on_precedent_edge")
def _():
    """The store seam contract: upsert_precedent MUST persist source_*
    onto the precedent edge so the audit chain survives the in-memory
    MappingResult. Read back via list_confirmed_precedents directly
    (bypassing get_precedent_mapping) to assert the persistence path."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-PERSIST",
        name="Equity Prices Real Time",
        source_content_hash="sha256:abc123",
        source_file_audit_id="audit-row-7",
    )
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.95)

    confirmed = fake.list_confirmed_precedents()
    matches = [c for c in confirmed if c["product_id"] == "EQ-PERSIST"]
    assert len(matches) == 1, confirmed
    rec = matches[0]
    assert rec["source_content_hash"] == "sha256:abc123"
    assert rec["source_file_audit_id"] == "audit-row-7"


@case("M8_bundle_round_trip_preserves_source_fields_across_envs")
def _():
    """Env A approves with source identity X; export; import into env B;
    map_vendor_product in B for the same product replays X as the audit
    identity, not None and not a re-derived value. This is the closed
    federated-audit chain across environments — the bundle artifact alone
    is enough to reproduce decision-time provenance."""
    # Env A
    fake_a = _fresh_store()
    fake_a.set_score(EQ_PRICES_IRI, 0.95)
    ref_a = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-XENV",
        name="Equity Prices Real Time",
        source_content_hash="sha256:envA-original",
        source_file_audit_id="audit-envA-42",
    )
    apply_decision(ref_a, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=EQ_PRICES_IRI, suggested_confidence=0.95)
    bundle = export_bundle(source_env="env-A",
                           created_at="2026-06-09T00:00:00.000Z")

    # Bundle pattern carries the source fields on provenance.
    assert len(bundle.patterns) == 1
    prov = bundle.patterns[0].provenance
    assert prov.source_content_hash == "sha256:envA-original"
    assert prov.source_file_audit_id == "audit-envA-42"

    # Env B — fresh store, same taxonomy. Import.
    fake_b = _fresh_store()
    summary = import_bundle(bundle)
    assert summary.applied == 1, summary

    # Map a fresh ref in B with DIFFERENT source identity. The precedent
    # reuse path returns the persisted decision-time identity from env A,
    # NOT what env B's fresh ref happens to carry.
    ref_b_fresh = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-XENV",
        name="Equity Prices Real Time",
        source_content_hash="sha256:envB-stale-different",
        source_file_audit_id="audit-envB-unrelated",
    )
    result_b = map_vendor_product(ref_b_fresh)
    assert result_b.status == MappingStatus.APPROVED
    assert result_b.source_content_hash == "sha256:envA-original", \
        result_b.source_content_hash
    assert result_b.source_file_audit_id == "audit-envA-42", \
        result_b.source_file_audit_id


@case("M8_inline_path_leaves_source_fields_None")
def _():
    """When name/description are supplied inline by the HTTP caller (no
    frame lookup), source_* stay None — there is no upstream provenance to
    record. Backward-compatible for every test that pre-dates M8.
    """
    _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-INLINE",
        name="x", description="y",
    )
    assert ref.source_content_hash is None
    assert ref.source_file_audit_id is None


# ──────────────────────────────────────────────────────────────────────────
# M9 — agent runner (scripted backend; Bedrock backend tested separately
# when sandbox access is wired)
# ──────────────────────────────────────────────────────────────────────────

@case("AGENT_scripted_emits_full_well_formed_event_sequence")
def _():
    """Scripted agent should emit, in order: start → tool_call/tool_result
    pairs → agent_message(s) → final_result → done. Every event JSON-
    serialises. The 'final_result' carries a MappingResult-shaped payload."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-AGENT-1",
        name="Equity Prices Real Time",
    )

    events = list(ScriptedMappingAgent().run(ref))
    types = [e.type for e in events]

    # First and last events are pinned.
    assert types[0] == "start", types
    assert types[-1] == "done", types
    # Final result lands before done.
    assert "final_result" in types
    assert types.index("final_result") < types.index("done")
    # Tool calls and results come in pairs (every call has a result).
    tool_calls = [e for e in events if e.type == "tool_call"]
    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_calls) == len(tool_results), (tool_calls, tool_results)
    # map_vendor_product is always called — it's the authoritative step.
    assert any(c.payload["tool"] == "map_vendor_product" for c in tool_calls)
    # Every event JSON-serialises.
    for e in events:
        json.loads(e.to_json())


@case("AGENT_scripted_final_result_matches_deterministic_matcher")
def _():
    """The agent's final_result MUST equal what map_vendor_product returns
    for the same ref. The agent's role is to make the MCP exercise visible,
    not to second-guess the matcher (M9 contract pinned in matching.py)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-AGENT-2",
        name="Equity Prices Real Time",
    )

    direct = map_vendor_product(ref).model_dump(mode="json")

    final = None
    for ev in ScriptedMappingAgent().run(ref):
        if ev.type == "final_result":
            final = ev.payload["mapping"]

    assert final is not None, "agent did not emit final_result"
    # The matcher is replay-safe, so the dumps compare equal field by field.
    for k in ("vendor", "product_id", "mapped_node_iri", "mapped_node_label",
              "status", "confidence", "rationale"):
        assert final[k] == direct[k], (k, final[k], direct[k])


@case("AGENT_scripted_out_of_scope_short_circuits_to_OUT_OF_SCOPE")
def _():
    """Out-of-scope vendor should still produce a clean event sequence and
    a final_result with status OUT_OF_SCOPE. The agent must not pretend
    to score it."""
    _fresh_store()
    ref = VendorProductRef(
        vendor="NotAVendor", product_id="X", name="anything",
    )
    events = list(ScriptedMappingAgent().run(ref))
    final = next(e for e in events if e.type == "final_result")
    assert final.payload["mapping"]["status"] == MappingStatus.OUT_OF_SCOPE.value


@case("AGENT_factory_honours_SCUDO_AGENT_BACKEND_env")
def _():
    """get_agent() defaults to scripted; explicit 'scripted' is also scripted;
    explicit 'bedrock' returns the Bedrock class (constructor only — its
    .run() is lazy so we don't actually invoke boto3 here)."""
    import os
    saved = os.environ.get("SCUDO_AGENT_BACKEND")
    try:
        if saved is not None:
            del os.environ["SCUDO_AGENT_BACKEND"]
        default = get_agent()
        assert type(default).__name__ == "ScriptedMappingAgent", type(default)

        os.environ["SCUDO_AGENT_BACKEND"] = "scripted"
        explicit_scripted = get_agent()
        assert type(explicit_scripted).__name__ == "ScriptedMappingAgent"

        os.environ["SCUDO_AGENT_BACKEND"] = "bedrock"
        bedrock = get_agent()
        # We DO NOT call .run() here — that would try to import strands and
        # hit boto3/Bedrock. We only verify the factory returned the class
        # and that the default model id is what we expect for eu-west-2.
        assert type(bedrock).__name__ == "BedrockMappingAgent"
        assert "claude-opus-4-8" in bedrock._model_id  # type: ignore[attr-defined]
    finally:
        if saved is None:
            os.environ.pop("SCUDO_AGENT_BACKEND", None)
        else:
            os.environ["SCUDO_AGENT_BACKEND"] = saved


@case("AGENT_event_payload_carries_backend_identity")
def _():
    """The 'start' event must identify which backend produced it, so the
    frontend can label the activity log ('scripted' vs 'bedrock' vs swap).
    """
    _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-AGENT-LBL",
        name="Equity Prices Real Time",
    )
    first = next(ScriptedMappingAgent().run(ref))
    assert first.type == "start"
    assert first.payload["agent_backend"] == "scripted"


# ──────────────────────────────────────────────────────────────────────
# Three-MCP trust gradient: HMAC verdict seal + trust isolation + the
# I5 gate (agent-driven AUTO_MAPPED never writes; HITL does).
# ──────────────────────────────────────────────────────────────────────

def _ensure_dev_signing_key():
    """Smoke runs with the dev fallback HMAC key. Production sets
    SCUDO_VERDICT_SIGNING_KEY via Secrets Manager."""
    import os as _os
    _os.environ["SCUDO_VERDICT_ALLOW_DEV"] = "1"


@case("VERDICT_sign_verify_roundtrip_is_ok")
def _():
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v
    seal = v.sign(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-1",
        mapped_node_iri=EQ_PRICES_IRI,
        status="auto_mapped", confidence=0.95,
    )
    r = v.verify(seal,
                 expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
    assert r.ok, r.reason
    assert r.payload["mapped_node_iri"] == EQ_PRICES_IRI
    assert r.payload["status"] == "auto_mapped"


@case("VERDICT_forged_seal_is_refused")
def _():
    """Tampering the HMAC bytes (without changing the payload) must
    refuse via seal_mismatch. This is the core forgery-resistance test."""
    import base64
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v
    seal = v.sign(vendor=IN_SCOPE_VENDOR, product_id="EQ-1",
                  mapped_node_iri="cdao:x", status="auto_mapped",
                  confidence=0.95)
    forged = {
        "payload_b64": seal["payload_b64"],
        "hmac_b64": base64.b64encode(b"X" * 32).decode("ascii"),
    }
    r = v.verify(forged,
                 expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
    assert not r.ok
    assert r.reason == "seal_mismatch", r.reason


@case("VERDICT_expired_seal_is_refused")
def _():
    """A seal older than SCUDO_VERDICT_MAX_AGE_SECONDS is refused — agents
    can't sit on a verdict for hours and quietly commit later."""
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v
    seal = v.sign(vendor=IN_SCOPE_VENDOR, product_id="EQ-1",
                  mapped_node_iri="cdao:x", status="auto_mapped",
                  confidence=0.95, ts_ms=1000)  # 1970
    r = v.verify(seal,
                 expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
    assert not r.ok
    assert r.reason == "seal_expired", r.reason


@case("VERDICT_identity_replay_is_refused")
def _():
    """A valid seal for product A cannot be replayed against product B.
    Closes the I8-at-the-seal-boundary hole."""
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v
    seal_a = v.sign(vendor=IN_SCOPE_VENDOR, product_id="A",
                    mapped_node_iri="cdao:x", status="auto_mapped",
                    confidence=0.95)
    r = v.verify(seal_a, expected_vendor=IN_SCOPE_VENDOR,
                 expected_product_id="B")
    assert not r.ok
    assert r.reason == "identity_mismatch", r.reason


@case("TRUST_ingestion_mcp_imports_no_writers")
def _():
    """Static AST check: the Ingestion MCP module does NOT import any
    write-side module. If a future change adds `from .feedback import ...`
    or `from .bundle import import_bundle`, this fails — making the trust
    boundary load-bearing, not aspirational."""
    import ast, pathlib
    path = pathlib.Path(
        "scudo_mapping_mcp/ingestion_mcp.py"
    ).resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.endswith("feedback") or mod.endswith("bundle"):
                forbidden.add(mod)
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle",
                              "upsert_precedent"}:
                    forbidden.add(f"{mod}.{n.name}")
        elif isinstance(node, ast.Import):
            for n in node.names:
                if n.name.endswith(".feedback") or n.name.endswith(".bundle"):
                    forbidden.add(n.name)
    assert not forbidden, (
        f"Ingestion MCP must not import write surfaces; found: {forbidden}"
    )


@case("TRUST_match_verify_mcp_imports_no_writers")
def _():
    """Same static check for Match & Verify. The verifier runs here but
    nothing it does should mutate canonical state."""
    import ast, pathlib
    path = pathlib.Path(
        "scudo_mapping_mcp/match_verify_mcp.py"
    ).resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.endswith("feedback"):
                forbidden.add(mod)
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle",
                              "upsert_precedent"}:
                    forbidden.add(f"{mod}.{n.name}")
    assert not forbidden, (
        f"Match & Verify MCP must not import write surfaces; found: "
        f"{forbidden}"
    )


@case("TRUST_persistence_mcp_imports_writers")
def _():
    """The inverse: Persistence MCP IS the only writer, so it must
    import feedback (for record_decision) and bundle (for import_bundle).
    Asserts the writer role is co-located, not scattered."""
    import ast, pathlib
    path = pathlib.Path(
        "scudo_mapping_mcp/persistence_mcp.py"
    ).resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle",
                              "export_bundle"}:
                    seen.add(n.name)
    assert "apply_decision" in seen, (
        "Persistence MCP must own the HITL write path"
    )
    assert "import_bundle" in seen, (
        "Persistence MCP must own bundle import"
    )


@case("GATE_refuses_agent_driven_auto_mapped_per_I5")
def _():
    """The load-bearing demo of the trust gradient: even when the
    matcher emits AUTO_MAPPED at 0.95 confidence and the seal verifies
    cryptographically, the Gate refuses agent-driven commit and routes
    to the reviewer queue. I5 enforced physically, not by doctrine.
    """
    _ensure_dev_signing_key()
    import asyncio
    from scudo_mapping_mcp import verdict as v, persistence_mcp as pm

    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-GATE-1",
        name="Equity Prices Real Time",
    )

    # Sign an AUTO_MAPPED verdict the way Match & Verify would.
    direct = map_vendor_product(ref)
    assert direct.status == MappingStatus.AUTO_MAPPED, direct.status
    seal = v.sign(
        vendor=ref.vendor, product_id=ref.product_id,
        mapped_node_iri=direct.mapped_node_iri,
        status=direct.status.value, confidence=direct.confidence,
    )

    params = pm.CommitInput(
        vendor=ref.vendor, product_id=ref.product_id,
        verdict=direct.model_dump(mode="json"), seal=seal,
    )
    result = asyncio.run(pm.commit_mapping(params))
    body = json.loads(result)
    assert body["committed"] is False
    assert body["refusal"]["reason"] == "auto_mapped_requires_review", body
    # Queued for HITL — exactly the I5 escalate path.
    assert "queue_id" in body["refusal"]
    assert pm.reviewer_queue_snapshot(), "verdict must be enqueued for HITL"


@case("GATE_refuses_forged_seal_before_anything_else")
def _():
    """A forged seal must be the FIRST gate to fire — before scope, before
    I5, before any other check. Otherwise an attacker probing refusal
    reasons can fingerprint the gate's policy order."""
    _ensure_dev_signing_key()
    import asyncio, base64
    from scudo_mapping_mcp import verdict as v, persistence_mcp as pm

    _fresh_store()
    real_seal = v.sign(
        vendor=IN_SCOPE_VENDOR, product_id="X",
        mapped_node_iri="cdao:x", status="auto_mapped", confidence=0.95,
    )
    forged = {
        "payload_b64": real_seal["payload_b64"],
        "hmac_b64": base64.b64encode(b"X" * 32).decode("ascii"),
    }
    params = pm.CommitInput(
        vendor=IN_SCOPE_VENDOR, product_id="X",
        verdict={"status": "auto_mapped"}, seal=forged,
    )
    body = json.loads(asyncio.run(pm.commit_mapping(params)))
    assert body["committed"] is False
    assert body["refusal"]["reason"] == "seal_mismatch", body


@case("GATE_writes_via_HITL_record_decision_path")
def _():
    """The legal write path: a human approves via persist.record_decision,
    which calls feedback.apply_decision, which atomically upserts the
    confirmed precedent. Subsequent map calls reuse it."""
    _ensure_dev_signing_key()
    import asyncio
    from scudo_mapping_mcp import persistence_mcp as pm

    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-HITL-1",
        name="Equity Prices Real Time",
    )
    params = pm.DecisionInput(
        vendor=ref.vendor, product_id=ref.product_id,
        decision="approve", decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI, name=ref.name,
        suggested_confidence=0.95,
    )
    body = json.loads(asyncio.run(pm.record_decision(params)))
    assert body["committed"] is True
    assert body["result"]["status"] == "approved", body

    # Next map call reuses the precedent (proves the write landed).
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.APPROVED, r.status


@case("DEFENSE_IN_DEPTH_scope_gate_called_at_all_three_layers")
def _():
    """The scope gate is the only fail-closed gate that fires at every
    layer. Static check: it's imported by all three MCP modules."""
    import ast, pathlib
    for mod_name in (
        "ingestion_mcp.py",
        "match_verify_mcp.py",
        "persistence_mcp.py",
    ):
        path = pathlib.Path(
            f"scudo_mapping_mcp/{mod_name}"
        ).resolve()
        src = path.read_text(encoding="utf-8")
        # Either check_scope directly imported, OR (for Persistence) the
        # apply_decision import which calls check_scope transitively.
        has_check_scope = "check_scope" in src
        has_apply_decision = "apply_decision" in src
        assert has_check_scope or has_apply_decision, (
            f"{mod_name} must enforce the scope gate (directly via "
            f"check_scope, or transitively via apply_decision)"
        )


# ──────────────────────────────────────────────────────────────────────────
# COST LADDER — three-band Gate (matching.py)
# ──────────────────────────────────────────────────────────────────────────


@case("LADDER_pass_band_auto_maps_without_specialist")
def _():
    """Clearly above floor: status AUTO_MAPPED, band 'pass', specialist NOT
    consulted (it'd be pure cost with no upside)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)  # well above floor+0.05 == 0.85
    fake.set_score(EQUITIES_IRI, 0.30)

    calls: list[VendorProductRef] = []
    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None
    set_specialist_scorer(spy)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-PASS",
            name="Equity Prices Real Time",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.band == "pass", r.band
    assert calls == [], (
        f"PASS band must not invoke specialist; called {len(calls)} time(s)"
    )


@case("LADDER_borderline_band_invokes_specialist")
def _():
    """In the ±0.05 window around the floor: specialist IS consulted."""
    fake = _fresh_store()
    # 0.82: above floor (0.80), below pass threshold (0.85)
    fake.set_score(EQ_PRICES_IRI, 0.82)
    fake.set_score(EQUITIES_IRI, 0.30)

    calls: list[VendorProductRef] = []
    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None  # abstain
    set_specialist_scorer(spy)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-BORDER",
            name="Equity Prices Real Time",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.band == "borderline", r.band
    assert len(calls) == 1, (
        f"BORDERLINE must invoke specialist exactly once; got {len(calls)}"
    )


@case("LADDER_fail_band_skips_specialist")
def _():
    """Clearly below floor: status NEEDS_REVIEW, band 'fail', specialist
    NOT consulted (ladder discipline — LLM only runs on resolvable cases)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.40)  # well below floor-0.05 == 0.75
    fake.set_score(EQUITIES_IRI, 0.30)

    calls: list[VendorProductRef] = []
    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None
    set_specialist_scorer(spy)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-FAIL",
            name="random gibberish",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert r.band == "fail", r.band
    assert calls == [], (
        f"FAIL band must not invoke specialist; called {len(calls)} time(s)"
    )


@case("LADDER_specialist_disagreement_caps_confidence_below_floor")
def _():
    """Specialist picks a different node from the sparse ranker — confidence
    is capped below the floor and the case lands in NEEDS_REVIEW. The
    disagreement, not the number, decides."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.82)   # sparse ranker would pick this
    fake.set_score(EQUITIES_IRI, 0.81)    # specialist will pick this instead
    fake.set_score(FX_IRI, 0.10)

    def disagreeing_specialist(ref, candidates):  # noqa: ANN001
        # Sparse ranker's top is EQ_PRICES; specialist picks EQUITIES at
        # claimed-high confidence. The matcher must IGNORE the claim and
        # cap below floor because the two arms disagree.
        return Candidate(
            node=TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
            similarity=0.99,
        )

    set_specialist_scorer(disagreeing_specialist)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-DISAGREE",
            name="Equity Prices Real Time",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.band == "borderline", r.band
    assert r.status == MappingStatus.NEEDS_REVIEW, (
        f"disagreement must NOT auto-map (I5); got {r.status}"
    )
    assert r.confidence < config_mod.settings.confidence_floor, (
        f"confidence must be capped below floor; got {r.confidence}"
    )
    # Reviewer sees the specialist's pick — it carries more semantic
    # information than the sparse ranker's.
    assert r.mapped_node_iri == EQUITIES_IRI, r.mapped_node_iri


@case("LADDER_specialist_concurrence_in_borderline_can_auto_map")
def _():
    """Borderline + specialist agrees on the sparse ranker's top pick at a
    confidence above the floor -> AUTO_MAPPED. This is the 'borderline
    retry succeeds' path the diagram calls out."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.82)   # borderline
    fake.set_score(EQUITIES_IRI, 0.30)

    def concurring_specialist(ref, candidates):  # noqa: ANN001
        # Same node as best candidate, but reports higher confidence.
        return Candidate(
            node=TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices",
                              parent_iri=EQUITIES_IRI),
            similarity=0.91,
        )

    set_specialist_scorer(concurring_specialist)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-CONCUR",
            name="Equity Prices Real Time",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.band == "borderline", r.band
    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.mapped_node_iri == EQ_PRICES_IRI
    assert r.confidence >= config_mod.settings.confidence_floor


@case("LADDER_required_validation_failure_skips_specialist")
def _():
    """A required-validation failure is a hard FAIL — even at high similarity
    the specialist must NOT be consulted (I6: invariants stay outside the
    model)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.99)
    fake.get_taxonomy_node = lambda iri: None  # identifier_resolves fails

    calls: list[VendorProductRef] = []
    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None
    set_specialist_scorer(spy)
    try:
        r = map_vendor_product(VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="LADDER-REQFAIL",
            name="Equity Prices Real Time",
        ))
    finally:
        set_specialist_scorer(None)

    assert r.band == "fail", r.band
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert calls == [], (
        "required-validation failure must not consult specialist"
    )


@case("LADDER_out_of_scope_carries_band_na")
def _():
    """OUT_OF_SCOPE is outside the band model entirely."""
    _fresh_store()
    r = map_vendor_product(VendorProductRef(
        vendor="NotAVendor", product_id="x", name="anything",
    ))
    assert r.status == MappingStatus.OUT_OF_SCOPE
    assert r.band == "n/a", r.band


# ──────────────────────────────────────────────────────────────────────────
# FALKOR FUSION — BM25 sidecar + Reciprocal Rank Fusion
# ──────────────────────────────────────────────────────────────────────────


@case("FUSION_bm25_recovers_exact_token_match_over_semantic_neighbour")
def _():
    """BM25 must score a document containing the query's exact token higher
    than a semantically-adjacent doc that lacks the token. This is what
    recovers ticker / RIC / ISIN matches that pure-semantic arms miss."""
    docs = [
        ("with_token", "RIC AAPL.O equity real time price"),
        ("semantic_neighbour", "real time equity quote feed"),
        ("unrelated", "fixed income spreads"),
    ]
    scores = RetrievalStore.bm25_scores("AAPL.O", docs)
    assert scores["with_token"] > scores["semantic_neighbour"], scores
    assert scores["semantic_neighbour"] == 0.0, (
        f"semantic neighbour shares no query tokens; got {scores}"
    )


@case("FUSION_rrf_blends_rankings_without_letting_either_dominate")
def _():
    """RRF must surface a doc that is moderately ranked by BOTH arms above
    a doc that is #1 in one arm but absent from the other. That's the
    "neither arm dominates" property the brief calls out — a hit visible
    to both rankers compounds; a single-arm spike does not.
    """
    # Arm A: dense (semantic). Sorted: dense_only (1), blended (2). lex_only ABSENT.
    dense = {"dense_only": 0.95, "blended": 0.50}
    # Arm B: lexical. Sorted: lex_only (1), blended (2). dense_only ABSENT.
    lexical = {"lex_only": 12.0, "blended": 6.0}

    fused = RetrievalStore.reciprocal_rank_fusion([dense, lexical])

    # blended sits at rank 2 in BOTH arms; both single-arm winners are #1
    # in one and missing/lower in the other. The doc visible to both
    # rankers must win.
    assert fused["blended"] > fused["dense_only"], fused
    assert fused["blended"] > fused["lex_only"], fused


@case("FUSION_rrf_constant_is_seamed")
def _():
    """RRF_K is exposed on the seam so the matcher tunes it in one place."""
    assert RetrievalStore.RRF_K == 60


# ──────────────────────────────────────────────────────────────────────────
# Hydration — pull the canonical M6 bundle from S3 at container startup
# ──────────────────────────────────────────────────────────────────────────

def _hydrate_setup(objects=None, *, bucket="test-bucket"):
    """Wire hydrate._s3_client to a FakeS3Client. Returns (fake, saved_settings).
    Caller restores via _restore_settings(saved).
    """
    _fresh_store()
    fake = FakeS3Client(objects=objects)
    hydrate_mod._set_s3_client_for_test(fake)
    saved = _swap_settings(s3_bucket=bucket)
    return fake, saved


def _hydrate_teardown(saved):
    hydrate_mod._set_s3_client_for_test(None)
    _restore_settings(saved)


def _sample_bundle_json(taxonomy_node_iri=EQ_PRICES_IRI):
    """Build a valid MappingBundle with one CONFIRMED precedent and return its
    JSON-encoded bytes. The pattern points at a taxonomy node that the
    _fresh_store() FakeStore knows about, so import_bundle applies it."""
    fake = _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="HYD-1",
        name="Equity Pricing Real Time",
    )
    apply_decision(ref, decision="approve", decided_by="reviewer@jpmc",
                   node_iri=taxonomy_node_iri, suggested_confidence=0.93)
    bundle = export_bundle(
        source_env="test-source",
        created_at="2026-06-09T00:00:00.000Z",
    )
    return bundle.model_dump_json().encode("utf-8")


@case("HYDRATE_cold_start_returns_skipped_no_bundle")
def _():
    """No canonical bundle in S3 yet (first deploy). hydrate() returns
    skipped_no_bundle=True and does NOT raise — that's how we let the
    very first deploy come up green with an empty FalkorDB."""
    fake, saved = _hydrate_setup()  # FakeS3 with no objects
    try:
        result = hydrate(strict=True)
        assert result.skipped_no_bundle is True
        assert result.applied == 0
        assert result.total == 0
        assert result.bundle_version is None
    finally:
        _hydrate_teardown(saved)


@case("HYDRATE_happy_path_applies_bundle_patterns")
def _():
    """Canonical bundle present. hydrate() pulls it, validates the
    MappingBundle shape, replays via import_bundle, returns applied count."""
    body = _sample_bundle_json()
    fake, saved = _hydrate_setup()
    try:
        # Reset the store after _sample_bundle_json's apply_decision so we're
        # hydrating into a fresh, empty store and the applied count is honest.
        _fresh_store()
        fake.put("test-bucket", "canonical/bundle-latest.json", body)
        result = hydrate(strict=True)
        assert result.skipped_no_bundle is False, result
        assert result.applied >= 1, result
        assert result.bundle_version is not None
        assert result.bundle_source_env == "test-source"
    finally:
        _hydrate_teardown(saved)


@case("HYDRATE_corrupt_json_raises_HydrationError")
def _():
    """A non-JSON body in the canonical key is a hard fail. Better to be
    unhealthy than half-hydrated."""
    fake, saved = _hydrate_setup()
    try:
        fake.put("test-bucket", "canonical/bundle-latest.json", b"{not json")
        try:
            hydrate(strict=True)
        except HydrationError as e:
            assert "not valid JSON" in str(e), str(e)
        else:
            raise AssertionError("expected HydrationError on corrupt JSON")
    finally:
        _hydrate_teardown(saved)


@case("HYDRATE_schema_mismatch_raises_HydrationError")
def _():
    """Valid JSON but does not match MappingBundle shape. Hard fail."""
    fake, saved = _hydrate_setup()
    try:
        fake.put("test-bucket", "canonical/bundle-latest.json",
                 json.dumps({"foo": "bar"}).encode("utf-8"))
        try:
            hydrate(strict=True)
        except HydrationError as e:
            assert "MappingBundle shape" in str(e), str(e)
        else:
            raise AssertionError("expected HydrationError on schema mismatch")
    finally:
        _hydrate_teardown(saved)


@case("HYDRATE_replay_is_idempotent")
def _():
    """Hydrating the same bundle twice into the same store: applied count is
    identical on both runs and the store state is unchanged after the second
    call (upsert_precedent uses MERGE; rank signal is derived not incremented)."""
    body = _sample_bundle_json()
    fake, saved = _hydrate_setup()
    try:
        _fresh_store()
        fake.put("test-bucket", "canonical/bundle-latest.json", body)
        first = hydrate(strict=True)
        # Capture the store state after the first hydration.
        store_pkg_fake = store_pkg.get_store()
        edges_first = len(getattr(store_pkg_fake, "_precedents", {}))
        second = hydrate(strict=True)
        edges_second = len(getattr(store_pkg_fake, "_precedents", {}))
        assert first.applied == second.applied, (first, second)
        assert edges_first == edges_second, (edges_first, edges_second)
    finally:
        _hydrate_teardown(saved)


@case("HYDRATE_canonical_bundle_uri_env_overrides_defaults")
def _():
    """SCUDO_CANONICAL_BUNDLE_URI overrides the default bucket+key resolution.
    Pin for cross-account setups (Persistence writes one bucket; Match&Verify
    hydrates from the same bucket configured via this env var)."""
    body = _sample_bundle_json()
    fake, saved = _hydrate_setup(bucket="default-bucket")
    saved_env = os.environ.get("SCUDO_CANONICAL_BUNDLE_URI")
    os.environ["SCUDO_CANONICAL_BUNDLE_URI"] = "s3://override-bucket/special/key.json"
    try:
        _fresh_store()
        # Body lives at the OVERRIDE bucket/key, not the default-bucket fallback.
        fake.put("override-bucket", "special/key.json", body)
        result = hydrate(strict=True)
        assert result.skipped_no_bundle is False, result
        assert result.applied >= 1, result
    finally:
        if saved_env is None:
            os.environ.pop("SCUDO_CANONICAL_BUNDLE_URI", None)
        else:
            os.environ["SCUDO_CANONICAL_BUNDLE_URI"] = saved_env
        _hydrate_teardown(saved)


@case("HYDRATE_format_major_mismatch_raises_HydrationError")
def _():
    """A bundle with a format major version this build doesn't understand is
    rejected with HydrationError. import_bundle's _enforce_format_compat
    raises ValueError; hydrate() wraps that as HydrationError so the
    container-startup orchestration can treat it uniformly."""
    body = _sample_bundle_json()
    # Hand-edit the JSON to bump major version.
    payload = json.loads(body.decode("utf-8"))
    payload["version"] = "9.9.9"  # major mismatch vs BUNDLE_FORMAT_VERSION
    body2 = json.dumps(payload).encode("utf-8")
    fake, saved = _hydrate_setup()
    try:
        _fresh_store()
        fake.put("test-bucket", "canonical/bundle-latest.json", body2)
        try:
            hydrate(strict=True)
        except HydrationError as e:
            assert "import_bundle refused" in str(e), str(e)
        else:
            raise AssertionError("expected HydrationError on format major mismatch")
    finally:
        _hydrate_teardown(saved)


def main() -> int:
    for name, ok, detail in _results:
        if ok:
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}\n        {detail}")
    failed = [r for r in _results if not r[1]]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} pass")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
