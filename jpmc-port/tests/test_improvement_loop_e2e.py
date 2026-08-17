from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scudo import aurora_memory, local_state
from scudo.promotion_monitor import monitor_promotion_window
from scudo.matching_self_improvement import (
    EvaluationPolicy,
    GoldenCase,
    GoldenSet,
    MatchingPrediction,
    PromotionApproval,
    PromotionRejected,
    issue_signed_evaluation_envelope,
    issue_signed_monitoring_envelope,
    monitoring_source_record_digest,
    trusted_evidence_for,
    verify_signed_evaluation_envelope,
)


def _write_command(path: Path, source: str) -> list[str]:
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


def _run_json(command: list[str], request: dict) -> dict:
    result = subprocess.run(
        command,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_shared_signed_local_promotion_and_rollback_lifecycle(
    monkeypatch,
    tmp_path,
):
    optimizer = _write_command(
        tmp_path / "optimizer.py",
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'candidate_content':request['seed']+' optimized'}))\n",
    )
    predictor = _write_command(
        tmp_path / "predictor.py",
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "content=request['candidate_content']\n"
        "harmful='harmful' in content\n"
        "version=next((n for n in (4,3,2,1) if f'v{n}' in content),1)\n"
        "prediction=({'target_iri':'wrong','confidence':0.99,"
        "'status':'auto_mapped','auto_pass':True}"
        " if harmful else {'target_iri':'target','confidence':0.75+version*0.05,"
        "'status':'auto_mapped','auto_pass':True})\n"
        "print(json.dumps({'predictions':[{'case_id':case['case_id'],"
        "'prediction':prediction} for case in request['cases']]}))\n",
    )
    golden = GoldenSet(
        version="jpmc-e2e",
        cases=[
            GoldenCase(
                case_id="one",
                vendor="lseg",
                vendor_product_ref="ONE",
                product_name="Prices",
                expected_target_iri="target",
                split="holdout",
            ),
            GoldenCase(
                case_id="adversarial",
                vendor="ice",
                vendor_product_ref="ADV",
                product_name="Rates",
                expected_target_iri="target",
                split="adversarial",
            ),
        ],
    )
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    monkeypatch.setattr(local_state, "is_local", lambda: True)
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("SCUDO_MONITORING_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("SCUDO_MONITORING_AUDIENCE", "scudo-monitor")
    monkeypatch.setenv("SCUDO_MONITORING_DEPLOYMENT_ID", "jpmc-local")
    monkeypatch.setenv("SCUDO_MONITORING_KEY_ID", "monitor-key-1")
    local_state.reset()

    policy = EvaluationPolicy(max_brier_score=1.0)

    def envelope_for(candidate: str, version: int):
        output = _run_json(
            predictor,
            {
                "candidate_content": candidate,
                "cases": [{"case_id": "one"}, {"case_id": "adversarial"}],
            },
        )
        predictions = {
            row["case_id"]: MatchingPrediction.model_validate(row["prediction"])
            for row in output["predictions"]
        }
        holdout = trusted_evidence_for(
            golden,
            policy=policy,
            split="holdout",
            prediction_runs=({"one": predictions["one"]},) * 2,
        )
        adversarial = trusted_evidence_for(
            golden,
            policy=policy,
            split="adversarial",
            prediction_runs=({"adversarial": predictions["adversarial"]},) * 2,
        )
        return issue_signed_evaluation_envelope(
            candidate_content=candidate,
            artifact_id=f"matching-skill-{version}",
            artifact_version=version,
            artifact_kind="matching_skill",
            trusted_evidence=holdout,
            adversarial_evidence=adversarial,
            candidate_version=f"candidate-{version}",
            baseline_version=None,
            evaluator_id="jpmc-shared-contract-evaluator",
            evaluator_version="1",
            private_key_pem=private_pem,
        )

    def promote(candidate: str, version: int, envelope, expected_sequence: int):
        assert aurora_memory.promote_protected_skill(
            skill_text=candidate,
            version=version,
            evaluation=envelope.report,
            approval=PromotionApproval(
                approved_by="gate",
                approval_ref=f"AUTO-{version}",
                rationale="shared signed local lifecycle",
            ),
            trusted_evidence=envelope.evidence,
            evaluation_attestation=None,
            signed_evaluation_envelope=envelope,
            evaluation_public_key_pem=public_pem,
            signing_key="promotion-key",
            expected_sequence=expected_sequence,
        )

    candidates = {}
    for version in (1, 2, 3):
        candidate = _run_json(optimizer, {"seed": f"skill v{version}"})[
            "candidate_content"
        ]
        candidates[version] = candidate
        envelope = envelope_for(candidate, version)
        assert verify_signed_evaluation_envelope(envelope, public_key_pem=public_pem)
        promote(candidate, version, envelope, version - 1)

    snapshot = local_state.memory_snapshot()
    harmful = _run_json(optimizer, {"seed": "harmful skill v4"})["candidate_content"]
    with pytest.raises(PromotionRejected, match="adversarial"):
        envelope_for(harmful, 4)
    assert local_state.memory_snapshot() == snapshot
    assert aurora_memory.consult_best_skill() == (candidates[3], 3)

    good_v4 = _run_json(optimizer, {"seed": "good skill v4"})["candidate_content"]
    good_envelope = envelope_for(good_v4, 4)
    assert verify_signed_evaluation_envelope(
        good_envelope,
        public_key_pem=public_pem,
    )
    promote(good_v4, 4, good_envelope, 3)
    assert aurora_memory.consult_best_skill() == (good_v4, 4)
    safe_sample = MatchingPrediction(
        target_iri="target",
        confidence=0.95,
        status="auto_mapped",
        auto_pass=True,
    )
    live_pointer, _ = aurora_memory._resolve_verified_artifact_from_memory(
        local_state.MEMORY,
        signing_key="promotion-key",
    )
    monitoring_now = datetime.now(timezone.utc)

    def monitoring_envelope(window_id: str, samples: list[dict]):
        records = []
        for sample in samples:
            record = {
                "source_event_id": sample["sample_id"],
                "observed_at": monitoring_now - timedelta(minutes=30),
                "artifact_key": live_pointer.artifact_key,
                "artifact_version": live_pointer.artifact_version,
                "artifact_digest": live_pointer.artifact_digest,
                "pointer_sequence": live_pointer.sequence,
                "prediction": sample["prediction"],
                "authoritative_outcome": {
                    "target_iri": sample.get("authoritative_target_iri"),
                    "abstain": sample.get("authoritative_abstain", False),
                },
            }
            local_state.MEMORY[f"monitoring-source:{sample['sample_id']}"] = {
                "memory_type": "monitoring_source",
                "payload": {"immutable": True, "record": record},
            }
            records.append(
                {
                    **record,
                    "source_record_digest": monitoring_source_record_digest(record),
                }
            )
        return issue_signed_monitoring_envelope(
            window_id=window_id,
            artifact_key=live_pointer.artifact_key,
            artifact_version=live_pointer.artifact_version,
            artifact_digest=live_pointer.artifact_digest,
            pointer_sequence=live_pointer.sequence,
            observations=records,
            private_key_pem=private_pem,
            audience="scudo-monitor",
            deployment_id="jpmc-local",
            key_id="monitor-key-1",
            issued_at=monitoring_now,
            not_before=monitoring_now - timedelta(minutes=1),
            expires_at=monitoring_now + timedelta(minutes=5),
            observation_start=monitoring_now - timedelta(hours=1),
            observation_end=monitoring_now,
        )

    insufficient_samples = [
        {
            "sample_id": f"insufficient-{index}",
            "prediction": safe_sample,
            "authoritative_target_iri": "target",
        }
        for index in range(19)
    ]
    insufficient = monitor_promotion_window(
        envelope=monitoring_envelope("v4-insufficient", insufficient_samples),
        signing_key="promotion-key",
    )
    assert insufficient.action == "insufficient_samples"
    assert insufficient.persisted is False
    assert "monitor:v4-insufficient" not in local_state.MEMORY
    assert not any(key.startswith("monitor-observation:") for key in local_state.MEMORY)
    assert aurora_memory.consult_best_skill() == (good_v4, 4)
    safe_samples = [
        {
            "sample_id": f"safe-{index}",
            "prediction": safe_sample,
            "authoritative_target_iri": "target",
        }
        for index in range(20)
    ]
    safe = monitor_promotion_window(
        envelope=monitoring_envelope("v4-safe", safe_samples),
        signing_key="promotion-key",
    )
    assert safe.action == "retain"
    assert aurora_memory.consult_best_skill() == (good_v4, 4)
    unsafe_samples = [
        {
            "sample_id": f"unsafe-{index}",
            "prediction": safe_sample,
            "authoritative_target_iri": "wrong" if index == 19 else "target",
        }
        for index in range(20)
    ]
    rolled_back = monitor_promotion_window(
        envelope=monitoring_envelope("v4-false-auto-pass", unsafe_samples),
        signing_key="promotion-key",
    )
    assert rolled_back.action == "rollback"
    assert rolled_back.rollback_succeeded is True
    assert aurora_memory.consult_best_skill() == (candidates[3], 3)
    snapshot_after_monitor = deepcopy(local_state.MEMORY)
    assert (
        monitor_promotion_window(
            envelope=monitoring_envelope("v4-false-auto-pass", unsafe_samples),
            signing_key="promotion-key",
        )
        == rolled_back
    )
    assert local_state.MEMORY == snapshot_after_monitor
    assert (
        local_state.MEMORY["skill:matching:promotion:5"]["payload"]["rolled_back_from"][
            "artifact_version"
        ]
        == 4
    )
    assert aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-3",
        reason="candidate three also regressed",
        expected_sequence=5,
        signing_key="promotion-key",
    )
    assert aurora_memory.consult_best_skill() == (candidates[2], 2)
    assert aurora_memory.consult_best_skill()[1] != 4

    local_state.MEMORY["skill:matching:promotion:1"]["payload"]["committed_pointer"][
        "predecessor_version"
    ] = 99
    tampered_snapshot = deepcopy(local_state.MEMORY)
    assert not aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-TAMPERED",
        reason="must reject unsigned ancestry changes",
        expected_sequence=6,
        signing_key="promotion-key",
    )
    assert local_state.MEMORY == tampered_snapshot
    assert aurora_memory.consult_best_skill() == (candidates[2], 2)
