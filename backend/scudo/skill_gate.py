"""The SkillOpt held-out validation gate decision — deliberately its own
tiny, neutral module, imported by the LIVE `scudo.aurora_memory` (and
therefore `scudo.lambda_handler`). Never import anything else from here;
this module must stay safe for the live Lambda path to depend on forever,
even as `scudo.skillopt_sleep_runner` (the offline runner) grows heavier
dependencies. See test_skill_gate.py::test_skill_gate_module_stays_neutral.

Split out from `scudo.skillopt_sleep_runner` per a Codex review this
session: `aurora_memory.py` used to import `should_promote` directly from
`skillopt_sleep_runner.py`, which meant importing `scudo.lambda_handler`
transitively loaded `scudo.skillopt_sleep_runner` into `sys.modules` —
contradicting the stated design ("skillopt_sleep_runner.py — NEVER imported
by lambda_handler.py"). Harmless today only because skillopt_sleep_runner.py
doesn't import the real `skillopt` package at module level either, but
fragile — and not what the architecture states. See
test_skillopt_sleep_runner.py's
test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded.
"""

from __future__ import annotations

from typing import Optional


def should_promote(candidate_score: float, current_best_score: Optional[float]) -> bool:
    """The SkillOpt held-out validation gate: promote a candidate skill doc
    only if there's no current best yet, or the candidate STRICTLY improves
    on it. A tie is not a strict improvement — a flat/no-op edit must never
    displace an already-validated skill, matching SkillOpt's own "accepted
    only when it strictly improves" rule.
    """
    if current_best_score is None:
        return True
    return candidate_score > current_best_score
