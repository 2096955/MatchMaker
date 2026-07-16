"""Top1-vs-top2 margin gate — flag-gated ambiguity demotion at Rung 5.

Behind its OWN opt-in flag ``SCUDO_MARGIN_GATE`` (default off, call-time
read — measured-rollout discipline, same as SCUDO_ASSET_CLASS_VALIDATION).
Threshold comes from ``SCUDO_MARGIN_MIN`` (default 0.02, clamped).

The invariant: an unassisted AUTO_MAPPED verdict must LEAD the candidate
field, not merely clear the floor. Candidates come back from the store in
RRF fusion order, NOT similarity order, so candidates[1].similarity can
EXCEED candidates[0].similarity — a NEGATIVE margin is the strongest
possible near-tie signal and must also trip the gate. The demotion flips
status to NEEDS_REVIEW only; band / confidence / mapped_node_* stay
truthful (the issue is ambiguity, not score) and the rationale is appended
with a "MARGIN GATE:" sentence naming both contenders.
"""

from __future__ import annotations

import pytest

from scudo_mapping_mcp.matching import map_vendor_product
from scudo_mapping_mcp.models import (
    Candidate,
    MappingStatus,
    TaxonomyNode,
    VendorProductRef,
)

FLAG = "SCUDO_MARGIN_GATE"
MIN_ENV = "SCUDO_MARGIN_MIN"

TOP1_IRI = "jpmorgan:data:cdao:EquityPrices"
TOP2_IRI = "jpmorgan:data:cdao:FixedIncomePrices"


class _StubStore:
    """Minimal store surface the auto-map path touches — lets a test pin
    EXACT similarities AND exact candidate ORDER (the FakeStore sorts by
    score, which would hide the RRF-order-vs-similarity-order inversion the
    negative-margin test exists to exercise)."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates
        self._nodes = {c.node.iri: c.node for c in candidates}

    def get_precedent_mapping(self, vendor, product_id):
        return None

    def find_similar_products(self, ref, max_results=10, **_kwargs):
        return self._candidates[:max_results]

    def get_taxonomy_node(self, node_iri):
        return self._nodes.get(node_iri)


def _candidates(*sims: float) -> list[Candidate]:
    """Build candidates IN THE GIVEN ORDER (RRF order), first two on the
    two named IRIs so rationale assertions can reference the labels."""
    iris_labels = [
        (TOP1_IRI, "Equity Prices"),
        (TOP2_IRI, "Fixed Income Prices"),
        ("jpmorgan:data:cdao:Extra3", "Extra Three"),
        ("jpmorgan:data:cdao:Extra4", "Extra Four"),
    ]
    return [
        Candidate(node=TaxonomyNode(iri=iri, label=label), similarity=sim)
        for (iri, label), sim in zip(iris_labels, sims)
    ]


def _ref() -> VendorProductRef:
    return VendorProductRef(vendor="LSEG", product_id="EQ-1", name="Prices")


def _map(sims: tuple[float, ...], **kwargs):
    return map_vendor_product(_ref(), store=_StubStore(_candidates(*sims)), **kwargs)


# ── flag OFF (default): byte-identical behaviour ───────────────────────────


def test_flag_off_near_tie_still_auto_maps(monkeypatch):
    """Measured-rollout pin: with SCUDO_MARGIN_GATE unset, a near-tie
    (0.85 vs 0.849) auto-maps exactly as before — no demotion, no rationale
    change."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.85, 0.849))
    assert result.status == MappingStatus.AUTO_MAPPED
    assert result.band == "pass"
    assert "MARGIN GATE" not in result.rationale


# ── flag ON: near-tie demotion ─────────────────────────────────────────────


