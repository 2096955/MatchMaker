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
from .. import enrichment as enrichment_mod
from .. import frames as frames_mod
from .. import hydrate as hydrate_mod
from .. import store as store_pkg
from ..agent import (
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
    ConceptualEdge,
    ConceptualEdgeKind,
    ConceptualNode,
    ConceptualNodeKind,
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
    conceptual_iri,
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

    def put_object(self, *, Bucket, Key, Body, **kwargs):  # noqa: N803 — boto3 kwarg names
        """Write path for export_to_s3. Accepts Body as bytes; the test passes bytes."""
        self._objects[(Bucket, Key)] = {
            "Body": Body if isinstance(Body, bytes) else Body.encode("utf-8"),
            "Metadata": {},
        }
        return {}

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
    fake = FakeStore(
        nodes=[
            TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
            TaxonomyNode(
                iri=EQ_PRICES_IRI, label="Equity Prices", parent_iri=EQUITIES_IRI
            ),
            TaxonomyNode(iri=FX_IRI, label="Foreign Exchange"),
        ]
    )
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
            _results.append(
                (name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            )
        return fn

    return deco


@case("iri_is_deterministic")
def _():
    assert mds_iri(IN_SCOPE_VENDOR, "EQ-RT-001") == mds_iri(
        IN_SCOPE_VENDOR, "EQ-RT-001"
    )
    assert mds_iri("S&P Global", "X").startswith("mds.sandpglobal:")


@case("out_of_scope_vendor_is_fail_closed")
def _():
    _fresh_store()
    r = map_vendor_product(
        VendorProductRef(
            vendor="NotAVendor",
            product_id="x",
            name="anything",
        )
    )
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
    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="X1",
            name="random gibberish that wont match anything",
        )
    )
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert r.confidence < 0.80


@case("above_floor_returns_auto_mapped")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.60)
    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="EQ-RT-001",
            name="Equity Prices Real Time",
        )
    )
    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.mapped_node_iri == EQ_PRICES_IRI
    assert r.confidence >= 0.80


@case("required_validation_failure_forces_needs_review")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.99)
    # identifier_resolves fails when the validator can't look the node up.
    fake.get_taxonomy_node = lambda iri: None  # type: ignore[assignment]
    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="EQ-RT-002",
            name="Equity Prices Real Time",
        )
    )
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    failed = [v for v in r.validations if v.required and v.status == "fail"]
    names = [v.name for v in failed]
    assert "identifier_resolves" in names, names


@case("reject_excludes_node_from_future_candidates")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    fake.set_score(EQUITIES_IRI, 0.85)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-003", name="Equity Prices Real Time"
    )
    apply_decision(
        ref, decision="reject", decided_by="reviewer@jpmc", node_iri=EQ_PRICES_IRI
    )
    r = map_vendor_product(ref)
    assert r.mapped_node_iri == EQUITIES_IRI, r.mapped_node_iri
    assert all(c.node.iri != EQ_PRICES_IRI for c in r.candidates)


@case("approve_writes_confirmed_precedent")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-004", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005", name="Equity Prices Real Time"
    )
    apply_decision(
        ref, decision="override", decided_by="reviewer@jpmc", node_iri=EQUITIES_IRI
    )
    sibling = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-005-B", name="Equity Prices Real Time"
    )
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
            VendorProductRef(
                vendor=IN_SCOPE_VENDOR,
                product_id=f"PRIOR-{i}",
                name="Equity Prices Real Time",
            ),
            decision="approve",
            decided_by=f"reviewer{i}@jpmc",
            node_iri=EQ_PRICES_IRI,
            suggested_confidence=0.91,
        )
    # New sibling product — different IRI, same signature.
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-NEW", name="Equity Prices Real Time"
    )
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-OV", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.95,
    )
    apply_decision(
        ref, decision="override", decided_by="reviewer@jpmc", node_iri=EQUITIES_IRI
    )

    # Precedent reuse now points to the OVERRIDE target, not the stale approve.
    r = map_vendor_product(ref)
    assert r.mapped_node_iri == EQUITIES_IRI, r.mapped_node_iri
    assert r.status == MappingStatus.OVERRIDDEN, r.status

    # Boost on the old node has been decremented back to 0; new node now at 1.
    sig = fake.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time", "EQ-RT-OV")
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-ING",
        name="Equity Prices Real Time",
        description="vendor description",
    )
    # Step 1: ingest writes the row (real flow goes through ingest.ingest_bytes
    # -> store.upsert_vendor_product per parsed row).
    fake.upsert_vendor_product(ref)
    # Step 2: HITL approves the previously-ingested product.
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )

    sig = fake.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time", "EQ-ING")
    boosts = fake.rank_signals_for(sig)
    assert boosts.get(EQ_PRICES_IRI, 0) == 1, (
        f"ingest-then-approve must contribute to rank signal; got {boosts!r}"
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-IDEMP", name="Equity Prices Real Time"
    )

    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
    sig = fake.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time", "EQ-IDEMP")
    boosts_first = fake.rank_signals_for(sig)
    precedent_first = fake.get_precedent_mapping(IN_SCOPE_VENDOR, "EQ-IDEMP")

    # Same decision, again, three times.
    for _ in range(3):
        apply_decision(
            ref,
            decision="approve",
            decided_by="reviewer@jpmc",
            node_iri=EQ_PRICES_IRI,
            suggested_confidence=0.91,
        )

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
        vendor = property(
            lambda self: (_ for _ in ()).throw(
                RuntimeError("simulated odrl lookup failure")
            )
        )

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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RA", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
    apply_decision(
        ref, decision="reject", decided_by="reviewer@jpmc", node_iri=EQUITIES_IRI
    )

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
    ref_x = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="X", name="Equity Prices Real Time"
    )
    ref_y = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="Y", name="Equity Prices Real Time"
    )
    apply_decision(
        ref_x, decision="reject", decided_by="reviewer@jpmc", node_iri=EQ_PRICES_IRI
    )
    # Product Y has no negative precedent; the matcher still considers
    # EQ_PRICES_IRI for Y.
    r = map_vendor_product(ref_y)
    assert r.mapped_node_iri == EQ_PRICES_IRI, r.mapped_node_iri


@case("out_of_scope_apply_decision_is_rejected_at_function_boundary")
def _():
    """Direct caller (not just HTTP) cannot write an out-of-scope precedent."""
    _fresh_store()
    ref = VendorProductRef(vendor="NotAVendor", product_id="X", name="anything")
    try:
        apply_decision(
            ref,
            decision="approve",
            decided_by="u",
            node_iri=EQ_PRICES_IRI,
            suggested_confidence=0.9,
        )
    except ValueError as e:
        assert "out of scope" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for out-of-scope vendor")


@case("apply_decision_validates_inputs")
def _():
    fake = _fresh_store()
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="X", name="foo")
    for case_args, expected in [
        (
            {
                "decision": "maybe",
                "decided_by": "u",
                "node_iri": EQ_PRICES_IRI,
                "suggested_confidence": 0.9,
            },
            "decision must be",
        ),
        (
            {
                "decision": "approve",
                "decided_by": "",
                "node_iri": EQ_PRICES_IRI,
                "suggested_confidence": 0.9,
            },
            "decided_by",
        ),
        (
            {
                "decision": "approve",
                "decided_by": "u",
                "node_iri": "",
                "suggested_confidence": 0.9,
            },
            "node_iri",
        ),
        (
            {
                "decision": "approve",
                "decided_by": "u",
                "node_iri": EQ_PRICES_IRI,
            },  # missing suggested_confidence
            "suggested_confidence is required",
        ),
        (
            {
                "decision": "approve",
                "decided_by": "u",
                "node_iri": "cdao:nope",
                "suggested_confidence": 0.9,
            },
            "unknown taxonomy node",
        ),
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-PROV", name="Equity Prices Real Time"
    )
    fake.upsert_precedent(
        ref=ref,
        node=TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices"),
        decision="approve",
        decided_by="auto",
        confidence=0.95,
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-RT-REUSE", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.95,
    )
    # Pretend the node was retired from the taxonomy after the approval.
    del fake._nodes[EQ_PRICES_IRI]
    r = map_vendor_product(ref)
    assert r.status == MappingStatus.APPROVED, r.status
    required_fails = [v for v in r.validations if v.required and v.status == "fail"]
    assert not required_fails, (
        f"precedent reuse must not emit required-fail validations: {r.validations}"
    )


@case("M6_export_bundle_carries_confirmed_precedent_with_rank")
def _():
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-RT-EX1",
        name="Equity Prices Real Time",
        description="Real-time equity pricing feed",
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )

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
    ref_confirmed = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-CONF", name="Equity Prices Real Time"
    )
    ref_prov = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-PROV", name="Equity Prices Real Time"
    )
    ref_rej = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-REJ", name="Equity Prices Real Time"
    )
    fake.set_score(EQ_PRICES_IRI, 0.95)
    apply_decision(
        ref_confirmed,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
    fake.upsert_precedent(
        ref=ref_prov,
        node=TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices"),
        decision="approve",
        decided_by="auto",
        confidence=0.95,
        provisional=True,
    )
    apply_decision(
        ref_rej, decision="reject", decided_by="reviewer@jpmc", node_iri=EQ_PRICES_IRI
    )

    bundle = export_bundle(source_env="test", created_at="2026-01-01T00:00:00.000Z")
    pids = [p.product_id for p in bundle.patterns]
    assert pids == ["EQ-CONF"], pids


@case("M6_round_trip_reproduces_confirmed_mappings_in_fresh_env")
def _():
    # Env A
    fake_a = _fresh_store()
    fake_a.set_score(EQ_PRICES_IRI, 0.95)
    refs = [
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id=pid, name="Equity Prices Real Time"
        )
        for pid in ("EQ-RT-A1", "EQ-RT-A2")
    ]
    for r in refs:
        apply_decision(
            r,
            decision="approve",
            decided_by="reviewer@jpmc",
            node_iri=EQ_PRICES_IRI,
            suggested_confidence=0.91,
        )
    bundle = export_bundle(source_env="test-A", created_at="2026-01-01T00:00:00.000Z")

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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-IDEM", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
    bundle = export_bundle(source_env="test", created_at="2026-01-01T00:00:00.000Z")

    fake_b = _fresh_store()
    import_bundle(bundle)
    rank_after_first = fake_b.rank_signals_for(
        fake_b.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time", "EQ-IDEM")
    ).get(EQ_PRICES_IRI, 0)
    # Second import on the SAME store — must not double the rank signal.
    import_bundle(bundle)
    rank_after_second = fake_b.rank_signals_for(
        fake_b.vendor_signature(IN_SCOPE_VENDOR, "Equity Prices Real Time", "EQ-IDEM")
    ).get(EQ_PRICES_IRI, 0)
    assert rank_after_first == rank_after_second == 1, (
        rank_after_first,
        rank_after_second,
    )
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
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-SKIP", name="Equity Prices Real Time"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.91,
    )
    bundle = export_bundle(source_env="test", created_at="2026-01-01T00:00:00.000Z")

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
        BundleProvenance,
        FieldRule,
        MappingBundle,
        MappingPattern,
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
# ENRICHMENT (M10) — conceptual layer + LLM abstention contracts
# ──────────────────────────────────────────────────────────────────────────


def _swap_enrichment_backend(backend: str):
    saved = enrichment_mod.settings
    enrichment_mod.settings = dataclasses.replace(saved, enrichment_backend=backend)
    return saved


