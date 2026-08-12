from __future__ import annotations

import os
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scudo.matching_self_improvement import (
    MonitoringOutcome,
    MonitoringPolicy,
    SignedMonitoringEnvelope,
    evaluate_monitoring_window,
    issue_live_pointer,
    issue_signed_monitoring_envelope,
    monitoring_source_record_digest,
    verify_signed_monitoring_envelope,
)
from scudo import aurora_memory, aurora_store
from scudo.promotion_monitor import MonitorContext, monitor_promotion_window


ARTIFACT_KEY = "skill:matching:artifact:4"
ARTIFACT_DIGEST = "a" * 64
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
PERSIST_MONITORING_OUTCOME = aurora_memory.persist_monitoring_outcome


@pytest.fixture
def key_pair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    )


def observations(
    count: int = 20,
    *,
    auto_passes: int = 20,
    false_index: int | None = None,
    gate_index: int | None = None,
    abstain: bool = False,
) -> list[dict]:
    rows = []
    for index in range(count):
        auto_pass = index < auto_passes
        record = {
            "source_event_id": f"event-{index}",
            "observed_at": NOW - timedelta(minutes=30),
            "artifact_key": ARTIFACT_KEY,
            "artifact_version": 4,
            "artifact_digest": ARTIFACT_DIGEST,
            "pointer_sequence": 4,
            "prediction": {
                "target_iri": "target",
                "confidence": 0.95,
                "status": "auto_mapped" if auto_pass else "needs_review",
                "auto_pass": auto_pass,
                "abstained": not auto_pass,
            },
            "authoritative_outcome": {
                "target_iri": None
                if abstain
                else ("wrong" if index == false_index else "target"),
                "abstain": abstain,
                "publish_gate_violations": (
                    ["candidate_membership"] if index == gate_index else []
                ),
            },
        }
        rows.append(
            {**record, "source_record_digest": monitoring_source_record_digest(record)}
        )
    return rows


def envelope(
    private_key: str,
    *,
    window_id: str = "window",
    rows=None,
    issued_at: datetime = NOW,
):
    return issue_signed_monitoring_envelope(
        window_id=window_id,
        artifact_key=ARTIFACT_KEY,
        artifact_version=4,
        artifact_digest=ARTIFACT_DIGEST,
        pointer_sequence=4,
        observations=observations() if rows is None else rows,
        private_key_pem=private_key,
        audience="scudo-monitor",
        deployment_id="backend-test",
        key_id="monitor-key-1",
        issued_at=issued_at,
        not_before=NOW - timedelta(minutes=1),
        expires_at=issued_at + timedelta(minutes=5),
        observation_start=NOW - timedelta(hours=1),
        observation_end=issued_at,
    )


