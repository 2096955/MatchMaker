"""Offline unit tests for the FalkorDB matcher bridge + the AWS adapter.

WHAT THIS COVERS (and the zero-coverage gap it closes)
------------------------------------------------------
The deployed Lambda is moving OFF the mock candidate set (AWS_HANDOFF.md §43:
"the matcher runs over MOCK candidates") and ONTO the real cost-ladder matcher
(``scudo_mapping_mcp.matching.map_vendor_product``) reading a FalkorDB OSS
graph. ``scudo.matcher_bridge`` is the seam that adapts the Lambda's plain-dict
world to the matcher's typed ``VendorProductRef`` -> ``MappingResult`` contract.

These tests pin the bridge's public contract WITHOUT a real FalkorDB, AWS
credentials, or boto3 — every external dependency is faked via monkeypatch:

  1. ``run_match`` maps a real ``MappingResult`` into the documented flat dict
     (all keys present, types correct).
  2. ``run_match`` fails CLOSED — when the underlying matcher/store raises, the
     bridge raises ``BridgeError`` rather than silently returning mock data.
  3. ``aws_resources.put_audit_record`` / ``put_outbox_record`` are no-ops when
     their env table vars are unset — early return, no boto3 import, no throw.

HOW THE FAKES ARE INJECTED
--------------------------
``scudo.matcher_bridge.run_match`` does its heavy imports LAZILY inside the
function (the offline-smoke contract: importing ``scudo`` must not pull in
pydantic / falkordb / a TCP connection). It then, in order:

  1. ``from scudo_mapping_mcp.config import settings`` and asserts
     ``settings.store_backend == "falkordb"``;
  2. ``get_store()`` (and probes ``store.health()``) — fails closed if the
     FalkorDB connection can't be built or is unhealthy;
  3. ``map_vendor_product(ref, max_candidates=...)`` — the real cost ladder.

Because those are LOCAL imports resolved at call time, the seam to patch is the
SOURCE module attributes, not anything bound on the bridge:

  * ``scudo_mapping_mcp.config.settings``        -> fake settings (backend=falkordb)
  * ``scudo_mapping_mcp.store.get_store``        -> returns a fake healthy store
  * ``scudo_mapping_mcp.matching.map_vendor_product`` -> fake matcher

The ``_falkordb_seam`` helper wires all three so NO real FalkorDB, store, or
network is ever touched. The frozen ``Settings`` dataclass can't be mutated in
place (FrozenInstanceError), so we REPLACE the module attribute with a stand-in.

IMPORT SAFETY
-------------
``scudo.matcher_bridge`` may not exist yet in a given checkout (the orchestrator
integrates it). The bridge tests use ``pytest.importorskip`` so this module
always imports cleanly; the bridge cases SKIP until the bridge lands, then run
for real. The aws_resources cases run unconditionally — that module ships now.

Run:
  python -m pytest scudo/tests/test_falkordb_bridge.py -v
"""

from __future__ import annotations

import importlib
import sys

import pytest

# The real, typed matcher contract. Imports with NO env and NO FalkorDB —
# the store is only constructed lazily inside map_vendor_product, never at
# import time (verified: scudo_mapping_mcp.models / matching import offline).
from scudo_mapping_mcp.models import (
    Candidate,
    MappingResult,
    MappingStatus,
    TaxonomyNode,
)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ────────────────────────────────────────────────────────────────────────────
def _sample_vendor_product() -> dict:
    """A plain dict in the shape the Lambda hands the bridge (no Pydantic)."""
    return {
        "vendor": "LSEG",
        "product_id": "LSEG-IBES-EST-001",
        "name": "I/B/E/S Estimates",
        "description": "Consensus analyst earnings estimates",
        "raw": {"sku": "IBES-EST-001"},
    }