def test_near_tie_in_pass_band_demoted_to_review(monkeypatch):
    """0.85 vs 0.849 with the gate on: status flips to NEEDS_REVIEW but
    band / confidence / mapped node stay truthful, and the rationale names
    both contenders plus the margin."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.85, 0.849))
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert result.band == "pass"  # band stays truthful — demotion is about ambiguity
    assert result.confidence == 0.85
    assert result.mapped_node_iri == TOP1_IRI
    assert "MARGIN GATE" in result.rationale
    assert "Equity Prices" in result.rationale
    assert "Fixed Income Prices" in result.rationale
    assert "0.001" in result.rationale  # the margin, at 3 dp


def test_clear_winner_not_demoted(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.85, 0.60))
    assert result.status == MappingStatus.AUTO_MAPPED
    assert "MARGIN GATE" not in result.rationale


def test_negative_margin_trips_gate(monkeypatch):
    """RRF order ≠ similarity order: candidates[1] can OUTSCORE
    candidates[0]. A negative margin is the strongest near-tie signal and
    must trip the gate too."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.82, 0.86))
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert "MARGIN GATE" in result.rationale
    assert "-0.040" in result.rationale  # negative margin reads sensibly


def test_challenger_is_strongest_tail_similarity_not_positional_top2(monkeypatch):
    """RRF order can bury the real challenger deeper than candidates[1]:
    (0.85, 0.60, 0.849) — positional top-2 is a clear 0.25 winner, but the
    THIRD candidate sits 0.001 behind top-1. The gate must compare against
    the strongest remaining similarity, wherever it sits."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.85, 0.60, 0.849))
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert "MARGIN GATE" in result.rationale
    assert "Extra Three" in result.rationale  # the buried challenger, by label
    assert "0.001" in result.rationale


def test_single_candidate_never_trips(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.85,))
    assert result.status == MappingStatus.AUTO_MAPPED
    assert "MARGIN GATE" not in result.rationale


def test_borderline_band_auto_map_path_demoted(monkeypatch):
    """The gate also guards the borderline-band deterministic auto-map path
    (no specialist, similarity ≥ floor): 0.78 ≥ floor 0.75 auto-maps, but
    a second candidate at 0.77 is a near-tie → demoted."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.78, 0.77), floor=0.75)
    assert result.band == "borderline"
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert "MARGIN GATE" in result.rationale


# ── SCUDO_MARGIN_MIN honoured ──────────────────────────────────────────────


def test_margin_min_env_tightens_gate(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.setenv(MIN_ENV, "0.05")
    result = _map((0.85, 0.82))  # margin 0.03 < 0.05 → trips
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert "MARGIN GATE" in result.rationale


def test_margin_min_env_loosens_gate(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.setenv(MIN_ENV, "0.01")
    result = _map((0.85, 0.82))  # margin 0.03 ≥ 0.01 → no trip
    assert result.status == MappingStatus.AUTO_MAPPED
    assert "MARGIN GATE" not in result.rationale


@pytest.mark.parametrize("bad", ["garbage", "-0.5", "1.5", "nan", ""])
def test_margin_min_malformed_falls_back_to_default(monkeypatch, bad):
    """A config typo must not take the matcher down: malformed / out-of-band
    SCUDO_MARGIN_MIN falls back to MARGIN_MIN_DEFAULT (0.02) — never raises."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.setenv(MIN_ENV, bad)
    # margin 0.001 < default 0.02 → trips under the fallback
    result = _map((0.85, 0.849))
    assert result.status == MappingStatus.NEEDS_REVIEW
    # margin 0.03 ≥ default 0.02 → no trip under the fallback
    result = _map((0.85, 0.82))
    assert result.status == MappingStatus.AUTO_MAPPED


# ── gate guards AUTO_MAPPED only ───────────────────────────────────────────


def test_already_needs_review_untouched(monkeypatch):
    """Fail band is already NEEDS_REVIEW — the gate must not stack a
    MARGIN GATE sentence onto a non-auto-map verdict."""
    monkeypatch.setenv(FLAG, "on")
    monkeypatch.delenv(MIN_ENV, raising=False)
    result = _map((0.60, 0.59))
    assert result.band == "fail"
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert "MARGIN GATE" not in result.rationale
