"""Part D/E2 — offline/nightly SkillOpt-Sleep orchestrator.

microsoft/SkillOpt (verified real: PyPI `skillopt`, arXiv:2605.23904) accepts
a candidate skill-doc edit ONLY when it strictly improves a held-out
validation score; `SkillOpt-Sleep` is the nightly harvest -> mine -> replay ->
consolidate variant, same gate. The gate decision itself
(`should_promote`) now lives in the neutral `scudo.skill_gate` module (see
test_skill_gate.py) — split out from this file per a Codex review, since
this file used to be imported transitively by the live Lambda path via
`aurora_memory.py`, contradicting the "never imported by lambda_handler.py"
design (see test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded
below, which proves the transitive load is now gone).

`run_sleep_cycle()` (Part E2, gap-closure) is a real, injectable offline
orchestrator: harvest -> deterministic held-out split -> mine (injectable
optimizer) -> evaluate (injectable evaluator) -> gate+promote (injectable
store), so the whole cycle is testable with fakes and never touches real
Aurora/network/skillopt. The default optimizer/evaluator (used only when a
caller supplies none) still can't call the real skillopt Python API (only
its CLI/README-level description is verified, not its internal API surface)
— see `scudo.skillopt_adapter` for the CLI-based adapter that IS real,
usable as an explicit injection instead.
"""

from __future__ import annotations

import pytest


def test_runner_module_is_never_imported_by_lambda_handler():
    """The live Lambda hot path must not depend on the offline runner (which
    would, when fully implemented, depend on the unvendored skillopt
    package) — verified by checking for an actual import LINE, not merely a
    comment mentioning the module by name (which IS expected, as in-code
    documentation of where the offline gate plugs in)."""
    import scudo.lambda_handler as lambda_handler_module

    assert not hasattr(lambda_handler_module, "skillopt_sleep_runner")
    with open(lambda_handler_module.__file__, encoding="utf-8") as fh:
        import_lines = [
            line.strip() for line in fh if line.strip().startswith(("import ", "from "))
        ]
    assert not any("skillopt_sleep_runner" in line for line in import_lines), (
        "lambda_handler.py must not import skillopt_sleep_runner"
    )


