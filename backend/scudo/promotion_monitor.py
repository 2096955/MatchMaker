"""Signed offline post-promotion monitor; invoke from an external scheduler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import aurora_memory
from .matching_self_improvement import (
    MonitoringOutcome,
    MonitoringPolicy,
    MonitoringObservation,
    SignedMonitoringEnvelope,
    evaluate_monitoring_window,
    monitoring_source_record_digest,
    verify_signed_monitoring_envelope,
)

_MONITOR_PREFIX = "monitor:"
_SOURCE_PREFIX = "monitoring-source:"

SourceResolver = Callable[[str], dict]


@dataclass(frozen=True)
class MonitorContext:
    public_key_pem: str
    expected_audience: str
    expected_deployment_id: str
    expected_key_id: str
    source_resolver: SourceResolver
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    max_clock_skew: timedelta = timedelta(seconds=30)


def _monitor_key(window_id: str) -> str:
    if not window_id or not all(
        char.isalnum() or char in {"-", "_"} for char in window_id
    ):
        raise ValueError("window_id must be a strict slug")
    return f"{_MONITOR_PREFIX}{window_id}"


def _read_outcome(window_id: str) -> Optional[MonitoringOutcome]:
    payload = aurora_memory._read_memory_payload(_monitor_key(window_id))
    if not payload or payload.get("status") != "finalized":
        return None
    return MonitoringOutcome.model_validate(payload["outcome"])


def _default_source_resolver(source_event_id: str) -> dict:
    payload = aurora_memory._read_memory_payload(f"{_SOURCE_PREFIX}{source_event_id}")
    if payload is None:
        payload = aurora_memory._read_memory_payload(f"trajectory:{source_event_id}")
    if payload is None:
        raise RuntimeError(f"immutable monitoring source not found: {source_event_id}")
    if payload.get("immutable") is not True:
        raise RuntimeError("monitoring source record is not immutable")
    return payload.get("record", payload)


def _context_from_environment() -> MonitorContext:
    required = {
        "public_key_pem": os.environ.get("SCUDO_MONITORING_PUBLIC_KEY"),
        "expected_audience": os.environ.get("SCUDO_MONITORING_AUDIENCE"),
        "expected_deployment_id": os.environ.get("SCUDO_MONITORING_DEPLOYMENT_ID"),
        "expected_key_id": os.environ.get("SCUDO_MONITORING_KEY_ID"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("missing monitoring configuration: " + ",".join(missing))
    return MonitorContext(**required, source_resolver=_default_source_resolver)


def _verify_envelope(
    envelope: SignedMonitoringEnvelope,
    context: MonitorContext,
) -> tuple[MonitoringObservation, ...]:
    if not verify_signed_monitoring_envelope(
        envelope, public_key_pem=context.public_key_pem
    ):
        raise RuntimeError("invalid signed monitoring envelope")
    if envelope.audience != context.expected_audience:
        raise RuntimeError("monitoring envelope audience mismatch")
    if envelope.deployment_id != context.expected_deployment_id:
        raise RuntimeError("monitoring envelope deployment mismatch")
    if envelope.key_id != context.expected_key_id:
        raise RuntimeError("monitoring envelope key ID mismatch")
    now = context.clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("monitor clock must return a timezone-aware datetime")
    if envelope.issued_at > now + context.max_clock_skew:
        raise RuntimeError("monitoring envelope was issued in the future")
    if not (envelope.observation_end <= envelope.issued_at <= envelope.expires_at):
        raise RuntimeError("monitoring envelope temporal ordering is invalid")
    if now + context.max_clock_skew < envelope.not_before:
        raise RuntimeError("monitoring envelope is not yet active")
    if now - context.max_clock_skew > envelope.expires_at:
        raise RuntimeError("monitoring envelope has expired")
    resolved: list[MonitoringObservation] = []
    for observation in envelope.observations:
        record = context.source_resolver(observation.source_event_id)
        digest = monitoring_source_record_digest(record)
        if digest != observation.source_record_digest:
            raise RuntimeError("monitoring source record digest mismatch")
        authoritative = MonitoringObservation.model_validate(
            {**record, "source_record_digest": digest}
        )
        if authoritative != observation:
            raise RuntimeError("monitoring observation does not match source record")
        resolved.append(authoritative)
    return tuple(resolved)


def monitor_promotion_window(
    *,
    envelope: SignedMonitoringEnvelope | dict,
    signing_key: Optional[str] = None,
    context: Optional[MonitorContext] = None,
) -> MonitoringOutcome:
    """Verify authority evidence and atomically decide one immutable window."""

    validated = (
        envelope
        if isinstance(envelope, SignedMonitoringEnvelope)
        else SignedMonitoringEnvelope.model_validate(envelope)
    )
    runtime = context or _context_from_environment()
    observations = _verify_envelope(validated, runtime)
    policy = MonitoringPolicy()
    evaluation = evaluate_monitoring_window(observations)
    if not evaluation.sufficient_samples:
        action = "insufficient_samples"
        reason = (
            f"monitoring window {validated.window_id} has "
            f"{evaluation.sample_count} total and {evaluation.auto_pass_count} "
            "auto-pass samples; minimums are 20 and 20"
        )
        rollback_succeeded = False
    elif evaluation.breached:
        action = "rollback"
        reason = f"automatic monitoring breach in {validated.window_id}: " + ",".join(
            evaluation.breach_reasons
        )
        rollback_succeeded = True
    else:
        action = "retain"
        reason = f"monitoring window {validated.window_id} satisfied protected policy"
        rollback_succeeded = False
    outcome = MonitoringOutcome(
        window_id=validated.window_id,
        artifact_key=validated.artifact_key,
        artifact_version=validated.artifact_version,
        artifact_digest=validated.artifact_digest,
        pointer_sequence=validated.pointer_sequence,
        input_digest=validated.input_digest,
        policy_digest=validated.policy_digest,
        observations=observations,
        policy=policy,
        evaluation=evaluation,
        action=action,
        persisted=action != "insufficient_samples",
        rollback_succeeded=rollback_succeeded,
        reason=reason,
    )
    if not outcome.persisted:
        return outcome
    existing = _read_outcome(validated.window_id)
    if existing is not None:
        if existing.input_digest != validated.input_digest:
            raise RuntimeError("monitoring window ID already has different input")
        return existing
    for attempt in range(2):
        try:
            aurora_memory.persist_monitoring_outcome(
                outcome=outcome,
                signing_key=signing_key,
            )
            return outcome
        except RuntimeError:
            finalized = _read_outcome(validated.window_id)
            if finalized is not None:
                if finalized.input_digest != validated.input_digest:
                    raise RuntimeError(
                        "monitoring window ID already has different input"
                    )
                return finalized
            if attempt:
                raise
    raise RuntimeError("monitoring transaction did not finalize")
