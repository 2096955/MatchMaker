"""Strict Opus seam and the batch-level circuit-breaker contract.

WHY THIS FILE EXISTS
    `opus_dense_score()` decides per candidate whether to fall back to
    Jaro-Winkler. That is right for its legacy/specialist callers, but wrong
    for retrieval scoring, where a mixed list ranks two incomparable scales
    against each other and lets thread timing move the published band.

    So the batch path needs two things this file pins:
      1. a STRICT scorer that returns a model score or raises — never
         substituting a fallback of its own, and
      2. a breaker contract the batch can consult ONCE and report to ONCE,
         instead of every worker mutating breaker globals independently.
"""

from __future__ import annotations

import threading
import time

import pytest

from scudo_mapping_mcp import opus_dense


@pytest.fixture(autouse=True)
def _reset_breaker_state():
    """Snapshot and restore the module's breaker globals.

    These are process-local by design, so without this a test that trips the
    breaker silently changes the arm every later test runs on — the same class
    of leak that made a store test fail only inside the full suite.
    """
    saved = (
        opus_dense._breaker_failures,
        opus_dense._breaker_opened_at,
        opus_dense._breaker_probe_inflight,
        opus_dense._breaker_probe_started_at,
        opus_dense._breaker_generation,
        opus_dense._breaker_probe_owner,
    )
    yield
    (
        opus_dense._breaker_failures,
        opus_dense._breaker_opened_at,
        opus_dense._breaker_probe_inflight,
        opus_dense._breaker_probe_started_at,
        opus_dense._breaker_generation,
        opus_dense._breaker_probe_owner,
    ) = saved


# ── the strict seam ────────────────────────────────────────────────────────


def test_strict_seam_returns_the_clamped_model_score(monkeypatch):
    monkeypatch.setattr(opus_dense, "_opus_invoke_score", lambda *a, **k: 1.4)
    assert opus_dense.opus_dense_score_strict("a", "b", "c", "d") == 1.0

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", lambda *a, **k: 0.42)
    assert opus_dense.opus_dense_score_strict("a", "b", "c", "d") == 0.42


def test_strict_seam_raises_even_when_fallback_is_enabled(monkeypatch):
    """The whole point: the strict seam must NOT make its own fallback call.

    With SCUDO_DENSE_FALLBACK=1 the non-strict scorer would quietly return a
    Jaro-Winkler value here. The batch caller has to see the failure so it can
    discard the entire batch.
    """
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")

    def boom(*_a, **_k):
        raise RuntimeError("bedrock said no")

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", boom)
    with pytest.raises(RuntimeError):
        opus_dense.opus_dense_score_strict("a", "b", "c", "d")


