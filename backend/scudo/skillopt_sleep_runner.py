"""Offline/nightly SkillOpt-Sleep orchestrator — NEVER imported by
lambda_handler.py (the live Lambda hot path; see
test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded in
test_skillopt_sleep_runner.py, which proves this transitively, not just by
grepping import lines). See
docs/superpowers/specs/2026-07-07-aurora-memory-rights-model-zone-tool-design.md
Part D and Part E2 for the full design and the ground-truth research behind
it; docs/superpowers/plans/2026-07-07-aurora-memory-rights-model-zone-tool-design-plan.md
for the implementation plan.

microsoft/SkillOpt (verified real: PyPI `skillopt`, MIT, arXiv:2605.23904)
trains a skill document as the trainable state of a frozen agent: rollout ->
reflect -> aggregate -> select -> update -> evaluate, with a candidate edit
accepted only when it strictly improves a held-out validation score.
`SkillOpt-Sleep` is the nightly variant of the same loop: harvest -> mine ->
replay -> consolidate, same gate.

STATUS (updated after a Codex review, gap-closure round): `run_sleep_cycle`
below is a REAL, injectable orchestrator wiring the whole cycle - HARVEST
(`store.harvest_trajectories()`) -> deterministic held-out split
(`default_held_out_split`) -> MINE (injectable `optimizer`) -> EVALUATE
(injectable `evaluator`) -> GATE+PROMOTE (`store.promote_skill(...)`, which
internally calls `scudo.skill_gate.should_promote` - see that module, not
this one, for the gate decision itself). The ONE piece still not real: the
default optimizer/evaluator (`_lazy_skillopt_optimizer`/
`_lazy_skillopt_evaluator`, used only when a caller supplies none) lazily
load the real `skillopt` package and raise a clear error if it isn't
installed - the actual "mine a candidate" / "score against held-out" calls
into skillopt's Python API are NOT implemented, because only skillopt's
CLI/README-level description was ever verified this session, not its
internal Python API surface. See `scudo.skillopt_adapter` for the CLI-based
adapter that IS wired (subprocess against the real `skillopt-sleep` CLI when
installed), usable as an explicit `optimizer=`/`evaluator=` injection
instead of the lazy defaults.

This module has zero side effects when imported — safe to import in tests
without any AWS/network dependency (the real `skillopt` package is only
ever imported lazily, inside `_lazy_skillopt_optimizer`/
`_lazy_skillopt_evaluator`'s function bodies, never at module load).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

from .matching_self_improvement import EvaluationReport, PromotionApproval

Optimizer = Callable[[list, Optional[str]], str]
Evaluator = Callable[[str, list], float | EvaluationReport]


def default_held_out_split(
    trajectories: list, held_out_ratio: float = 0.2
) -> tuple[list, list]:
    """Deterministic held-out split: the LAST ``ceil(n * held_out_ratio)``
    trajectories (by harvest order — newest first, per
    ``aurora_memory.harvest_trajectories``) become the held-out set, the rest
    are training data. Deliberately NOT random: a nightly run must be
    reproducible for the same harvested trajectories, both for debugging and
    for tests.
    """
    n = len(trajectories)
    if n == 0:
        return [], []
    held_out_count = min(math.ceil(n * held_out_ratio), n)
    split_index = n - held_out_count
    return trajectories[:split_index], trajectories[split_index:]


def _lazy_skillopt_optimizer(
    train_trajectories: list, current_skill_text: Optional[str]
) -> str:
    """The default optimizer, only used when a caller runs ``run_sleep_cycle``
    without injecting one. Imports the real ``skillopt`` package LAZILY,
    inside this function body — never at module load, so importing this
    module (and everything that transitively imports it, which per
    ``test_runner_module_is_never_imported_by_lambda_handler`` is nothing in
    the live Lambda) never requires ``skillopt`` to be installed.
    """
    try:
        import skillopt  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "run_sleep_cycle's default optimizer requires the `skillopt` "
            "package (PyPI `skillopt`), which is not installed in this "
            "environment. Either `pip install skillopt`, or call "
            "run_sleep_cycle(..., optimizer=<your callable>) explicitly."
        ) from e
    # TODO(skillopt-integration): the real skillopt Python API call (mining a
    # candidate skill-doc edit from train_trajectories + current_skill_text)
    # is not implemented here — only skillopt's CLI/README-level description
    # is verified this session, not its internal Python API surface.
    raise NotImplementedError(
        "skillopt Python API integration is not implemented yet; inject an "
        "optimizer= callable for testing or until this is built."
    )


def _lazy_skillopt_evaluator(candidate_text: str, held_out: list) -> float:
    """The default evaluator, mirroring ``_lazy_skillopt_optimizer``: lazy
    import, clear ``RuntimeError`` if ``skillopt`` isn't installed, otherwise
    a documented stub (the held-out scoring call is unverified API surface,
    same as mining)."""
    try:
        import skillopt  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "run_sleep_cycle's default evaluator requires the `skillopt` "
            "package (PyPI `skillopt`), which is not installed in this "
            "environment. Either `pip install skillopt`, or call "
            "run_sleep_cycle(..., evaluator=<your callable>) explicitly."
        ) from e
    # TODO(skillopt-integration): the real skillopt held-out scoring call is
    # not implemented here, same caveat as _lazy_skillopt_optimizer above.
    raise NotImplementedError(
        "skillopt Python API integration is not implemented yet; inject an "
        "evaluator= callable for testing or until this is built."
    )


def run_sleep_cycle(
    *,
    store: Any,
    optimizer: Optional[Optimizer] = None,
    evaluator: Optional[Evaluator] = None,
    held_out_ratio: float = 0.2,
    approval: Optional[PromotionApproval | dict] = None,
) -> bool:
    """The offline SkillOpt-Sleep cycle: HARVEST -> split -> MINE -> EVALUATE
    -> GATE+PROMOTE. ``store`` needs ``harvest_trajectories()``,
    ``consult_best_skill()``, and ``promote_skill(*, skill_text,
    validation_score, version)`` — ``aurora_memory`` already provides all
    three. ``optimizer``/``evaluator`` are injectable so tests never touch
    real Aurora, the network, or the unvendored ``skillopt`` package; the
    default optimizer (used only when the caller supplies none) requires
    ``skillopt`` to be installed and raises a clear error otherwise.

    Returns whether a new skill doc was actually promoted. The gate itself
    (``should_promote``) is NOT re-checked here — ``store.promote_skill``
    already re-checks it, so this function just propagates that decision
    rather than duplicating the logic.
    """
    trajectories = store.harvest_trajectories()
    if not trajectories:
        return False

    train, held_out = default_held_out_split(
        trajectories, held_out_ratio=held_out_ratio
    )
    if not train or not held_out:
        return False

    current = store.consult_best_skill()
    current_skill_text = current["skill_text"] if current else None
    current_version = current["version"] if current else 0

    mine = optimizer or _lazy_skillopt_optimizer
    candidate_text = mine(train, current_skill_text)

    evaluate = evaluator or _lazy_skillopt_evaluator
    score = evaluate(candidate_text, held_out)

    # A real evaluation report can cross the offline-to-live boundary. A
    # legacy scalar remains useful for dry-run/test adapters, but
    # aurora_memory.promote_skill deliberately refuses to make it live.
    if isinstance(score, EvaluationReport):
        return store.promote_skill(
            skill_text=candidate_text,
            validation_score=score.metrics.exact_match_rate,
            version=current_version + 1,
            evaluation=score,
            approval=approval,
            source_trajectory_refs=[
                str(t.get("bundle_ref"))
                for t in trajectories
                if isinstance(t, dict) and t.get("bundle_ref")
            ],
        )

    return store.promote_skill(
        skill_text=candidate_text,
        validation_score=score,
        version=current_version + 1,
    )


def run_evaluated_sleep_cycle(
    *,
    store: Any,
    optimizer: Optimizer,
    evaluator: Callable[[str, list], EvaluationReport],
    approval: PromotionApproval | dict,
    held_out_ratio: float = 0.2,
) -> bool:
    """Run the strict offline cycle that is allowed to promote an artifact.

    The evaluator must return a versioned holdout ``EvaluationReport`` and the
    caller must provide a named approval. The store remains the final
    enforcement point, so this helper is safe to use with a real
    ``aurora_memory`` module or a test double that implements the same
    contract.
    """

    trajectories = store.harvest_trajectories()
    if not trajectories:
        return False
    train, held_out = default_held_out_split(
        trajectories, held_out_ratio=held_out_ratio
    )
    if not train or not held_out:
        return False

    current = store.consult_best_skill()
    current_skill_text = current["skill_text"] if current else None
    current_version = current["version"] if current else 0
    candidate_text = optimizer(train, current_skill_text)
    report = evaluator(candidate_text, held_out)
    if not isinstance(report, EvaluationReport):
        raise TypeError(
            "run_evaluated_sleep_cycle evaluator must return EvaluationReport"
        )

    return store.promote_skill(
        skill_text=candidate_text,
        validation_score=report.metrics.exact_match_rate,
        version=current_version + 1,
        evaluation=report,
        approval=approval,
        source_trajectory_refs=[
            str(t.get("bundle_ref"))
            for t in trajectories
            if isinstance(t, dict) and t.get("bundle_ref")
        ],
    )