class StatefulMonitoringDataApi:
    """Transaction-aware fake for the monitor's exact agent_memory SQL."""

    def __init__(self, pointer: dict) -> None:
        self.rows = {
            "skill:matching:best": {
                "memory_type": "skill_doc",
                "payload": {"pointer": deepcopy(pointer)},
            }
        }
        self.transactions: dict[str, dict[str, dict]] = {}
        self.fail_at: str | None = None
        self.event_failure_index = 0
        self.event_inserts = 0
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0

    @staticmethod
    def _params(kwargs: dict) -> dict:
        return {
            item["name"]: next(iter(item["value"].values()))
            for item in kwargs.get("parameters", [])
        }

    def begin_transaction(self, **kwargs) -> dict:
        self.begins += 1
        transaction_id = f"tx-{self.begins}"
        self.transactions[transaction_id] = deepcopy(self.rows)
        return {"transactionId": transaction_id}

    def commit_transaction(self, **kwargs) -> dict:
        if self.fail_at == "commit":
            raise RuntimeError("injected commit failure")
        transaction_id = kwargs["transactionId"]
        self.rows = self.transactions.pop(transaction_id)
        self.commits += 1
        return {}

    def rollback_transaction(self, **kwargs) -> dict:
        self.transactions.pop(kwargs["transactionId"], None)
        self.rollbacks += 1
        return {}

    def execute_statement(self, **kwargs) -> dict:
        sql = " ".join(kwargs["sql"].lower().split())
        params = self._params(kwargs)
        transaction_id = kwargs.get("transactionId")
        rows = self.transactions[transaction_id] if transaction_id else self.rows

        if sql.startswith("select memory_key, memory_type, payload"):
            row = rows.get(params["memory_key"])
            if row is None:
                return {"records": [], "numberOfRecordsUpdated": 0}
            return {
                "records": [
                    [
                        {"stringValue": params["memory_key"]},
                        {"stringValue": row["memory_type"]},
                        {"stringValue": json.dumps(row["payload"])},
                    ]
                ],
                "numberOfRecordsUpdated": 0,
            }
        if sql.startswith("insert into scudo.agent_memory"):
            key = params["key"]
            if "'promotion_monitor_decision'" in sql:
                stage = "window_claim"
                memory_type = "promotion_monitor_decision"
            elif "'monitoring_observation_claim'" in sql:
                stage = "event_claim"
                memory_type = "monitoring_observation_claim"
                event_index = self.event_inserts
                self.event_inserts += 1
                if self.fail_at == stage and event_index == self.event_failure_index:
                    raise RuntimeError("injected event claim failure")
            elif "'skill_promotion_sequence'" in sql:
                stage = "rollback_sequence"
                memory_type = "skill_promotion_sequence"
            else:
                raise AssertionError(f"unsupported insert: {sql}")
            if self.fail_at == stage and stage != "event_claim":
                raise RuntimeError(f"injected {stage} failure")
            if key in rows:
                return {"numberOfRecordsUpdated": 0}
            rows[key] = {
                "memory_type": memory_type,
                "payload": json.loads(params["payload"]),
            }
            return {"numberOfRecordsUpdated": 1}
        if sql.startswith("select payload") and "for update" in sql:
            if self.fail_at == "pointer_lock":
                return {"records": [], "numberOfRecordsUpdated": 0}
            row = rows.get(params["key"])
            return {
                "records": (
                    [[{"stringValue": json.dumps(row["payload"])}]] if row else []
                ),
                "numberOfRecordsUpdated": 0,
            }
        if sql.startswith("update scudo.agent_memory"):
            key = params["key"]
            if "expected_sequence" in params:
                if self.fail_at == "pointer_cas":
                    return {"numberOfRecordsUpdated": 0}
                row = rows.get(key)
                pointer = (row or {}).get("payload", {}).get("pointer", {})
                if (
                    pointer.get("sequence") != params["expected_sequence"]
                    or pointer.get("artifact_digest") != params["expected_digest"]
                ):
                    return {"numberOfRecordsUpdated": 0}
            else:
                if self.fail_at == "outcome_finalize":
                    return {"numberOfRecordsUpdated": 0}
                row = rows.get(key)
                if (
                    not row
                    or row["payload"].get("status") != "pending"
                    or row["payload"].get("input_digest") != params["input_digest"]
                ):
                    return {"numberOfRecordsUpdated": 0}
            rows[key]["payload"] = json.loads(params["payload"])
            return {"numberOfRecordsUpdated": 1}
        raise AssertionError(f"unsupported SQL: {sql}")

    def monitoring_rows(self) -> dict[str, dict]:
        return {
            key: value
            for key, value in self.rows.items()
            if key.startswith(("monitor:", "monitor-observation:"))
            or key == "skill:matching:promotion:5"
        }


def _runtime(public: str, rows: list[dict]) -> MonitorContext:
    sources = {
        row["source_event_id"]: {
            key: value for key, value in row.items() if key != "source_record_digest"
        }
        for row in rows
    }
    return MonitorContext(
        public_key_pem=public,
        expected_audience="scudo-monitor",
        expected_deployment_id="backend-test",
        expected_key_id="monitor-key-1",
        source_resolver=sources.__getitem__,
        clock=lambda: NOW,
    )


def _complete_outcome(
    monkeypatch,
    private: str,
    public: str,
    *,
    window_id: str,
    breached: bool,
) -> MonitoringOutcome:
    rows = observations(false_index=19 if breached else None)
    captured = []
    monkeypatch.setattr(aurora_memory, "_read_memory_payload", lambda key: None)
    monkeypatch.setattr(
        aurora_memory,
        "persist_monitoring_outcome",
        lambda **kwargs: captured.append(kwargs["outcome"]),
    )
    result = monitor_promotion_window(
        envelope=envelope(private, window_id=window_id, rows=rows),
        context=_runtime(public, rows),
    )
    assert captured == [result]
    return result


