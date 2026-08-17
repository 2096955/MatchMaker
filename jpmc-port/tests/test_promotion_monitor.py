from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scudo import local_state
from scudo.matching_self_improvement import (
    issue_signed_monitoring_envelope,
    issue_live_pointer,
    monitoring_source_record_digest,
)
from scudo.promotion_monitor import MonitorContext, monitor_promotion_window

NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
CONTEXT: MonitorContext | None = None


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


def observations(digest: str, *, false_index=None, gate_index=None, prefix="event"):
    rows = []
    for index in range(20):
        record = {
            "source_event_id": f"{prefix}-{index}",
            "observed_at": NOW - timedelta(minutes=30),
            "artifact_key": "skill:matching:artifact:2",
            "artifact_version": 2,
            "artifact_digest": digest,
            "pointer_sequence": 2,
            "prediction": {
                "target_iri": "target",
                "confidence": 0.95,
                "status": "auto_mapped",
                "auto_pass": True,
            },
            "authoritative_outcome": {
                "target_iri": "wrong" if index == false_index else "target",
                "publish_gate_violations": (
                    ["vendor_iri"] if index == gate_index else []
                ),
            },
        }
        rows.append(
            {**record, "source_record_digest": monitoring_source_record_digest(record)}
        )
    return rows


def signed(
    private: str,
    digest: str,
    window: str,
    *,
    rows=None,
    issued_at: datetime = NOW,
):
    source_rows = observations(digest) if rows is None else rows
    for row in source_rows:
        local_state.MEMORY.setdefault(
            f"monitoring-source:{row['source_event_id']}",
            {
                "memory_type": "monitoring_source",
                "payload": {
                    "immutable": True,
                    "record": {
                        key: value
                        for key, value in row.items()
                        if key != "source_record_digest"
                    },
                },
            },
        )
    return issue_signed_monitoring_envelope(
        window_id=window,
        artifact_key="skill:matching:artifact:2",
        artifact_version=2,
        artifact_digest=digest,
        pointer_sequence=2,
        observations=source_rows,
        private_key_pem=private,
        audience="scudo-monitor",
        deployment_id="jpmc-local",
        key_id="monitor-key-1",
        issued_at=issued_at,
        not_before=NOW - timedelta(minutes=1),
        expires_at=issued_at + timedelta(minutes=5),
        observation_start=NOW - timedelta(hours=1),
        observation_end=issued_at,
    )


def monitor(envelope, *, signing_key="promotion"):
    assert CONTEXT is not None
    return monitor_promotion_window(
        envelope=envelope,
        signing_key=signing_key,
        context=CONTEXT,
    )


@pytest.fixture
def monitor_state(monkeypatch):
    global CONTEXT
    private, public = key_pair()
    monkeypatch.setenv("SCUDO_MONITORING_PUBLIC_KEY", public)
    monkeypatch.setenv("SCUDO_MONITORING_AUDIENCE", "scudo-monitor")
    monkeypatch.setenv("SCUDO_MONITORING_DEPLOYMENT_ID", "jpmc-local")
    monkeypatch.setenv("SCUDO_MONITORING_KEY_ID", "monitor-key-1")
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion")
    pointer = issue_live_pointer(
        artifact_key="skill:matching:artifact:2",
        artifact_version=2,
        artifact_digest="a" * 64,
        predecessor_version=1,
        predecessor_digest="b" * 64,
        sequence=2,
        signing_key="promotion",
    )
    monkeypatch.setattr(
        "scudo.aurora_memory._resolve_verified_artifact_from_memory",
        lambda memory, signing_key=None: (pointer, object()),
    )
    monkeypatch.setattr(
        "scudo.aurora_memory._apply_protected_rollback_to_memory",
        lambda memory, **kwargs: memory.update({"rolled-back": {"payload": kwargs}}),
    )
    local_state.reset()
    local_state.MEMORY["skill:matching:best"] = {
        "payload": {"pointer": pointer.model_dump(mode="json")}
    }
    CONTEXT = MonitorContext(
        public_key_pem=public,
        expected_audience="scudo-monitor",
        expected_deployment_id="jpmc-local",
        expected_key_id="monitor-key-1",
        source_resolver=lambda event_id: local_state.MEMORY[
            f"monitoring-source:{event_id}"
        ]["payload"]["record"],
        clock=lambda: NOW,
    )
    return private


def test_jpmc_retain_duplicate_replay_and_conflict(monitor_state):
    envelope = signed(monitor_state, "a" * 64, "safe")
    retained = monitor(envelope)
    assert retained.action == "retain"
    snapshot = deepcopy(local_state.MEMORY)
    assert monitor(envelope) == retained
    assert local_state.MEMORY == snapshot
    replay = signed(
        monitor_state,
        "a" * 64,
        "another-window",
        rows=observations("a" * 64),
    )
    with pytest.raises(RuntimeError, match="already claimed"):
        monitor(replay)
    conflicting = signed(
        monitor_state,
        "a" * 64,
        "safe",
        rows=observations("a" * 64, false_index=0, prefix="conflict"),
    )
    with pytest.raises(RuntimeError, match="different input"):
        monitor(conflicting)


def test_jpmc_rejects_future_issued_envelope_beyond_skew_and_accepts_within_skew(
    monitor_state,
):
    with pytest.raises(RuntimeError, match="issued in the future"):
        monitor(
            signed(
                monitor_state,
                "a" * 64,
                "future-issued",
                issued_at=NOW + timedelta(minutes=4),
            )
        )

    accepted = monitor(
        signed(
            monitor_state,
            "a" * 64,
            "within-skew",
            issued_at=NOW + timedelta(seconds=30),
        )
    )
    assert accepted.action == "retain"


@pytest.mark.parametrize("kind", ["false", "gate"])
def test_jpmc_breach_rolls_back_atomically(monitor_state, kind):
    rows = observations(
        "a" * 64,
        false_index=0 if kind == "false" else None,
        gate_index=0 if kind == "gate" else None,
        prefix=kind,
    )
    outcome = monitor(signed(monitor_state, "a" * 64, kind, rows=rows))
    assert outcome.action == "rollback"
    assert outcome.rollback_succeeded
    assert "rolled-back" in local_state.MEMORY


def test_jpmc_invalid_signature_artifact_and_transaction_failure(monitor_state):
    valid = signed(monitor_state, "a" * 64, "invalid")
    with pytest.raises(RuntimeError, match="invalid signed"):
        monitor(valid.model_copy(update={"signature": "bad"}))
    with pytest.raises(RuntimeError, match="live pointer"):
        monitor(
            signed(
                monitor_state,
                "c" * 64,
                "artifact",
                rows=observations("c" * 64, prefix="other"),
            )
        )
    before = deepcopy(local_state.MEMORY)
    with pytest.raises(RuntimeError):
        local_state.atomic_memory_update(
            lambda memory: (
                memory.update({"partial": {}}),
                (_ for _ in ()).throw(RuntimeError("failure")),
            )
        )
    assert local_state.MEMORY == before
