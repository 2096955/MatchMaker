from __future__ import annotations

import os
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scudo import aurora_memory, local_state
from scudo.matching_self_improvement import (
    EVALUATION_PASS_CUT,
    EvaluationPolicy,
    GoldenCase,
    GoldenSet,
    LearningArtifact,
    MatchingPrediction,
    PromotionApproval,
    evaluate_golden_set,
    issue_evaluation_attestation,
    issue_live_pointer,
    learning_artifact_digest,
    promotion_receipt_for,
    trusted_evidence_for,
)


def test_jpmc_runtime_exposes_canonical_protected_evaluation_contract():
    golden = GoldenSet(
        version="jpmc-parity",
        cases=[
            GoldenCase(
                case_id="one",
                vendor="lseg",
                vendor_product_ref="LSEG-ONE",
                expected_target_iri="jpmorgan:data:cdao:EquityPrices",
                split="holdout",
            )
        ],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "mapped_node_iri": case.expected_target_iri,
            "confidence": 0.95,
            "status": "auto_mapped",
        },
        candidate_version="candidate-1",
        policy=EvaluationPolicy(max_brier_score=1.0),
        artifact_content="shared skill",
        repeat_runs=2,
    )

    assert report.metrics.correct_auto_pass_cases == 1
    assert report.metrics.auto_publish_precision == 1.0
    assert report.stable is True
    assert len(report.golden_set_hash) == 64
    assert len(report.policy_hash) == 64
    assert len(report.metric_definition_hash) == 64
    assert len(report.artifact_content_hash) == 64
    assert "content" in LearningArtifact.model_fields


def test_evaluation_pass_cut_matches_backend_runtime_config():
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scudo_mapping_mcp.config import PASS_CUT; print(PASS_CUT)",
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=True,
    )

    assert EVALUATION_PASS_CUT == float(result.stdout.strip()) == 0.80


def test_consult_best_skill_reads_persisted_canonical_content(monkeypatch):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    local_state.reset()
    golden = GoldenSet(
        version="persisted",
        cases=[
            GoldenCase(
                case_id="one",
                vendor="lseg",
                vendor_product_ref="LSEG-ONE",
                expected_target_iri="jpmorgan:data:cdao:EquityPrices",
            )
        ],
    )
    report = evaluate_golden_set(
        golden,
        lambda case: {
            "mapped_node_iri": case.expected_target_iri,
            "confidence": 0.95,
            "status": "auto_mapped",
        },
        candidate_version="candidate-3",
        policy=EvaluationPolicy(max_brier_score=1.0),
        artifact_content="canonical persisted skill",
        repeat_runs=2,
    )
    artifact = LearningArtifact(
        artifact_id="skill-3",
        artifact_kind="matching_skill",
        version=3,
        content="canonical persisted skill",
        evaluation=report,
        approval=PromotionApproval(
            approved_by="protected-gate",
            approval_ref="AUTO-3",
            rationale="Protected evaluation passed.",
        ),
    )
    evidence = trusted_evidence_for(
        golden,
        policy=report.policy,
        prediction_runs=(
            {
                "one": MatchingPrediction(
                    target_iri="jpmorgan:data:cdao:EquityPrices",
                    confidence=0.95,
                    status="auto_mapped",
                    auto_pass=True,
                )
            },
        )
        * 2,
    )
    receipt = promotion_receipt_for(
        artifact,
        trusted_evidence=evidence,
        evaluation_attestation=issue_evaluation_attestation(
            report,
            trusted_evidence=evidence,
            artifact_content=artifact.content,
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.version,
            artifact_kind=artifact.artifact_kind,
            evaluator_id="jpmc-test-evaluator",
            evaluator_version="1",
            signing_key="evaluation-key",
            promotion_key="test-promotion-key",
        ),
        signing_key="test-promotion-key",
        evaluation_signing_key="evaluation-key",
    )
    artifact_key = "skill:matching:artifact:3"
    local_state.MEMORY[artifact_key] = {
        "memory_type": "skill",
        "payload": {
            "artifact": artifact.model_dump(mode="json"),
            "status": "approved",
            "immutable": True,
            "protected_promotion_receipt": receipt.model_dump(mode="json"),
        },
        "updated_at_ms": 1,
    }
    pointer = issue_live_pointer(
        artifact_key=artifact_key,
        artifact_version=3,
        artifact_digest=learning_artifact_digest(artifact),
        predecessor_version=None,
        predecessor_digest=None,
        sequence=1,
        signing_key="test-promotion-key",
    )
    local_state.MEMORY["skill:matching:best"] = {
        "memory_type": "skill_pointer",
        "payload": {"pointer": pointer.model_dump(mode="json")},
        "updated_at_ms": 2,
    }
    local_state.MEMORY["skill:matching:promotion:1"] = {
        "memory_type": "skill_promotion_sequence",
        "payload": {
            "committed": True,
            "committed_pointer": pointer.model_dump(mode="json"),
        },
        "updated_at_ms": 2,
    }

    assert aurora_memory.consult_best_skill() == ("canonical persisted skill", 3)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "wrong-key")
    assert aurora_memory.consult_best_skill() == (None, None)
    monkeypatch.delenv("SCUDO_SKILL_PROMOTION_KEY")
    assert aurora_memory.consult_best_skill() == (None, None)


