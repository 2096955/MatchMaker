"""Per-call gate-window override on map_vendor_product (Phase 2a / 2b).

The matcher's PASS/BORDERLINE/FAIL bands are normally read from
``settings.confidence_floor`` / ``settings.borderline_half_width``. Phase 2
threads an EXPLICIT per-call override (``floor=`` / ``half=``) — no module
global, no contextvar — so the same fixed dense score lands in a different
band depending on the window the caller passes.

Pattern mirrors test_rest_specialist.py: a minimal fake store returns one
candidate at a controlled similarity, so the band is a pure function of the
window. Run PER-FILE (the suite has known store-cache cross-file pollution):

    PYTHONPATH=. python3 -m pytest scudo/tests/test_threshold_override.py -q
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
    """Minimal store returning one candidate at a fixed similarity."""

    def __init__(self, sim: float):
        self._node = TaxonomyNode(iri="jpmorgan:data:cdao:concept:x", label="X Concept")
        self._sim = sim

    def get_precedent_mapping(self, *a, **k):
        return None

    def find_similar_products(self, ref, max_results=8):
        return [Candidate(node=self._node, similarity=self._sim)]

    def get_taxonomy_node(self, iri):
        return self._node


def test_default_window_0_78_is_borderline_needs_review():
    """sim 0.78 with the default 0.80/0.05 window:
    pass cut 0.85, borderline cut 0.75 → 0.75 <= 0.78 < 0.85 → BORDERLINE.
    No specialist → falls back to floor 0.80; 0.78 < 0.80 → NEEDS_REVIEW.
    """
    store = _FakeStore(0.78)
    result = map_vendor_product(_ref(), store=store, floor=0.80, half=0.05)
    assert result.band == "borderline", result.rationale
    assert result.status == MappingStatus.NEEDS_REVIEW, result.rationale


def test_lowered_window_0_78_becomes_auto_mapped():
    """Drop the window to floor=0.75/half=0.05 → pass cut 0.80, borderline
    cut 0.70. Now 0.78 < 0.80 so it is NOT pass; it is borderline (>=0.70).
    With no specialist and 0.78 >= floor 0.75 → AUTO_MAPPED.
    """
    store = _FakeStore(0.78)
    result = map_vendor_product(_ref(), store=store, floor=0.75, half=0.05)
    assert result.status == MappingStatus.AUTO_MAPPED, result.rationale


def test_window_with_low_pass_cut_makes_0_78_a_clean_pass():
    """floor=0.70/half=0.05 → pass cut 0.75. 0.78 >= 0.75 → PASS band,
    AUTO_MAPPED, no specialist needed.
    """
    store = _FakeStore(0.78)
    result = map_vendor_product(_ref(), store=store, floor=0.70, half=0.05)
    assert result.band == "pass", result.rationale
    assert result.status == MappingStatus.AUTO_MAPPED, result.rationale


def test_raised_window_pushes_0_78_into_fail():
    """floor=0.90/half=0.05 → borderline cut 0.85. 0.78 < 0.85 → FAIL band,
    NEEDS_REVIEW, specialist not consulted.
    """
    store = _FakeStore(0.78)
    result = map_vendor_product(_ref(), store=store, floor=0.90, half=0.05)
    assert result.band == "fail", result.rationale
    assert result.status == MappingStatus.NEEDS_REVIEW, result.rationale


def test_none_window_matches_explicit_default_window():
    """Omitting floor/half must be byte-identical to passing the settings
    default window explicitly — defaults preserved exactly (HARD constraint).
    """
    store_a = _FakeStore(0.82)
    store_b = _FakeStore(0.82)
    default = map_vendor_product(_ref(), store=store_a)
    explicit = map_vendor_product(_ref(), store=store_b, floor=0.80, half=0.05)
    assert default.status == explicit.status
    assert default.band == explicit.band
    assert default.confidence == explicit.confidence


# ──────────────────────────────────────────────────────────────────────────
# Scripted-agent override (Phase 2b)
# ──────────────────────────────────────────────────────────────────────────
class _AgentFakeStore(_FakeStore):
    """_FakeStore + the extra read surface the scripted agent narrates over
    (get_ontology_neighbourhood). Same single fixed-similarity candidate.
    """

    def get_ontology_neighbourhood(self, iri, max_depth=2, max_nodes=20):
        from scudo_mapping_mcp.models import Subgraph

        return Subgraph(root_iri=iri, nodes=[self._node], edges=[])


def _final_status(events):
    """Pull the status off the scripted agent's final_result event."""
    for ev in events:
        if ev.type == "final_result":
            return ev.payload["mapping"]["status"]
    raise AssertionError("no final_result event emitted")


