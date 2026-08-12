from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scudo import aurora_memory, aurora_store
from scudo.matching_self_improvement import (
    PromotionApproval,
    PromotionRejected,
    issue_signed_monitoring_envelope,
    monitoring_source_record_digest,
)
from scudo.protected_evaluator_adapter import run_protected_evaluator_command
from scudo.promotion_monitor import MonitorContext, monitor_promotion_window
from scudo.skill_optimizer_adapter import run_skill_optimizer_command


class StatefulRdsDataApi:
    """Small transactional fake for the agent_memory SQL used by this lifecycle."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.transactions: dict[str, dict[str, dict]] = {}
        self.commits = 0
        self.rollbacks = 0

    @staticmethod
    def _params(kwargs: dict) -> dict:
        return {
            item["name"]: next(iter(item["value"].values()))
            for item in kwargs.get("parameters", [])
        }

    def begin_transaction(self, **kwargs) -> dict:
        transaction_id = f"tx-{len(self.transactions) + 1}"
        self.transactions[transaction_id] = deepcopy(self.rows)
        return {"transactionId": transaction_id}

    def commit_transaction(self, **kwargs) -> dict:
        transaction_id = kwargs["transactionId"]
        self.rows = self.transactions.pop(transaction_id)
        self.commits += 1
        return {}

    def rollback_transaction(self, **kwargs) -> dict:
        self.transactions.pop(kwargs["transactionId"])
        self.rollbacks += 1
        return {}

    def execute_statement(self, **kwargs) -> dict:
        sql = " ".join(kwargs["sql"].lower().split())
        sql = sql.removeprefix("/* compare-and-swap */ ")
        params = self._params(kwargs)
        transaction_id = kwargs.get("transactionId")
        rows = self.transactions[transaction_id] if transaction_id else self.rows

        if sql.startswith("select memory_key, memory_type, payload"):
            if "memory_type = 'skill_promotion_sequence'" in sql:
                matches = []
                for key, row in rows.items():
                    pointer = row.get("payload", {}).get("committed_pointer", {})
                    if (
                        row.get("memory_type") == "skill_promotion_sequence"
                        and pointer.get("transition_kind") == "promote"
                        and pointer.get("artifact_version")
                        == params["artifact_version"]
                        and pointer.get("artifact_digest") == params["artifact_digest"]
                    ):
                        matches.append((pointer.get("sequence", 0), key, row))
                if not matches:
                    return {"records": [], "numberOfRecordsUpdated": 0}
                _, key, row = min(matches)
                return {
                    "records": [
                        [
                            {"stringValue": key},
                            {"stringValue": row["memory_type"]},
                            {"stringValue": json.dumps(row["payload"])},
                        ]
                    ],
                    "numberOfRecordsUpdated": 0,
                }
            key = params["memory_key"]
            row = rows.get(key)
            if row is None:
                return {"records": [], "numberOfRecordsUpdated": 0}
            return {
                "records": [
                    [
                        {"stringValue": key},
                        {"stringValue": row["memory_type"]},
                        {"stringValue": json.dumps(row["payload"])},
                    ]
                ],
                "numberOfRecordsUpdated": 0,
            }
        if sql.startswith("select payload") and "for update" in sql:
            key = params["key"]
            if "legacy" not in params:
                row = rows.get(key)
                return {
                    "records": (
                        [[{"stringValue": json.dumps(row["payload"])}]] if row else []
                    ),
                    "numberOfRecordsUpdated": 0,
                }
            matches = rows.get(key, {}).get("payload") == json.loads(params["legacy"])
            return {
                "records": [[{"stringValue": params["legacy"]}]] if matches else [],
                "numberOfRecordsUpdated": 0,
            }
        if sql.startswith("insert into scudo.agent_memory"):
            key_name = next(
                name
                for name in ("artifact_key", "sequence_key", "memory_key", "key")
                if name in params
            )
            payload_name = next(
                name
                for name in (
                    "artifact_payload",
                    "sequence_payload",
                    "payload",
                    "pointer",
                )
                if name in params
            )
            key = params[key_name]
            if key in rows:
                return {"numberOfRecordsUpdated": 0}
            if "skill_artifact" in sql:
                memory_type = "skill_artifact"
            elif "skill_promotion_sequence" in sql:
                memory_type = "skill_promotion_sequence"
            elif "skill_legacy_migration" in sql:
                memory_type = "skill_legacy_migration"
            else:
                memory_type = params.get("memory_type", "skill_doc")
            rows[key] = {
                "memory_type": memory_type,
                "payload": json.loads(params[payload_name]),
            }
            return {"numberOfRecordsUpdated": 1}
        if sql.startswith("update scudo.agent_memory"):
            key = params["key"] if "key" in params else params["memory_key"]
            row = rows.get(key)
            if row is None:
                return {"numberOfRecordsUpdated": 0}
            current = row["payload"]
            if "legacy" in params and current != json.loads(params["legacy"]):
                return {"numberOfRecordsUpdated": 0}
            pointer = current.get("pointer", {})
            if (
                "expected_sequence" in params
                and pointer.get("sequence") != params["expected_sequence"]
            ):
                return {"numberOfRecordsUpdated": 0}
            if (
                "expected_version" in params
                and pointer.get("artifact_version") != params["expected_version"]
            ):
                return {"numberOfRecordsUpdated": 0}
            if (
                "expected_digest" in params
                and pointer.get("artifact_digest") != params["expected_digest"]
            ):
                return {"numberOfRecordsUpdated": 0}
            if "input_digest" in params and (
                current.get("status") != "pending"
                or current.get("input_digest") != params["input_digest"]
            ):
                return {"numberOfRecordsUpdated": 0}
            payload_name = "pointer" if "pointer" in params else "payload"
            row["payload"] = json.loads(params[payload_name])
            if key == "skill:matching:best":
                row["memory_type"] = "skill_doc"
            return {"numberOfRecordsUpdated": 1}
        raise AssertionError(f"unsupported SQL: {sql}")


def _write_executable(path: Path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    return f"{sys.executable} {path}"


def _key_pair() -> tuple[str, str]:
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
    return private_pem, public_pem


def _protected_bundle(predictor_command: str) -> dict:
    cases = [
        {
            "case_id": "holdout",
            "vendor": "lseg",
            "vendor_product_ref": "H",
            "product_name": "Prices",
            "description": "Equity prices",
            "expected_target_iri": "target",
            "split": "holdout",
        },
        {
            "case_id": "adversarial",
            "vendor": "ice",
            "vendor_product_ref": "A",
            "product_name": "Rates",
            "description": "Rates",
            "expected_target_iri": "target",
            "split": "adversarial",
        },
    ]
    return {
        "repeat_count": 2,
        "predictor_command": predictor_command,
        "golden_set": {"version": "e2e", "cases": cases},
        "policy": {"min_cases": 1, "max_brier_score": 1.0},
        "evaluator_id": "e2e-evaluator",
        "evaluator_version": "1",
    }


def test_real_subprocess_evaluation_promotion_rollback_and_migration_lifecycle(
    monkeypatch,
    tmp_path,
):
    optimizer = _write_executable(
        tmp_path / "optimizer.py",
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'candidate_content': request['seed']+' optimized'}))\n",
    )
    predictor = _write_executable(
        tmp_path / "predictor.py",
        "import hashlib,json,sys\n"
        "request=json.load(sys.stdin)\n"
        "content=request['candidate_content']\n"
        "harmful='harmful' in content\n"
        "version=next((n for n in (4,3,2,1) if f'v{n}' in content),1)\n"
        "prediction=({'target_iri':'wrong','confidence':0.99,"
        "'status':'auto_mapped','auto_pass':True}"
        " if harmful else {'target_iri':'target','confidence':0.75+version*0.05,"
        "'status':'auto_mapped','auto_pass':True})\n"
        "print(json.dumps({'candidate_content_hash':hashlib.sha256("
        "request['candidate_content'].encode()).hexdigest(),"
        "'predictions':[{'case_id':case['case_id'],'prediction':prediction}"
        " for case in request['cases']]}))\n",
    )
    private_pem, public_pem = _key_pair()
    protected_root = tmp_path / "protected"
    protected_root.mkdir()
    bundle = _protected_bundle(predictor)
    raw_bundle = json.dumps(bundle, sort_keys=True).encode()
    (protected_root / "request.json").write_bytes(raw_bundle)
    (protected_root / "index.json").write_text(
        json.dumps({"request": hashlib.sha256(raw_bundle).hexdigest()}),
        encoding="utf-8",
    )
    evaluator = _write_executable(
        tmp_path / "independent_evaluator_wrapper.py",
        "import os,runpy\n"
        "assert 'SCUDO_PROTECTED_EVALUATION_ROOT' not in os.environ\n"
        "assert 'SCUDO_EVALUATION_PRIVATE_KEY' not in os.environ\n"
        f"os.environ['SCUDO_PROTECTED_EVALUATION_ROOT']={str(protected_root)!r}\n"
        f"os.environ['SCUDO_EVALUATION_PRIVATE_KEY']={private_pem!r}\n"
        f"runpy.run_path({str(Path(__file__).parents[1] / 'scripts' / 'protected_evaluator.py')!r},"
        "run_name='__main__')\n",
    )
    client = StatefulRdsDataApi()
    for name, value in {
        "SCUDO_AURORA_CLUSTER_ARN": "cluster",
        "SCUDO_AURORA_SECRET_ARN": "secret",
        "SCUDO_AURORA_DATABASE_NAME": "scudo",
        "SCUDO_EVALUATION_PUBLIC_KEY": public_pem,
        "SCUDO_MONITORING_PUBLIC_KEY": public_pem,
        "SCUDO_MONITORING_AUDIENCE": "scudo-monitor",
        "SCUDO_MONITORING_DEPLOYMENT_ID": "backend-e2e",
        "SCUDO_MONITORING_KEY_ID": "monitor-key-1",
        "SCUDO_SKILL_PROMOTION_KEY": "promotion-key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(aurora_store, "_rds_data", lambda: client)

    def evaluate(candidate: str, version: int):
        return run_protected_evaluator_command(
            {
                "candidate_content": candidate,
                "artifact_id": f"matching-skill-{version}",
                "artifact_version": version,
                "artifact_kind": "matching_skill",
                "candidate_version": f"candidate-{version}",
                "evaluation_request_id": "request",
            },
            command=evaluator,
        )

    def promote(candidate: str, version: int, envelope) -> bool:
        return aurora_memory.promote_protected_skill(
            skill_text=candidate,
            version=version,
            evaluation=envelope.report,
            approval=PromotionApproval(
                approved_by="gate",
                approval_ref=f"AUTO-{version}",
                rationale="protected e2e",
            ),
            trusted_evidence=envelope.evidence,
            evaluation_attestation=None,
            signed_evaluation_envelope=envelope,
            evaluation_public_key_pem=public_pem,
            signing_key="promotion-key",
        )

    for version in (1, 2, 3):
        candidate = run_skill_optimizer_command(
            {"seed": f"skill v{version}"},
            command=optimizer,
        )
        assert promote(candidate, version, evaluate(candidate, version))
    assert aurora_memory.consult_best_skill()["version"] == 3

    rows_before_harmful = deepcopy(client.rows)
    harmful = run_skill_optimizer_command(
        {"seed": "harmful skill v4"},
        command=optimizer,
    )
    with pytest.raises(subprocess.CalledProcessError):
        evaluate(harmful, 4)
    assert client.rows == rows_before_harmful
    assert aurora_memory.consult_best_skill()["version"] == 3

    good_v4 = run_skill_optimizer_command(
        {"seed": "good skill v4"},
        command=optimizer,
    )
    assert promote(good_v4, 4, evaluate(good_v4, 4))
    assert aurora_memory.consult_best_skill()["skill_text"] == good_v4
    safe_prediction = {
        "target_iri": "target",
        "confidence": 0.95,
        "status": "auto_mapped",
        "auto_pass": True,
    }
    live_pointer, _ = aurora_memory._resolve_verified_current_artifact(fail_closed=True)
    monitoring_sources = {}
    monitoring_now = datetime.now(timezone.utc)
    monitor_context = MonitorContext(
        public_key_pem=public_pem,
        expected_audience="scudo-monitor",
        expected_deployment_id="backend-e2e",
        expected_key_id="monitor-key-1",
        source_resolver=monitoring_sources.__getitem__,
    )

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
                    "publish_gate_violations": sample.get(
                        "publish_gate_violations", ()
                    ),
                },
            }
            monitoring_sources[sample["sample_id"]] = record
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
            deployment_id="backend-e2e",
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
            "prediction": safe_prediction,
            "authoritative_target_iri": "target",
        }
        for index in range(19)
    ]
    assert (
        monitor_promotion_window(
            envelope=monitoring_envelope("v4-insufficient", insufficient_samples),
            signing_key="promotion-key",
            context=monitor_context,
        ).action
        == "insufficient_samples"
    )
    assert aurora_memory.consult_best_skill()["version"] == 4
    safe_samples = [
        {
            "sample_id": f"safe-{index}",
            "prediction": safe_prediction,
            "authoritative_target_iri": "target",
        }
        for index in range(20)
    ]
    assert (
        monitor_promotion_window(
            envelope=monitoring_envelope("v4-safe", safe_samples),
            signing_key="promotion-key",
            context=monitor_context,
        ).action
        == "retain"
    )
    assert aurora_memory.consult_best_skill()["version"] == 4
    unsafe_samples = [
        {
            "sample_id": f"unsafe-{index}",
            "prediction": safe_prediction,
            "authoritative_target_iri": "wrong" if index == 19 else "target",
        }
        for index in range(20)
    ]
    monitored = monitor_promotion_window(
        envelope=monitoring_envelope("v4-false-auto-pass", unsafe_samples),
        signing_key="promotion-key",
        context=monitor_context,
    )
    assert monitored.action == "rollback"
    assert monitored.rollback_succeeded is True
    assert aurora_memory.consult_best_skill()["version"] == 3
    rows_after_monitor = deepcopy(client.rows)
    assert (
        monitor_promotion_window(
            envelope=monitoring_envelope("v4-false-auto-pass", unsafe_samples),
            signing_key="promotion-key",
            context=monitor_context,
        )
        == monitored
    )
    assert client.rows == rows_after_monitor
    rollback_audit = client.rows["skill:matching:promotion:5"]["payload"]
    assert rollback_audit["rolled_back_from"]["artifact_version"] == 4
    assert rollback_audit["operator_rollback_ref"] == "auto-monitor:v4-false-auto-pass"
    assert monitored.input_digest in rollback_audit["reason"]
    monitor_row = client.rows["monitor:v4-false-auto-pass"]["payload"]
    assert monitor_row["status"] == "finalized"
    assert monitor_row["input_digest"] == monitored.input_digest
    with pytest.raises(RuntimeError, match="different input"):
        monitor_promotion_window(
            envelope=monitoring_envelope(
                "v4-false-auto-pass",
                unsafe_samples[:-1]
                + [{**unsafe_samples[-1], "authoritative_target_iri": "target"}],
            ),
            signing_key="promotion-key",
            context=monitor_context,
        )
    assert aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-3",
        reason="continued regression",
        expected_sequence=5,
        signing_key="promotion-key",
    )
    assert aurora_memory.consult_best_skill()["version"] == 2
    assert aurora_memory.consult_best_skill()["version"] != 4

    tampered_pointer = client.rows["skill:matching:promotion:1"]["payload"][
        "committed_pointer"
    ]
    tampered_pointer["predecessor_version"] = 99
    tampered_snapshot = deepcopy(client.rows)
    assert not aurora_memory.rollback_protected_skill(
        operator_rollback_ref="ROLLBACK-TAMPERED",
        reason="must reject unsigned ancestry changes",
        expected_sequence=6,
        signing_key="promotion-key",
    )
    assert client.rows == tampered_snapshot
    assert aurora_memory.consult_best_skill()["version"] == 2

    legacy = {"skill_text": "legacy flat", "version": 7}
    client.rows = {
        "skill:matching:best": {
            "memory_type": "skill_doc",
            "payload": deepcopy(legacy),
        }
    }
    migrated_candidate = run_skill_optimizer_command(
        {"seed": "good skill v4 migration"},
        command=optimizer,
    )
    migrated_envelope = evaluate(migrated_candidate, 8)
    migration_kwargs = dict(
        legacy_payload=legacy,
        operator_migration_ref="MIGRATE-1",
        skill_text=migrated_candidate,
        version=8,
        evaluation=migrated_envelope.report,
        approval=PromotionApproval(
            approved_by="gate",
            approval_ref="MIGRATE-8",
            rationale="protected migration",
        ),
        trusted_evidence=migrated_envelope.evidence,
        evaluation_attestation=None,
        signed_evaluation_envelope=migrated_envelope,
        evaluation_public_key_pem=public_pem,
        signing_key="promotion-key",
    )
    assert aurora_memory.migrate_legacy_best_skill(**migration_kwargs)
    assert aurora_memory.consult_best_skill()["skill_text"] == migrated_candidate
    persisted_after_migration = deepcopy(client.rows)
    assert aurora_memory.migrate_legacy_best_skill(**migration_kwargs)
    assert client.rows == persisted_after_migration
    assert (
        client.rows["skill:matching:legacy-migration:MIGRATE-1"]["payload"][
            "operator_migration_ref"
        ]
        == "MIGRATE-1"
    )
    conflicting_candidate = f"{migrated_candidate} conflict"
    conflicting_envelope = evaluate(conflicting_candidate, 8)
    with pytest.raises(PromotionRejected, match="another migration"):
        aurora_memory.migrate_legacy_best_skill(
            **{
                **migration_kwargs,
                "skill_text": conflicting_candidate,
                "evaluation": conflicting_envelope.report,
                "trusted_evidence": conflicting_envelope.evidence,
                "signed_evaluation_envelope": conflicting_envelope,
            }
        )