def _auto_mapped_result() -> MappingResult:
    """A realistic AUTO_MAPPED result straight from the cost ladder (PASS band).

    Built with the SAME constructor the matcher uses, so the bridge's dict
    projection is tested against a genuine MappingResult, not a stand-in.
    """
    node = TaxonomyNode(
        iri="mds.cdao:equity-consensus-estimates",
        label="Equity Consensus Estimates",
    )
    best = Candidate(node=node, similarity=0.91)
    runner_up = Candidate(
        node=TaxonomyNode(iri="mds.cdao:equity-pricing", label="Equity Pricing"),
        similarity=0.71,
    )
    return MappingResult(
        vendor_product_iri="mds.lseg:00000000-0000-0000-0000-000000000001",
        vendor="LSEG",
        product_id="LSEG-IBES-EST-001",
        product_name="I/B/E/S Estimates",
        mapped_node_iri=node.iri,
        mapped_node_label=node.label,
        confidence=0.91,
        status=MappingStatus.AUTO_MAPPED,
        candidates=[best, runner_up],
        rationale="PASS band — best candidate at 0.91 >= pass threshold 0.85.",
        band="pass",
    )


def _load_bridge():
    """Import scudo.matcher_bridge or skip the whole test if it isn't built yet.

    The orchestrator integration adds this module; until then the bridge
    cases are skipped so the file stays import-safe and collectable.
    """
    return pytest.importorskip(
        "scudo.matcher_bridge",
        reason="scudo.matcher_bridge not present yet (orchestrator integrates it)",
    )


class _FakeSettings:
    """Stand-in for the frozen ``Settings`` dataclass.

    The bridge reads ``store_backend`` (must be "falkordb"), and quotes
    ``falkordb_url`` / ``graph_name`` in its error messages. We can't mutate the
    real frozen instance in place, so we swap the module attribute for this.
    """

    store_backend = "falkordb"
    falkordb_url = "falkordb://falkordb.scudo.local:6379"
    graph_name = "scudo_mapping"


class _FakeStore:
    """A healthy FalkorDB store stand-in. ``health()`` returns True so the
    bridge's reachability probe passes without a real connection."""

    def __init__(self, *, healthy: bool = True):
        self._healthy = healthy

    def health(self) -> bool:
        return self._healthy


def _falkordb_seam(monkeypatch, fake_matcher, *, store=None) -> None:
    """Wire the three Runtime-B seams the bridge resolves at call time.

    Patches the SOURCE module attributes (not the bridge) because the bridge's
    imports are lazy/local. After this, ``run_match`` runs end-to-end with no
    real FalkorDB, store, or network: backend reads as falkordb, ``get_store``
    yields a healthy fake, and the matcher is ``fake_matcher``.
    """
    import scudo_mapping_mcp.config as config_mod
    import scudo_mapping_mcp.matching as matching_mod
    import scudo_mapping_mcp.store as store_mod

    monkeypatch.setattr(config_mod, "settings", _FakeSettings(), raising=False)
    monkeypatch.setattr(
        store_mod, "get_store", lambda: store or _FakeStore(), raising=False
    )
    monkeypatch.setattr(matching_mod, "map_vendor_product", fake_matcher, raising=False)