def test_strict_seam_never_calls_the_jaro_fallback(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    called = []

    def boom(*_a, **_k):
        raise RuntimeError("bedrock said no")

    monkeypatch.setattr(opus_dense, "_opus_invoke_score", boom)
    monkeypatch.setattr(
        opus_dense,
        "_jaro_winkler_score",
        lambda *a, **k: called.append(True) or 0.5,
    )
    with pytest.raises(RuntimeError):
        opus_dense.opus_dense_score_strict("a", "b", "c", "d")
    assert called == []


# ── the batch decision contract ────────────────────────────────────────────


def test_closed_breaker_permits_an_opus_batch(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    opus_dense._breaker_failures = 0
    assert opus_dense.begin_dense_batch().attempt_opus is True


def test_open_breaker_inside_cooldown_refuses_the_batch(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    decision = opus_dense.begin_dense_batch()
    opus_dense.record_dense_batch_failure(decision)
    opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    assert opus_dense.begin_dense_batch().attempt_opus is False


def test_open_breaker_after_cooldown_permits_one_half_open_batch(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    assert opus_dense.begin_dense_batch().attempt_opus is True


def test_concurrent_callers_receive_exactly_one_probe_permission(monkeypatch):
    """Without a lock every worker probes at once and a dead key costs the
    full serial penalty again — the very thing the breaker exists to avoid."""
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())

    permissions: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def probe():
        start.wait()
        decision = opus_dense.begin_dense_batch()
        with lock:
            permissions.append(decision.attempt_opus)

    threads = [threading.Thread(target=probe) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert permissions.count(True) == 1


def test_successful_batch_resets_the_breaker(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    opus_dense.record_dense_batch_success(opus_dense.begin_dense_batch())
    assert opus_dense._breaker_failures == 0
    assert opus_dense.begin_dense_batch().attempt_opus is True


def test_failed_batch_records_one_failure_not_one_per_candidate(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    before = opus_dense._breaker_failures
    opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    assert opus_dense._breaker_failures == before + 1


def test_fallback_disabled_open_breaker_refuses_with_circuit_reason(monkeypatch):
    """Fail-loud mode still uses the breaker; refusal must never imply Jaro."""
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    refused = opus_dense.begin_dense_batch()
    assert refused.attempt_opus is False
    assert refused.refusal_reason == "circuit_open"


def test_fallback_disabled_allows_exactly_one_probe_after_cooldown(monkeypatch):
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())

    probe = opus_dense.begin_dense_batch()
    refused = opus_dense.begin_dense_batch()

    assert probe.attempt_opus is True
    assert probe.probe is True
    assert refused.attempt_opus is False
    assert refused.refusal_reason == "probe_inflight"


def test_fallback_disabled_probe_success_closes_and_failure_reopens(monkeypatch):
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())

    successful_probe = opus_dense.begin_dense_batch()
    opus_dense.record_dense_batch_success(successful_probe)
    assert opus_dense._breaker_failures == 0
    assert opus_dense.begin_dense_batch().probe is False

    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    failed_probe = opus_dense.begin_dense_batch()
    opus_dense.record_dense_batch_failure(failed_probe)

    assert opus_dense._breaker_failures >= opus_dense._BREAKER_THRESHOLD
    assert opus_dense._breaker_probe_inflight is False


def test_dense_arm_status_still_reports_degradation(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    status = opus_dense.dense_arm_status()
    assert status["degraded"] is True
    assert status["effective"] == "jaro_winkler"
    assert status["configured"] == "opus"


def test_dense_arm_status_never_claims_jaro_when_fallback_is_off(monkeypatch):
    monkeypatch.setenv("SCUDO_DENSE_BACKEND", "opus")
    monkeypatch.delenv("SCUDO_DENSE_FALLBACK", raising=False)
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "9999")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())

    status = opus_dense.dense_arm_status()

    assert status["degraded"] is True
    assert status["effective"] == "circuit_open"


def test_an_abandoned_probe_does_not_block_recovery_for_ever(monkeypatch):
    """A probe that never reports back must not pin the breaker open.

    Regression: the fallback-disabled re-raise path (and any crashed worker)
    left _breaker_probe_inflight True, and every later batch was refused for
    the life of the process — verified before the guard existed.
    """
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    monkeypatch.setattr(opus_dense, "_PROBE_ABANDON_S", 0.01)
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())

    assert opus_dense.begin_dense_batch().probe is True  # granted, never reported
    assert opus_dense.begin_dense_batch().attempt_opus is False  # still in flight
    time.sleep(0.05)
    assert opus_dense.begin_dense_batch().attempt_opus is True  # abandoned


def _open_breaker(monkeypatch) -> None:
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())


@pytest.mark.parametrize("report", ["success", "failure"])
def test_abandoned_probe_cannot_mutate_its_successor(monkeypatch, report):
    """A late result belongs to its probe generation, not the current probe."""
    _open_breaker(monkeypatch)
    monkeypatch.setattr(opus_dense, "_PROBE_ABANDON_S", 1.0)
    stale = opus_dense.begin_dense_batch()
    opus_dense._breaker_probe_started_at -= 2.0
    successor = opus_dense.begin_dense_batch()
    failures_before = opus_dense._breaker_failures

    getattr(opus_dense, f"record_dense_batch_{report}")(stale)

    assert successor.probe is True
    assert successor.probe_token != stale.probe_token
    assert opus_dense._breaker_probe_inflight is True
    assert opus_dense._breaker_probe_owner == successor.probe_token
    assert opus_dense._breaker_failures == failures_before


@pytest.mark.parametrize("report", ["success", "failure"])
def test_stale_closed_batch_cannot_mutate_open_probe(monkeypatch, report):
    """A batch admitted while closed cannot clear or reopen a later probe."""
    monkeypatch.setenv("SCUDO_DENSE_FALLBACK", "1")
    monkeypatch.setenv("SCUDO_BEDROCK_BREAKER_COOLDOWN_S", "0")
    stale_closed = opus_dense.begin_dense_batch()
    for _ in range(opus_dense._BREAKER_THRESHOLD):
        opus_dense.record_dense_batch_failure(opus_dense.begin_dense_batch())
    probe = opus_dense.begin_dense_batch()
    failures_before = opus_dense._breaker_failures

    getattr(opus_dense, f"record_dense_batch_{report}")(stale_closed)

    assert probe.probe is True
    assert opus_dense._breaker_probe_inflight is True
    assert opus_dense._breaker_probe_owner == probe.probe_token
    assert opus_dense._breaker_failures == failures_before
