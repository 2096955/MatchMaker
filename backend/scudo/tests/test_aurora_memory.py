"""aurora_memory: real CONSULT/DISTILL for the Orchestrator pipeline.

Prior behaviour (verified this session): the Orchestrator's "precedent" was
entirely FABRICATED — ``_build_bundle_assembler`` in lambda_handler.py only
invented a canned PrecedentMapping when the caller set ``has_precedent=true``,
never reading any real store; and every verified/published outcome evaporated
via ``InMemoryPublishSink()`` at the end of the Lambda invocation. These tests
pin the real replacement:

  1. consult_priors() reads a real precedent + promoted rules from
     scudo.agent_memory via the RDS Data API — no fabrication.
  2. consult_priors() fails OPEN on a read error (log + empty priors), same
     philosophy as hydrate.py's "cold start is a WARN, not a failure".
  3. record_verified_precedent() upserts a precedent row and is fail LOUD —
     a lost precedent write defeats the entire point of this feature.

Run per-file:
    PYTHONPATH=. python3 -m pytest scudo/tests/test_aurora_memory.py -q
"""

from __future__ import annotations

import json
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from scudo.matching_self_improvement import (
    EvaluationPolicy,
    EvaluationReport,
    GoldenCase,
    GoldenSet,
    MatchingPrediction,
    PromotionApproval,
    evaluate_golden_set,
    evaluation_report_digest,
    issue_evaluation_attestation,
    trusted_evidence_for,
)


class _FakeRdsData:
    def __init__(self, fail=False, records=None):
        self.calls = []
        self.fail = fail
        self._records = records if records is not None else []

    def execute_statement(self, **kwargs):
        if self.fail:
            raise RuntimeError("data api down")
        self.calls.append(kwargs)
        return {"records": self._records, "numberOfRecordsUpdated": 1}

    def begin_transaction(self, **kwargs):
        self.calls.append({"operation": "begin_transaction", **kwargs})
        return {"transactionId": "tx-1"}

    def commit_transaction(self, **kwargs):
        self.calls.append({"operation": "commit_transaction", **kwargs})
        return {}

    def rollback_transaction(self, **kwargs):
        self.calls.append({"operation": "rollback_transaction", **kwargs})
        return {}


def _wire(monkeypatch, client):
    monkeypatch.setenv("SCUDO_AURORA_CLUSTER_ARN", "arn:cluster")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("SCUDO_AURORA_DATABASE_NAME", "scudo")
    from scudo import aurora_memory, aurora_store

    monkeypatch.setattr(aurora_store, "_rds_data", lambda: client)
    return aurora_memory


def _memory_row(memory_key: str, memory_type: str, payload: dict) -> list[dict]:
    """One RDS Data API 'record' row: memory_key, memory_type, payload."""
    return [
        {"stringValue": memory_key},
        {"stringValue": memory_type},
        {"stringValue": json.dumps(payload)},
    ]


def _evaluation_payload(
    *,
    candidate_version: str,
    exact_match_rate: float = 1.0,
    brier_score: float = 0.01,
    passed: bool = True,
) -> dict:
    return {
        "candidate_version": candidate_version,
        "golden_set_version": "golden-2026-07-16",
        "split": "holdout",
        "case_ids": ["case-1", "case-2"],
        "metrics": {
            "total_cases": 2,
            "expected_match_cases": 1,
            "expected_abstain_cases": 1,
            "predicted_abstain_cases": 1,
            "auto_pass_cases": 1,
            "correct_target_cases": 1,
            "correct_abstention_cases": 1,
            "false_auto_pass_cases": 0,
            "exact_match_rate": exact_match_rate,
            "abstention_recall": 1.0,
            "coverage": 0.5,
            "false_auto_pass_rate": 0.0,
            "calibration_mae": brier_score,
            "brier_score": brier_score,
        },
        "policy": {
            "min_cases": 1,
            "min_exact_match_rate": 0.5,
            "max_false_auto_pass_rate": 0.0,
            "max_brier_score": 1.0,
        },
        "passed": passed,
    }


def _approval_payload(ref: str = "MR-2026-07-16-1") -> dict:
    return {
        "approved_by": "reviewer@example.com",
        "approval_ref": ref,
        "approved_at": "2026-07-16T00:00:00+00:00",
        "rationale": "Reviewed against the versioned holdout report.",
    }


