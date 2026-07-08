"""Safety/env-validation tests for the sleep-cycle scheduler/job wrapper.

The job calls run_sleep_cycle against REAL aurora_memory - these tests pin
the safety contract (env validation, dry-run store wrapping) WITHOUT any
real AWS credentials or network call, following the same pattern as
test_cleanup_stale_cdao.py (test the pure/testable pieces directly, never
main() itself for an infra-touching script).
"""

from __future__ import annotations

from scudo.scripts.run_sleep_cycle_job import (
    _env_validation_errors,
    _make_dry_run_store,
    _parse_args,
)


def _args(*flags):
    return _parse_args(["run_sleep_cycle_job", *flags])


def test_no_flags_defaults_to_dry_run():
    """A bare invocation must NOT write to Aurora — dry-run is the safe
    default, same convention as cleanup_stale_cdao.py."""
    args = _args()
    assert args.dry_run is True


def test_apply_flag_disables_dry_run():
    args = _args("--apply")
    assert args.dry_run is False


def test_explicit_dry_run_flag():
    args = _args("--dry-run")
    assert args.dry_run is True


def test_held_out_ratio_defaults_and_is_overridable():
    assert _args().held_out_ratio == 0.2
    assert _args("--held-out-ratio", "0.3").held_out_ratio == 0.3


def test_env_validation_reports_all_missing_vars(monkeypatch):
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    monkeypatch.delenv("SCUDO_AURORA_SECRET_ARN", raising=False)
    monkeypatch.delenv("SCUDO_AURORA_DATABASE_NAME", raising=False)

    errors = _env_validation_errors()
    assert "SCUDO_AURORA_CLUSTER_ARN" in errors
    assert "SCUDO_AURORA_SECRET_ARN" in errors
    assert "SCUDO_AURORA_DATABASE_NAME" in errors


def test_env_validation_passes_when_all_vars_present(monkeypatch):
    monkeypatch.setenv("SCUDO_AURORA_CLUSTER_ARN", "arn:aws:rds:...")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "arn:aws:secretsmanager:...")
    monkeypatch.setenv("SCUDO_AURORA_DATABASE_NAME", "scudo")

    assert _env_validation_errors() == []


def test_env_validation_reports_partial_missing(monkeypatch):
    monkeypatch.setenv("SCUDO_AURORA_CLUSTER_ARN", "arn:aws:rds:...")
    monkeypatch.delenv("SCUDO_AURORA_SECRET_ARN", raising=False)
    monkeypatch.setenv("SCUDO_AURORA_DATABASE_NAME", "scudo")

    assert _env_validation_errors() == ["SCUDO_AURORA_SECRET_ARN"]


class _FakeRealStore:
    """Stands in for the real aurora_memory module - no Aurora/network."""

    def __init__(self, *, current_best=None, harvested=None):
        self._current_best = current_best
        self._harvested = harvested or []
        self.promote_skill_calls = []  # must stay empty in dry-run mode

    def harvest_trajectories(self):
        return self._harvested

    def consult_best_skill(self):
        return self._current_best

    def promote_skill(self, *, skill_text, validation_score, version):
        self.promote_skill_calls.append(
            {
                "skill_text": skill_text,
                "validation_score": validation_score,
                "version": version,
            }
        )
        return True


def test_dry_run_store_never_calls_real_promote_skill():
    """The whole point of dry-run: reads pass through, the write never
    happens, even if the gate WOULD have accepted the candidate."""
    real_store = _FakeRealStore(current_best=None)
    dry_store = _make_dry_run_store(real_store)

    result = dry_store.promote_skill(
        skill_text="candidate", validation_score=0.9, version=1
    )

    assert result is True  # reports "would have promoted"
    assert real_store.promote_skill_calls == []  # but never actually wrote


def test_dry_run_store_reports_false_when_gate_would_reject():
    real_store = _FakeRealStore(current_best={"validation_score": 0.95, "version": 3})
    dry_store = _make_dry_run_store(real_store)

    result = dry_store.promote_skill(
        skill_text="weaker", validation_score=0.5, version=4
    )

    assert result is False
    assert real_store.promote_skill_calls == []


def test_dry_run_store_passes_through_reads():
    real_store = _FakeRealStore(harvested=[{"vendor": "LSEG"}])
    dry_store = _make_dry_run_store(real_store)

    assert dry_store.harvest_trajectories() == [{"vendor": "LSEG"}]
    assert dry_store.consult_best_skill() is None


def test_job_script_is_never_imported_by_lambda_handler():
    """Same live-Lambda-boundary discipline as the other offline modules."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scudo.lambda_handler; import sys; "
            "print('scudo.scripts.run_sleep_cycle_job' in sys.modules)",
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
