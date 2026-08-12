from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from scudo import aurora_memory
from scudo.matching_self_improvement import (
    issue_live_pointer,
    issue_signed_monitoring_envelope,
    monitoring_source_record_digest,
)
from scudo.promotion_monitor import MonitorContext, monitor_promotion_window
from scudo.tests.test_promotion_monitor import (
    ARTIFACT_DIGEST,
    ARTIFACT_KEY,
    StatefulMonitoringDataApi,
    _install_transaction_state,
)


NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
JPMC_ROOT = Path(__file__).parents[3] / "jpmc-port" / "scudo"


def _jpmc_modules():
    package_name = "_monitor_parity_jpmc"
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            JPMC_ROOT / "__init__.py",
            submodule_search_locations=[str(JPMC_ROOT)],
        )
        assert spec and spec.loader
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return (
        __import__(f"{package_name}.local_state", fromlist=["local_state"]),
        __import__(f"{package_name}.aurora_memory", fromlist=["aurora_memory"]),
        __import__(f"{package_name}.promotion_monitor", fromlist=["promotion_monitor"]),
    )


@pytest.fixture
def signed_corpus():
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

    def build(window_id: str, *, count=20, breach=False, prefix="event"):
        rows = []
        for index in range(count):
            record = {
                "source_event_id": f"{prefix}-{index}",
                "observed_at": NOW - timedelta(minutes=30),
                "artifact_key": ARTIFACT_KEY,
                "artifact_version": 4,
                "artifact_digest": ARTIFACT_DIGEST,
                "pointer_sequence": 4,
                "prediction": {
                    "target_iri": "target",
                    "confidence": 0.95,
                    "status": "auto_mapped",
                    "auto_pass": True,
                    "abstained": False,
                },
                "authoritative_outcome": {
                    "target_iri": "wrong"
                    if breach and index == count - 1
                    else "target",
                    "abstain": False,
                    "publish_gate_violations": [],
                },
            }
            rows.append(
                {
                    **record,
                    "source_record_digest": monitoring_source_record_digest(record),
                }
            )
        envelope = issue_signed_monitoring_envelope(
            window_id=window_id,
            artifact_key=ARTIFACT_KEY,
            artifact_version=4,
            artifact_digest=ARTIFACT_DIGEST,
            pointer_sequence=4,
            observations=rows,
            private_key_pem=private_pem,
            audience="scudo-monitor",
            deployment_id="parity",
            key_id="monitor-key-1",
            issued_at=NOW,
            not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
            observation_start=NOW - timedelta(hours=1),
            observation_end=NOW,
        )
        sources = {
            row["source_event_id"]: {
                key: value
                for key, value in row.items()
                if key != "source_record_digest"
            }
            for row in rows
        }
        return envelope, sources

    return public_pem, build


def _normalized(outcome):
    return {
        "metrics": outcome.evaluation.model_dump(mode="json"),
        "action": outcome.action,
        "persisted": outcome.persisted,
    }


def _backend_run(monkeypatch, public_pem, envelope, sources, *, duplicate=False):
    if envelope.observations:
        client, _ = _install_transaction_state(monkeypatch)
    else:
        current = issue_live_pointer(
            artifact_key=ARTIFACT_KEY,
            artifact_version=4,
            artifact_digest=ARTIFACT_DIGEST,
            predecessor_version=3,
            predecessor_digest="b" * 64,
            sequence=4,
            signing_key="promotion",
        )
        client = StatefulMonitoringDataApi(current.model_dump(mode="json"))
    monkeypatch.setattr(
        aurora_memory,
        "_read_memory_payload",
        lambda key: (client.rows.get(key, {}).get("payload")),
    )
    context = MonitorContext(
        public_key_pem=public_pem,
        expected_audience="scudo-monitor",
        expected_deployment_id="parity",
        expected_key_id="monitor-key-1",
        source_resolver=sources.__getitem__,
        clock=lambda: NOW,
    )
    first = monitor_promotion_window(
        envelope=envelope,
        signing_key="promotion",
        context=context,
    )
    result = (
        monitor_promotion_window(
            envelope=envelope,
            signing_key="promotion",
            context=context,
        )
        if duplicate and first.persisted
        else first
    )
    pointer = client.rows["skill:matching:best"]["payload"]["pointer"]
    return result, pointer["artifact_version"], pointer["sequence"], client