@case("ENRICHMENT_conceptual_iri_is_deterministic")
def _():
    a = conceptual_iri(EQ_PRICES_IRI, ConceptualNodeKind.FIELD, "Ticker")
    b = conceptual_iri(EQ_PRICES_IRI, ConceptualNodeKind.FIELD, "ticker")
    c = conceptual_iri(EQ_PRICES_IRI, ConceptualNodeKind.FIELD_GROUP, "ticker")
    assert a == b, (a, b)
    assert a != c, (a, c)
    assert a.startswith("mds.enrich:"), a


@case("ENRICHMENT_backend_off_abstains_without_llm")
def _():
    saved = _swap_enrichment_backend("off")
    try:
        ref = VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="EQ-M10-OFF",
            name="Equity Prices",
            raw={"ticker": "JPM"},
        )
        graph = enrichment_mod.extract_field_structure(ref, EQ_PRICES_IRI)
        assert graph.root_concept_iri == EQ_PRICES_IRI
        assert graph.nodes == []
        assert graph.edges == []

        candidate = ConceptualNode(
            iri=conceptual_iri(
                EQ_PRICES_IRI,
                ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
                "price",
            ),
            kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
            label="Price",
            attaches_to_concept_iri=EQ_PRICES_IRI,
        )
        assert (
            enrichment_mod.classify_business_concept(ref, EQ_PRICES_IRI, [candidate])
            is None
        )
    finally:
        enrichment_mod.settings = saved


@case("ENRICHMENT_extract_abstains_on_malformed_llm_output")
def _():
    saved = _swap_enrichment_backend("opus")
    orig_extract = enrichment_mod._extract_invoke
    try:
        enrichment_mod._extract_invoke = lambda ref: (_ for _ in ()).throw(  # type: ignore[assignment]
            RuntimeError("malformed model JSON")
        )
        ref = VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="EQ-M10-BAD",
            name="Equity Prices",
            raw={"ticker": "JPM"},
        )
        graph = enrichment_mod.extract_field_structure(ref, EQ_PRICES_IRI)
        assert graph.nodes == []
        assert graph.edges == []
    finally:
        enrichment_mod._extract_invoke = orig_extract  # type: ignore[assignment]
        enrichment_mod.settings = saved


@case("ENRICHMENT_classify_rejects_empty_and_off_list_candidates")
def _():
    saved = _swap_enrichment_backend("opus")
    orig_pick = enrichment_mod._classify_best_pick
    try:
        ref = VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="EQ-M10-CLASSIFY",
            name="Equity Prices",
            raw={},
        )
        assert enrichment_mod.classify_business_concept(ref, EQ_PRICES_IRI, []) is None

        offered = ConceptualNode(
            iri=conceptual_iri(
                EQ_PRICES_IRI,
                ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
                "offered",
            ),
            kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
            label="Offered",
            attaches_to_concept_iri=EQ_PRICES_IRI,
        )
        rogue = ConceptualNode(
            iri=conceptual_iri(
                EQ_PRICES_IRI,
                ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
                "rogue",
            ),
            kind=ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
            label="Rogue",
            attaches_to_concept_iri=EQ_PRICES_IRI,
        )
        enrichment_mod._classify_best_pick = (  # type: ignore[assignment]
            lambda ref, concept_iri, candidates: rogue
        )
        assert (
            enrichment_mod.classify_business_concept(ref, EQ_PRICES_IRI, [offered])
            is None
        )
    finally:
        enrichment_mod._classify_best_pick = orig_pick  # type: ignore[assignment]
        enrichment_mod.settings = saved


@case("ENRICHMENT_fake_store_round_trips_conceptual_graph")
def _():
    fake = _fresh_store()
    group = ConceptualNode(
        iri=conceptual_iri(EQ_PRICES_IRI, ConceptualNodeKind.FIELD_GROUP, "fg"),
        kind=ConceptualNodeKind.FIELD_GROUP,
        label="Equity price fields",
        attaches_to_concept_iri=EQ_PRICES_IRI,
    )
    field = ConceptualNode(
        iri=conceptual_iri(EQ_PRICES_IRI, ConceptualNodeKind.FIELD, "ticker"),
        kind=ConceptualNodeKind.FIELD,
        label="Ticker",
        attaches_to_concept_iri=EQ_PRICES_IRI,
        vendor_field_name="ticker",
        data_type="string",
        primary_key=True,
    )
    edge = ConceptualEdge(
        from_iri=group.iri,
        to_iri=field.iri,
        kind=ConceptualEdgeKind.CONTAINS,
        label="contains",
    )
    fake.upsert_conceptual_node(group)
    fake.upsert_conceptual_node(field)
    fake.upsert_conceptual_edge(edge)

    graph = fake.get_conceptual_graph(EQ_PRICES_IRI)
    assert {n.iri for n in graph.nodes} == {group.iri, field.iri}
    assert [(e.from_iri, e.to_iri, e.kind) for e in graph.edges] == [
        (group.iri, field.iri, ConceptualEdgeKind.CONTAINS)
    ]


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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-RT-001",
                "name": "Equity Pricing Real Time",
                "description": "Real-time equity prices",
                "raw": {"permId": "EQ-RT-001"},
            }
        ).encode("utf-8")
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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-1",
                "name": "x",
            },
            sort_keys=True,
        ).encode("utf-8")
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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-2",
                "name": "x",
            }
        ).encode("utf-8")
        fake.put(
            "test-bucket",
            f"{IN_SCOPE_VENDOR}/EQ-2.json",
            body,
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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-3",
                "name": "x",
            }
        ).encode("utf-8")
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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-4",
                "name": "x",
                "source_content_hash": "ATTACKER-CONTROLLED",
                "source_file_audit_id": "ATTACKER-CONTROLLED",
            }
        ).encode("utf-8")
        fake.put(
            "test-bucket",
            f"{IN_SCOPE_VENDOR}/EQ-4.json",
            body,
            metadata={"file-audit-id": "real-audit-id"},
        )
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
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-5",
                "name": "x",
            }
        ).encode("utf-8")
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-PARITY-001",
        name="Equity Pricing Real Time",
    )
    iri_mock = ref_mock.iri

    # S3 path — same (vendor, product_id) shape lands in the bucket.
    fake, saved = _m8_setup()
    try:
        body = json.dumps(
            {
                "vendor": IN_SCOPE_VENDOR,
                "product_id": "EQ-PARITY-001",
                "name": "Equity Pricing Real Time",
            }
        ).encode("utf-8")
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-PROP-1",
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
        vendor="NotAVendor",
        product_id="X",
        name="x",
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
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.95,
    )
    ref_next = VendorProductRef(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-PROP-1",
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
    fake._precedent_meta[(IN_SCOPE_VENDOR, "EQ-PROP-1")]["source_content_hash"] = None
    fake._precedent_meta[(IN_SCOPE_VENDOR, "EQ-PROP-1")]["source_file_audit_id"] = None
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-PERSIST",
        name="Equity Prices Real Time",
        source_content_hash="sha256:abc123",
        source_file_audit_id="audit-row-7",
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.95,
    )

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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-XENV",
        name="Equity Prices Real Time",
        source_content_hash="sha256:envA-original",
        source_file_audit_id="audit-envA-42",
    )
    apply_decision(
        ref_a,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.95,
    )
    bundle = export_bundle(source_env="env-A", created_at="2026-06-09T00:00:00.000Z")

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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-XENV",
        name="Equity Prices Real Time",
        source_content_hash="sha256:envB-stale-different",
        source_file_audit_id="audit-envB-unrelated",
    )
    result_b = map_vendor_product(ref_b_fresh)
    assert result_b.status == MappingStatus.APPROVED
    assert result_b.source_content_hash == "sha256:envA-original", (
        result_b.source_content_hash
    )
    assert result_b.source_file_audit_id == "audit-envA-42", (
        result_b.source_file_audit_id
    )


@case("M8_inline_path_leaves_source_fields_None")
def _():
    """When name/description are supplied inline by the HTTP caller (no
    frame lookup), source_* stay None — there is no upstream provenance to
    record. Backward-compatible for every test that pre-dates M8.
    """
    _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-INLINE",
        name="x",
        description="y",
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-AGENT-1",
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-AGENT-2",
        name="Equity Prices Real Time",
    )

    direct = map_vendor_product(ref).model_dump(mode="json")

    final = None
    for ev in ScriptedMappingAgent().run(ref):
        if ev.type == "final_result":
            final = ev.payload["mapping"]

    assert final is not None, "agent did not emit final_result"
    # The matcher is replay-safe, so the dumps compare equal field by field.
    for k in (
        "vendor",
        "product_id",
        "mapped_node_iri",
        "mapped_node_label",
        "status",
        "confidence",
        "rationale",
    ):
        assert final[k] == direct[k], (k, final[k], direct[k])