def test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded():
    """The DIRECT-import-line check above (test_runner_module_is_never_
    imported_by_lambda_handler) isn't enough: a Codex review this session
    found that lambda_handler.py imports aurora_memory, and aurora_memory
    used to import should_promote directly FROM skillopt_sleep_runner - so
    importing scudo.lambda_handler TRANSITIVELY loaded
    scudo.skillopt_sleep_runner into sys.modules even though lambda_handler.py
    itself never mentions it. should_promote now lives in the neutral
    scudo.skill_gate module instead (see test_skill_gate.py) - this test
    proves the transitive load is actually gone, not just the direct one."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scudo.lambda_handler; import sys; "
            "print('scudo.skillopt_sleep_runner' in sys.modules)",
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", (
        f"importing scudo.lambda_handler must not load scudo.skillopt_sleep_runner "
        f"(transitively or otherwise); stdout was: {result.stdout!r}, "
        f"stderr: {result.stderr!r}"
    )


def test_runner_module_does_not_eagerly_import_the_skillopt_package():
    """The skillopt PyPI package is not installed/vendored in this repo
    (verified via pip show this session) — the module must not hard-import
    it at MODULE LOAD time (that would crash import of this module entirely
    on any machine without skillopt installed, including this one). A LAZY
    import inside the default optimizer function — only reached if that
    function is actually called with no fake optimizer supplied — is fine
    and expected; this checks for a TOP-LEVEL (unindented) import line only.
    """
    import scudo.skillopt_sleep_runner as runner_module  # succeeds without skillopt installed — proves no eager import

    with open(runner_module.__file__, encoding="utf-8") as fh:
        top_level_import_lines = [
            line
            for line in fh
            if line.startswith("import ") or line.startswith("from ")
        ]
    assert not any(
        "skillopt" in line and "skillopt_sleep_runner" not in line
        for line in top_level_import_lines
    ), "skillopt must only ever be imported lazily, inside a function body"


# ──────────────────────────────────────────────────────────────────────────
# Part E2 — run_sleep_cycle: a real, injectable offline orchestrator


class _FakeSleepStore:
    """Fakes the aurora_memory surface run_sleep_cycle needs: harvest,
    consult current best, gate+promote. Records promote_skill calls so tests
    can assert the exact arguments the orchestrator computed."""

    def __init__(self, *, trajectories=None, current_best=None, promote_result=True):
        self.trajectories = list(trajectories) if trajectories is not None else []
        self.current_best = current_best
        self.promote_result = promote_result
        self.promote_calls = []

    def harvest_trajectories(self):
        return list(self.trajectories)

    def consult_best_skill(self):
        return self.current_best

    def promote_skill(self, *, skill_text, validation_score, version):
        self.promote_calls.append(
            {
                "skill_text": skill_text,
                "validation_score": validation_score,
                "version": version,
            }
        )
        return self.promote_result


def test_default_held_out_split_takes_last_n_deterministically():
    """The split must be reproducible across runs — no random sampling —
    so tests (and real nightly runs) get the same train/held-out partition
    for the same harvested trajectories."""
    from scudo.skillopt_sleep_runner import default_held_out_split

    trajectories = [f"t{i}" for i in range(10)]

    train, held_out = default_held_out_split(trajectories, held_out_ratio=0.2)

    assert train == [f"t{i}" for i in range(8)]
    assert held_out == ["t8", "t9"]

    train_again, held_out_again = default_held_out_split(
        trajectories, held_out_ratio=0.2
    )
    assert (train_again, held_out_again) == (train, held_out)


def test_default_held_out_split_returns_empty_lists_for_empty_input():
    from scudo.skillopt_sleep_runner import default_held_out_split

    assert default_held_out_split([], held_out_ratio=0.2) == ([], [])


def test_run_sleep_cycle_promotes_on_happy_path_with_no_current_best():
    from scudo.skillopt_sleep_runner import run_sleep_cycle

    store = _FakeSleepStore(
        trajectories=[f"t{i}" for i in range(10)], current_best=None
    )
    seen_mine_args = {}
    seen_eval_args = {}

    def fake_optimizer(train, current_skill_text):
        seen_mine_args["train"] = train
        seen_mine_args["current_skill_text"] = current_skill_text
        return "candidate-skill-text"

    def fake_evaluator(candidate_text, held_out):
        seen_eval_args["candidate_text"] = candidate_text
        seen_eval_args["held_out"] = held_out
        return 0.95

    result = run_sleep_cycle(
        store=store, optimizer=fake_optimizer, evaluator=fake_evaluator
    )

    assert result is True
    assert len(seen_mine_args["train"]) == 8
    assert seen_mine_args["current_skill_text"] is None
    assert len(seen_eval_args["held_out"]) == 2
    assert store.promote_calls == [
        {"skill_text": "candidate-skill-text", "validation_score": 0.95, "version": 1}
    ]


def test_run_sleep_cycle_passes_current_skill_text_and_increments_version():
    from scudo.skillopt_sleep_runner import run_sleep_cycle

    store = _FakeSleepStore(
        trajectories=[f"t{i}" for i in range(10)],
        current_best={
            "skill_text": "current text",
            "version": 3,
            "validation_score": 0.5,
        },
    )
    seen_mine_args = {}

    def fake_optimizer(train, current_skill_text):
        seen_mine_args["current_skill_text"] = current_skill_text
        return "candidate-skill-text"

    def fake_evaluator(candidate_text, held_out):
        return 0.9

    run_sleep_cycle(store=store, optimizer=fake_optimizer, evaluator=fake_evaluator)

    assert seen_mine_args["current_skill_text"] == "current text"
    assert store.promote_calls[0]["version"] == 4


def test_run_sleep_cycle_short_circuits_when_no_trajectories_harvested():
    """Nothing to mine — the optimizer/evaluator/promote_skill must never be
    invoked, and no promotion happens."""
    from scudo.skillopt_sleep_runner import run_sleep_cycle

    store = _FakeSleepStore(trajectories=[])
    calls = []

    def fake_optimizer(train, current_skill_text):
        calls.append("optimizer")
        return "candidate"

    def fake_evaluator(candidate_text, held_out):
        calls.append("evaluator")
        return 1.0

    result = run_sleep_cycle(
        store=store, optimizer=fake_optimizer, evaluator=fake_evaluator
    )

    assert result is False
    assert calls == []
    assert store.promote_calls == []


def test_run_sleep_cycle_short_circuits_when_split_leaves_no_training_data():
    """A single harvested trajectory with the default 0.2 held-out ratio puts
    the whole thing into held-out (ceil(1*0.2) == 1), leaving nothing to
    train on — the cycle must bail out rather than mine from an empty set."""
    from scudo.skillopt_sleep_runner import run_sleep_cycle

    store = _FakeSleepStore(trajectories=["only-one"])
    calls = []

    def fake_optimizer(train, current_skill_text):
        calls.append("optimizer")
        return "candidate"

    def fake_evaluator(candidate_text, held_out):
        calls.append("evaluator")
        return 1.0

    result = run_sleep_cycle(
        store=store, optimizer=fake_optimizer, evaluator=fake_evaluator
    )

    assert result is False
    assert calls == []
    assert store.promote_calls == []


def test_run_sleep_cycle_returns_false_when_store_rejects_promotion():
    """The store's promote_skill is where should_promote is actually
    re-checked (aurora_memory.promote_skill's real gate) — run_sleep_cycle
    must not duplicate that logic, just propagate whatever the store
    decides. Mining and evaluation still happen; only promotion is refused."""
    from scudo.skillopt_sleep_runner import run_sleep_cycle

    store = _FakeSleepStore(
        trajectories=[f"t{i}" for i in range(10)],
        current_best={
            "skill_text": "current text",
            "version": 1,
            "validation_score": 0.99,
        },
        promote_result=False,
    )

    result = run_sleep_cycle(
        store=store,
        optimizer=lambda train, current_skill_text: "weaker-candidate",
        evaluator=lambda candidate_text, held_out: 0.1,
    )

    assert result is False
    assert len(store.promote_calls) == 1


def test_run_evaluated_sleep_cycle_requires_a_structured_holdout_report():
    from scudo.matching_self_improvement import (
        EvaluationPolicy,
        GoldenCase,
        GoldenSet,
        PromotionApproval,
        evaluate_golden_set,
    )
    from scudo.skillopt_sleep_runner import run_evaluated_sleep_cycle

    class _StrictStore(_FakeSleepStore):
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
            self.promote_calls.append(
                {
                    "skill_text": skill_text,
                    "validation_score": validation_score,
                    "version": version,
                    "evaluation": evaluation,
                    "approval": approval,
                    "source_trajectory_refs": source_trajectory_refs,
                }
            )
            return True

    golden = GoldenSet(
        version="golden-1",
        cases=[
            GoldenCase(
                case_id="case-1",
                vendor="lseg",
                vendor_product_ref="LSEG-1",
                expected_target_iri="jpmorgan:data:cdao:EquityPrices",
                split="holdout",
            )
        ],
    )
    store = _StrictStore(
        trajectories=[
            {"bundle_ref": f"bundle-{i}"}
            for i in range(5)
        ],
        current_best=None,
    )
    approval = PromotionApproval(
        approved_by="reviewer@example.com",
        approval_ref="MR-1",
        rationale="reviewed",
    )

    result = run_evaluated_sleep_cycle(
        store=store,
        optimizer=lambda train, current: "candidate",
        evaluator=lambda candidate, held_out: evaluate_golden_set(
            golden,
            lambda case: {
                "mapped_node_iri": "jpmorgan:data:cdao:EquityPrices",
                "confidence": 0.95,
                "status": "auto_mapped",
            },
            candidate_version="candidate-1",
            policy=EvaluationPolicy(
                min_exact_match_rate=1.0,
                max_false_auto_pass_rate=0.0,
                max_brier_score=1.0,
            ),
        ),
        approval=approval,
    )

    assert result is True
    assert store.promote_calls[0]["evaluation"].passed is True
    assert store.promote_calls[0]["approval"] == approval
    assert store.promote_calls[0]["source_trajectory_refs"] == [
        f"bundle-{i}" for i in range(5)
    ]


def test_lazy_skillopt_optimizer_raises_runtime_error_when_package_not_installed():
    """The skillopt package is confirmed not installed/vendored in this repo
    this session — calling the default optimizer directly (bypassing
    run_sleep_cycle's injection seam) must fail with a clear, actionable
    error rather than a bare ModuleNotFoundError."""
    from scudo.skillopt_sleep_runner import _lazy_skillopt_optimizer

    with pytest.raises(RuntimeError, match="skillopt"):
        _lazy_skillopt_optimizer(["some-trajectory"], None)