def _jpmc_run(monkeypatch, public_pem, envelope, sources, *, duplicate=False):
    local_state, jpmc_memory, jpmc_monitor = _jpmc_modules()
    local_state.reset()
    current = issue_live_pointer(
        artifact_key=ARTIFACT_KEY,
        artifact_version=4,
        artifact_digest=ARTIFACT_DIGEST,
        predecessor_version=3,
        predecessor_digest="b" * 64,
        sequence=4,
        signing_key="promotion",
    )
    local_state.MEMORY["skill:matching:best"] = {
        "payload": {"pointer": current.model_dump(mode="json")}
    }
    monkeypatch.setattr(
        jpmc_memory,
        "_resolve_verified_artifact_from_memory",
        lambda memory, signing_key=None: (
            type(current).model_validate(
                memory["skill:matching:best"]["payload"]["pointer"]
            ),
            object(),
        ),
    )

    def rollback(memory, **kwargs):
        rolled_back = issue_live_pointer(
            artifact_key="skill:matching:artifact:3",
            artifact_version=3,
            artifact_digest="b" * 64,
            predecessor_version=2,
            predecessor_digest="c" * 64,
            sequence=5,
            transition_kind="rollback",
            signing_key="promotion",
        )
        memory["skill:matching:promotion:5"] = {
            "payload": {"committed_pointer": rolled_back.model_dump(mode="json")}
        }
        memory["skill:matching:best"] = {
            "payload": {"pointer": rolled_back.model_dump(mode="json")}
        }

    monkeypatch.setattr(jpmc_memory, "_apply_protected_rollback_to_memory", rollback)
    context = jpmc_monitor.MonitorContext(
        public_key_pem=public_pem,
        expected_audience="scudo-monitor",
        expected_deployment_id="parity",
        expected_key_id="monitor-key-1",
        source_resolver=sources.__getitem__,
        clock=lambda: NOW,
    )
    first = jpmc_monitor.monitor_promotion_window(
        envelope=envelope.model_dump(mode="json"),
        signing_key="promotion",
        context=context,
    )
    result = (
        jpmc_monitor.monitor_promotion_window(
            envelope=envelope.model_dump(mode="json"),
            signing_key="promotion",
            context=context,
        )
        if duplicate and first.persisted
        else first
    )
    pointer = local_state.MEMORY["skill:matching:best"]["payload"]["pointer"]
    return (
        result,
        pointer["artifact_version"],
        pointer["sequence"],
        deepcopy(local_state.MEMORY),
    )


@pytest.mark.parametrize(
    ("case", "count", "breach", "duplicate"),
    [
        ("insufficient_transient", 19, False, False),
        ("safe_retain", 20, False, False),
        ("breach_rollback", 20, True, False),
        ("exact_duplicate", 20, False, True),
    ],
)
def test_backend_and_jpmc_monitor_behavioral_parity(
    monkeypatch,
    signed_corpus,
    case,
    count,
    breach,
    duplicate,
):
    public_pem, build = signed_corpus
    envelope, sources = build(case, count=count, breach=breach, prefix=case)
    backend = _backend_run(
        monkeypatch, public_pem, envelope, sources, duplicate=duplicate
    )
    jpmc = _jpmc_run(monkeypatch, public_pem, envelope, sources, duplicate=duplicate)

    assert _normalized(backend[0]) == _normalized(jpmc[0])
    assert backend[1:3] == jpmc[1:3]


def test_backend_and_jpmc_monitor_conflict_parity(
    monkeypatch,
    signed_corpus,
):
    public_pem, build = signed_corpus
    original, original_sources = build("conflict", prefix="original")
    conflicting, conflicting_sources = build("conflict", breach=True, prefix="changed")
    backend_first = _backend_run(monkeypatch, public_pem, original, original_sources)
    backend_client = backend_first[3]
    monkeypatch.setattr(
        aurora_memory,
        "_read_memory_payload",
        lambda key: (backend_client.rows.get(key, {}).get("payload")),
    )
    backend_context = MonitorContext(
        public_key_pem=public_pem,
        expected_audience="scudo-monitor",
        expected_deployment_id="parity",
        expected_key_id="monitor-key-1",
        source_resolver=conflicting_sources.__getitem__,
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="different input"):
        monitor_promotion_window(
            envelope=conflicting,
            signing_key="promotion",
            context=backend_context,
        )

    _jpmc_run(monkeypatch, public_pem, original, original_sources)
    local_state, _, jpmc_monitor = _jpmc_modules()
    jpmc_context = jpmc_monitor.MonitorContext(
        public_key_pem=public_pem,
        expected_audience="scudo-monitor",
        expected_deployment_id="parity",
        expected_key_id="monitor-key-1",
        source_resolver=conflicting_sources.__getitem__,
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="different input"):
        jpmc_monitor.monitor_promotion_window(
            envelope=conflicting.model_dump(mode="json"),
            signing_key="promotion",
            context=jpmc_context,
        )

    backend_pointer = backend_client.rows["skill:matching:best"]["payload"]["pointer"]
    jpmc_pointer = local_state.MEMORY["skill:matching:best"]["payload"]["pointer"]
    assert (
        (
            backend_pointer["artifact_version"],
            backend_pointer["sequence"],
        )
        == (
            jpmc_pointer["artifact_version"],
            jpmc_pointer["sequence"],
        )
        == (4, 4)
    )