# ────────────────────────────────────────────────────────────────────────────
# 1. run_match maps a real MappingResult into the documented dict shape.
# ────────────────────────────────────────────────────────────────────────────
def test_run_match_maps_result_to_documented_dict(monkeypatch):
    bridge = _load_bridge()

    captured: dict = {}

    def fake_map(ref, max_candidates=8, *, specialist=None):
        # Confirm the bridge built a typed VendorProductRef from the dict and
        # threaded max_candidates through to the real matcher.
        captured["vendor"] = ref.vendor
        captured["product_id"] = ref.product_id
        captured["max_candidates"] = max_candidates
        return _auto_mapped_result()

    _falkordb_seam(monkeypatch, fake_map)

    out = bridge.run_match(_sample_vendor_product(), max_candidates=5)

    # --- The bridge marshalled the dict into the typed ref correctly ---------
    assert captured["vendor"] == "LSEG"
    assert captured["product_id"] == "LSEG-IBES-EST-001"
    assert captured["max_candidates"] == 5

    # --- Documented dict shape: every key present ---------------------------
    expected_keys = {
        "outcome",
        "status",
        "mapped_node_iri",
        "mapped_node_label",
        "confidence",
        "band",
        "candidates",
        "rationale",
        "raw",
    }
    assert isinstance(out, dict)
    assert expected_keys <= set(out), f"missing keys: {expected_keys - set(out)}"

    # --- Documented types ---------------------------------------------------
    assert isinstance(out["status"], str)
    assert out["status"] == MappingStatus.AUTO_MAPPED.value
    assert isinstance(out["mapped_node_iri"], str)
    assert out["mapped_node_iri"] == "mds.cdao:equity-consensus-estimates"
    assert isinstance(out["mapped_node_label"], str)
    assert out["mapped_node_label"] == "Equity Consensus Estimates"
    assert isinstance(out["confidence"], float)
    assert out["confidence"] == pytest.approx(0.91)
    assert out["band"] in {"pass", "borderline", "fail", "n/a"}
    assert out["band"] == "pass"
    assert isinstance(out["rationale"], str) and out["rationale"]

    # candidates must be JSON-serialisable (list of dicts, not Pydantic) so the
    # Lambda can put the payload straight into DynamoDB / the HTTP response.
    assert isinstance(out["candidates"], list)
    assert len(out["candidates"]) == 2
    for cand in out["candidates"]:
        assert isinstance(cand, dict), "candidates must be plain dicts, not models"
    # The top candidate's similarity survives the projection as a float.
    sims = [c.get("similarity") for c in out["candidates"] if "similarity" in c]
    assert all(isinstance(s, float) for s in sims) and sims, sims

    # outcome is a non-empty string label (auto-mapped vs review etc).
    assert isinstance(out["outcome"], str) and out["outcome"]

    # raw is the untouched echo of the matcher's view — a dict.
    assert isinstance(out["raw"], dict)

    # Whole payload must be JSON round-trippable (Lambda response / audit).
    import json

    json.dumps(out)


def test_run_match_passes_default_max_candidates(monkeypatch):
    """The documented default (max_candidates=8) reaches the matcher unchanged."""
    bridge = _load_bridge()

    seen: dict = {}

    def fake_map(ref, max_candidates=8, *, specialist=None):
        seen["max_candidates"] = max_candidates
        return _auto_mapped_result()

    _falkordb_seam(monkeypatch, fake_map)

    bridge.run_match(_sample_vendor_product())  # no max_candidates -> default
    assert seen["max_candidates"] == 8


def test_run_match_surfaces_needs_review_band(monkeypatch):
    """A NEEDS_REVIEW / fail-band result is mapped through (not dropped)."""
    bridge = _load_bridge()

    node = TaxonomyNode(iri="mds.cdao:misc", label="Miscellaneous")
    result = MappingResult(
        vendor_product_iri="mds.lseg:zz",
        vendor="LSEG",
        product_id="P-LOWCONF",
        product_name="Obscure Feed",
        mapped_node_iri=node.iri,
        mapped_node_label=node.label,
        confidence=0.42,
        status=MappingStatus.NEEDS_REVIEW,
        candidates=[Candidate(node=node, similarity=0.42)],
        rationale="FAIL band — best candidate at 0.42 < borderline threshold.",
        band="fail",
    )

    _falkordb_seam(
        monkeypatch, lambda ref, max_candidates=8, *, specialist=None: result
    )

    out = bridge.run_match(_sample_vendor_product())
    assert out["status"] == MappingStatus.NEEDS_REVIEW.value
    assert out["band"] == "fail"
    assert out["confidence"] == pytest.approx(0.42)


