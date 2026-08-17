"""Every user teaching event must distill into agent memory (fail-loud)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SCUDO_LOCAL"] = "1"


def setup_function():
    from scudo import local_state

    local_state.reset()


def test_approve_teaches_exact_precedent_and_quarantines_rule_candidate():
    from scudo import aurora_memory, local_state

    receipt = aurora_memory.learn_from_teaching(
        ticket="HITL-1",
        decision="approve",
        vendor="lseg",
        vendor_product_ref="LSEG-IBES-EST-001",
        source_iri="mds.lseg:00000000-0000-4000-8000-000000000001",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        lesson="IBES estimates map to Equity Research, not Marketing.",
        confidence=0.95,
    )
    assert receipt["learned"] is True
    assert receipt["precedent_written"] is True
    assert receipt["rule_candidate_written"] is True
    assert receipt["rule_written"] is False

    priors = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001"
    )
    assert priors.precedent is not None
    assert priors.precedent["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert priors.rules == []
    unrelated = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-UNRELATED"
    )
    assert unrelated.precedent is None
    assert unrelated.rules == []
    assert any(
        row.get("memory_type") == "rule_candidate"
        and "IBES" in (row.get("payload", {}).get("text") or "")
        for row in local_state.MEMORY.values()
    )
    assert any(
        row.get("memory_type") == "teaching" for row in local_state.MEMORY.values()
    )


def test_reject_retains_candidate_lesson_without_positive_precedent():
    from scudo import aurora_memory, local_state

    receipt = aurora_memory.learn_from_teaching(
        ticket="HITL-2",
        decision="reject",
        vendor="lseg",
        vendor_product_ref="LSEG-BAD-001",
        source_iri="mds.lseg:00000000-0000-4000-8000-000000000099",
        target_iri="jpmorgan:data:cdao:Marketing",
        lesson="Never force Marketing domain for LSEG financial products.",
    )
    assert receipt["learned"] is True
    assert receipt["precedent_written"] is False
    assert receipt["rule_candidate_written"] is True
    assert receipt["rule_written"] is False

    priors = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-BAD-001"
    )
    assert priors.precedent is None
    assert priors.rules == []
    candidates = [
        row["payload"]
        for row in local_state.MEMORY.values()
        if row.get("memory_type") == "rule_candidate"
    ]
    assert any(r.get("polarity") == "avoid" for r in candidates)
    assert any("Marketing" in (r.get("text") or "") for r in candidates)


def test_correct_overwrites_precedent_with_user_target():
    from scudo import aurora_memory

    aurora_memory.record_verified_precedent(
        vendor="ice",
        vendor_product_ref="ICE-1",
        source_iri="mds.ice:a",
        target_iri="jpmorgan:data:cdao:Wrong",
        confidence=0.9,
    )
    receipt = aurora_memory.learn_from_teaching(
        ticket="HITL-3",
        decision="correct",
        vendor="ice",
        vendor_product_ref="ICE-1",
        source_iri="mds.ice:a",
        target_iri="jpmorgan:data:cdao:EquityPrices",
        lesson="Correct target is EquityPrices.",
        confidence=1.0,
    )
    assert receipt["precedent_written"] is True
    priors = aurora_memory.consult_priors(vendor="ice", vendor_product_ref="ICE-1")
    assert priors.precedent["target_iri"] == "jpmorgan:data:cdao:EquityPrices"


def test_teaching_notes_are_audit_only_not_live_prompt_context():
    from scudo import aurora_memory

    aurora_memory.learn_from_teaching(
        ticket="HITL-4",
        decision="approve",
        vendor="sp",
        vendor_product_ref="SP-1",
        source_iri="mds.sp:1",
        target_iri="jpmorgan:data:cdao:CreditRatings",
        lesson="S&P ratings products → CreditRatings.",
    )
    notes = aurora_memory.consult_teaching_notes(limit=10)
    assert notes == ""


def test_decision_endpoint_always_learns():
    from scudo import aurora_memory, local_state
    from scudo.handler import handle

    local_state.reset()
    resp = handle(
        {
            "path": "/decision",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "ticket": "HITL-99",
                "decision": "approve",
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-TEACH-1",
                "source_iri": "mds.lseg:00000000-0000-4000-8000-000000000010",
                "target_iri": "jpmorgan:data:cdao:EquityResearch",
                "lesson": "User taught: this is EquityResearch.",
                "mapping_object": {
                    "mapping_result": {
                        "vendor_product_iri": "mds.lseg:00000000-0000-4000-8000-000000000010",
                        "proposed_target_iri": "jpmorgan:data:cdao:EquityResearch",
                        "confidence": 0.88,
                        "rationale": "hitl",
                    }
                },
            },
        }
    )
    assert resp["statusCode"] == 200
    assert resp["body"]["learned"] is True
    assert resp["body"].get("trajectory_written") is True or any(
        row.get("memory_type") == "trajectory" for row in local_state.MEMORY.values()
    )
    priors = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-TEACH-1"
    )
    assert priors.precedent is not None


def test_sparse_decision_payload_still_writes_trajectory():
    """Raw /decision payloads often omit band — must still FAIL-LOUD write trajectory."""
    from scudo import aurora_memory, local_state

    local_state.reset()
    receipt = aurora_memory.learn_from_teaching(
        ticket="HITL-SPARSE",
        decision="approve",
        vendor="lseg",
        vendor_product_ref="LSEG-SPARSE-1",
        source_iri="mds.lseg:sparse",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        lesson="sparse payload",
        mapping_object={
            "mapping_result": {
                "vendor_product_iri": "mds.lseg:sparse",
                "proposed_target_iri": "jpmorgan:data:cdao:EquityResearch",
                "confidence": 0.88,
                # deliberately no band / no rationale
            }
        },
    )
    assert receipt["trajectory_written"] is True
    assert any(
        row.get("memory_type") == "trajectory" for row in local_state.MEMORY.values()
    )


def test_vendor_case_normalized_across_teach_and_consult():
    from scudo import aurora_memory, local_state

    local_state.reset()
    aurora_memory.learn_from_teaching(
        ticket="HITL-CASE",
        decision="approve",
        vendor="LSEG",
        vendor_product_ref="CASE-1",
        source_iri="mds.lseg:1",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        lesson="taught under LSEG",
    )
    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="CASE-1")
    assert priors.precedent is not None
    assert priors.precedent["target_iri"] == "jpmorgan:data:cdao:EquityResearch"


def test_learn_from_teaching_propagates_store_failure(monkeypatch):
    """Store write errors must raise — not be swallowed (fail-loud)."""
    from scudo import aurora_memory, local_state

    local_state.reset()

    def boom(*_args, **_kwargs):
        raise RuntimeError("injected store failure")

    monkeypatch.setattr(aurora_memory, "_write_memory", boom)
    try:
        aurora_memory.learn_from_teaching(
            ticket="HITL-FAIL",
            decision="approve",
            vendor="lseg",
            vendor_product_ref="FAIL-1",
            source_iri="mds.lseg:fail",
            target_iri="jpmorgan:data:cdao:EquityResearch",
            lesson="must raise",
        )
        raise AssertionError("expected RuntimeError from store failure")
    except RuntimeError as exc:
        assert "injected store failure" in str(exc)
    assert not any(
        row.get("memory_type") == "teaching" for row in local_state.MEMORY.values()
    )


def test_bundle_assembler_does_not_inject_unpromoted_teaching_notes():
    from scudo import aurora_memory, local_state
    from scudo.handler import _build_bundle_assembler
    from scudo.schemas import IntakeRequest, Route

    local_state.reset()
    aurora_memory.learn_from_teaching(
        ticket="HITL-5",
        decision="approve",
        vendor="lseg",
        vendor_product_ref="LSEG-HINT-1",
        source_iri="mds.lseg:1",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        lesson="Always prefer EquityResearch for IBES-style estimates.",
    )
    assemble = _build_bundle_assembler()
    bundle = assemble(
        IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-HINT-1"),
        Route.NEW_MAPPING,
    )
    assert "USER TEACHINGS" not in (bundle.skill_hint or "")
    assert "Always prefer EquityResearch" not in (bundle.skill_hint or "")