@case("AGENT_scripted_out_of_scope_short_circuits_to_OUT_OF_SCOPE")
def _():
    """Out-of-scope vendor should still produce a clean event sequence and
    a final_result with status OUT_OF_SCOPE. The agent must not pretend
    to score it."""
    _fresh_store()
    ref = VendorProductRef(
        vendor="NotAVendor",
        product_id="X",
        name="anything",
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-AGENT-LBL",
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-1",
        mapped_node_iri=EQ_PRICES_IRI,
        status="auto_mapped",
        confidence=0.95,
    )
    r = v.verify(seal, expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
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

    seal = v.sign(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-1",
        mapped_node_iri="cdao:x",
        status="auto_mapped",
        confidence=0.95,
    )
    forged = {
        "payload_b64": seal["payload_b64"],
        "hmac_b64": base64.b64encode(b"X" * 32).decode("ascii"),
    }
    r = v.verify(forged, expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
    assert not r.ok
    assert r.reason == "seal_mismatch", r.reason


@case("VERDICT_expired_seal_is_refused")
def _():
    """A seal older than SCUDO_VERDICT_MAX_AGE_SECONDS is refused — agents
    can't sit on a verdict for hours and quietly commit later."""
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v

    seal = v.sign(
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-1",
        mapped_node_iri="cdao:x",
        status="auto_mapped",
        confidence=0.95,
        ts_ms=1000,
    )  # 1970
    r = v.verify(seal, expected_vendor=IN_SCOPE_VENDOR, expected_product_id="EQ-1")
    assert not r.ok
    assert r.reason == "seal_expired", r.reason


@case("VERDICT_identity_replay_is_refused")
def _():
    """A valid seal for product A cannot be replayed against product B.
    Closes the I8-at-the-seal-boundary hole."""
    _ensure_dev_signing_key()
    from scudo_mapping_mcp import verdict as v

    seal_a = v.sign(
        vendor=IN_SCOPE_VENDOR,
        product_id="A",
        mapped_node_iri="cdao:x",
        status="auto_mapped",
        confidence=0.95,
    )
    r = v.verify(seal_a, expected_vendor=IN_SCOPE_VENDOR, expected_product_id="B")
    assert not r.ok
    assert r.reason == "identity_mismatch", r.reason


@case("TRUST_ingestion_mcp_imports_no_writers")
def _():
    """Static AST check: the Ingestion MCP module does NOT import any
    write-side module. If a future change adds `from .feedback import ...`
    or `from .bundle import import_bundle`, this fails — making the trust
    boundary load-bearing, not aspirational."""
    import ast
    import pathlib

    path = pathlib.Path("scudo_mapping_mcp/ingestion_mcp.py").resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.endswith("feedback") or mod.endswith("bundle"):
                forbidden.add(mod)
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle", "upsert_precedent"}:
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
    import ast
    import pathlib

    path = pathlib.Path("scudo_mapping_mcp/match_verify_mcp.py").resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.endswith("feedback"):
                forbidden.add(mod)
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle", "upsert_precedent"}:
                    forbidden.add(f"{mod}.{n.name}")
    assert not forbidden, (
        f"Match & Verify MCP must not import write surfaces; found: {forbidden}"
    )


@case("TRUST_persistence_mcp_imports_writers")
def _():
    """The inverse: Persistence MCP IS the only writer, so it must
    import feedback (for record_decision) and bundle (for import_bundle).
    Asserts the writer role is co-located, not scattered."""
    import ast
    import pathlib

    path = pathlib.Path("scudo_mapping_mcp/persistence_mcp.py").resolve()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for n in node.names:
                if n.name in {"apply_decision", "import_bundle", "export_bundle"}:
                    seen.add(n.name)
    assert "apply_decision" in seen, "Persistence MCP must own the HITL write path"
    assert "import_bundle" in seen, "Persistence MCP must own bundle import"


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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-GATE-1",
        name="Equity Prices Real Time",
    )

    # Sign an AUTO_MAPPED verdict the way Match & Verify would.
    direct = map_vendor_product(ref)
    assert direct.status == MappingStatus.AUTO_MAPPED, direct.status
    seal = v.sign(
        vendor=ref.vendor,
        product_id=ref.product_id,
        mapped_node_iri=direct.mapped_node_iri,
        status=direct.status.value,
        confidence=direct.confidence,
    )

    params = pm.CommitInput(
        vendor=ref.vendor,
        product_id=ref.product_id,
        verdict=direct.model_dump(mode="json"),
        seal=seal,
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
    import asyncio
    import base64
    from scudo_mapping_mcp import verdict as v, persistence_mcp as pm

    _fresh_store()
    real_seal = v.sign(
        vendor=IN_SCOPE_VENDOR,
        product_id="X",
        mapped_node_iri="cdao:x",
        status="auto_mapped",
        confidence=0.95,
    )
    forged = {
        "payload_b64": real_seal["payload_b64"],
        "hmac_b64": base64.b64encode(b"X" * 32).decode("ascii"),
    }
    params = pm.CommitInput(
        vendor=IN_SCOPE_VENDOR,
        product_id="X",
        verdict={"status": "auto_mapped"},
        seal=forged,
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
        vendor=IN_SCOPE_VENDOR,
        product_id="EQ-HITL-1",
        name="Equity Prices Real Time",
    )
    params = pm.DecisionInput(
        vendor=ref.vendor,
        product_id=ref.product_id,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=EQ_PRICES_IRI,
        name=ref.name,
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
    import pathlib

    for mod_name in (
        "ingestion_mcp.py",
        "match_verify_mcp.py",
        "persistence_mcp.py",
    ):
        path = pathlib.Path(f"scudo_mapping_mcp/{mod_name}").resolve()
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

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-PASS",
            name="Equity Prices Real Time",
        ),
        specialist=spy,
    )

    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    assert r.band == "pass", r.band
    assert calls == [], (
        f"PASS band must not invoke specialist; called {len(calls)} time(s)"
    )


@case("LADDER_borderline_band_invokes_specialist")
def _():
    """In the ±0.05 window around the floor: specialist IS consulted."""
    fake = _fresh_store()
    # 0.77: above floor (0.75), below pass threshold (0.80)
    fake.set_score(EQ_PRICES_IRI, 0.77)
    fake.set_score(EQUITIES_IRI, 0.30)

    calls: list[VendorProductRef] = []

    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None  # abstain

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-BORDER",
            name="Equity Prices Real Time",
        ),
        specialist=spy,
    )

    assert r.band == "borderline", r.band
    assert len(calls) == 1, (
        f"BORDERLINE must invoke specialist exactly once; got {len(calls)}"
    )


@case("LADDER_fail_band_skips_specialist")
def _():
    """Clearly below floor: status NEEDS_REVIEW, band 'fail', specialist
    NOT consulted (ladder discipline — LLM only runs on resolvable cases)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.40)  # well below borderline edge == 0.70
    fake.set_score(EQUITIES_IRI, 0.30)

    calls: list[VendorProductRef] = []

    def spy(ref, cands):  # noqa: ANN001
        calls.append(ref)
        return None

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-FAIL",
            name="random gibberish",
        ),
        specialist=spy,
    )

    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert r.band == "fail", r.band
    assert calls == [], (
        f"FAIL band must not invoke specialist; called {len(calls)} time(s)"
    )


@case("LADDER_specialist_disagreement_caps_confidence_below_floor")
def _():
    """Specialist picks a different node from the sparse ranker — confidence
    is capped below the floor and the case lands in NEEDS_REVIEW. The
    disagreement, not the number, decides. The primary mapped node stays
    anchored to the sparse ranker's pick (so it matches candidates[0]),
    and the specialist's alternative is preserved on the result so the
    reviewer sees BOTH picks (no information loss)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.77)  # sparse ranker would pick this
    fake.set_score(EQUITIES_IRI, 0.76)  # specialist will pick this instead
    fake.set_score(FX_IRI, 0.10)

    def disagreeing_specialist(ref, candidates):  # noqa: ANN001
        return Candidate(
            node=TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
            similarity=0.99,  # hallucinated; matcher must ignore
        )

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-DISAGREE",
            name="Equity Prices Real Time",
        ),
        specialist=disagreeing_specialist,
    )

    assert r.band == "borderline", r.band
    assert r.status == MappingStatus.NEEDS_REVIEW, (
        f"disagreement must NOT auto-map (I5); got {r.status}"
    )
    assert r.confidence < config_mod.settings.confidence_floor, (
        f"confidence must be capped below floor; got {r.confidence}"
    )
    # Primary stays anchored to the sparse ranker's pick — same as candidates[0]
    assert r.mapped_node_iri == EQ_PRICES_IRI, r.mapped_node_iri
    assert r.candidates[0].node.iri == r.mapped_node_iri
    # The specialist's alternative is preserved on the result so the
    # reviewer queue carries BOTH picks. No information loss.
    assert r.alternative_mapped_node_iri == EQUITIES_IRI, r.alternative_mapped_node_iri


@case("TRUST_specialist_anchored_to_top_candidate")
def _():
    """ARB review pack §5.3 invariant — the specialist is ANCHORED to the
    candidate set the sparse ranker surfaced. It scores within the top-N
    anchor window; it does NOT bring its own picks in from the wider
    taxonomy. A specialist returning an off-list IRI (hallucinated node /
    stale taxonomy / prompt injection) MUST fail closed: NEEDS_REVIEW with
    the violation reason surfaced, and the off-list IRI DISCARDED — not
    silently surfaced as an alternative the reviewer might select.
    """
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.77)  # borderline: above floor, below pass
    fake.set_score(EQUITIES_IRI, 0.30)
    fake.set_score(FX_IRI, 0.10)

    OFF_LIST_IRI = "cdao:does-not-exist-anywhere"

    def off_list_specialist(ref, candidates):  # noqa: ANN001
        # Returns an IRI that is NOT in the candidate list — simulates a
        # hallucinated taxonomy node / stale model / prompt-injection result.
        return Candidate(
            node=TaxonomyNode(iri=OFF_LIST_IRI, label="Hallucinated Node"),
            similarity=0.99,
        )

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-OFF-LIST",
            name="Equity Prices Real Time",
        ),
        specialist=off_list_specialist,
    )

    # Routes to NEEDS_REVIEW with the violation reason surfaced.
    assert r.status == MappingStatus.NEEDS_REVIEW, (
        f"off-list specialist pick must fail closed to NEEDS_REVIEW; got {r.status}"
    )
    assert r.invariant_violation == "specialist_off_list", (
        f"invariant_violation must surface the reason; got {r.invariant_violation!r}"
    )
    # Off-list IRI is DISCARDED — abstention means it is NOT surfaced for
    # a reviewer to accidentally select. The violation itself is the signal.
    assert r.alternative_mapped_node_iri is None, (
        f"off-list IRI must NOT surface as alternative; got "
        f"{r.alternative_mapped_node_iri!r}"
    )
    assert r.alternative_mapped_node_label is None, r.alternative_mapped_node_label
    # OFF_LIST_IRI must not be the mapped_node_iri either — primary stays
    # anchored to the sparse ranker's pick (candidates[0]).
    assert r.mapped_node_iri != OFF_LIST_IRI, r.mapped_node_iri
    assert r.mapped_node_iri == EQ_PRICES_IRI, r.mapped_node_iri
    # Confidence capped into the fail band — must not auto-map at nominally-
    # borderline dense similarity when the LLM violated its contract.
    assert r.confidence < config_mod.settings.confidence_floor, (
        f"confidence must be capped below floor; got {r.confidence}"
    )
    # Rationale carries the reason string explicitly.
    assert "specialist_off_list" in r.rationale, r.rationale


@case("LADDER_specialist_concurrence_caps_at_min_of_dense_and_specialist")
def _():
    """Borderline + specialist concurs at HIGHER confidence — the matcher
    must NOT trust the specialist's claim past the deterministic anchor
    (I5). Confidence = min(dense, specialist), not specialist alone.
    A hallucinating LLM returning 0.99 cannot push a 0.77 dense above the
    floor."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.77)  # borderline, above floor
    fake.set_score(EQUITIES_IRI, 0.30)

    def concurring_specialist(ref, candidates):  # noqa: ANN001
        return Candidate(
            node=TaxonomyNode(
                iri=EQ_PRICES_IRI, label="Equity Prices", parent_iri=EQUITIES_IRI
            ),
            similarity=0.99,  # specialist claims higher; matcher caps
        )

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-CONCUR-CAP",
            name="Equity Prices Real Time",
        ),
        specialist=concurring_specialist,
    )

    assert r.band == "borderline", r.band
    assert r.status == MappingStatus.AUTO_MAPPED, r.status
    # Concurrence + dense above floor still auto-maps, but confidence is
    # the DETERMINISTIC dense score, not the LLM's claim.
    assert abs(r.confidence - 0.77) < 1e-6, (
        f"confidence must be capped at min(0.77, 0.99) = 0.77; got {r.confidence}"
    )


@case("LADDER_specialist_concurrence_below_floor_cannot_be_lifted")
def _():
    """Borderline + dense BELOW floor + specialist concurs at HIGH
    confidence -> matcher must NOT auto-map. The specialist cannot inflate
    a sub-floor dense score (I5)."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.73)  # borderline, BELOW floor
    fake.set_score(EQUITIES_IRI, 0.30)

    def lying_specialist(ref, candidates):  # noqa: ANN001
        return Candidate(
            node=TaxonomyNode(
                iri=EQ_PRICES_IRI, label="Equity Prices", parent_iri=EQUITIES_IRI
            ),
            similarity=0.99,  # hallucinated; must be ignored for floor check
        )

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-LYING-LLM",
            name="Equity Prices Real Time",
        ),
        specialist=lying_specialist,
    )

    assert r.band == "borderline", r.band
    assert r.status == MappingStatus.NEEDS_REVIEW, (
        f"specialist must NOT lift a sub-floor dense score; got {r.status}"
    )
    assert r.confidence < config_mod.settings.confidence_floor


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

    r = map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="LADDER-REQFAIL",
            name="Equity Prices Real Time",
        ),
        specialist=spy,
    )

    assert r.band == "fail", r.band
    assert r.status == MappingStatus.NEEDS_REVIEW, r.status
    assert calls == [], "required-validation failure must not consult specialist"