# ────────────────────────────────────────────────────────────────────────────
# 2. run_match fails CLOSED — BridgeError on any matcher/store failure.
#    No silent mock fallback (the whole point of the bridge swap).
# ────────────────────────────────────────────────────────────────────────────
def test_run_match_raises_bridge_error_on_matcher_failure(monkeypatch):
    bridge = _load_bridge()

    assert hasattr(bridge, "BridgeError"), "bridge must expose BridgeError"
    assert issubclass(bridge.BridgeError, Exception)

    class _StoreDown(RuntimeError):
        """Stand-in for a FalkorDB connection / Cypher failure."""

    def boom(ref, max_candidates=8, *, specialist=None):
        raise _StoreDown("falkordb://falkordb.scudo.local:6379 unreachable")

    _falkordb_seam(monkeypatch, boom)

    with pytest.raises(bridge.BridgeError):
        bridge.run_match(_sample_vendor_product())


def test_run_match_does_not_fall_back_to_mock_on_failure(monkeypatch):
    """Fail-closed means NO mock candidates leak through on store failure.

    Even if a mock-candidate helper is importable, a store failure must raise,
    never return a (possibly mock) result dict.
    """
    bridge = _load_bridge()

    def boom(ref, max_candidates=8, *, specialist=None):
        raise ConnectionError("graph unavailable")

    _falkordb_seam(monkeypatch, boom)

    with pytest.raises(bridge.BridgeError):
        result = bridge.run_match(_sample_vendor_product())
        # Should never reach here; guard against an accidental mock return.
        assert result is None, "fail-closed violated: bridge returned a result"


def test_run_match_rejects_malformed_vendor_product(monkeypatch):
    """A dict with no vendor / product identifier fails closed as BridgeError.

    The bridge needs a vendor (scope gate + IRI derivation) and a product
    identifier (deterministic IRI). A dict missing both must raise BridgeError,
    not leak a raw KeyError / pydantic ValidationError — and must NOT reach the
    matcher (the FalkorDB store + health probe pass; the input is the problem).
    """
    bridge = _load_bridge()

    # Matcher must never be reached — the input is rejected before the ladder.
    def must_not_call(ref, max_candidates=8, *, specialist=None):  # pragma: no cover
        raise AssertionError("matcher invoked on a malformed vendor_product")

    _falkordb_seam(monkeypatch, must_not_call)

    with pytest.raises(bridge.BridgeError):
        bridge.run_match({"description": "no vendor, no product_id"})


def test_run_match_fails_closed_on_wrong_backend(monkeypatch):
    """If STORE_BACKEND isn't falkordb, the bridge refuses rather than matching
    against the wrong store (memory/neptune). Fail-closed on misconfiguration."""
    bridge = _load_bridge()

    import scudo_mapping_mcp.config as config_mod

    class _MemorySettings(_FakeSettings):
        store_backend = "memory"

    monkeypatch.setattr(config_mod, "settings", _MemorySettings(), raising=False)

    with pytest.raises(bridge.BridgeError):
        bridge.run_match(_sample_vendor_product())


def test_run_match_fails_closed_when_store_unhealthy(monkeypatch):
    """An unreachable FalkorDB (health() -> False) is surfaced as BridgeError
    before the matcher runs — no silent match against a degraded store."""
    bridge = _load_bridge()

    def must_not_call(ref, max_candidates=8, *, specialist=None):  # pragma: no cover
        raise AssertionError("matcher invoked against an unhealthy store")

    _falkordb_seam(monkeypatch, must_not_call, store=_FakeStore(healthy=False))

    with pytest.raises(bridge.BridgeError):
        bridge.run_match(_sample_vendor_product())


def test_run_match_accepts_vendor_product_ref_alias(monkeypatch):
    """The Lambda /run body uses 'vendor_product_ref' / 'title'; the bridge
    accepts those aliases for product_id / name."""
    bridge = _load_bridge()

    seen: dict = {}

    def fake_map(ref, max_candidates=8, *, specialist=None):
        seen["product_id"] = ref.product_id
        seen["name"] = ref.name
        return _auto_mapped_result()

    _falkordb_seam(monkeypatch, fake_map)

    bridge.run_match(
        {
            "vendor": "LSEG",
            "vendor_product_ref": "LSEG-IBES-EST-001",
            "title": "I/B/E/S Estimates",
        }
    )
    assert seen["product_id"] == "LSEG-IBES-EST-001"
    assert seen["name"] == "I/B/E/S Estimates"