def test_consult_best_skill_rejects_old_pointer_replay(monkeypatch):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion-key")
    local_state.reset()
    old = issue_live_pointer(
        artifact_key="skill:matching:artifact:1",
        artifact_version=1,
        artifact_digest="a" * 64,
        predecessor_version=None,
        predecessor_digest=None,
        sequence=1,
        signing_key="promotion-key",
    )
    current = issue_live_pointer(
        artifact_key="skill:matching:artifact:2",
        artifact_version=2,
        artifact_digest="b" * 64,
        predecessor_version=1,
        predecessor_digest="a" * 64,
        sequence=2,
        signing_key="promotion-key",
    )
    local_state.MEMORY["skill:matching:best"] = {
        "memory_type": "skill_pointer",
        "payload": {"pointer": old.model_dump(mode="json")},
        "updated_at_ms": 3,
    }
    local_state.MEMORY["skill:matching:promotion:1"] = {
        "memory_type": "skill_promotion_sequence",
        "payload": {
            "committed": True,
            "committed_pointer": current.model_dump(mode="json"),
        },
        "updated_at_ms": 3,
    }

    assert aurora_memory.consult_best_skill() == (None, None)


def test_jpmc_local_protected_writer_is_atomic_and_monotonic(monkeypatch):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    local_state.reset()
    golden = GoldenSet(
        version="writer",
        cases=[
            GoldenCase(
                case_id="one",
                vendor="lseg",
                vendor_product_ref="ONE",
                expected_target_iri="target",
            )
        ],
    )
    prediction = MatchingPrediction(
        target_iri="target", confidence=0.95, status="auto_mapped", auto_pass=True
    )
    policy = EvaluationPolicy(max_brier_score=1.0)
    report = evaluate_golden_set(
        golden,
        lambda case: prediction,
        candidate_version="candidate-1",
        policy=policy,
        artifact_content="skill one",
        repeat_runs=2,
    )
    evidence = trusted_evidence_for(
        golden, policy=policy, prediction_runs=({"one": prediction},) * 2
    )
    attestation = issue_evaluation_attestation(
        report,
        trusted_evidence=evidence,
        artifact_content="skill one",
        artifact_id="matching-skill-1",
        artifact_version=1,
        artifact_kind="matching_skill",
        evaluator_id="jpmc-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )
    approval = PromotionApproval(
        approved_by="gate",
        approval_ref="AUTO-1",
        rationale="protected",
    )

    assert aurora_memory.promote_protected_skill(
        skill_text="skill one",
        version=1,
        evaluation=report,
        approval=approval,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        expected_sequence=0,
    )
    snapshot = dict(local_state.MEMORY)
    assert not aurora_memory.promote_protected_skill(
        skill_text="skill one",
        version=1,
        evaluation=report,
        approval=approval,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        expected_sequence=0,
    )
    assert local_state.MEMORY == snapshot
    assert not aurora_memory.promote_protected_skill(
        skill_text="skill one",
        version=1,
        evaluation=report,
        approval=approval,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        expected_sequence=1,
        inject_failure=True,
    )
    assert local_state.MEMORY == snapshot


def test_jpmc_local_rollback_restores_predecessor_without_mutating_artifacts(
    monkeypatch,
):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    local_state.reset()
    golden = GoldenSet(
        version="rollback",
        cases=[
            GoldenCase(
                case_id="one",
                vendor="lseg",
                vendor_product_ref="ONE",
                expected_target_iri="target",
            )
        ],
    )
    policy = EvaluationPolicy(max_brier_score=1.0)

    for version, content, confidence, expected_sequence in (
        (1, "skill one", 0.90, 0),
        (2, "skill two", 0.95, 1),
    ):
        prediction = MatchingPrediction(
            target_iri="target",
            confidence=confidence,
            status="auto_mapped",
            auto_pass=True,
        )
        evidence = trusted_evidence_for(
            golden, policy=policy, prediction_runs=({"one": prediction},) * 2
        )
        report = evaluate_golden_set(
            golden,
            lambda case: prediction,
            candidate_version=f"candidate-{version}",
            policy=policy,
            artifact_content=content,
            repeat_runs=2,
        )
        attestation = issue_evaluation_attestation(
            report,
            trusted_evidence=evidence,
            artifact_content=content,
            artifact_id=f"matching-skill-{version}",
            artifact_version=version,
            artifact_kind="matching_skill",
            evaluator_id="jpmc-evaluator",
            evaluator_version="1",
            signing_key="evaluation-key",
            promotion_key="promotion-key",
        )
        assert aurora_memory.promote_protected_skill(
            skill_text=content,
            version=version,
            evaluation=report,
            approval=PromotionApproval(
                approved_by="gate",
                approval_ref=f"AUTO-{version}",
                rationale="protected",
            ),
            trusted_evidence=evidence,
            evaluation_attestation=attestation,
            expected_sequence=expected_sequence,
        )

    artifacts_before = {
        key: value
        for key, value in local_state.MEMORY.items()
        if key.startswith("skill:matching:artifact:")
    }
    assert aurora_memory.consult_best_skill() == ("skill two", 2)
    assert aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-1",
        reason="regression detected",
        expected_sequence=2,
    )
    assert aurora_memory.consult_best_skill() == ("skill one", 1)
    assert {
        key: value
        for key, value in local_state.MEMORY.items()
        if key.startswith("skill:matching:artifact:")
    } == artifacts_before
    assert not aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-STALE",
        reason="stale request",
        expected_sequence=2,
    )