@case("LADDER_out_of_scope_carries_band_na")
def _():
    """OUT_OF_SCOPE is outside the band model entirely."""
    _fresh_store()
    r = map_vendor_product(
        VendorProductRef(
            vendor="NotAVendor",
            product_id="x",
            name="anything",
        )
    )
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


@case("FUSION_bm25_preserves_dotted_identifier_as_one_token")
def _():
    """Tokeniser preserves periods inside tokens so RIC-style identifiers
    (AAPL.O) and decimal numbers survive intact. A doc containing the
    EXACT dotted identifier must outscore a doc that only has the stem."""
    docs = [
        ("exact_dotted", "instrument AAPL.O quote"),
        ("stem_only", "instrument aapl quote"),
        ("unrelated", "fixed income spreads"),
    ]
    scores = RetrievalStore.bm25_scores("AAPL.O", docs)
    assert scores["exact_dotted"] > scores["stem_only"], scores
    assert scores["unrelated"] == 0.0, scores


@case("FUSION_tokeniser_strips_trailing_period")
def _():
    """A sentence-trailing period must NOT bind to the preceding token —
    'price.' tokenises as 'price', not 'price.'."""
    tokens = RetrievalStore._tokenise("Get the latest price.")
    assert "price" in tokens, tokens
    assert "price." not in tokens, tokens


# ──────────────────────────────────────────────────────────────────────────
# VERDICT seal — band carriage + back-compat
# ──────────────────────────────────────────────────────────────────────────


@case("VERDICT_seal_carries_band_v2")
def _():
    """New seals are v=2 and carry the band field."""
    import base64
    import json
    from .. import verdict as verdict_mod

    _ensure_dev_signing_key()
    seal = verdict_mod.sign(
        vendor=IN_SCOPE_VENDOR,
        product_id="X",
        mapped_node_iri="cdao:x",
        status="auto_mapped",
        confidence=0.9,
        band="pass",
    )
    payload = json.loads(base64.b64decode(seal["payload_b64"]))
    assert payload["v"] == 2, payload
    assert payload["band"] == "pass", payload
    # Round-trip verify reads the band back.
    result = verdict_mod.verify(
        seal,
        expected_vendor=IN_SCOPE_VENDOR,
        expected_product_id="X",
    )
    assert result.ok, result.reason
    assert result.payload["band"] == "pass", result.payload


@case("VERDICT_v1_seal_still_verifies_band_defaults_to_na")
def _():
    """Legacy v=1 seals (signed before band existed) must still verify.
    Their band defaults to 'n/a' so persistence gates degrade gracefully."""
    import base64
    import hashlib
    import hmac
    import json
    import time as _time
    from .. import verdict as verdict_mod

    _ensure_dev_signing_key()
    # Synthesise a v=1 payload by hand (matches the old sign signature).
    payload = {
        "v": 1,
        "input_hash": verdict_mod.input_hash(IN_SCOPE_VENDOR, "X"),
        "mapped_node_iri": "cdao:x",
        "status": "auto_mapped",
        "confidence": 0.9,
        "ts_ms": int(_time.time() * 1000),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = verdict_mod._load_signing_key()
    mac = hmac.new(key, canonical, hashlib.sha256).digest()
    seal = {
        "payload_b64": base64.b64encode(canonical).decode("ascii"),
        "hmac_b64": base64.b64encode(mac).decode("ascii"),
    }
    result = verdict_mod.verify(
        seal,
        expected_vendor=IN_SCOPE_VENDOR,
        expected_product_id="X",
    )
    assert result.ok, result.reason
    assert result.payload["band"] == "n/a", result.payload


# ──────────────────────────────────────────────────────────────────────────
# SPECIALIST — per-call DI does not leak across calls (thread-safety)
# ──────────────────────────────────────────────────────────────────────────


@case("SPECIALIST_per_call_DI_does_not_leak_across_calls")
def _():
    """A specialist passed to call A must NOT be invoked on call B that
    omits the specialist kwarg. The seam is per-call DI — no module
    state — so concurrent requests can't poison each other."""
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.77)
    fake.set_score(EQUITIES_IRI, 0.30)

    calls_a: list[VendorProductRef] = []

    def spy_a(ref, cands):  # noqa: ANN001
        calls_a.append(ref)
        return None

    # Call A: passes a spy.
    map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="PERCALL-A",
            name="Equity Prices Real Time",
        ),
        specialist=spy_a,
    )
    assert len(calls_a) == 1, len(calls_a)

    # Call B: borderline range, omits specialist. Must NOT re-invoke spy_a.
    map_vendor_product(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="PERCALL-B",
            name="Equity Prices Real Time",
        )
    )
    assert len(calls_a) == 1, (
        f"specialist must not leak across calls; spy was invoked "
        f"{len(calls_a)} times across both calls"
    )


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
        vendor=IN_SCOPE_VENDOR,
        product_id="HYD-1",
        name="Equity Pricing Real Time",
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="reviewer@jpmc",
        node_iri=taxonomy_node_iri,
        suggested_confidence=0.93,
    )
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
        fake.put(
            "test-bucket",
            "canonical/bundle-latest.json",
            json.dumps({"foo": "bar"}).encode("utf-8"),
        )
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


@case("M6_export_to_s3_round_trips_through_hydrate")
def _():
    """export_to_s3 writes the bundle to the exact S3 location hydrate() reads.
    A fresh store hydrating from the same S3 replays the precedent."""
    # Populate a store with one confirmed precedent, then export it.
    fake = _fresh_store()
    fake.set_score(EQ_PRICES_IRI, 0.95)
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="EQ-1", name="Equity Prices"
    )
    apply_decision(
        ref,
        decision="approve",
        decided_by="tester",
        node_iri=EQ_PRICES_IRI,
        suggested_confidence=0.93,
    )

    # Export and write to S3.
    s3 = FakeS3Client()
    hydrate_mod._set_s3_client_for_test(s3)
    saved = _swap_settings(s3_bucket="test-bucket")
    try:
        bundle = export_bundle(
            source_env="mock-local", created_at="2026-01-01T00:00:00Z"
        )
        bucket, key = hydrate_mod.export_to_s3(bundle)
        assert (bucket, key) in s3._objects, "export_to_s3 wrote nothing to S3"

        # Verify the written content matches the exported bundle.
        stored = s3._objects[(bucket, key)]
        stored_body = stored["Body"]
        assert json.loads(stored_body.decode("utf-8")) == json.loads(
            bundle.model_dump_json()
        ), "written S3 bytes do not match the exported bundle"

        # Fresh store + hydrate from the same fake S3 replays the precedent.
        _fresh_store()
        hydrate_mod._set_s3_client_for_test(s3)
        result = hydrate_mod.hydrate(strict=True)
        assert result.applied == 1, f"expected 1 applied, got {result.applied}"
    finally:
        _restore_settings(saved)
        hydrate_mod._set_s3_client_for_test(None)


# ──────────────────────────────────────────────────────────────────────────
# THREE-SEAM vendor-agnostic contract (#18) — pin the env vars wired by the
# deploy task def (SCUDO_VENDOR_ADAPTERS / SCUDO_TAXONOMY_LOADER /
# SCUDO_PERSIST_TARGET) so code actually consumes them, not just CFN.
# ──────────────────────────────────────────────────────────────────────────


def _with_env(**overrides):
    """Context-managerless save/restore for os.environ. Returns the prior
    values dict so the caller restores them in a try/finally."""
    saved: dict[str, str | None] = {}
    for k, v in overrides.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@case("THREE_SEAM_vendor_adapters_default_matches_priority_vendors")
def _():
    """No env override -> vendor_adapters defaults to PRIORITY_VENDORS
    normalised the SAME way the deploy task def's SCUDO_VENDOR_ADAPTERS
    literal is written. Pin the exact tuple by LITERAL — re-computing
    'expected' with the same transform the helper uses would let a buggy
    helper agree with a buggy expected and silently drift. That's exactly
    what happened in the first cut of this gate (s&p_global vs sp_global).

    Normalisation: lower-case, spaces -> '_', strip non-alphanumeric.
    'S&P Global' -> 'sp_global' (matches infra/scudo-dev-deploy.yaml).
    """
    saved = _with_env(SCUDO_VENDOR_ADAPTERS=None)
    try:
        s = config_mod.Settings.from_env()
        # Hard literal pin — DO NOT derive from the helper.
        assert s.vendor_adapters == (
            "lseg",
            "sp_global",
            "bloomberg",
            "ice",
            "factset",
        ), s.vendor_adapters
        # Belt + braces: order preserved, count matches.
        assert len(s.vendor_adapters) == len(PRIORITY_VENDORS)
        # And specifically: 'sp_global' (no ampersand) is the canonical form.
        assert "sp_global" in s.vendor_adapters
        assert "s&p_global" not in s.vendor_adapters
    finally:
        _restore_env(saved)


@case("THREE_SEAM_vendor_adapters_env_override")
def _():
    """SCUDO_VENDOR_ADAPTERS env override produces a DIFFERENT tuple from
    the default — confirming the env var is actually consumed by code,
    not silently ignored (#18 was opened because only the deploy task def
    set these vars, with no reader on the Python side)."""
    saved = _with_env(SCUDO_VENDOR_ADAPTERS="lseg,bloomberg")
    try:
        s = config_mod.Settings.from_env()
        assert s.vendor_adapters == ("lseg", "bloomberg"), s.vendor_adapters
        default = tuple(v.lower().replace(" ", "_") for v in PRIORITY_VENDORS)
        assert s.vendor_adapters != default, "env override must change the tuple"
    finally:
        _restore_env(saved)


@case("THREE_SEAM_taxonomy_loader_defaults_to_cdao")
def _():
    """No env override -> taxonomy_loader='cdao'. Only allowed value today;
    locks the contract until other client ontologies are wired."""
    saved = _with_env(SCUDO_TAXONOMY_LOADER=None)
    try:
        s = config_mod.Settings.from_env()
        assert s.taxonomy_loader == "cdao", s.taxonomy_loader
    finally:
        _restore_env(saved)


@case("THREE_SEAM_persist_target_defaults_to_store_backend")
def _():
    """No SCUDO_PERSIST_TARGET override -> persist_target falls back to
    store_backend. Dev runs with falkordb for both, and the matching
    strategy stays coherent (canonical write goes to the same store the
    matcher reads from)."""
    saved = _with_env(SCUDO_PERSIST_TARGET=None, STORE_BACKEND="falkordb")
    try:
        s = config_mod.Settings.from_env()
        assert s.store_backend == "falkordb", s.store_backend
        assert s.persist_target == s.store_backend, (
            s.persist_target,
            s.store_backend,
        )
    finally:
        _restore_env(saved)


# ─────────────────────────────────────────────────────────────────────────
# WS-B — Opus-as-dense seam (opus_dense.py).
# Pins the arb-review §5.2 invariant: ``Candidate.similarity`` equals the
# RAW DENSE SCORE the backend returned, never a fused / boosted /
# reranked value. The 0.80 confidence floor is calibrated against that
# raw quantity; contamination would silently break I4.
# ─────────────────────────────────────────────────────────────────────────
from .. import opus_dense as opus_dense_mod


