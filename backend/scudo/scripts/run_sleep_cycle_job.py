"""Scheduler/job wrapper for the offline SkillOpt-Sleep cycle
(scudo.skillopt_sleep_runner.run_sleep_cycle) against REAL scudo.aurora_memory.

DRY-RUN by default — pass --apply to actually write a promoted skill doc to
Aurora, same safety convention as scudo/scripts/cleanup_stale_cdao.py. In
dry-run mode, harvest/consult reads still go against real Aurora (they are
already fail-open and read-only), but the promotion WRITE never happens —
the gate decision is reported instead (see _make_dry_run_store).

This is NOT wired into any EventBridge/cron infra by this change (actual
AWS scheduling infra deployment is out of scope here, same as the rest of
this repo's Aurora work — see infra/ for the deployed stack definitions).
To run nightly, wire this as an EventBridge Scheduled Rule (or a cron
entry on whatever host runs it) invoking:

    python -m scudo.scripts.run_sleep_cycle_job --apply

with SCUDO_AURORA_CLUSTER_ARN / SCUDO_AURORA_SECRET_ARN /
SCUDO_AURORA_DATABASE_NAME set in the invocation's environment (the same
three variables scudo/aurora_store.py already requires for every other
Aurora write in this codebase).

Never imported by lambda_handler.py — see
test_run_sleep_cycle_job.py::test_job_script_is_never_imported_by_lambda_handler.
Heavy/live imports (aurora_memory, run_sleep_cycle) stay lazy inside main()
so importing this module offline never touches Aurora or the network.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Any, Optional

_REQUIRED_AURORA_ENV_VARS = (
    "SCUDO_AURORA_CLUSTER_ARN",
    "SCUDO_AURORA_SECRET_ARN",
    "SCUDO_AURORA_DATABASE_NAME",
)
_REQUIRED_APPLY_ENV_VARS = (
    "SCUDO_EVALUATION_PUBLIC_KEY",
    "SCUDO_SKILL_PROMOTION_KEY",
    "SCUDO_PROTECTED_EVALUATOR_COMMAND",
    "SCUDO_PROTECTED_EVALUATION_REQUEST_ID",
    "SCUDO_SKILL_OPTIMIZER_COMMAND",
)


def _env_validation_errors() -> list[str]:
    """Which required Aurora env vars are missing. Pure — no AWS call."""
    return [name for name in _REQUIRED_AURORA_ENV_VARS if not os.environ.get(name)]


def _make_dry_run_store(real_store: Any):
    """Wrap a real store (module or object exposing harvest_trajectories/
    consult_best_skill/promote_skill): reads pass through unchanged (already
    fail-open, read-only), but ``promote_skill`` invokes Aurora's structured
    promotion preflight — the same evaluation, approval, and no-regression
    validation used by a real promotion — WITHOUT writing anything."""

    class _DryRunStore:
        def harvest_trajectories(self):
            return real_store.harvest_trajectories()

        def consult_best_skill(self):
            return real_store.consult_best_skill()

        def next_skill_version(self, *, minimum=1):
            return real_store.next_skill_version(minimum=minimum)

        def promote_skill(
            self,
            *,
            skill_text,
            validation_score,
            version,
            evaluation,
            approval,
            source_trajectory_refs,
        ):
            would_promote = bool(
                real_store.preflight_skill_promotion(
                    skill_text=skill_text,
                    version=version,
                    evaluation=evaluation,
                    approval=approval,
                    source_trajectory_refs=source_trajectory_refs,
                )
            )
            print(
                f"[run_sleep_cycle_job] DRY-RUN: candidate v{version} "
                f"score={validation_score:.4f} "
                f"-> would_promote={would_promote} (NOT written to Aurora)"
            )
            return would_promote

    return _DryRunStore()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one SkillOpt-Sleep cycle against real Aurora agent "
        "memory. DRY-RUN by default — pass --apply to actually promote."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write a promoted skill doc to Aurora if the gate "
        "accepts it. Without this flag, the cycle runs fully (harvest -> "
        "mine -> evaluate -> gate) but never writes (dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (the default). Report the gate decision "
        "without writing.",
    )
    parser.add_argument(
        "--held-out-ratio",
        type=float,
        default=0.2,
        help="Fraction of harvested trajectories held out for evaluation "
        "(default: 0.2, same default as run_sleep_cycle).",
    )
    args = parser.parse_args(argv[1:])
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if not math.isfinite(args.held_out_ratio) or not 0 < args.held_out_ratio < 1:
        parser.error("--held-out-ratio must be finite and between 0 and 1")
    # Safe default: anything that is not an explicit --apply is a dry-run.
    args.dry_run = not args.apply
    return args


def main(argv: Optional[list[str]] = None, *, store_override: Any = None) -> int:
    """CLI entrypoint. Returns a process exit code (0 = success)."""
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv)

    missing = _env_validation_errors()
    if args.apply:
        missing.extend(
            name for name in _REQUIRED_APPLY_ENV_VARS if not os.environ.get(name)
        )
    if missing:
        print(
            f"[run_sleep_cycle_job] ERROR: missing required env var(s): "
            f"{', '.join(missing)}. Aurora persistence cannot proceed.",
            file=sys.stderr,
        )
        return 2

    from scudo import aurora_memory
    from scudo.skillopt_sleep_runner import (
        run_protected_sleep_cycle,
        run_sleep_cycle,
    )
    from scudo.protected_evaluator_adapter import run_protected_evaluator_command
    from scudo.skill_optimizer_adapter import run_skill_optimizer_command

    mode = "dry-run" if args.dry_run else "apply"
    print(f"[run_sleep_cycle_job] mode={mode} held_out_ratio={args.held_out_ratio}")

    real_store = store_override or aurora_memory
    store = _make_dry_run_store(real_store) if args.dry_run else real_store

    try:
        if args.apply:

            def configured_evaluator(request):
                request = {
                    **request,
                    "evaluation_request_id": os.environ[
                        "SCUDO_PROTECTED_EVALUATION_REQUEST_ID"
                    ],
                }
                return run_protected_evaluator_command(
                    request,
                    command=os.environ["SCUDO_PROTECTED_EVALUATOR_COMMAND"],
                )

            promoted = run_protected_sleep_cycle(
                store=store,
                optimizer=lambda train, current: run_skill_optimizer_command(
                    {"trajectories": train, "current_skill": current},
                    command=os.environ["SCUDO_SKILL_OPTIMIZER_COMMAND"],
                ),
                evaluator=configured_evaluator,
                approval={
                    "approved_by": "protected-sleep-evaluator",
                    "approval_ref": "scheduled-protected-apply",
                    "rationale": "Protected scheduled evaluation.",
                },
                evaluation_signing_key="",
                promotion_signing_key=os.environ["SCUDO_SKILL_PROMOTION_KEY"],
                evaluator_id="skillopt-sleep",
                evaluator_version="1",
                evaluation_public_key_pem=os.environ["SCUDO_EVALUATION_PUBLIC_KEY"],
                held_out_ratio=args.held_out_ratio,
            )
        else:
            promoted = run_sleep_cycle(store=store, held_out_ratio=args.held_out_ratio)
    except RuntimeError as exc:
        # The default optimizer/evaluator (scudo.skillopt_sleep_runner's
        # lazy skillopt import) raises RuntimeError with a clear message
        # when the `skillopt` package isn't installed — surface it as-is,
        # this IS the "fail clearly when SkillOpt is unavailable" contract.
        print(f"[run_sleep_cycle_job] ERROR: {exc}", file=sys.stderr)
        return 3

    print(f"[run_sleep_cycle_job] DONE: promoted={promoted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