def test_scripted_agent_run_honours_overridden_window(monkeypatch):
    """The scripted agent threads confidence_floor/borderline_half_width into
    its authoritative map_vendor_product call. A fixed 0.78 score is
    NEEDS_REVIEW under the default window but AUTO_MAPPED once the window is
    lowered — proving the override reaches the matcher through the agent.
    """
    from scudo_mapping_mcp import agent as agent_mod
    from scudo_mapping_mcp import matching as matching_mod

    store = _AgentFakeStore(0.78)
    # The scripted agent calls get_store() for narration AND the matcher
    # resolves get_store() internally (store= left None on the agent path).
    monkeypatch.setattr(agent_mod, "get_store", lambda: store)
    monkeypatch.setattr(matching_mod, "get_store", lambda: store)

    a = agent_mod.ScriptedMappingAgent()

    default_events = list(a.run(_ref()))
    assert _final_status(default_events) == MappingStatus.NEEDS_REVIEW.value, (
        "0.78 should be needs_review under the default 0.80/0.05 window"
    )

    lowered_events = list(
        a.run(_ref(), confidence_floor=0.75, borderline_half_width=0.05)
    )
    assert _final_status(lowered_events) == MappingStatus.AUTO_MAPPED.value, (
        "0.78 should auto-map once the floor drops to 0.75"
    )


def test_bedrock_override_run_does_not_pollute_default_cache(monkeypatch):
    """Bedrock agent: an override run must use a LOCAL agent and NOT overwrite
    the cached ``self._agent`` (the default-threshold build), so an override
    window can never bleed into a later default run on the same instance.

    Hermetic — monkeypatches the lazy Strands build + streaming + matcher so no
    boto3/strands_agents dependency is exercised.
    """
    from scudo_mapping_mcp import agent as agent_mod

    a = agent_mod.BedrockMappingAgent()

    built: list[dict] = []

    def fake_build(*, floor=None, half=None):
        sentinel = object()
        built.append({"obj": sentinel, "floor": floor, "half": half})
        return sentinel

    monkeypatch.setattr(a, "_build_agent", fake_build)
    monkeypatch.setattr(
        agent_mod, "_iter_strands_events", lambda agent, prompt: iter(())
    )
    monkeypatch.setattr(agent_mod, "map_vendor_product", lambda ref, **kw: object())
    monkeypatch.setattr(agent_mod, "_mapping_result_dict", lambda result: {})

    ref = _ref()

    # Override run: builds with the window but must NOT cache into self._agent.
    list(a.run(ref, confidence_floor=0.70, borderline_half_width=0.05))
    assert a._agent is None, "override run polluted the default agent cache"
    assert built[-1]["floor"] == 0.70 and built[-1]["half"] == 0.05

    # Default run: builds + caches a default-threshold (None/None) agent.
    list(a.run(ref))
    assert a._agent is built[-1]["obj"]
    assert built[-1]["floor"] is None and built[-1]["half"] is None

    # A second override run must still leave the cached default agent untouched.
    cached = a._agent
    list(a.run(ref, confidence_floor=0.65, borderline_half_width=0.05))
    assert a._agent is cached, "second override run disturbed the default cache"