@case("TRUST_dense_similarity_is_raw")
def _():
    """Candidate.similarity == raw dense score from the backend.

    Drives the store's find_similar_products with a stubbed dense
    backend that returns KNOWN scores per candidate, then asserts
    every returned Candidate.similarity equals exactly what the
    stub handed back — no rerank, no fusion, no boost contamination.

    This is the arb-review §5.2 invariant: the quantity the 0.80
    confidence floor sees IS the quantity the dense scorer returned.
    """
    from ..store import falkordb_store as fs_mod

    # Known dense scores keyed by candidate label substring. The stub
    # picks the right score per call so we don't depend on call order.
    KNOWN = {
        "Equity Prices": 0.873,
        "Equities": 0.612,
        "Foreign Exchange": 0.142,
    }

    def fake_opus_dense_score(
        query_label,
        query_desc,
        candidate_label,
        candidate_desc,
    ):
        for needle, score in KNOWN.items():
            if candidate_label == needle:
                return score
        return 0.0

    # Monkeypatch the seam: switch SCUDO_DENSE_BACKEND=opus AND swap
    # opus_dense_score for the stub so we don't actually hit Bedrock.
    saved_backend = os.environ.get("SCUDO_DENSE_BACKEND")
    os.environ["SCUDO_DENSE_BACKEND"] = "opus"
    saved_fn = opus_dense_mod.opus_dense_score
    opus_dense_mod.opus_dense_score = fake_opus_dense_score  # type: ignore[assignment]
    try:
        # FakeStore short-circuits the dense path with its own oracle, so
        # to exercise the FalkorDB store's branch we drive it directly
        # via the module-level function. We use a minimal stand-in store
        # class that mimics only the surface find_similar_products
        # needs — the test is about the score plumbing, not falkordb IO.
        from ..models import VendorProductRef as VPR

        class _DenseProbeStore(fs_mod.FalkorDBStore):
            """Subclass that skips the FalkorDB connection (we never call
            it) but reuses find_similar_products's logic. The two _ro
            calls (taxonomy scan + RETURN 1 health) and rank_signals_for
            are stubbed inline."""

            def __init__(self):  # noqa: D401 — skip super().__init__
                pass

            def _ro(self, cypher, params=None):
                if "MATCH (t:TaxonomyNode)" in cypher:
                    return [
                        (EQ_PRICES_IRI, "Equity Prices", EQUITIES_IRI),
                        (EQUITIES_IRI, "Equities", None),
                        (FX_IRI, "Foreign Exchange", None),
                    ]
                return []

            def get_negative_precedents(self, vendor, product_id):
                return []

            def rank_signals_for(self, vendor_signature):
                return {}

        probe = _DenseProbeStore()
        cands = probe.find_similar_products(
            VPR(
                vendor=IN_SCOPE_VENDOR,
                product_id="WS-B-RAW",
                name="Equity Prices Real Time",
            ),
            max_results=10,
        )
        assert cands, "expected candidates from the probe store"
        # Every returned Candidate.similarity MUST equal the raw stub
        # score for that label — no rerank, no boost, no fusion.
        by_iri = {c.node.iri: c for c in cands}
        eq_prices_sim = by_iri[EQ_PRICES_IRI].similarity
        equities_sim = by_iri[EQUITIES_IRI].similarity
        fx_sim = by_iri[FX_IRI].similarity
        # Pydantic Candidate rounds to 4 decimal places; KNOWN scores are
        # already 3dp so equality is exact.
        assert eq_prices_sim == 0.873, (
            f"Candidate.similarity must be the RAW dense score; "
            f"got {eq_prices_sim} for EQ_PRICES (stub returned 0.873)"
        )
        assert equities_sim == 0.612, equities_sim
        assert fx_sim == 0.142, fx_sim
        # And the sort order tracks dense (no fusion / no RRF distortion).
        assert cands[0].node.iri == EQ_PRICES_IRI, (
            f"top hit must be the highest raw dense score; got "
            f"{[c.node.iri for c in cands]}"
        )
    finally:
        opus_dense_mod.opus_dense_score = saved_fn  # type: ignore[assignment]
        if saved_backend is None:
            os.environ.pop("SCUDO_DENSE_BACKEND", None)
        else:
            os.environ["SCUDO_DENSE_BACKEND"] = saved_backend


@case("TRUST_opus_dense_backend_env_var_contract")
def _():
    """SCUDO_DENSE_BACKEND env var contract — jaro_winkler default,
    opus enabled by explicit set, unknown values raise.

    The three-seam discipline says: backends are selected by env var
    (so the deploy task def is the swap point), defaults are safe for
    CI (jaro_winkler — deterministic, no network), and an unknown
    value fails fast rather than silently picking one.
    """
    saved_backend = os.environ.get("SCUDO_DENSE_BACKEND")
    saved_fallback = os.environ.get("SCUDO_DENSE_FALLBACK")
    try:
        # Default (unset) -> jaro_winkler; deterministic, no exception.
        os.environ.pop("SCUDO_DENSE_BACKEND", None)
        s_default = opus_dense_mod.opus_dense_score(
            "Equity Prices",
            "",
            "Equity Prices",
            "",
        )
        assert 0.0 <= s_default <= 1.0, s_default
        # Same input -> same output under the deterministic backend.
        s_again = opus_dense_mod.opus_dense_score(
            "Equity Prices",
            "",
            "Equity Prices",
            "",
        )
        assert s_default == s_again, (s_default, s_again)

        # Explicit jaro_winkler -> same.
        os.environ["SCUDO_DENSE_BACKEND"] = "jaro_winkler"
        s_jw = opus_dense_mod.opus_dense_score(
            "Equity Prices",
            "",
            "Equity Prices",
            "",
        )
        assert s_jw == s_default, (s_jw, s_default)

        # Unknown -> ValueError. Fail fast, not silent default.
        os.environ["SCUDO_DENSE_BACKEND"] = "titan"
        raised = False
        try:
            opus_dense_mod.opus_dense_score(
                "Equity Prices",
                "",
                "Equity Prices",
                "",
            )
        except ValueError:
            raised = True
        assert raised, "unknown backend must raise ValueError"
    finally:
        if saved_backend is None:
            os.environ.pop("SCUDO_DENSE_BACKEND", None)
        else:
            os.environ["SCUDO_DENSE_BACKEND"] = saved_backend
        if saved_fallback is None:
            os.environ.pop("SCUDO_DENSE_FALLBACK", None)
        else:
            os.environ["SCUDO_DENSE_FALLBACK"] = saved_fallback


# ─────────────────────────────────────────────────────────────────────────
# RETRIEVAL — multi-path orchestrator (retrieval.py).
# Pins the SDK-inspired shape: BM25 pre-filters, dense rescores survivors,
# negative-precedents drop pre-dense, degraded fallback below the floor.
# ─────────────────────────────────────────────────────────────────────────
from ..retrieval import multi_path_retrieve


@case("RETRIEVAL_bm25_prefilter_caps_at_25")
def _():
    """BM25 nominates AT MOST 25 candidates regardless of universe size.

    Seed 40 distinct TaxonomyNodes; the dense_scorer stub records the size
    of the list it actually sees. The pre-filter must clamp it to 25.
    """
    nodes = [
        TaxonomyNode(iri=f"cdao:rt-cap-{i:03d}", label=f"Equities Variant {i}")
        for i in range(40)
    ]
    fake = FakeStore(nodes=nodes)

    seen_sizes: list[int] = []

    def stub(query: str, survivors: list[Candidate]) -> list[Candidate]:
        seen_sizes.append(len(survivors))
        return [Candidate(node=c.node, similarity=0.7) for c in survivors]

    result = multi_path_retrieve(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR, product_id="X-CAP", name="Equities Variant 7"
        ),
        fake,
        max_results=10,
        dense_scorer=stub,
    )
    assert seen_sizes, "dense_scorer was never called"
    assert seen_sizes[0] <= 25, (
        f"BM25 pre-filter must cap at 25, dense saw {seen_sizes[0]}"
    )
    # Sanity — the cap is the pin, not coincidence: with 40 universe nodes
    # and no other filtering, the pre-filter should saturate exactly at 25.
    assert seen_sizes[0] == 25, seen_sizes[0]
    assert len(result) == 10, len(result)


@case("RETRIEVAL_dense_scorer_runs_only_on_survivors")
def _():
    """dense_scorer must NOT see negative-precedent-rejected nodes.

    The rejected node is dropped BEFORE the dense rescore so an expensive
    Opus call is never spent on a node the human already vetoed.
    """
    fake = _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="X-SURV", name="Equity Prices Real Time"
    )
    # Reject EQ_PRICES_IRI up-front. The orchestrator must drop it.
    apply_decision(
        ref, decision="reject", decided_by="reviewer@jpmc", node_iri=EQ_PRICES_IRI
    )

    seen_iris: list[str] = []

    def stub(query: str, survivors: list[Candidate]) -> list[Candidate]:
        seen_iris.extend(c.node.iri for c in survivors)
        return [Candidate(node=c.node, similarity=0.7) for c in survivors]

    result = multi_path_retrieve(ref, fake, dense_scorer=stub)
    assert EQ_PRICES_IRI not in seen_iris, (
        f"rejected node leaked into dense rescore: {seen_iris}"
    )
    # And it never makes it into the final result either.
    assert all(c.node.iri != EQ_PRICES_IRI for c in result), [
        c.node.iri for c in result
    ]


@case("RETRIEVAL_falls_back_to_constant_similarity_when_no_dense_scorer")
def _():
    """No dense_scorer wired → every survivor gets the degraded constant.

    The constant is BELOW the 0.80 floor by construction so a misconfigured
    run cannot accidentally auto-map anything. Also pins the value at 0.5
    so a reviewer can tell at a glance that the run was degraded.
    """
    fake = _fresh_store()
    result = multi_path_retrieve(
        VendorProductRef(
            vendor=IN_SCOPE_VENDOR,
            product_id="X-DEGRADED",
            name="Equity Prices Real Time",
        ),
        fake,
        dense_scorer=None,  # explicit — this is the degraded path
    )
    assert result, "expected the orchestrator to still return candidates"
    sims = [c.similarity for c in result]
    assert all(s == 0.5 for s in sims), sims
    # Confidence floor sanity — degraded sims cannot pass auto-map.
    assert all(s < config_mod.CONFIDENCE_FLOOR for s in sims), sims


@case("RETRIEVAL_negative_precedents_dropped_before_dense")
def _():
    """Negative-precedent drop happens BEFORE the dense rescore call.

    Distinct from the survivors gate: this pins the ORDER. If dense were
    called first, a future scorer with side-effects (tokens, latency) would
    waste budget on a node that's about to be discarded anyway.
    """
    fake = _fresh_store()
    ref = VendorProductRef(
        vendor=IN_SCOPE_VENDOR, product_id="X-ORDER", name="Equity Prices Real Time"
    )
    apply_decision(ref, decision="reject", decided_by="reviewer@jpmc", node_iri=FX_IRI)

    call_log: list[tuple[str, str]] = []

    # Wrap the store's get_negative_precedents so we can record when it
    # fires relative to the dense_scorer call.
    real_get_neg = fake.get_negative_precedents

    def spy_get_neg(vendor, product_id):
        call_log.append(("get_negative_precedents", f"{vendor}:{product_id}"))
        return real_get_neg(vendor, product_id)

    fake.get_negative_precedents = spy_get_neg  # type: ignore[assignment]

    def stub(query: str, survivors: list[Candidate]) -> list[Candidate]:
        call_log.append(("dense_scorer", f"n={len(survivors)}"))
        return [Candidate(node=c.node, similarity=0.7) for c in survivors]

    multi_path_retrieve(ref, fake, dense_scorer=stub)

    # The first negative-precedent call must precede the first dense call.
    neg_idx = next(
        (
            i
            for i, (name, _) in enumerate(call_log)
            if name == "get_negative_precedents"
        ),
        -1,
    )
    dense_idx = next(
        (i for i, (name, _) in enumerate(call_log) if name == "dense_scorer"),
        -1,
    )
    assert neg_idx >= 0, call_log
    assert dense_idx >= 0, call_log
    assert neg_idx < dense_idx, (
        f"negative-precedent drop must precede dense rescore; got {call_log}"
    )