# ────────────────────────────────────────────────────────────────────────────
# 3. aws_resources audit/outbox writers now delegate to the Aurora store and are
#    FAIL-LOUD: with Aurora env unset they RAISE (naming the missing var) and
#    never construct a Data API client. The DynamoDB fail-soft no-op that used
#    to live here is exactly the behaviour the 5-zone migration removes.
#    (This module already ships — runs unconditionally, no importorskip.)
# ────────────────────────────────────────────────────────────────────────────
_AURORA_ENV = (
    "SCUDO_AURORA_CLUSTER_ARN",
    "SCUDO_AURORA_SECRET_ARN",
    "SCUDO_AURORA_DATABASE_NAME",
)


def test_put_audit_record_fails_loud_when_aurora_unset(monkeypatch):
    from scudo import aurora_store, aws_resources

    for var in _AURORA_ENV:
        monkeypatch.delenv(var, raising=False)

    # If the Data API client were built, this would explode — proving the
    # config check short-circuits before any boto3 client construction.
    def _explode():  # pragma: no cover - asserts the fail-loud path is hit
        raise AssertionError(
            "rds-data client must NOT be built when Aurora env is unset"
        )

    monkeypatch.setattr(aurora_store, "_rds_data", _explode)

    with pytest.raises(RuntimeError, match="SCUDO_AURORA_CLUSTER_ARN"):
        aws_resources.put_audit_record(
            item_id="vp-1",
            event_type="MAPPING_AUTO_MAPPED",
            payload={"confidence": 0.91},
        )


def test_put_outbox_record_fails_loud_when_aurora_unset(monkeypatch):
    from scudo import aurora_store, aws_resources

    for var in _AURORA_ENV:
        monkeypatch.delenv(var, raising=False)

    def _explode():  # pragma: no cover - asserts the fail-loud path is hit
        raise AssertionError(
            "rds-data client must NOT be built when Aurora env is unset"
        )

    monkeypatch.setattr(aurora_store, "_rds_data", _explode)

    with pytest.raises(RuntimeError, match="SCUDO_AURORA_CLUSTER_ARN"):
        aws_resources.put_outbox_record(
            event_id="evt-1",
            detail_type="MappingCompleted",
            detail={"vendor": "LSEG", "product_id": "P1"},
        )


def test_aws_resources_imports_without_boto3():
    """The adapter imports with boto3 absent — lazy import is real, not nominal.

    Simulate a boto3-less environment (offline smoke) by masking the module,
    then (re)import scudo.aws_resources and confirm it loads. boto3 is only
    pulled inside _boto3() / aurora_store._rds_data(), which the fail-loud
    config check never reaches when Aurora env is unset — so the write raises
    RuntimeError, not ImportError.
    """
    real_boto3 = sys.modules.get("boto3")
    try:
        sys.modules["boto3"] = None  # any `import boto3` now raises ImportError
        importlib.reload(importlib.import_module("scudo.aws_resources"))
        from scudo import aws_resources

        import os

        for var in _AURORA_ENV:
            os.environ.pop(var, None)
        with pytest.raises(RuntimeError):
            aws_resources.put_audit_record(item_id="x", event_type="t", payload={})
        with pytest.raises(RuntimeError):
            aws_resources.put_outbox_record(event_id="e", detail_type="d", detail={})
    finally:
        if real_boto3 is not None:
            sys.modules["boto3"] = real_boto3
        else:
            sys.modules.pop("boto3", None)
        # Restore a clean module object for any later importers.
        importlib.reload(importlib.import_module("scudo.aws_resources"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
