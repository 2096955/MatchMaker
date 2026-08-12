"""The 10-dimension rubric is defined once and shared by both models.

WHAT THIS REPLACED
    ``VerifierDimension`` was ten bare enum names with no definition anywhere
    in the repo. So the VERIFIER invented what each meant on every call (and
    its ``total_score`` drives a hard publish/retry/HITL gate), while the
    SPECIALIST was never shown the dimensions at all — graded on a rubric it
    could not see.

    The tell was in ``verifier_prompt``: a hand-coded "include the ontology
    snapshot so taxonomy_freshness can be scored" line — one dimension patched
    by hand because the list was not shared.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scudo import prompts  # noqa: E402
from scudo.schemas import VerifierDimension  # noqa: E402


def test_every_dimension_has_a_definition():
    """The guard that makes this durable: a new dimension cannot ship
    undefined, because rubric_text() raises rather than quietly omitting it."""
    missing = [d.value for d in VerifierDimension if d not in prompts._RUBRIC]
    assert not missing, f"dimensions with no definition: {missing}"


def test_rubric_text_raises_on_an_undefined_dimension(monkeypatch):
    """Mutation check: remove a definition and the builder must refuse."""
    trimmed = {
        k: v
        for k, v in prompts._RUBRIC.items()
        if k is not VerifierDimension.SEMANTIC_FIT
    }
    monkeypatch.setattr(prompts, "_RUBRIC", trimmed)
    with pytest.raises(ValueError, match="semantic_fit"):
        prompts.rubric_text(audience="verifier")


@pytest.mark.parametrize("audience", ["specialist", "verifier"])
def test_both_audiences_get_all_ten_dimensions(audience):
    text = prompts.rubric_text(audience=audience)
    for dimension in VerifierDimension:
        assert dimension.value in text, f"{dimension.value} missing for {audience}"


def test_unknown_audience_is_rejected():
    with pytest.raises(ValueError):
        prompts.rubric_text(audience="nobody")


def test_the_two_audiences_share_the_definitions_verbatim():
    """Same standard, different framing. If the definition bodies ever diverge,
    the specialist is optimising for something the verifier is not scoring."""
    spec = prompts.rubric_text(audience="specialist")
    veri = prompts.rubric_text(audience="verifier")
    for body in prompts._RUBRIC.values():
        assert body in spec, f"specialist missing: {body[:40]}"
        assert body in veri, f"verifier missing: {body[:40]}"
    assert spec != veri, "the framing should differ even though the bodies match"


# ── the prompts actually carry it ──────────────────────────────────────────


def _bundle():
    from datetime import datetime, timezone

    from scudo.schemas import BriefBundle, CandidateNode, IntakeRequest, Route

    return BriefBundle(
        request=IntakeRequest(vendor="LSEG", vendor_product_ref="R1"),
        route=Route.NEW_MAPPING,
        vendor_product_iri="mds.lseg:573c1ba2-eefe-5eeb-9a51-3515b3df80f8",
        vendor_assertion={},
        candidates=[
            CandidateNode(
                iri="jpmorgan:data:cdao:EquityResearch",
                label="Equity Research",
                score=0.9,
            )
        ],
        precedent=None,
        conflicts=[],
        assembled_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        bundle_ref="b",
    )


def test_specialist_prompt_shows_the_rubric():
    """THE regression test: the specialist used to be graded blind."""
    text = prompts.mapping_prompt(_bundle())
    for dimension in VerifierDimension:
        assert dimension.value in text, f"specialist cannot see {dimension.value}"


def test_specialist_is_warned_off_gaming_the_scorer():
    """Naming the rubric invites writing-to-the-scorer. The instruction is
    explicit that the goal is the work, not the score."""
    text = prompts.mapping_prompt(_bundle())
    assert "do not write to the scorer" in text


def test_verifier_prompt_carries_the_definitions():
    from scudo.schemas import Band, MappingResult

    result = MappingResult(
        vendor_product_iri="x",
        proposed_target_iri="y",
        rationale="r",
        confidence=0.9,
        band=Band.HIGH,
        evidence=[],
        proposed_triples=[],
    )
    text = prompts.verifier_prompt(result, rubric_version="v1", ontology_snapshot="s")
    for dimension in VerifierDimension:
        assert dimension.value in text
    assert prompts._RUBRIC[VerifierDimension.SEMANTIC_FIT] in text, (
        "verifier got dimension NAMES but not their definitions — that is the "
        "state where two models score the same word differently"
    )
