"""The SkillOpt held-out validation gate decision, in its own tiny, neutral
module (`scudo.skill_gate`) — deliberately separate from
`scudo.skillopt_sleep_runner` (the offline runner).

Ground truth from a Codex review this session: `scudo.aurora_memory`
(imported by the LIVE `scudo.lambda_handler`) used to import `should_promote`
directly from `scudo.skillopt_sleep_runner` — meaning importing
`scudo.lambda_handler` transitively loaded `scudo.skillopt_sleep_runner` into
`sys.modules`, contradicting the stated design ("skillopt_sleep_runner.py —
NEVER imported by lambda_handler.py"). Harmless today only because
skillopt_sleep_runner.py doesn't import the real `skillopt` package at
module level either — but fragile, and not what the architecture states.
`should_promote` now lives here instead, and `aurora_memory` imports it from
here. See test_skillopt_sleep_runner.py's
test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded for the
transitive-import proof.
"""

from __future__ import annotations


def test_should_promote_true_when_no_current_best_exists():
    from scudo.skill_gate import should_promote

    assert should_promote(candidate_score=0.5, current_best_score=None) is True


def test_should_promote_true_on_strict_improvement():
    from scudo.skill_gate import should_promote

    assert should_promote(candidate_score=0.82, current_best_score=0.79) is True


def test_should_promote_false_on_tie():
    """A tie is NOT a strict improvement — SkillOpt's gate requires the
    candidate to strictly beat the current best, not merely match it, so a
    flat/no-op edit never displaces a validated skill."""
    from scudo.skill_gate import should_promote

    assert should_promote(candidate_score=0.80, current_best_score=0.80) is False


def test_should_promote_false_on_regression():
    from scudo.skill_gate import should_promote

    assert should_promote(candidate_score=0.60, current_best_score=0.80) is False


def test_skill_gate_module_stays_neutral():
    """Deliberately tiny and neutral: the only imports allowed are
    `__future__` and `typing` (pure type-hint machinery, zero runtime cost,
    zero transitive dependencies) - no `scudo.*`, no third-party package, no
    aurora_store/requests/skillopt - so the live Lambda path can depend on
    this module forever, even as the offline runner grows heavier
    dependencies."""
    import ast

    import scudo.skill_gate as skill_gate_module

    with open(skill_gate_module.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    allowed_modules = {"__future__", "typing"}
    disallowed = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module not in allowed_modules:
            disallowed.append(node.module)
        elif isinstance(node, ast.Import):
            disallowed.extend(alias.name for alias in node.names)
    assert not disallowed, (
        f"skill_gate.py must only import from {allowed_modules}, to stay "
        f"safe for the live Lambda path: found {disallowed}"
    )
