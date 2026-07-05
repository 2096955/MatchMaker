"""REST /map wires a real specialist into the borderline band, fail-safe
(Codex A5). Borderline + no/abstaining specialist must NOT auto-map on the REST
path — it must route to NEEDS_REVIEW.
"""

from __future__ import annotations

import os

from scudo_mapping_mcp.matching import map_vendor_product
from scudo_mapping_mcp.models import (
    Candidate,
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
)


def _ref():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    return VendorProductRef(vendor="LSEG", product_id="X-1", name="Some Product")


class _FakeStore:
    """Minimal store returning one borderline candidate (sim in [0.70,0.80))."""

    def __init__(self, sim: float):
        self._node = TaxonomyNode(iri="jpmorgan:data:cdao:concept:x", label="X Concept")
        self._sim = sim

    def get_precedent_mapping(self, *a, **k):
        return None

    def find_similar_products(self, ref, max_results=8):
        return [Candidate(node=self._node, similarity=self._sim)]

    def get_taxonomy_node(self, iri):
        return self._node


def test_borderline_abstain_requires_specialist_goes_needs_review():
    # sim 0.77 is borderline AND >= floor 0.75 → legacy path would AUTO_MAP.
    store = _FakeStore(0.77)
    result = map_vendor_product(
        _ref(),
        store=store,
        specialist=lambda ref, cands: None,  # abstains
        borderline_requires_specialist=True,
    )
    assert result.status == MappingStatus.NEEDS_REVIEW, result.rationale
    assert result.band == "borderline"


def test_borderline_specialist_concurs_can_automap():
    store = _FakeStore(0.77)
    node = store._node

    def concur(ref, cands):
        return Candidate(node=node, similarity=0.83)

    result = map_vendor_product(
        _ref(), store=store, specialist=concur, borderline_requires_specialist=True
    )
    # specialist concurred above floor → AUTO_MAPPED is allowed
    assert result.status == MappingStatus.AUTO_MAPPED, result.rationale


def test_legacy_path_still_automaps_on_abstain():
    # Without borderline_requires_specialist, the agent/legacy path keeps its
    # floor-fallback behavior (no regression).
    store = _FakeStore(0.77)
    result = map_vendor_product(_ref(), store=store, specialist=lambda r, c: None)
    assert result.status == MappingStatus.AUTO_MAPPED


def test_rest_specialist_abstains_on_error(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    def _boom(*a, **k):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(sp, "_best_pick", _boom)
    spec = sp.make_rest_specialist()
    node = TaxonomyNode(iri="x", label="X")
    out = spec(_ref(), [Candidate(node=node, similarity=0.8)])
    assert out is None  # error → abstain, never raises


def test_rest_specialist_timeout_is_wall_clock(monkeypatch):
    """A slow specialist must abstain at ~the timeout, NOT after the full slow
    call (Codex A5 INCOMPLETE: the `with` executor re-blocked on shutdown)."""
    import time

    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setattr(sp, "_TIMEOUT_S", 0.3)

    def _slow(*a, **k):
        time.sleep(5)  # far longer than the timeout
        return None

    monkeypatch.setattr(sp, "_best_pick", _slow)
    spec = sp.make_rest_specialist()
    node = TaxonomyNode(iri="x", label="X")
    start = time.monotonic()
    out = spec(_ref(), [Candidate(node=node, similarity=0.8)])
    elapsed = time.monotonic() - start
    assert out is None
    # Must return near the 0.3s timeout, not the 5s slow call. Allow headroom.
    assert elapsed < 2.0, f"timeout not a wall-clock guard: took {elapsed:.2f}s"
