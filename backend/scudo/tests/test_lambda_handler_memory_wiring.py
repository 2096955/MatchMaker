"""lambda_handler.py wiring for real CONSULT/DISTILL (replaces fabrication).

Prior behaviour (verified this session): _build_bundle_assembler's _assemble()
fabricated a canned PrecedentMapping (hardcoded "prior accepted mapping",
confidence 0.88) whenever request.has_precedent was True — never reading any
real store. These tests pin the real replacement: _assemble() now calls
aurora_memory.consult_priors() and reflects REALITY, not a flag.

Note: `has_precedent` still independently drives Route selection in
orchestrator.py (EXTEND_MAPPING vs NEW_MAPPING) — that is unchanged and
untested here; these tests cover only the `precedent` payload content.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_assemble_uses_real_aurora_precedent_when_one_exists(monkeypatch):
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import IntakeRequest, Route

    real = aurora_memory.Priors(
        precedent={
            "target_iri": "jpmorgan:data:cdao:EquityResearch",
            "confidence": 0.91,
            "rationale": "verified auto-pass, prior run",
        },
        rules=[],
    )
    monkeypatch.setattr(aurora_memory, "consult_priors", lambda **kw: real)

    assemble = lambda_handler._build_bundle_assembler(
        {"vendor": "lseg", "vendor_product_ref": "LSEG-EQ-1"}
    )
    request = IntakeRequest(
        vendor="lseg", vendor_product_ref="LSEG-EQ-1", has_precedent=True
    )
    bundle = assemble(request, Route.EXTEND_MAPPING)

    assert bundle.precedent is not None
    assert bundle.precedent.target_iri == "jpmorgan:data:cdao:EquityResearch"
    assert bundle.precedent.confidence == 0.91
    # the OLD fabricated values must be gone
    assert bundle.precedent.rationale != "prior accepted mapping"
    assert bundle.precedent.confidence != 0.88


def test_assemble_has_no_precedent_when_aurora_has_none_even_if_flag_true(monkeypatch):
    """The has_precedent FLAG still drives routing (untouched, tested
    elsewhere) but the PAYLOAD must reflect reality — no more fabrication
    just because the caller asserted has_precedent=True."""
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import IntakeRequest, Route

    monkeypatch.setattr(
        aurora_memory, "consult_priors", lambda **kw: aurora_memory.Priors()
    )

    assemble = lambda_handler._build_bundle_assembler(
        {"vendor": "lseg", "vendor_product_ref": "LSEG-EQ-1"}
    )
    request = IntakeRequest(
        vendor="lseg", vendor_product_ref="LSEG-EQ-1", has_precedent=True
    )
    bundle = assemble(request, Route.EXTEND_MAPPING)

    assert bundle.precedent is None


def test_assemble_populates_skill_hint_from_aurora_when_promoted(monkeypatch):
    """Part D — _assemble() must populate BriefBundle.skill_hint from
    aurora_memory.consult_best_skill(), the same CONSULT pattern already used
    for precedent."""
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import IntakeRequest, Route

    monkeypatch.setattr(
        aurora_memory, "consult_priors", lambda **kw: aurora_memory.Priors()
    )
    monkeypatch.setattr(
        aurora_memory,
        "consult_best_skill",
        lambda: {"skill_text": "Prefer exact vendor-code matches.", "version": 2},
    )

    assemble = lambda_handler._build_bundle_assembler(
        {"vendor": "lseg", "vendor_product_ref": "LSEG-EQ-1"}
    )
    request = IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-EQ-1")
    bundle = assemble(request, Route.NEW_MAPPING)

    assert bundle.skill_hint == "Prefer exact vendor-code matches."


def test_assemble_skill_hint_is_none_when_nothing_promoted_yet(monkeypatch):
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import IntakeRequest, Route

    monkeypatch.setattr(
        aurora_memory, "consult_priors", lambda **kw: aurora_memory.Priors()
    )
    monkeypatch.setattr(aurora_memory, "consult_best_skill", lambda: None)

    assemble = lambda_handler._build_bundle_assembler(
        {"vendor": "lseg", "vendor_product_ref": "LSEG-EQ-1"}
    )
    request = IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-EQ-1")
    bundle = assemble(request, Route.NEW_MAPPING)

    assert bundle.skill_hint is None


def test_record_precedent_if_published_calls_aurora_on_published_outcome(monkeypatch):
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import MappingObject, MappingResult, Outcome, Route, Band

    calls = []
    monkeypatch.setattr(
        aurora_memory,
        "record_verified_precedent",
        lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(aurora_memory, "record_trajectory", lambda **kw: None)

    result = MappingResult(
        vendor_product_iri="mds.lseg:x",
        proposed_target_iri="jpmorgan:data:cdao:EquityResearch",
        rationale="verifier passed",
        confidence=0.9,
        band=Band.HIGH,
    )
    obj = MappingObject(
        route=Route.NEW_MAPPING,
        bundle_ref="lambda-abc123",
        mapping_result=result,
        outcome=Outcome.PUBLISHED,
        outcome_reason="verifier >= 16, confidence >= 0.80",
    )

    lambda_handler._record_precedent_if_published(
        obj, vendor="lseg", vendor_product_ref="LSEG-EQ-1"
    )

    assert len(calls) == 1
    assert calls[0]["vendor"] == "lseg"
    assert calls[0]["vendor_product_ref"] == "LSEG-EQ-1"
    assert calls[0]["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert calls[0]["confidence"] == 0.9
    assert calls[0]["source_outcome_ref"] == "lambda-abc123"


def test_record_precedent_if_published_skips_non_published_outcomes(monkeypatch):
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import MappingObject, Outcome, Route

    calls = []
    monkeypatch.setattr(
        aurora_memory,
        "record_verified_precedent",
        lambda **kw: calls.append(kw),
    )

    obj = MappingObject(
        route=Route.NEW_MAPPING,
        bundle_ref="lambda-abc123",
        mapping_result=None,
        outcome=Outcome.HITL,
        outcome_reason="confidence below floor",
        hitl_ticket="HITL-00001",
    )

    lambda_handler._record_precedent_if_published(
        obj, vendor="lseg", vendor_product_ref="LSEG-EQ-1"
    )

    assert calls == []


def test_record_precedent_if_published_also_records_trajectory(monkeypatch):
    """Part D — a verified auto-pass also records SkillOpt-style rollout
    evidence for the offline harvest step, alongside (not instead of) the
    existing precedent write."""
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import MappingObject, MappingResult, Outcome, Route, Band

    monkeypatch.setattr(aurora_memory, "record_verified_precedent", lambda **kw: None)
    trajectory_calls = []
    monkeypatch.setattr(
        aurora_memory,
        "record_trajectory",
        lambda **kw: trajectory_calls.append(kw),
    )

    result = MappingResult(
        vendor_product_iri="mds.lseg:x",
        proposed_target_iri="jpmorgan:data:cdao:EquityResearch",
        rationale="verifier passed",
        confidence=0.9,
        band=Band.HIGH,
    )
    obj = MappingObject(
        route=Route.NEW_MAPPING,
        bundle_ref="lambda-abc123",
        mapping_result=result,
        outcome=Outcome.PUBLISHED,
        outcome_reason="verifier >= 16, confidence >= 0.80",
    )

    lambda_handler._record_precedent_if_published(
        obj, vendor="lseg", vendor_product_ref="LSEG-EQ-1"
    )

    assert len(trajectory_calls) == 1
    assert trajectory_calls[0]["bundle_ref"] == "lambda-abc123"
    assert trajectory_calls[0]["vendor"] == "lseg"
    assert trajectory_calls[0]["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert trajectory_calls[0]["confidence"] == 0.9