# ──────────────────────────────────────────────────────────────────────────
# OPUS-DENSE feature flag (config) + boost scaling for [0, 1] dense scores
# ──────────────────────────────────────────────────────────────────────────


@case("CONFIG_use_opus_dense_defaults_false")
def _():
    """No SCUDO_USE_OPUS_DENSE env -> use_opus_dense is False. The legacy
    Jaro-Winkler + BM25 + RRF path stays the default; the prior smoke gates
    therefore keep exercising the same code they always have."""
    saved = _with_env(SCUDO_USE_OPUS_DENSE=None)
    try:
        s = config_mod.Settings.from_env()
        assert s.use_opus_dense is False, s.use_opus_dense
    finally:
        _restore_env(saved)


@case("CONFIG_use_opus_dense_reads_env")
def _():
    """SCUDO_USE_OPUS_DENSE=1 flips the flag on. Confirms the env var is
    actually consumed by Settings.from_env, not silently ignored (#18-style
    drift where the deploy task def sets a var with no reader on the
    Python side)."""
    saved = _with_env(SCUDO_USE_OPUS_DENSE="1")
    try:
        s = config_mod.Settings.from_env()
        assert s.use_opus_dense is True, s.use_opus_dense
    finally:
        _restore_env(saved)


@case("BOOST_scaled_for_dense_uses_raw_value")
def _():
    """compute_rank_boost_scaled_for_dense returns the RAW boost (≤ 0.10),
    NOT the RRF-scaled boost (≤ 0.10 * 1/(RRF_K+1) ≈ 0.00164). Under
    Opus-dense, the sort key is a [0, 1] similarity; the structural tilt
    must be commensurate with that range, or it becomes invisible.

    Pin the magnitude by construction: 3 approvals -> 0.06 raw boost.
    Compare against the legacy RRF-scaled value to make the bug this
    method exists to fix concrete in the test name.
    """
    # 3 approvals -> raw boost = 3 * RANK_BOOST_PER_APPROVAL = 0.06.
    boosts = {"cdao:x": 3}
    raw = RetrievalStore.compute_rank_boost_scaled_for_dense(boosts, "cdao:x")
    expected = 3 * RetrievalStore.RANK_BOOST_PER_APPROVAL
    assert abs(raw - expected) < 1e-9, (raw, expected)
    # The dense-scaled value MUST NOT be the legacy * rrf_top product —
    # that's the scaling bug this method exists to fix.
    rrf_top = 1.0 / (RetrievalStore.RRF_K + 1)
    legacy_scaled = raw * rrf_top
    assert raw > legacy_scaled * 10, (raw, legacy_scaled)
    # Saturation at RANK_BOOST_CAP still holds — boost is bounded so an
    # under-similar node can never overtake a clearly-better one in [0, 1].
    saturated = RetrievalStore.compute_rank_boost_scaled_for_dense(
        {"cdao:x": 1000},
        "cdao:x",
    )
    assert saturated == RetrievalStore.RANK_BOOST_CAP, saturated
    # Missing node -> 0.0 (the comparable boost for an unranked candidate).
    assert (
        RetrievalStore.compute_rank_boost_scaled_for_dense(
            {},
            "cdao:absent",
        )
        == 0.0
    )


# ──────────────────────────────────────────────────────────────────────────
# DENSE SCORER — Opus-as-dense-scorer stand-in for parked Titan embeddings
# ──────────────────────────────────────────────────────────────────────────


@case("DENSE_score_returns_candidates_with_updated_similarity")
def _():
    """The injected model client is called with the prompt; the returned
    candidates keep input order and carry the Opus-derived similarity.
    The TaxonomyNode fields are copied unchanged — Opus is not trusted to
    invent or rewrite taxonomy structure (I5)."""
    from scudo_mapping_mcp import dense_scorer as ds

    nodes = [
        TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices", parent_iri=EQUITIES_IRI),
        TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
        TaxonomyNode(iri=FX_IRI, label="Foreign Exchange"),
    ]
    cands = [Candidate(node=n, similarity=0.1) for n in nodes]

    captured = {}

    def fake_model(*, system, user, temperature):
        captured["system"] = system
        captured["user"] = user
        captured["temperature"] = temperature
        return json.dumps(
            {
                "scores": [
                    {"iri": EQ_PRICES_IRI, "score": 0.93},
                    {"iri": EQUITIES_IRI, "score": 0.55},
                    {"iri": FX_IRI, "score": 0.10},
                ],
            }
        )

    out = ds.score_candidates(
        "Equity Prices Real Time feed",
        cands,
        model_client=fake_model,
    )

    # Order preserved on the input ordering — load-bearing for the matcher.
    assert [c.node.iri for c in out] == [
        EQ_PRICES_IRI,
        EQUITIES_IRI,
        FX_IRI,
    ], [c.node.iri for c in out]
    # Similarity replaced by the Opus-derived score.
    assert out[0].similarity == 0.93, out[0].similarity
    assert out[1].similarity == 0.55, out[1].similarity
    assert out[2].similarity == 0.10, out[2].similarity
    # TaxonomyNode metadata copied unchanged — Opus cannot invent structure.
    assert out[0].node.label == "Equity Prices"
    assert out[0].node.parent_iri == EQUITIES_IRI
    # Prompt shape pins: low temperature + system rubric language present.
    assert captured["temperature"] == 0.1, captured["temperature"]
    assert "[0, 1]" in captured["system"], captured["system"]
    assert "Vendor product" in captured["user"], captured["user"]
    assert EQ_PRICES_IRI in captured["user"], captured["user"]


@case("DENSE_rejects_oversize_candidate_list")
def _():
    """The matcher should pre-filter — silent truncation would hide a
    retrieval regression. The contract is: hard ValueError when callers
    overshoot ``max_candidates``."""
    from scudo_mapping_mcp import dense_scorer as ds

    # 26 dummy candidates — over the default cap of 25.
    cands = [
        Candidate(
            node=TaxonomyNode(iri=f"cdao:dummy-{i}", label=f"Dummy {i}"),
            similarity=0.5,
        )
        for i in range(26)
    ]

    invoked = {"count": 0}

    def should_not_be_called(**kwargs):
        invoked["count"] += 1
        return "{}"

    try:
        ds.score_candidates(
            "anything",
            cands,
            model_client=should_not_be_called,
        )
    except ValueError as e:
        assert "max_candidates" in str(e), str(e)
    else:
        raise AssertionError(
            "expected ValueError when len(candidates) > max_candidates"
        )
    # And critically: the model was NEVER invoked — we fail fast at the
    # boundary, before paying any Bedrock cost.
    assert invoked["count"] == 0, invoked


@case("DENSE_validates_response_shape")
def _():
    """Opus responses are validated at the seam — malformed JSON, missing
    IRIs, hallucinated IRIs, and out-of-range scores all surface as
    ``DenseScoreError`` so the matcher can fall back to the deterministic
    floor check (same path as 'no specialist passed')."""
    from scudo_mapping_mcp import dense_scorer as ds

    nodes = [
        TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices"),
        TaxonomyNode(iri=EQUITIES_IRI, label="Equities"),
    ]
    cands = [Candidate(node=n, similarity=0.1) for n in nodes]

    def model_returning(text):
        def _fn(**kwargs):
            return text

        return _fn

    # 1. Not JSON at all.
    try:
        ds.score_candidates(
            "x",
            cands,
            model_client=model_returning("not json at all"),
        )
    except ds.DenseScoreError as e:
        assert "not valid JSON" in str(e) or "missing" in str(e), str(e)
    else:
        raise AssertionError("expected DenseScoreError on malformed JSON")

    # 2. Missing one of the requested IRIs.
    bad_missing = json.dumps(
        {
            "scores": [{"iri": EQ_PRICES_IRI, "score": 0.9}],
        }
    )
    try:
        ds.score_candidates(
            "x",
            cands,
            model_client=model_returning(bad_missing),
        )
    except ds.DenseScoreError as e:
        assert "omitted" in str(e).lower() or EQUITIES_IRI in str(e), str(e)
    else:
        raise AssertionError("expected DenseScoreError on missing IRI")

    # 3. Hallucinated IRI not in the input set.
    bad_hallucinated = json.dumps(
        {
            "scores": [
                {"iri": EQ_PRICES_IRI, "score": 0.9},
                {"iri": EQUITIES_IRI, "score": 0.5},
                {"iri": "cdao:not-in-input", "score": 0.7},
            ],
        }
    )
    try:
        ds.score_candidates(
            "x",
            cands,
            model_client=model_returning(bad_hallucinated),
        )
    except ds.DenseScoreError as e:
        msg = str(e).lower()
        assert "unknown candidate" in msg or "invented" in msg, str(e)
    else:
        raise AssertionError("expected DenseScoreError on hallucinated IRI")

    # 4. Score out of [0, 1] range.
    bad_range = json.dumps(
        {
            "scores": [
                {"iri": EQ_PRICES_IRI, "score": 1.5},
                {"iri": EQUITIES_IRI, "score": 0.5},
            ],
        }
    )
    try:
        ds.score_candidates(
            "x",
            cands,
            model_client=model_returning(bad_range),
        )
    except ds.DenseScoreError as e:
        assert "out of" in str(e).lower() or "[0,1]" in str(e), str(e)
    else:
        raise AssertionError("expected DenseScoreError on out-of-range score")

    # 5. Duplicate IRI in the response.
    bad_dup = json.dumps(
        {
            "scores": [
                {"iri": EQ_PRICES_IRI, "score": 0.9},
                {"iri": EQ_PRICES_IRI, "score": 0.4},
                {"iri": EQUITIES_IRI, "score": 0.5},
            ],
        }
    )
    try:
        ds.score_candidates(
            "x",
            cands,
            model_client=model_returning(bad_dup),
        )
    except ds.DenseScoreError as e:
        msg = str(e).lower()
        assert "more than once" in msg or "duplicate" in msg, str(e)
    else:
        raise AssertionError("expected DenseScoreError on duplicate IRI")


# ──────────────────────────────────────────────────────────────────────────
# IFUSION — mock SPI V2 publish seam
# ──────────────────────────────────────────────────────────────────────────