def test_local_state_atomic_update_swaps_once_and_rolls_back_on_error():
    local_state.reset()
    original = local_state.MEMORY

    def successful_update(memory):
        memory["one"] = {"payload": 1}
        return True

    assert local_state.atomic_memory_update(successful_update) is True
    assert local_state.MEMORY is not original
    committed = local_state.MEMORY

    def failing_update(memory):
        memory["two"] = {"payload": 2}
        raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        local_state.atomic_memory_update(failing_update)
    assert local_state.MEMORY is committed
    assert "two" not in local_state.MEMORY


def test_consult_best_skill_quarantines_legacy_skill_text(monkeypatch):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    local_state.reset()
    local_state.MEMORY["skill:matching:best"] = {
        "memory_type": "skill",
        "payload": {
            "artifact": {
                "version": 2,
                "skill_text": "legacy persisted skill",
                "evaluation": {"passed": True},
                "approval": {"approver": "legacy-reviewer"},
            },
            "status": "approved",
            "immutable": True,
        },
        "updated_at_ms": 1,
    }

    assert aurora_memory.consult_best_skill() == (None, None)


def test_consult_best_skill_quarantines_unattested_canonical_artifact(monkeypatch):
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    local_state.reset()
    local_state.MEMORY["skill:matching:best"] = {
        "memory_type": "skill",
        "payload": {
            "artifact": {
                "artifact_id": "skill-4",
                "artifact_kind": "matching_skill",
                "version": 4,
                "content": "unattested canonical skill",
                "evaluation": {
                    "candidate_version": "candidate-4",
                    "golden_set_version": "g",
                    "case_ids": ["one"],
                    "metrics": {
                        "total_cases": 1,
                        "expected_match_cases": 1,
                        "expected_abstain_cases": 0,
                        "predicted_abstain_cases": 0,
                        "correct_target_cases": 1,
                        "correct_abstention_cases": 0,
                        "false_auto_pass_cases": 0,
                        "exact_match_rate": 1.0,
                        "abstention_recall": 0.0,
                        "coverage": 1.0,
                        "false_auto_pass_rate": 0.0,
                        "calibration_mae": 0.0,
                        "brier_score": 0.0,
                    },
                    "policy": {},
                    "passed": True,
                },
                "approval": {
                    "approved_by": "reviewer",
                    "approval_ref": "A",
                    "rationale": "reviewed",
                },
            },
            "status": "approved",
            "immutable": True,
        },
        "updated_at_ms": 1,
    }

    assert aurora_memory.consult_best_skill() == (None, None)


def test_jpmc_contract_imports_from_isolated_package_copy(tmp_path):
    isolated = tmp_path / "jpmc-port"
    shutil.copytree(
        os.path.dirname(os.path.dirname(__file__)),
        isolated,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from scudo.matching_self_improvement import GoldenCase; "
                "print(GoldenCase.__name__)"
            ),
        ],
        cwd=isolated,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "GoldenCase"


def test_vendored_contract_is_byte_identical_to_canonical_source():
    root = Path(__file__).resolve().parents[2]
    canonical = root / "backend" / "scudo" / "matching_self_improvement.py"
    vendored = root / "jpmc-port" / "scudo" / "_matching_self_improvement_canonical.py"

    assert (
        hashlib.sha256(vendored.read_bytes()).hexdigest()
        == hashlib.sha256(canonical.read_bytes()).hexdigest()
    )