def _skill_payload(
    *,
    version: int,
    skill_text: str,
    candidate_version: str,
    exact_match_rate: float = 1.0,
    brier_score: float = 0.01,
    passed: bool = True,
    approval_ref: str = "MR-2026-07-16-1",
    signed_receipt: bool = False,
) -> dict:
    evaluation = _evaluation_payload(
        candidate_version=candidate_version,
        exact_match_rate=exact_match_rate,
        brier_score=brier_score,
        passed=passed,
    )
    payload = {
        "status": "approved",
        "artifact_id": f"matching-skill-{version}",
        "artifact_kind": "matching_skill",
        "skill_text": skill_text,
        "version": version,
        "validation_score": exact_match_rate,
        "created_at": "2026-07-16T00:00:00+00:00",
        "promoted_at": 1720000000.0,
        "immutable": True,
        "source_trajectory_refs": ["lambda-abc123"],
        "evaluation": evaluation,
        "approval": _approval_payload(approval_ref),
    }
    if signed_receipt:
        content_hash = hashlib.sha256(
            json.dumps(
                skill_text,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        payload["evaluation"]["artifact_content_hash"] = content_hash
        report_digest = evaluation_report_digest(
            EvaluationReport.model_validate(payload["evaluation"])
        )
        signed = {
            "receipt_version": 1,
            "report_digest": report_digest,
            "evidence_digest": "b" * 64,
            "manifest_digest": "c" * 64,
            "artifact_id": payload["artifact_id"],
            "artifact_version": version,
            "artifact_kind": "matching_skill",
            "artifact_content_hash": content_hash,
        }
        canonical = json.dumps(
            signed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        payload["protected_promotion_receipt"] = {
            **signed,
            "signature": hmac.new(
                b"test-promotion-key",
                canonical,
                hashlib.sha256,
            ).hexdigest(),
        }
    return payload


def test_consult_priors_returns_real_precedent_when_one_exists(monkeypatch):
    payload = {
        "target_iri": "jpmorgan:data:cdao:EquityResearch",
        "confidence": 0.91,
        "rationale": "verified auto-pass, prior run",
    }
    client = _FakeRdsData(
        records=[_memory_row("precedent:lseg:LSEG-EQ-1", "precedent", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="LSEG-EQ-1")

    assert priors.precedent is not None
    assert priors.precedent["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert priors.precedent["confidence"] == 0.91
    assert priors.rules == []


def test_consult_priors_returns_promoted_rules(monkeypatch):
    rule_payload = {
        "rule_text": "LSEG '-EQ-' refs map to EquityResearch",
        "scope": "lseg",
    }
    client = _FakeRdsData(
        records=[_memory_row("rule:lseg:eq-pattern", "rule", rule_payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="LSEG-XX-9")

    assert priors.precedent is None
    assert priors.rules == [rule_payload]


def test_consult_priors_fails_open_on_read_error(monkeypatch, caplog):
    client = _FakeRdsData(fail=True)
    aurora_memory = _wire(monkeypatch, client)

    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="LSEG-EQ-1")

    assert priors.precedent is None
    assert priors.rules == []


def test_consult_priors_fails_open_when_aurora_env_missing(monkeypatch):
    """Missing Aurora config must not crash the mapping request — CONSULT is
    advisory, never a hard dependency."""
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    from scudo import aurora_memory

    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="LSEG-EQ-1")

    assert priors.precedent is None
    assert priors.rules == []


def test_record_verified_precedent_issues_parameterised_upsert(monkeypatch):
    client = _FakeRdsData()
    aurora_memory = _wire(monkeypatch, client)

    aurora_memory.record_verified_precedent(
        vendor="lseg",
        vendor_product_ref="LSEG-EQ-1",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        confidence=0.88,
        rationale="verifier >= 16, confidence >= floor",
        source_outcome_ref="lambda-abc123",
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert "insert into scudo.agent_memory" in call["sql"].lower()
    assert "on conflict" in call["sql"].lower()  # upsert, not blind insert
    names = {p["name"] for p in call["parameters"]}
    assert {"memory_key", "memory_type", "payload"} <= names
    key_param = next(p for p in call["parameters"] if p["name"] == "memory_key")
    assert key_param["value"]["stringValue"] == "precedent:lseg:LSEG-EQ-1"
    type_param = next(p for p in call["parameters"] if p["name"] == "memory_type")
    assert type_param["value"]["stringValue"] == "precedent"
    payload_param = next(p for p in call["parameters"] if p["name"] == "payload")
    payload = json.loads(payload_param["value"]["stringValue"])
    assert payload["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert payload["confidence"] == 0.88


def test_record_verified_precedent_is_fail_loud(monkeypatch):
    client = _FakeRdsData(fail=True)
    aurora_memory = _wire(monkeypatch, client)

    with pytest.raises(RuntimeError):
        aurora_memory.record_verified_precedent(
            vendor="lseg",
            vendor_product_ref="LSEG-EQ-1",
            target_iri="jpmorgan:data:cdao:EquityResearch",
            confidence=0.88,
            rationale="x",
            source_outcome_ref="lambda-abc123",
        )


def test_record_verified_precedent_fails_loud_when_aurora_env_missing(monkeypatch):
    """Unlike CONSULT, DISTILL must NEVER silently no-op — a lost precedent
    write defeats the entire point of this feature."""
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    from scudo import aurora_memory

    with pytest.raises(RuntimeError, match="SCUDO_AURORA_CLUSTER_ARN"):
        aurora_memory.record_verified_precedent(
            vendor="lseg",
            vendor_product_ref="LSEG-EQ-1",
            target_iri="jpmorgan:data:cdao:EquityResearch",
            confidence=0.88,
            rationale="x",
            source_outcome_ref="lambda-abc123",
        )


# ──────────────────────────────────────────────────────────────────────────
# Part D — SkillOpt-inspired matching skill memory (2026-07-07)
#
# microsoft/SkillOpt (verified real: PyPI `skillopt`, arXiv:2605.23904) trains
# a "skill document" as the trainable state of a frozen agent via a held-out
# validation gate; the deployed artifact is a compact best_skill.md read with
# ZERO inference-time model calls. These tests pin the LIVE half of that
# split: a plain-text CONSULT read (never imports the skillopt package) and
# fail-loud trajectory recording for the OFFLINE half (skillopt_sleep_runner)
# to later consume.
# ──────────────────────────────────────────────────────────────────────────
def test_consult_best_skill_returns_none_on_miss(monkeypatch):
    """No skill has ever been promoted yet — a real miss, not an error."""
    client = _FakeRdsData(records=[])
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_quarantines_duplicated_legacy_best_payload(monkeypatch):
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    payload = _skill_payload(
        version=3,
        skill_text="# Matching Skill\nPrefer exact vendor-code matches...",
        candidate_version="candidate-3",
        exact_match_rate=0.87,
        signed_receipt=True,
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None
    # must query the singleton "best" key specifically, not "current"
    call = client.calls[0]
    key_param = next(p for p in call["parameters"] if p["name"] == "memory_key")
    assert key_param["value"]["stringValue"] == "skill:matching:best"


def test_consult_best_skill_quarantines_legacy_scalar_payload(monkeypatch):
    client = _FakeRdsData(
        records=[
            _memory_row(
                "skill:matching:best",
                "skill_doc",
                {
                    "skill_text": "legacy candidate",
                    "version": 1,
                    "validation_score": 0.99,
                },
            )
        ]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_quarantines_unattested_artifact(monkeypatch):
    payload = _skill_payload(
        version=3,
        skill_text="unattested skill",
        candidate_version="candidate-3",
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_quarantines_mutated_pre_auto_pass_payload(monkeypatch):
    payload = _skill_payload(
        version=3,
        skill_text="established skill",
        candidate_version="candidate-3",
    )
    del payload["evaluation"]["metrics"]["auto_pass_cases"]
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_quarantines_failed_evaluation(monkeypatch):
    payload = _skill_payload(
        version=1,
        skill_text="failed candidate",
        candidate_version="candidate-1",
        passed=False,
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_fails_open_on_read_error(monkeypatch):
    client = _FakeRdsData(fail=True)
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.consult_best_skill() is None


def test_consult_best_skill_fails_open_when_aurora_env_missing(monkeypatch):
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    from scudo import aurora_memory

    assert aurora_memory.consult_best_skill() is None


def test_record_trajectory_issues_parameterised_insert(monkeypatch):
    client = _FakeRdsData()
    aurora_memory = _wire(monkeypatch, client)

    aurora_memory.record_trajectory(
        bundle_ref="lambda-abc123",
        vendor="lseg",
        vendor_product_ref="LSEG-EQ-1",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        confidence=0.9,
        rationale="verifier passed",
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert "insert into scudo.agent_memory" in call["sql"].lower()
    key_param = next(p for p in call["parameters"] if p["name"] == "memory_key")
    assert key_param["value"]["stringValue"] == "trajectory:lambda-abc123"
    type_param = next(p for p in call["parameters"] if p["name"] == "memory_type")
    assert type_param["value"]["stringValue"] == "trajectory"
    payload_param = next(p for p in call["parameters"] if p["name"] == "payload")
    payload = json.loads(payload_param["value"]["stringValue"])
    assert payload["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert payload["vendor"] == "lseg"
    assert payload["outcome"] == "published"
    assert payload["status"] == "auto_mapped"
    assert payload["auto_pass"] is True
    assert "matcher_version" in payload
    assert "decision_snapshot" in payload


def test_record_trajectory_is_fail_loud(monkeypatch):
    """A lost trajectory silently starves the offline harvest step — same
    fail-loud contract as record_verified_precedent."""
    client = _FakeRdsData(fail=True)
    aurora_memory = _wire(monkeypatch, client)

    with pytest.raises(RuntimeError):
        aurora_memory.record_trajectory(
            bundle_ref="lambda-abc123",
            vendor="lseg",
            vendor_product_ref="LSEG-EQ-1",
            target_iri="jpmorgan:data:cdao:EquityResearch",
            confidence=0.9,
            rationale="x",
        )


def test_record_engine_trajectory_normalises_matcher_result(monkeypatch):
    client = _FakeRdsData()
    aurora_memory = _wire(monkeypatch, client)

    aurora_memory.record_engine_trajectory(
        bundle_ref="engine-abc123",
        vendor="lseg",
        vendor_product_ref="LSEG-EQ-1",
        mapping_result={
            "mapped_node_iri": "jpmorgan:data:cdao:EquityResearch",
            "confidence": 0.91,
            "status": "auto_mapped",
            "band": "pass",
            "rationale": "deterministic candidate match",
        },
        matcher_version="cost-ladder-v2",
        ontology_snapshot="cdao-2026-07-16",
    )

    payload_param = next(
        p for p in client.calls[0]["parameters"] if p["name"] == "payload"
    )
    payload = json.loads(payload_param["value"]["stringValue"])
    assert payload["status"] == "auto_mapped"
    assert payload["auto_pass"] is True
    assert payload["matcher_version"] == "cost-ladder-v2"
    assert payload["decision_snapshot"]["target_iri"] == (
        "jpmorgan:data:cdao:EquityResearch"
    )


# ──────────────────────────────────────────────────────────────────────────
# Part E1 — aurora_memory.promote_skill() (fail-loud write, fail-open read
# of the current best via consult_best_skill) and harvest_trajectories()
# (fail-open — a failed harvest just means "nothing to mine this cycle").
# ──────────────────────────────────────────────────────────────────────────
def test_promote_skill_writes_when_no_current_best_exists(monkeypatch):
    client = _FakeRdsData(records=[])  # no "best" row yet
    aurora_memory = _wire(monkeypatch, client)

    promoted = aurora_memory.promote_skill(
        skill_text="Prefer exact vendor-code matches.",
        validation_score=0.6,
        version=1,
        evaluation=_evaluation_payload(candidate_version="candidate-1"),
        approval=_approval_payload(),
    )

    assert promoted is False
    write_calls = [c for c in client.calls if "insert into" in c["sql"].lower()]
    assert len(write_calls) == 1
    artifact_key_param = next(
        p for p in write_calls[0]["parameters"] if p["name"] == "memory_key"
    )
    assert artifact_key_param["value"]["stringValue"] == "skill:matching:artifact:1"
    assert "do nothing" in write_calls[0]["sql"].lower()
    payload_param = next(
        p for p in write_calls[0]["parameters"] if p["name"] == "payload"
    )
    payload = json.loads(payload_param["value"]["stringValue"])
    assert payload["skill_text"] == "Prefer exact vendor-code matches."
    assert payload["validation_score"] == 0.6
    assert payload["version"] == 1
    assert payload["status"] == "quarantined"
    assert payload["immutable"] is True
    assert payload["approval"]["approval_ref"] == "MR-2026-07-16-1"


def test_manual_promotion_preserves_existing_protected_pointer(monkeypatch):
    protected = _skill_payload(
        version=3,
        skill_text="protected live skill",
        candidate_version="candidate-3",
    )

    class _PointerPreservingClient(_FakeRdsData):
        def execute_statement(self, **kwargs):
            self.calls.append(kwargs)
            params = {
                param["name"]: param["value"] for param in kwargs.get("parameters", [])
            }
            key = params.get("memory_key", {}).get("stringValue")
            if kwargs["sql"].lower().startswith("select"):
                records = (
                    [_memory_row(key, "skill_doc", protected)]
                    if key == "skill:matching:best"
                    else []
                )
                return {"records": records, "numberOfRecordsUpdated": 0}
            return {"records": [], "numberOfRecordsUpdated": 1}

    client = _PointerPreservingClient()
    aurora_memory = _wire(monkeypatch, client)

    assert (
        aurora_memory.promote_skill(
            skill_text="manual candidate",
            validation_score=1.0,
            version=4,
            evaluation=_evaluation_payload(candidate_version="candidate-4"),
            approval=_approval_payload("MANUAL-4"),
        )
        is False
    )
    best_pointer_writes = [
        call
        for call in client.calls
        if "insert into" in call["sql"].lower()
        and any(
            parameter["name"] == "memory_key"
            and parameter["value"].get("stringValue") == "skill:matching:best"
            for parameter in call.get("parameters", [])
        )
    ]
    assert best_pointer_writes == []


def test_protected_promotion_writes_artifact_before_live_pointer(monkeypatch):
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    golden = GoldenSet(
        version="protected-1",
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
        target_iri="target",
        confidence=0.95,
        status="auto_mapped",
        auto_pass=True,
    )
    policy = EvaluationPolicy(max_brier_score=1.0)
    report = evaluate_golden_set(
        golden,
        lambda case: prediction,
        candidate_version="candidate-1",
        policy=policy,
        artifact_content="protected skill",
        repeat_runs=2,
    )
    evidence = trusted_evidence_for(
        golden,
        policy=policy,
        prediction_runs=({"one": prediction},) * 2,
    )
    client = _FakeRdsData(records=[])
    aurora_memory = _wire(monkeypatch, client)
    attestation = issue_evaluation_attestation(
        report,
        trusted_evidence=evidence,
        artifact_content="protected skill",
        artifact_id="matching-skill-1",
        artifact_version=1,
        artifact_kind="matching_skill",
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="test-promotion-key",
    )

    promoted = aurora_memory.promote_protected_skill(
        skill_text="protected skill",
        version=1,
        evaluation=report,
        approval=PromotionApproval(
            approved_by="protected-gate",
            approval_ref="AUTO-1",
            rationale="Protected evidence passed.",
        ),
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
    )

    assert promoted is True
    writes = [
        call
        for call in client.calls
        if "sql" in call and "insert into" in call["sql"].lower()
    ]
    assert len(writes) == 3
    written_keys = []
    for call in writes:
        key_param = next(
            parameter
            for parameter in call["parameters"]
            if parameter["name"] in {"memory_key", "sequence_key", "artifact_key"}
        )
        written_keys.append(key_param["value"]["stringValue"])
    assert written_keys == [
        "skill:matching:artifact:1",
        "skill:matching:promotion:1",
        "skill:matching:best",
    ]
    pointer_payload = json.loads(
        next(
            parameter["value"]["stringValue"]
            for parameter in writes[2]["parameters"]
            if parameter["name"] == "payload"
        )
    )
    assert pointer_payload["pointer"]["predecessor_version"] is None
    artifact_payload = json.loads(
        next(
            parameter["value"]["stringValue"]
            for parameter in writes[0]["parameters"]
            if parameter["name"] == "artifact_payload"
        )
    )
    assert artifact_payload["protected_promotion_receipt"]["signature"]
    assert any(call.get("operation") == "commit_transaction" for call in client.calls)


def test_protected_promotion_rejects_self_authored_evidence_without_attestation(
    monkeypatch,
):
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    golden = GoldenSet(
        version="protected-1",
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
        target_iri="target",
        confidence=0.95,
        status="auto_mapped",
        auto_pass=True,
    )
    policy = EvaluationPolicy(max_brier_score=1.0)
    report = evaluate_golden_set(
        golden,
        lambda case: prediction,
        candidate_version="candidate-1",
        policy=policy,
        artifact_content="protected skill",
        repeat_runs=2,
    )
    evidence = trusted_evidence_for(
        golden,
        policy=policy,
        prediction_runs=({"one": prediction},) * 2,
    )
    aurora_memory = _wire(monkeypatch, _FakeRdsData(records=[]))

    with pytest.raises(Exception, match="evaluation attestation"):
        aurora_memory.promote_protected_skill(
            skill_text="protected skill",
            version=1,
            evaluation=report,
            approval=PromotionApproval(
                approved_by="gate",
                approval_ref="AUTO",
                rationale="test",
            ),
            trusted_evidence=evidence,
            evaluation_attestation=None,
        )


def test_normal_protected_promotion_rejects_legacy_best_pointer(monkeypatch):
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")
    legacy = {
        "skill_text": "legacy skill",
        "version": 4,
        "validation_score": 0.9,
    }
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", legacy)]
    )
    aurora_memory = _wire(monkeypatch, client)

    with pytest.raises(Exception, match="malformed|legacy"):
        aurora_memory.promote_protected_skill(
            skill_text="candidate",
            version=5,
            evaluation=_evaluation_payload(candidate_version="candidate-5"),
            approval=_approval_payload("AUTO-5"),
            trusted_evidence=None,
            evaluation_attestation=None,
        )
    assert not any(
        "insert into" in call.get("sql", "").lower() for call in client.calls
    )


def test_protected_promotion_rejects_stale_predecessor(monkeypatch):
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "test-promotion-key")
    monkeypatch.setenv("SCUDO_EVALUATION_SIGNING_KEY", "evaluation-key")

    class _StaleCasClient(_FakeRdsData):
        def execute_statement(self, **kwargs):
            self.calls.append(kwargs)
            if "compare-and-swap" in kwargs["sql"].lower() and kwargs.get(
                "transactionId"
            ):
                return {"records": [], "numberOfRecordsUpdated": 0}
            if kwargs["sql"].lower().startswith("select"):
                return {"records": [], "numberOfRecordsUpdated": 0}
            return {"records": [], "numberOfRecordsUpdated": 1}

    golden = GoldenSet(
        version="protected-1",
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
        artifact_content="protected skill",
        repeat_runs=2,
    )
    evidence = trusted_evidence_for(
        golden, policy=policy, prediction_runs=({"one": prediction},) * 2
    )
    attestation = issue_evaluation_attestation(
        report,
        trusted_evidence=evidence,
        artifact_content="protected skill",
        artifact_id="matching-skill-1",
        artifact_version=1,
        artifact_kind="matching_skill",
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="test-promotion-key",
    )
    client = _StaleCasClient()
    aurora_memory = _wire(monkeypatch, client)

    with pytest.raises(RuntimeError, match="expected 1"):
        aurora_memory.promote_protected_skill(
            skill_text="protected skill",
            version=1,
            evaluation=report,
            approval=PromotionApproval(
                approved_by="gate",
                approval_ref="AUTO",
                rationale="test",
            ),
            trusted_evidence=evidence,
            evaluation_attestation=attestation,
        )
    assert any(call.get("operation") == "rollback_transaction" for call in client.calls)


def _migration_test_setup(monkeypatch, *, fail_at=None):
    from scudo import aurora_memory

    legacy = {"skill_text": "legacy", "version": 1}
    evaluation = EvaluationReport.model_validate(
        _evaluation_payload(candidate_version="candidate-2")
    )
    approval = PromotionApproval.model_validate(_approval_payload("MIGRATE-2"))
    receipt = SimpleNamespace(model_dump=lambda mode=None: {"signature": "signed"})
    pointer = SimpleNamespace(
        model_dump=lambda mode=None: {
            "artifact_key": "skill:matching:artifact:2",
            "artifact_version": 2,
            "artifact_digest": "a" * 64,
            "sequence": 1,
        }
    )
    monkeypatch.setattr(aurora_memory, "validate_promotion", lambda *a, **k: None)
    monkeypatch.setattr(aurora_memory, "promotion_receipt_for", lambda *a, **k: receipt)
    monkeypatch.setattr(aurora_memory, "issue_live_pointer", lambda *a, **k: pointer)
    monkeypatch.setattr(
        aurora_memory, "learning_artifact_digest", lambda artifact: "a" * 64
    )
    calls = []

    class Tx:
        def execute(self, sql, params, expected_rows=None):
            calls.append(sql)
            if fail_at is not None and len(calls) == fail_at:
                raise RuntimeError(f"failure-{fail_at}")
            return {"numberOfRecordsUpdated": expected_rows or 0}

    class Context:
        def __enter__(self):
            return Tx()

        def __exit__(self, exc_type, exc, tb):
            calls.append("rollback" if exc else "commit")
            return False

    monkeypatch.setattr(aurora_memory.aurora_store, "transaction", Context)
    return aurora_memory, legacy, evaluation, approval, calls


def test_legacy_migration_success_is_live_consultable(monkeypatch):
    aurora_memory, legacy, evaluation, approval, calls = _migration_test_setup(
        monkeypatch
    )

    assert aurora_memory.migrate_legacy_best_skill(
        legacy_payload=legacy,
        operator_migration_ref="OP-1",
        skill_text="protected",
        version=2,
        evaluation=evaluation,
        approval=approval,
        trusted_evidence=object(),
        evaluation_attestation=object(),
        signing_key="promotion",
    )
    assert calls[-1] == "commit"
    assert len(calls[:-1]) == 5


def test_legacy_migration_exact_retry_is_idempotent(monkeypatch):
    aurora_memory, legacy, evaluation, approval, calls = _migration_test_setup(
        monkeypatch
    )
    kwargs = dict(
        legacy_payload=legacy,
        operator_migration_ref="OP-1",
        skill_text="protected",
        version=2,
        evaluation=evaluation,
        approval=approval,
        trusted_evidence=object(),
        evaluation_attestation=object(),
        signing_key="promotion",
    )

    assert aurora_memory.migrate_legacy_best_skill(**kwargs)
    assert aurora_memory.migrate_legacy_best_skill(**kwargs)
    assert calls.count("commit") == 2


def test_legacy_migration_conflicting_retry_rejected(monkeypatch):
    aurora_memory, legacy, evaluation, approval, _ = _migration_test_setup(monkeypatch)

    with pytest.raises(Exception):
        aurora_memory.migrate_legacy_best_skill(
            legacy_payload=legacy,
            operator_migration_ref="OP-1",
            skill_text="protected",
            version=1,
            evaluation=evaluation,
            approval=approval,
            trusted_evidence=object(),
            evaluation_attestation=object(),
            signing_key="promotion",
        )


@pytest.mark.parametrize("failure_point", [1, 2, 3, 4, 5])
def test_legacy_migration_failure_rolls_back_legacy(monkeypatch, failure_point):
    aurora_memory, legacy, evaluation, approval, calls = _migration_test_setup(
        monkeypatch, fail_at=failure_point
    )

    with pytest.raises(RuntimeError, match=f"failure-{failure_point}"):
        aurora_memory.migrate_legacy_best_skill(
            legacy_payload=legacy,
            operator_migration_ref="OP-1",
            skill_text="protected",
            version=2,
            evaluation=evaluation,
            approval=approval,
            trusted_evidence=object(),
            evaluation_attestation=object(),
            signing_key="promotion",
        )
    assert calls[-1] == "rollback"


def test_preflight_skill_promotion_requires_evaluation_and_named_approval(monkeypatch):
    client = _FakeRdsData(records=[])
    aurora_memory = _wire(monkeypatch, client)

    assert (
        aurora_memory.preflight_skill_promotion(
            skill_text="candidate",
            version=1,
            evaluation=None,
            approval=_approval_payload(),
        )
        is None
    )
    assert (
        aurora_memory.preflight_skill_promotion(
            skill_text="candidate",
            version=1,
            evaluation=_evaluation_payload(candidate_version="candidate-1"),
            approval=None,
        )
        is None
    )
    assert not [c for c in client.calls if "insert into" in c["sql"].lower()]


def test_preflight_skill_promotion_rejects_a_conflicting_immutable_version(monkeypatch):
    existing = _skill_payload(
        version=1,
        skill_text="other candidate",
        candidate_version="candidate-1",
    )

    class _ArtifactConflictRdsData:
        def execute_statement(self, **kwargs):
            params = {
                param["name"]: param["value"] for param in kwargs.get("parameters", [])
            }
            key = params.get("memory_key", {}).get("stringValue")
            if key == "skill:matching:artifact:1":
                return {
                    "records": [
                        _memory_row(
                            "skill:matching:artifact:1",
                            "skill_artifact",
                            existing,
                        )
                    ],
                    "numberOfRecordsUpdated": 0,
                }
            return {"records": [], "numberOfRecordsUpdated": 0}

    aurora_memory = _wire(monkeypatch, _ArtifactConflictRdsData())

    assert (
        aurora_memory.preflight_skill_promotion(
            skill_text="candidate",
            version=1,
            evaluation=_evaluation_payload(candidate_version="candidate-1"),
            approval=_approval_payload(),
        )
        is None
    )


def test_promote_skill_refuses_missing_evaluation_or_approval(monkeypatch):
    client = _FakeRdsData(records=[])
    aurora_memory = _wire(monkeypatch, client)

    assert (
        aurora_memory.promote_skill(
            skill_text="candidate",
            validation_score=1.0,
            version=1,
            evaluation=None,
            approval=_approval_payload(),
        )
        is False
    )
    assert (
        aurora_memory.promote_skill(
            skill_text="candidate",
            validation_score=1.0,
            version=1,
            evaluation=_evaluation_payload(candidate_version="candidate-1"),
            approval=None,
        )
        is False
    )
    assert not [c for c in client.calls if "insert into" in c["sql"].lower()]


def test_next_skill_version_advances_past_existing_immutable_artifacts(monkeypatch):
    existing = _skill_payload(
        version=3,
        skill_text="old skill",
        candidate_version="candidate-3",
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:artifact:3", "skill_artifact", existing)]
    )
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.next_skill_version() == 4
    assert aurora_memory.next_skill_version(minimum=7) == 7


def test_next_skill_version_advances_past_quarantined_legacy_best_pointer(monkeypatch):
    legacy_pointer = _memory_row(
        "skill:matching:best",
        "skill_doc",
        {
            "skill_text": "legacy candidate",
            "version": 7,
            "validation_score": 0.99,
        },
    )

    class _VersionQueryClient(_FakeRdsData):
        def execute_statement(self, **kwargs):
            self.calls.append(kwargs)
            records = (
                [legacy_pointer]
                if "memory_key = :best_skill_key" in kwargs["sql"]
                else []
            )
            return {"records": records, "numberOfRecordsUpdated": 1}

    client = _VersionQueryClient()
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.next_skill_version() == 8


def test_manual_promote_writes_quarantine_without_advancing_pointer(monkeypatch):
    current = _skill_payload(
        version=1,
        skill_text="old skill",
        candidate_version="candidate-1",
        exact_match_rate=0.5,
        brier_score=0.5,
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", current)]
    )
    aurora_memory = _wire(monkeypatch, client)

    promoted = aurora_memory.promote_skill(
        skill_text="new better skill",
        validation_score=0.7,
        version=2,
        evaluation=_evaluation_payload(
            candidate_version="candidate-2",
            exact_match_rate=0.7,
            brier_score=0.2,
        ),
        approval=_approval_payload("MR-2026-07-16-2"),
    )

    assert promoted is False


def test_promote_skill_rejects_non_improvement(monkeypatch):
    """Same or worse than the current best -> no write at all, fail-loud
    write path must never even be attempted."""
    current = _skill_payload(
        version=1,
        skill_text="old skill",
        candidate_version="candidate-1",
        exact_match_rate=0.8,
        brier_score=0.01,
    )
    client = _FakeRdsData(
        records=[_memory_row("skill:matching:best", "skill_doc", current)]
    )
    aurora_memory = _wire(monkeypatch, client)

    promoted = aurora_memory.promote_skill(
        skill_text="not better",
        validation_score=0.8,
        version=2,
        evaluation=_evaluation_payload(
            candidate_version="candidate-2",
            exact_match_rate=0.8,
            brier_score=0.01,
        ),
        approval=_approval_payload("MR-2026-07-16-3"),
    )

    assert promoted is False
    write_calls = [c for c in client.calls if "insert into" in c["sql"].lower()]
    assert len(write_calls) == 1
    payload_param = next(
        parameter
        for parameter in write_calls[0]["parameters"]
        if parameter["name"] == "payload"
    )
    assert json.loads(payload_param["value"]["stringValue"])["status"] == "quarantined"


def test_manual_promote_artifact_read_fails_loud(monkeypatch):
    """A quarantine artifact conflict read is a persistence operation."""

    class _FlakyThenOkClient:
        def __init__(self):
            self.calls = []
            self._first = True

        def execute_statement(self, **kwargs):
            if self._first:
                self._first = False
                raise RuntimeError("data api down")
            self.calls.append(kwargs)
            return {"records": [], "numberOfRecordsUpdated": 1}

    aurora_memory = _wire(monkeypatch, _FlakyThenOkClient())

    with pytest.raises(RuntimeError, match="data api down"):
        aurora_memory.promote_skill(
            skill_text="candidate",
            validation_score=0.5,
            version=1,
            evaluation=_evaluation_payload(candidate_version="candidate-1"),
            approval=_approval_payload("MR-2026-07-16-4"),
        )


def test_manual_promote_artifact_write_is_fail_loud(monkeypatch):
    """The write itself, once the gate passes, must never silently swallow
    an error — a lost promotion means the improved skill never reaches
    live agents."""

    class _ReadOkWriteFailsClient:
        def __init__(self):
            self.calls = []

        def execute_statement(self, **kwargs):
            if "insert into" in kwargs["sql"].lower():
                raise RuntimeError("data api down")
            self.calls.append(kwargs)
            return {"records": [], "numberOfRecordsUpdated": 1}

    aurora_memory = _wire(monkeypatch, _ReadOkWriteFailsClient())

    with pytest.raises(RuntimeError):
        aurora_memory.promote_skill(
            skill_text="candidate",
            validation_score=0.5,
            version=1,
            evaluation=_evaluation_payload(candidate_version="candidate-1"),
            approval=_approval_payload("MR-2026-07-16-5"),
        )


def test_manual_promote_never_attempts_pointer_write(monkeypatch):
    class _RecoveringRdsData:
        def __init__(self):
            self.calls = []
            self.artifact = None
            self.best = None
            self.fail_pointer_once = True

        def execute_statement(self, **kwargs):
            self.calls.append(kwargs)
            sql = kwargs["sql"].lower()
            params = {
                param["name"]: param["value"] for param in kwargs.get("parameters", [])
            }
            key = params.get("memory_key", {}).get("stringValue")

            if sql.startswith("select"):
                payload = self.best if key == "skill:matching:best" else self.artifact
                records = [_memory_row(key, "skill_doc", payload)] if payload else []
                return {"records": records, "numberOfRecordsUpdated": 0}

            payload = json.loads(params["payload"]["stringValue"])
            if "do nothing" in sql:
                if self.artifact is not None:
                    return {"records": [], "numberOfRecordsUpdated": 0}
                self.artifact = payload
                return {"records": [], "numberOfRecordsUpdated": 1}

            if self.fail_pointer_once:
                self.fail_pointer_once = False
                raise RuntimeError("pointer write failed")
            self.best = payload
            return {"records": [], "numberOfRecordsUpdated": 1}

    client = _RecoveringRdsData()
    aurora_memory = _wire(monkeypatch, client)
    approval = _approval_payload("MR-2026-07-16-retry")
    approval.pop("approved_at")
    kwargs = {
        "skill_text": "candidate",
        "validation_score": 0.8,
        "version": 1,
        "evaluation": _evaluation_payload(candidate_version="candidate-1"),
        "approval": approval,
    }

    assert aurora_memory.promote_skill(**kwargs) is False
    assert client.best is None


def test_harvest_trajectories_returns_recorded_rows(monkeypatch):
    payload = {
        "vendor": "lseg",
        "vendor_product_ref": "LSEG-EQ-1",
        "target_iri": "jpmorgan:data:cdao:EquityResearch",
        "confidence": 0.9,
        "rationale": "x",
    }
    client = _FakeRdsData(
        records=[_memory_row("trajectory:lambda-abc123", "trajectory", payload)]
    )
    aurora_memory = _wire(monkeypatch, client)

    trajectories = aurora_memory.harvest_trajectories()

    assert len(trajectories) == 1
    assert trajectories[0]["vendor"] == "lseg"


def test_harvest_trajectories_fails_open_on_error(monkeypatch):
    client = _FakeRdsData(fail=True)
    aurora_memory = _wire(monkeypatch, client)

    assert aurora_memory.harvest_trajectories() == []


def test_harvest_trajectories_fails_open_when_aurora_env_missing(monkeypatch):
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    from scudo import aurora_memory

    assert aurora_memory.harvest_trajectories() == []