@case("IFUSION_publish_returns_mock_acceptance")
def _():
    """POST /api/ifusion/publish answers with a synthetic acceptance envelope.

    The publish step is a pure mock today — no SPI V2 call, no HMAC verify
    (that already happened upstream in Persistence). The gate pins the
    contract the UI relies on so a careless refactor of the mock doesn't
    silently break the demo's "publish accepted" badge.

    Also pins the idempotency rule: a second publish of the same
    (vendor, product_id, mapping_id) returns the SAME publish_id with
    status='duplicate', which is what lets a reviewer click the button
    twice during the demo without confusion.
    """
    # Importing here keeps Flask / route deps out of the smoke runner's
    # top-level import surface — the other ~85 gates don't need Flask.
    from flask import Flask

    # Defer this import too: it pulls in `routes.ifusion`, which imports
    # `logger` from the backend package root. Resolves cleanly under the
    # normal `python -m scudo_mapping_mcp.tests.smoke` invocation from
    # the backend/ cwd.
    from routes.ifusion import ifusion_bp, _reset_for_tests

    _reset_for_tests()

    app = Flask(__name__)
    # No auth gate — this is a blueprint-isolation test, not an integration
    # test against the production app.py. The auth gate's coverage is owned
    # by app.py, not by this blueprint.
    app.register_blueprint(ifusion_bp, url_prefix="/api")
    client = app.test_client()

    payload = {
        "vendor": IN_SCOPE_VENDOR,
        "product_id": "EQ-RT-PUB-001",
        "mapping_id": "mapping-abc-123",
    }

    # ── First publish: fresh acceptance ────────────────────────────────
    resp = client.post("/api/ifusion/publish", json=payload)
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body["published"] is True, body
    assert body["status"] == "accepted", body
    assert body["channel"] == "spi_v2", body
    pid = body.get("publish_id") or ""
    assert pid.startswith("mock-pub-"), pid
    assert body["vendor"] == IN_SCOPE_VENDOR
    assert body["product_id"] == "EQ-RT-PUB-001"
    assert body["mapping_id"] == "mapping-abc-123"

    # ── Replay: same tuple must return the SAME publish_id, duplicate ──
    resp2 = client.post("/api/ifusion/publish", json=payload)
    assert resp2.status_code == 200, resp2.status_code
    body2 = resp2.get_json()
    assert body2["publish_id"] == pid, (body2, pid)
    assert body2["status"] == "duplicate", body2

    # ── /recent surfaces the publish for the UI ───────────────────────
    resp3 = client.get("/api/ifusion/recent?limit=5")
    assert resp3.status_code == 200, resp3.status_code
    recent = resp3.get_json()
    assert recent["count"] >= 1, recent
    assert any(p["publish_id"] == pid for p in recent["publishes"]), recent


# ─────────────────────────────────────────────────────────────────────────
# MCP HOST — round-robin transport, circuit breaker, per-tier semaphore,
# visibility metrics. SCUDO owns this layer; the trust gradient lives on
# the MCP servers themselves, not on the host.
# Gates use a stubbed transport (call-counting fake) so no HTTP server
# is required. asyncio.run is used to satisfy the brief's host-gate
# convention even though McpHost.call() is synchronous.
# ─────────────────────────────────────────────────────────────────────────


def _host_fake_transport(
    *, fail_forever: bool = False, call_log=None, status_code: int = 200, body=None
):
    """Build a transport stub matching the McpHost transport signature.

    Returns (transport_callable, calls_list). The transport records every
    invocation against `calls_list` so gates can assert on which endpoint
    URL was picked by the round-robin / breaker logic without standing up
    a real HTTP listener.
    """
    if call_log is None:
        call_log = []

    def _transport(method, url, *, json=None, timeout=10.0):  # noqa: A002
        call_log.append(
            {"method": method, "url": url, "json": json, "timeout": timeout}
        )
        if fail_forever:
            raise RuntimeError("transport down")
        return status_code, (body if body is not None else {"ok": True, "echo": json})

    return _transport, call_log


@case("HOST_pool_lazy_init_creates_one_client_per_mcp")
def _():
    """First call to a tier picks one of its endpoints via round-robin; a
    second call to the SAME tier reuses the host's transport / endpoint
    state (no per-call client construction). Demonstrates the pool /
    visibility contract without standing up HTTP.
    """
    import asyncio
    from scudo_mapping_mcp.mcp_host import McpHost

    transport, calls = _host_fake_transport()
    host = McpHost(
        endpoints_by_tier={
            "ingestion": ["http://x:8001/mcp"],
            "match_verify": ["http://x:8002/mcp"],
            "persistence": ["http://x:8003/mcp"],
        },
        transport=transport,
    )

    async def _run():
        # Three calls into match_verify exercise the same endpoint state.
        host.call("match_verify", "matchverify.find_candidates", {"v": 1})
        host.call("match_verify", "matchverify.find_candidates", {"v": 2})
        host.call("ingestion", "ingest.list_frames", {})

    asyncio.run(_run())

    # Transport saw exactly 3 calls: 2 to match_verify's only endpoint
    # plus 1 to ingestion's. Persistence never touched.
    mv_calls = [c for c in calls if c["url"] == "http://x:8002/mcp"]
    in_calls = [c for c in calls if c["url"] == "http://x:8001/mcp"]
    pe_calls = [c for c in calls if c["url"] == "http://x:8003/mcp"]
    assert len(mv_calls) == 2, mv_calls
    assert len(in_calls) == 1, in_calls
    assert len(pe_calls) == 0, pe_calls

    # Metrics confirm the per-tier rollup: 2 calls on match_verify, 1 on
    # ingestion, 0 on persistence. Single _EndpointState object per URL.
    m = host.metrics()
    assert m["tiers"]["match_verify"]["calls_total"] == 2, m
    assert m["tiers"]["ingestion"]["calls_total"] == 1, m
    assert m["tiers"]["persistence"]["calls_total"] == 0, m
    # Lazy-init invariant phrased for tier model: untouched tier has 0
    # calls and the breaker stays CLOSED — no transport ever consulted.
    assert m["tiers"]["persistence"]["breaker_state"] == "closed", m


@case("HOST_breaker_opens_after_threshold_failures")
def _():
    """After breaker_threshold consecutive failures the endpoint's breaker
    trips OPEN. When the tier has only one endpoint, the NEXT call fails
    fast with McpHostUnavailable — the picker refuses to forward to an
    OPEN endpoint.
    """
    import asyncio
    from scudo_mapping_mcp.mcp_host import (
        McpHost,
        McpHostError,
        McpHostUnavailable,
    )

    transport, calls = _host_fake_transport(fail_forever=True)
    host = McpHost(
        endpoints_by_tier={"match_verify": ["http://x:8002/mcp"]},
        breaker_threshold=3,
        breaker_cooldown_s=30.0,
        transport=transport,
    )

    async def _run():
        # 3 transport failures -> breaker trips on this endpoint.
        for _ in range(3):
            try:
                host.call("match_verify", "t", {})
            except McpHostError as e:
                # The picker forwarded — these are real transport errors,
                # not McpHostUnavailable.
                assert not isinstance(e, McpHostUnavailable), e
        # Endpoint should now be OPEN.
        m = host.metrics()
        ep = m["tiers"]["match_verify"]["endpoints"][0]
        assert ep["breaker_state"] == "open", ep
        assert m["tiers"]["match_verify"]["breaker_state"] == "open", m

        # The next call MUST fail fast with McpHostUnavailable without
        # touching the transport at all.
        calls_before = len(calls)
        try:
            host.call("match_verify", "t", {})
        except McpHostUnavailable:
            pass
        else:
            raise AssertionError("expected McpHostUnavailable once breaker open")
        assert len(calls) == calls_before, (
            "OPEN breaker must not forward the call to the transport"
        )

    asyncio.run(_run())


@case("HOST_breaker_half_open_after_reset_timeout")
def _():
    """OPEN -> HALF_OPEN after breaker_cooldown_s elapses. The picker
    treats half_open as eligible (next call probes the endpoint); a
    successful probe slams the breaker back to CLOSED via _record_success.

    We use the McpHost(clock=...) injection seam so we can advance time
    without sleeping the gate.
    """
    import asyncio
    from scudo_mapping_mcp.mcp_host import McpHost

    now = [1000.0]
    clock = lambda: now[0]

    # Transport that fails until we flip the switch.
    healthy = {"flag": False}

    def transport(method, url, *, json=None, timeout=10.0):  # noqa: A002
        if not healthy["flag"]:
            raise RuntimeError("down")
        return 200, {"ok": True}

    host = McpHost(
        endpoints_by_tier={"match_verify": ["http://x:8002/mcp"]},
        breaker_threshold=2,
        breaker_cooldown_s=30.0,
        transport=transport,
        clock=clock,
    )

    async def _run():
        # Trip the breaker (2 failures at threshold=2).
        from scudo_mapping_mcp.mcp_host import McpHostError

        for _ in range(2):
            try:
                host.call("match_verify", "t", {})
            except McpHostError:
                pass
        m = host.metrics()
        ep = m["tiers"]["match_verify"]["endpoints"][0]
        assert ep["breaker_state"] == "open", ep

        # Advance the clock past breaker_cooldown_s — endpoint now
        # surfaces as half_open (informational only — picker treats it
        # as CLOSED so the probe lands).
        now[0] += 31.0
        m2 = host.metrics()
        ep2 = m2["tiers"]["match_verify"]["endpoints"][0]
        assert ep2["breaker_state"] == "half_open", ep2

        # Make the next call succeed: probe lands, breaker resets.
        healthy["flag"] = True
        body = host.call("match_verify", "t", {})
        assert body == {"ok": True}, body
        m3 = host.metrics()
        ep3 = m3["tiers"]["match_verify"]["endpoints"][0]
        assert ep3["breaker_state"] == "closed", ep3

        # And a fresh failure streak after recovery still has to traverse
        # the full breaker_threshold — proves the failure counter reset.
        healthy["flag"] = False
        from scudo_mapping_mcp.mcp_host import McpHostError as _Err

        try:
            host.call("match_verify", "t", {})
        except _Err:
            pass
        # One failure post-recovery must NOT re-open the breaker (cap
        # is 2). Endpoint stays closed.
        m4 = host.metrics()
        ep4 = m4["tiers"]["match_verify"]["endpoints"][0]
        assert ep4["breaker_state"] == "closed", ep4

    asyncio.run(_run())


@case("HOST_semaphore_caps_concurrent_invocations")
def _():
    """Per-tier semaphore caps concurrency. With max_concurrent_per_tier=N
    and N slots already held, a non-blocking acquire must fail and
    surface McpHostError so the agent doesn't wait forever.

    We drain the semaphore by hand (the public surface holds it for the
    duration of the underlying transport call, so the cleanest gate is
    to assert against the actual BoundedSemaphore the host owns).
    """
    import asyncio
    from scudo_mapping_mcp.mcp_host import McpHost, McpHostError

    transport, _calls = _host_fake_transport()
    host = McpHost(
        endpoints_by_tier={"match_verify": ["http://x:8002/mcp"]},
        max_concurrent_per_tier=2,
        timeout_s=0.05,  # very short so the gate doesn't hang under load
        transport=transport,
    )

    async def _run():
        # The internal tier-state object exposes the semaphore — a public
        # attribute on _TierState. Reach in via the same _tier helper the
        # host uses internally so the gate doesn't depend on naming.
        ts = host._tier("match_verify")
        sem = ts.semaphore

        # Grab 2 slots — same as 2 concurrent in-flight calls.
        acquired_1 = sem.acquire(blocking=False)
        acquired_2 = sem.acquire(blocking=False)
        assert acquired_1 and acquired_2, (acquired_1, acquired_2)

        # 3rd acquire MUST fail (cap=2). And a host.call() now MUST raise
        # McpHostError on semaphore exhaustion within timeout_s.
        third = sem.acquire(blocking=False)
        assert third is False, "semaphore must refuse a 3rd slot at cap=2"

        try:
            host.call("match_verify", "t", {})
        except McpHostError as e:
            assert "semaphore exhausted" in str(e), str(e)
        else:
            raise AssertionError(
                "expected McpHostError on semaphore exhaustion at cap=2"
            )

        # Release the held slots; the next call lands cleanly.
        sem.release()
        sem.release()
        body = host.call("match_verify", "t", {})
        assert body == {"ok": True, "echo": {"tool": "t", "args": {}}}, body

    asyncio.run(_run())