def _install_transaction_state(monkeypatch, *, fail_at: str | None = None):
    current = issue_live_pointer(
        artifact_key=ARTIFACT_KEY,
        artifact_version=4,
        artifact_digest=ARTIFACT_DIGEST,
        predecessor_version=3,
        predecessor_digest="b" * 64,
        sequence=4,
        signing_key="promotion",
    )
    rollback = issue_live_pointer(
        artifact_key="skill:matching:artifact:3",
        artifact_version=3,
        artifact_digest="b" * 64,
        predecessor_version=2,
        predecessor_digest="c" * 64,
        sequence=5,
        transition_kind="rollback",
        signing_key="promotion",
    )
    client = StatefulMonitoringDataApi(current.model_dump(mode="json"))
    client.fail_at = fail_at
    plan = aurora_memory.ProtectedRollbackPlan(
        current_pointer=current,
        rollback_pointer=rollback,
        sequence_payload={
            "committed": True,
            "committed_pointer": rollback.model_dump(mode="json"),
        },
    )
    for name, value in {
        "SCUDO_AURORA_CLUSTER_ARN": "cluster",
        "SCUDO_AURORA_SECRET_ARN": "secret",
        "SCUDO_AURORA_DATABASE_NAME": "scudo",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(aurora_store, "_rds_data", lambda: client)
    monkeypatch.setattr(
        aurora_memory,
        "persist_monitoring_outcome",
        PERSIST_MONITORING_OUTCOME,
    )
    monkeypatch.setattr(
        aurora_memory,
        "_prepare_protected_skill_rollback",
        lambda **kwargs: plan,
    )
    return client, current


def test_signature_binds_authoritative_outcomes(key_pair):
    private, public = key_pair
    signed = envelope(private)
    assert verify_signed_monitoring_envelope(signed, public_key_pem=public)
    forged = signed.model_copy(
        update={
            "observations": (
                signed.observations[0].model_copy(
                    update={
                        "authoritative_outcome": signed.observations[
                            0
                        ].authoritative_outcome.model_copy(
                            update={"target_iri": "fabricated"}
                        )
                    }
                ),
                *signed.observations[1:],
            )
        }
    )
    assert not verify_signed_monitoring_envelope(forged, public_key_pem=public)
    assert not verify_signed_monitoring_envelope(
        signed.model_copy(update={"signature": "invalid"}),
        public_key_pem=public,
    )


def test_fixed_policy_requires_total_and_auto_pass_evidence():
    policy = MonitoringPolicy()
    assert policy.min_total_samples == 20
    assert policy.min_auto_pass_samples == 20
    assert not evaluate_monitoring_window(
        observations(auto_passes=0, abstain=True)
    ).sufficient_samples
    assert not evaluate_monitoring_window(
        observations(auto_passes=19)
    ).sufficient_samples


def test_safe_false_pass_and_gate_violation_decisions():
    safe = evaluate_monitoring_window(observations())
    assert safe.sufficient_samples and not safe.breached
    false_pass = evaluate_monitoring_window(observations(false_index=19))
    assert false_pass.breached
    assert false_pass.false_auto_pass_count == 1
    gated = evaluate_monitoring_window(observations(gate_index=0))
    assert gated.breached
    assert "publish_gate_violations" in gated.breach_reasons


def test_envelope_rejects_duplicate_events_and_artifact_mismatch(key_pair):
    private, _ = key_pair
    duplicate = observations()
    duplicate[-1]["source_event_id"] = duplicate[0]["source_event_id"]
    with pytest.raises(ValueError, match="source event IDs"):
        envelope(private, rows=duplicate)
    mismatch = observations()
    mismatch[-1]["artifact_digest"] = "b" * 64
    with pytest.raises(ValueError, match="artifact binding"):
        envelope(private, rows=mismatch)


def test_runtime_contract_needs_only_public_key(key_pair, monkeypatch):
    private, public = key_pair
    signed = envelope(private)
    monkeypatch.setenv("SCUDO_MONITORING_PUBLIC_KEY", public)
    monkeypatch.delenv("SCUDO_MONITORING_PRIVATE_KEY", raising=False)
    assert "SCUDO_MONITORING_PRIVATE_KEY" not in os.environ
    assert verify_signed_monitoring_envelope(
        SignedMonitoringEnvelope.model_validate_json(signed.model_dump_json()),
        public_key_pem=os.environ["SCUDO_MONITORING_PUBLIC_KEY"],
    )


def test_runtime_rejects_future_issued_envelope_beyond_skew_and_accepts_within_skew(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    rows = observations()
    monkeypatch.setattr(aurora_memory, "_read_memory_payload", lambda key: None)
    monkeypatch.setattr(
        aurora_memory, "persist_monitoring_outcome", lambda **kwargs: None
    )
    runtime = _runtime(public, rows)

    with pytest.raises(RuntimeError, match="issued in the future"):
        monitor_promotion_window(
            envelope=envelope(
                private,
                window_id="future-issued",
                rows=rows,
                issued_at=NOW + timedelta(minutes=4),
            ),
            context=runtime,
        )

    accepted = monitor_promotion_window(
        envelope=envelope(
            private,
            window_id="within-skew",
            rows=rows,
            issued_at=NOW + timedelta(seconds=30),
        ),
        context=runtime,
    )
    assert accepted.action == "retain"


def test_signature_binds_lifecycle_fields(key_pair):
    private, public = key_pair
    signed = envelope(private)
    for field, value in (
        ("audience", "other-audience"),
        ("deployment_id", "other-deployment"),
        ("key_id", "other-key"),
        ("issued_at", NOW + timedelta(seconds=1)),
        ("not_before", NOW),
        ("expires_at", NOW + timedelta(minutes=6)),
        ("observation_start", NOW - timedelta(hours=2)),
        ("observation_end", NOW - timedelta(seconds=1)),
    ):
        assert not verify_signed_monitoring_envelope(
            signed.model_copy(update={field: value}),
            public_key_pem=public,
        )


def test_monitoring_timestamps_must_be_aware_and_ordered(key_pair):
    private, _ = key_pair
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_signed_monitoring_envelope(
            window_id="naive",
            artifact_key=ARTIFACT_KEY,
            artifact_version=4,
            artifact_digest=ARTIFACT_DIGEST,
            pointer_sequence=4,
            observations=observations(),
            private_key_pem=private,
            audience="scudo-monitor",
            deployment_id="backend-test",
            key_id="monitor-key-1",
            issued_at=NOW.replace(tzinfo=None),
            not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
            observation_start=NOW - timedelta(hours=1),
            observation_end=NOW,
        )


def test_runtime_rejects_wrong_audience_expired_future_and_fabricated_source(
    key_pair,
):
    private, public = key_pair
    rows = observations()
    sources = {
        row["source_event_id"]: {
            key: value for key, value in row.items() if key != "source_record_digest"
        }
        for row in rows
    }

    def context(**updates):
        values = {
            "public_key_pem": public,
            "expected_audience": "scudo-monitor",
            "expected_deployment_id": "backend-test",
            "expected_key_id": "monitor-key-1",
            "source_resolver": sources.__getitem__,
            "clock": lambda: NOW,
        }
        values.update(updates)
        return MonitorContext(**values)

    with pytest.raises(RuntimeError, match="audience"):
        monitor_promotion_window(
            envelope=envelope(private),
            context=context(expected_audience="other"),
        )
    expired = issue_signed_monitoring_envelope(
        window_id="expired",
        artifact_key=ARTIFACT_KEY,
        artifact_version=4,
        artifact_digest=ARTIFACT_DIGEST,
        pointer_sequence=4,
        observations=[{**row, "observed_at": NOW - timedelta(hours=2)} for row in rows],
        private_key_pem=private,
        audience="scudo-monitor",
        deployment_id="backend-test",
        key_id="monitor-key-1",
        issued_at=NOW - timedelta(hours=1),
        not_before=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(minutes=1),
        observation_start=NOW - timedelta(hours=3),
        observation_end=NOW - timedelta(hours=1),
    )
    with pytest.raises(RuntimeError, match="expired"):
        monitor_promotion_window(envelope=expired, context=context())
    future = issue_signed_monitoring_envelope(
        window_id="future",
        artifact_key=ARTIFACT_KEY,
        artifact_version=4,
        artifact_digest=ARTIFACT_DIGEST,
        pointer_sequence=4,
        observations=[
            {**row, "observed_at": NOW - timedelta(minutes=30)} for row in rows
        ],
        private_key_pem=private,
        audience="scudo-monitor",
        deployment_id="backend-test",
        key_id="monitor-key-1",
        issued_at=NOW + timedelta(hours=1),
        not_before=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=2),
        observation_start=NOW - timedelta(hours=1),
        observation_end=NOW,
    )
    with pytest.raises(RuntimeError, match="issued in the future"):
        monitor_promotion_window(envelope=future, context=context())
    sources["event-0"] = {
        **sources["event-0"],
        "authoritative_outcome": {
            "target_iri": "fabricated",
            "abstain": False,
            "publish_gate_violations": [],
        },
    }
    with pytest.raises(RuntimeError, match="digest mismatch"):
        monitor_promotion_window(envelope=envelope(private), context=context())


def test_insufficient_and_zero_traffic_are_transient(key_pair, monkeypatch):
    private, public = key_pair
    persisted = []
    monkeypatch.setattr(
        "scudo.aurora_memory.persist_monitoring_outcome",
        lambda **kwargs: persisted.append(kwargs),
    )
    for count in (0, 19):
        rows = observations(count=count)
        sources = {
            row["source_event_id"]: {
                key: value
                for key, value in row.items()
                if key != "source_record_digest"
            }
            for row in rows
        }
        signed = envelope(private, window_id=f"transient-{count}", rows=rows)
        runtime = MonitorContext(
            public_key_pem=public,
            expected_audience="scudo-monitor",
            expected_deployment_id="backend-test",
            expected_key_id="monitor-key-1",
            source_resolver=sources.__getitem__,
            clock=lambda: NOW,
        )
        first = monitor_promotion_window(envelope=signed, context=runtime)
        second = monitor_promotion_window(envelope=signed, context=runtime)
        assert first == second
        assert first.action == "insufficient_samples"
        assert first.persisted is False
    assert persisted == []


@pytest.mark.parametrize(
    "failure_stage",
    [
        "window_claim",
        "event_claim",
        "pointer_lock",
        "rollback_sequence",
        "pointer_cas",
        "outcome_finalize",
        "commit",
    ],
)
def test_complete_breach_transaction_failures_leave_no_partial_monitor_state(
    key_pair,
    monkeypatch,
    failure_stage,
):
    private, public = key_pair
    outcome = _complete_outcome(
        monkeypatch,
        private,
        public,
        window_id=f"failure-{failure_stage}",
        breached=True,
    )
    client, current = _install_transaction_state(
        monkeypatch,
        fail_at=failure_stage,
    )

    with pytest.raises(RuntimeError):
        aurora_memory.persist_monitoring_outcome(
            outcome=outcome,
            signing_key="promotion",
        )

    pointer = client.rows["skill:matching:best"]["payload"]["pointer"]
    assert pointer == current.model_dump(mode="json")
    assert pointer["artifact_version"] == 4
    assert client.monitoring_rows() == {}
    assert client.rollbacks == 1
    assert client.commits == 0


def test_representative_later_event_claim_failure_rolls_back_all_claims(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    outcome = _complete_outcome(
        monkeypatch,
        private,
        public,
        window_id="later-event-failure",
        breached=True,
    )
    client, current = _install_transaction_state(monkeypatch, fail_at="event_claim")
    client.event_failure_index = 9

    with pytest.raises(RuntimeError, match="event claim"):
        aurora_memory.persist_monitoring_outcome(
            outcome=outcome,
            signing_key="promotion",
        )

    assert client.rows["skill:matching:best"]["payload"]["pointer"] == (
        current.model_dump(mode="json")
    )
    assert client.monitoring_rows() == {}
    assert client.rollbacks == 1


def test_complete_retain_pointer_change_at_transactional_lock_is_not_finalized(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    outcome = _complete_outcome(
        monkeypatch,
        private,
        public,
        window_id="retain-stale-pointer",
        breached=False,
    )
    client, current = _install_transaction_state(monkeypatch)
    changed = issue_live_pointer(
        artifact_key="skill:matching:artifact:5",
        artifact_version=5,
        artifact_digest="d" * 64,
        predecessor_version=4,
        predecessor_digest=ARTIFACT_DIGEST,
        sequence=5,
        signing_key="promotion",
    )
    client.rows["skill:matching:best"]["payload"]["pointer"] = changed.model_dump(
        mode="json"
    )

    with pytest.raises(RuntimeError, match="signed live pointer"):
        aurora_memory.persist_monitoring_outcome(
            outcome=outcome,
            signing_key="promotion",
        )

    assert client.rows["skill:matching:best"]["payload"]["pointer"] != (
        current.model_dump(mode="json")
    )
    assert client.monitoring_rows() == {}
    assert client.rollbacks == 1


def test_concurrent_exact_window_claim_conflict_rereads_identical_finalized_outcome(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    rows = observations()
    signed = envelope(private, window_id="concurrent-exact", rows=rows)
    runtime = _runtime(public, rows)
    stored: dict[str, dict] = {}
    reads = 0

    def read_payload(key: str):
        nonlocal reads
        reads += 1
        # Both calls precheck before either sees a finalized row. The conflict
        # path's reread then sees the first caller's committed result.
        if reads <= 2:
            return None
        return stored.get(key)

    def persist(*, outcome, signing_key=None):
        key = f"monitor:{outcome.window_id}"
        if key in stored:
            raise RuntimeError("transaction statement expected 1 updated row(s), got 0")
        stored[key] = {
            "status": "finalized",
            "input_digest": outcome.input_digest,
            "policy_digest": outcome.policy_digest,
            "outcome": outcome.model_dump(mode="json"),
        }

    monkeypatch.setattr(aurora_memory, "_read_memory_payload", read_payload)
    monkeypatch.setattr(aurora_memory, "persist_monitoring_outcome", persist)

    first = monitor_promotion_window(envelope=signed, context=runtime)
    second = monitor_promotion_window(envelope=signed, context=runtime)

    assert second == first
    assert second.persisted is True
    assert reads == 3


def test_cross_window_source_event_replay_is_rejected_atomically(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    rows = observations()
    outcome_a = _complete_outcome(
        monkeypatch,
        private,
        public,
        window_id="window-a",
        breached=False,
    )
    client, current = _install_transaction_state(monkeypatch)
    aurora_memory.persist_monitoring_outcome(
        outcome=outcome_a,
        signing_key="promotion",
    )
    snapshot = deepcopy(client.rows)
    outcome_b = outcome_a.model_copy(
        update={
            "window_id": "window-b",
            "input_digest": envelope(
                private,
                window_id="window-b",
                rows=rows,
            ).input_digest,
            "reason": "monitoring window window-b satisfied protected policy",
        }
    )

    with pytest.raises(RuntimeError, match="expected 1 updated row"):
        aurora_memory.persist_monitoring_outcome(
            outcome=outcome_b,
            signing_key="promotion",
        )

    assert client.rows == snapshot
    assert "monitor:window-b" not in client.rows
    assert client.rows["skill:matching:best"]["payload"]["pointer"] == (
        current.model_dump(mode="json")
    )


def test_conflicting_source_record_digest_rejects_before_transaction(
    key_pair,
    monkeypatch,
):
    private, public = key_pair
    rows = observations()
    runtime = _runtime(public, rows)
    signed = envelope(private, window_id="source-conflict", rows=rows)
    client, _ = _install_transaction_state(monkeypatch)
    sources = {
        row["source_event_id"]: {
            key: value for key, value in row.items() if key != "source_record_digest"
        }
        for row in rows
    }
    sources["event-0"]["authoritative_outcome"] = {
        "target_iri": "different",
        "abstain": False,
        "publish_gate_violations": [],
    }
    runtime = MonitorContext(
        public_key_pem=runtime.public_key_pem,
        expected_audience=runtime.expected_audience,
        expected_deployment_id=runtime.expected_deployment_id,
        expected_key_id=runtime.expected_key_id,
        source_resolver=sources.__getitem__,
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="digest mismatch"):
        monitor_promotion_window(envelope=signed, context=runtime)

    assert client.begins == 0
    assert client.monitoring_rows() == {}