@case("HOST_status_snapshot_reports_per_mcp_state")
def _():
    """metrics() is the visibility-platform surface. It MUST report every
    configured tier (even untouched ones), every endpoint URL, breaker
    state, calls totals, in-flight counter, and the live config block
    the dashboard renders.
    """
    import asyncio
    from scudo_mapping_mcp.mcp_host import McpHost

    transport, _calls = _host_fake_transport()
    host = McpHost(
        endpoints_by_tier={
            "ingestion": ["http://x:8001/mcp"],
            "match_verify": ["http://x:8002a/mcp", "http://x:8002b/mcp"],
            "persistence": ["http://x:8003/mcp"],
        },
        max_concurrent_per_tier=4,
        breaker_threshold=7,
        breaker_cooldown_s=15.0,
        timeout_s=10.0,
        transport=transport,
    )

    # Untouched snapshot.
    m = host.metrics()
    assert m["enabled"] is True, m
    assert m["config"]["max_concurrent_per_tier"] == 4, m
    assert m["config"]["breaker_threshold"] == 7, m
    assert m["config"]["breaker_cooldown_s"] == 15.0, m
    assert m["config"]["timeout_s"] == 10.0, m

    assert set(m["tiers"].keys()) == {
        "ingestion",
        "match_verify",
        "persistence",
    }, m
    for tier, view in m["tiers"].items():
        assert view["calls_total"] == 0, (tier, view)
        assert view["calls_failed"] == 0, (tier, view)
        assert view["in_flight"] == 0, (tier, view)
        assert view["breaker_state"] == "closed", (tier, view)
        for ep in view["endpoints"]:
            assert ep["breaker_state"] == "closed", ep
            assert ep["calls_total"] == 0, ep
            assert ep["calls_failed"] == 0, ep
            assert ep["in_flight"] == 0, ep
    # match_verify has 2 endpoints; the other tiers have 1 each.
    assert len(m["tiers"]["match_verify"]["endpoints"]) == 2, m
    assert len(m["tiers"]["ingestion"]["endpoints"]) == 1, m

    async def _run():
        host.call("match_verify", "t", {"v": 1})
        host.call("match_verify", "t", {"v": 2})

    asyncio.run(_run())

    m2 = host.metrics()
    # Round-robin spread the 2 calls across the 2 match_verify endpoints,
    # one each. Worst case it's 2/0; in either case the tier total is 2.
    mv = m2["tiers"]["match_verify"]
    assert mv["calls_total"] == 2, mv
    assert sum(ep["calls_total"] for ep in mv["endpoints"]) == 2, mv
    # Untouched tiers still report cleanly.
    assert m2["tiers"]["ingestion"]["calls_total"] == 0, m2
    assert m2["tiers"]["persistence"]["calls_total"] == 0, m2
    assert m2["tiers"]["persistence"]["breaker_state"] == "closed", m2


@case("INV_mcp_host_round_robin_cycles")
def _():
    """Round-robin within a tier cycles deterministically across endpoints.

    The invariant the arb-review-pack pins: with N endpoints in a tier
    and the breaker closed on all of them, N successive calls hit each
    endpoint exactly once and N+1 wraps back to the first. This proves:

      1. The selector advances monotonically (no stuck cursor).
      2. The wrap-around is by `% len(endpoints)` (no off-by-one that
         would leak calls onto an endpoint that doesn't exist).
      3. Per-endpoint visibility counters tick correctly under load —
         the SCUDO dashboard's per-endpoint bars are not aliased.
    """
    from scudo_mapping_mcp.mcp_host import McpHost

    transport, calls = _host_fake_transport()
    host = McpHost(
        endpoints_by_tier={
            "match_verify": [
                "http://x:8002a/mcp",
                "http://x:8002b/mcp",
                "http://x:8002c/mcp",
            ],
        },
        transport=transport,
    )

    # 7 calls into a 3-endpoint tier -> cycle two full times plus one,
    # so the URL sequence MUST be a-b-c-a-b-c-a in order.
    for i in range(7):
        host.call("match_verify", "t", {"i": i})

    seen_urls = [c["url"] for c in calls]
    expected = [
        "http://x:8002a/mcp",
        "http://x:8002b/mcp",
        "http://x:8002c/mcp",
        "http://x:8002a/mcp",
        "http://x:8002b/mcp",
        "http://x:8002c/mcp",
        "http://x:8002a/mcp",
    ]
    assert seen_urls == expected, seen_urls

    # Per-endpoint counters: a=3, b=2, c=2. metrics() must reflect that.
    m = host.metrics()
    by_url = {ep["url"]: ep for ep in m["tiers"]["match_verify"]["endpoints"]}
    assert by_url["http://x:8002a/mcp"]["calls_total"] == 3, by_url
    assert by_url["http://x:8002b/mcp"]["calls_total"] == 2, by_url
    assert by_url["http://x:8002c/mcp"]["calls_total"] == 2, by_url
    assert m["tiers"]["match_verify"]["calls_total"] == 7, m

    # With one endpoint OPEN the picker must skip it on the next pick.
    host._force_open("match_verify", "http://x:8002b/mcp")
    calls.clear()
    host.call("match_verify", "t", {"j": 0})
    host.call("match_verify", "t", {"j": 1})
    skipped_urls = [c["url"] for c in calls]
    # 'b' was OPEN, so picker yields a then c (or c then a depending on
    # cursor position) — but NEVER b. The invariant is the set, not the
    # exact ordering, because the cursor advances past the OPEN endpoint.
    assert "http://x:8002b/mcp" not in skipped_urls, skipped_urls
    assert len(skipped_urls) == 2, skipped_urls


@case("INV_mcp_host_disabled_by_default_for_agent")
def _():
    """ScriptedMappingAgent defaults use_mcp_host=False — the visible
    behaviour is identical to the pre-WS-C in-process path.

    Locks the opt-in contract the brief calls out so the 86/86 (now 105+)
    smoke suite can't silently start depending on the host being wired.
    """
    from scudo_mapping_mcp.agent import ScriptedMappingAgent

    agent = ScriptedMappingAgent()
    assert agent._use_mcp_host is False, agent._use_mcp_host
    assert agent._host() is None, agent._host()

    # Explicit-False is also a no-op; passing a real host but use=False
    # must still route in-process. This is the safety net for a future
    # caller that gets the wiring half-right.
    from scudo_mapping_mcp.mcp_host import McpHost

    fake_transport, _ = _host_fake_transport()
    host = McpHost(
        endpoints_by_tier={"ingestion": ["http://x/mcp"]},
        transport=fake_transport,
    )
    a2 = ScriptedMappingAgent(use_mcp_host=False, mcp_host=host)
    assert a2._host() is None, a2._host()


# ──────────────────────────────────────────────────────────────────────────
# ARB FIX VERIFICATION — A1 / A2 / B1 / B2
# ──────────────────────────────────────────────────────────────────────────


@case("ARB_A1_flag_on_passes_real_scorer_not_none")
def _():
    """Closes ARB A1. SCUDO_USE_OPUS_DENSE=true must wire a real Opus
    scorer into multi_path_retrieve — NOT dense_scorer=None which would
    collapse every match to 0.5 and route everything to NEEDS_REVIEW.
    """
    from scudo_mapping_mcp.store import falkordb_store as fk_mod
    import inspect

    src = inspect.getsource(fk_mod.FalkorDBStore.find_similar_products)
    assert "make_opus_dense_scorer" in src, (
        "find_similar_products flag-on branch must call "
        "opus_dense.make_opus_dense_scorer, not pass dense_scorer=None"
    )
    # Verify the canonical scorer module is the one being wired.
    assert "opus_dense" in src, (
        "the canonical Opus surface (opus_dense) must be the import"
    )


@case("ARB_A2_dense_scorer_module_is_deprecated_in_docstring")
def _():
    """Closes ARB A2. The duplicate dense_scorer.py module must be
    visibly marked DEPRECATED so a maintainer reading the code on demo
    day can tell which Opus path is the real one.
    """
    from scudo_mapping_mcp import dense_scorer

    assert dense_scorer.__doc__ is not None
    assert "DEPRECATED" in dense_scorer.__doc__, (
        "dense_scorer.py must carry a DEPRECATED marker in its docstring"
    )
    assert "opus_dense" in dense_scorer.__doc__, (
        "dense_scorer docstring must point at the canonical opus_dense module"
    )


@case("ARB_B1_agent_host_calls_route_to_match_verify_tier")
def _():
    """Closes ARB B1. Agent host-mode calls for find_similar_products /
    get_taxonomy_node / get_ontology_neighbourhood must route to
    _TIER_MATCH_VERIFY (where those tools live), not _TIER_INGESTION
    (which only exposes ingest.list_frames / ingest.get_frame).
    """
    import inspect
    from scudo_mapping_mcp import agent as agent_module

    src = inspect.getsource(agent_module)
    # The three M&V tool calls must use match_verify tier + namespaced names.
    assert '_TIER_MATCH_VERIFY, "matchverify.find_candidates"' in src, (
        "find_similar_products call must be re-tiered to match_verify"
    )
    assert '_TIER_MATCH_VERIFY, "matchverify.get_node"' in src, (
        "get_taxonomy_node call must be re-tiered to match_verify"
    )
    assert '_TIER_MATCH_VERIFY, "matchverify.get_neighbourhood"' in src, (
        "get_ontology_neighbourhood call must be re-tiered to match_verify"
    )
    # No remaining ingestion-tier calls for M&V tools.
    assert '_TIER_INGESTION, "find_similar_products"' not in src
    assert '_TIER_INGESTION, "get_taxonomy_node"' not in src
    assert '_TIER_INGESTION, "get_ontology_neighbourhood"' not in src


@case("ARB_B2_dense_rescore_catches_scorer_exception_and_degrades")
def _():
    """Closes ARB B2. A failing dense_scorer (Bedrock outage / throttle /
    malformed response) must NOT crash the matcher. _dense_rescore catches
    the exception, logs a warning, and degrades every survivor to
    _DEGRADED_SIMILARITY so the floor gate routes the case to review.
    """
    from scudo_mapping_mcp import retrieval
    from scudo_mapping_mcp.models import Candidate, TaxonomyNode

    survivors = [
        Candidate(node=TaxonomyNode(iri="cdao:a", label="A"), similarity=0.0),
        Candidate(node=TaxonomyNode(iri="cdao:b", label="B"), similarity=0.0),
    ]

    def exploding_scorer(query, cands):
        raise RuntimeError("simulated Bedrock outage")

    # Must NOT raise.
    out = retrieval._dense_rescore("anything", survivors, exploding_scorer)
    assert len(out) == 2
    # Every survivor degraded.
    for c in out:
        assert abs(c.similarity - retrieval._DEGRADED_SIMILARITY) < 1e-9, c
    # Below floor — matcher's floor gate will route the whole case to review.
    assert retrieval._DEGRADED_SIMILARITY < 0.80


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
