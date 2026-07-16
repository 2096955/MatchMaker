from __future__ import annotations

import json

import pytest

from scudo.matching_self_improvement import (
    EvaluationMetrics,
    EvaluationPolicy,
    GoldenCase,
    GoldenSet,
    LearningArtifact,
    MatchingPrediction,
    PromotionApproval,
    PromotionRejected,
    evaluate_golden_set,
    load_golden_set,
    validate_promotion,
)


def _positive(case_id: str, *, split: str = "holdout", vendor: str = "lseg"):
    return GoldenCase(
        case_id=case_id,
        vendor=vendor,
        vendor_product_ref=f"{vendor.upper()}-{case_id}",
        product_name="Equity prices",
        expected_target_iri="jpmorgan:data:cdao:EquityPrices",
        split=split,
        taxonomy_group="market-data",
    )


def _abstain(case_id: str, *, split: str = "holdout", vendor: str = "ice"):
    return GoldenCase(
        case_id=case_id,
        vendor=vendor,
        vendor_product_ref=f"{vendor.upper()}-{case_id}",
        product_name="Prices",
        expected_abstain=True,
        split=split,
        taxonomy_group="ambiguous",
        tags=["adversarial"],
    )


def test_golden_set_rejects_duplicate_identity_across_splits():
    first = _positive("one", split="train")
    duplicate = first.model_copy(update={"case_id": "two", "split": "holdout"})

    with pytest.raises(ValueError, match="duplicate vendor/vendor_product_ref"):
        GoldenSet(version="golden-1", cases=[first, duplicate])


def test_golden_set_allows_abstention_only_holdout():
    # An abstention-only holdout set must still LOAD: the positive-holdout
    # requirement is scoped to the holdout evaluation path, not construction, so
    # such a set remains usable as --split adversarial evidence.
    golden = GoldenSet(
        version="golden-1",
        cases=[_abstain("one"), _abstain("two", vendor="lseg", split="adversarial")],
    )
    assert [c.case_id for c in golden.cases_for_split("holdout")] == ["one"]


def test_holdout_evaluation_requires_a_positive_case():
    golden = GoldenSet(version="golden-1", cases=[_abstain("one")])

    def predictor(case):
        return {"status": "needs_review", "requires_human_review": True}

    with pytest.raises(ValueError, match="positive mapping"):
        evaluate_golden_set(
            golden, predictor, candidate_version="cand-1", split="holdout"
        )


def test_adversarial_evaluation_allows_abstention_only_holdout():
    golden = GoldenSet(
        version="golden-1",
        cases=[_abstain("one"), _abstain("adv", vendor="lseg", split="adversarial")],
    )

    def predictor(case):
        return {"status": "needs_review", "requires_human_review": True}

    report = evaluate_golden_set(
        golden, predictor, candidate_version="cand-1", split="adversarial"
    )
    assert report.split == "adversarial"
    assert report.case_ids == ["adv"]


def test_jsonl_loader_validates_rows_and_version(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_positive("one").model_dump()),
                json.dumps(_abstain("two").model_dump()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    golden = load_golden_set(path, version="golden-2026-07-16")

    assert golden.version == "golden-2026-07-16"
    assert [case.case_id for case in golden.cases_for_split("holdout")] == [
        "one",
        "two",
    ]


def test_evaluator_supports_engine_and_agent_result_shapes_and_stratifies():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("engine", vendor="lseg"),
            _positive("agent", vendor="spglobal"),
            _abstain("ambiguous", vendor="ice"),
        ],
    )

    def predictor(case):
        if case.case_id == "engine":
            return {
                "mapped_node_iri": "jpmorgan:data:cdao:EquityPrices",
                "confidence": 0.91,
                "status": "auto_mapped",
                "band": "pass",
            }
        if case.case_id == "agent":
            return {
                "proposed_target_iri": "jpmorgan:data:cdao:EquityPrices",
                "confidence": 0.84,
                "band": "high",
                "requires_human_review": False,
            }
        return {
            "mapped_node_iri": "jpmorgan:data:cdao:concept:Prices",
            "confidence": 0.62,
            "status": "needs_review",
        }

    report = evaluate_golden_set(
        golden,
        predictor,
        candidate_version="candidate-2",
        policy=EvaluationPolicy(
            min_exact_match_rate=1.0,
            max_false_auto_pass_rate=0.0,
            max_brier_score=0.10,
        ),
    )

    assert report.passed is True
    assert report.metrics.total_cases == 3
    assert report.metrics.exact_match_rate == 1.0
    assert report.metrics.correct_abstention_cases == 1
    assert report.metrics.false_auto_pass_rate == 0.0
    assert report.metrics.coverage == pytest.approx(2 / 3)
    assert report.by_vendor["lseg"].exact_match_rate == 1.0
    assert report.by_vendor["ice"].abstention_recall == 1.0
    assert report.by_taxonomy_group["ambiguous"].false_auto_pass_rate == 0.0


def test_false_auto_pass_fails_policy_even_when_mapping_accuracy_is_high():
    golden = GoldenSet(version="golden-1", cases=[_positive("one"), _abstain("two")])

    report = evaluate_golden_set(
        golden,
        lambda case: MatchingPrediction(
            target_iri="jpmorgan:data:cdao:EquityPrices",
            confidence=0.95,
            status="auto_mapped",
            auto_pass=True,
        ),
        candidate_version="candidate-1",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.5,
            max_false_auto_pass_rate=0.0,
            max_brier_score=1.0,
        ),
    )

    assert report.metrics.false_auto_pass_cases == 1
    assert report.metrics.false_auto_pass_rate == 0.5
    assert report.passed is False


def test_wrong_auto_pass_on_positive_case_fails_false_auto_pass_policy():
    cases = [_positive(f"case-{index}", vendor="lseg") for index in range(20)]
    golden = GoldenSet(version="golden-1", cases=cases)

    def predictor(case):
        target = (
            "jpmorgan:data:cdao:Wrong"
            if case.case_id == "case-0"
            else case.expected_target_iri
        )
        return {
            "mapped_node_iri": target,
            "confidence": 0.99,
            "status": "auto_mapped",
            "auto_pass": True,
        }

    report = evaluate_golden_set(
        golden,
        predictor,
        candidate_version="unsafe-candidate",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.95,
            max_false_auto_pass_rate=0.0,
            max_brier_score=0.10,
        ),
    )

    assert report.metrics.correct_target_cases == 19
    assert report.metrics.false_auto_pass_cases == 1
    assert report.metrics.false_auto_pass_rate == pytest.approx(0.05)
    assert report.passed is False


def test_correct_abstention_is_not_scored_as_high_match_confidence():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("holdout-positive", split="holdout"),
            _abstain("one", split="adversarial"),
        ],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "confidence": 0.05,
            "status": "needs_review",
            "requires_human_review": True,
        },
        candidate_version="careful-candidate",
        split="adversarial",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            max_false_auto_pass_rate=0.0,
            max_brier_score=0.10,
        ),
    )

    assert report.metrics.correct_abstention_cases == 1
    assert report.metrics.calibration_mae == 0.0
    assert report.metrics.brier_score == 0.0
    assert report.passed is True


def test_all_abstain_split_is_not_an_exact_match_failure():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("holdout-positive", split="holdout"),
            _abstain("one", split="adversarial"),
        ],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {"status": "needs_review", "requires_human_review": True},
        candidate_version="abstain-candidate",
        split="adversarial",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.95,
            max_brier_score=0.10,
        ),
    )

    assert report.metrics.expected_match_cases == 0
    assert report.metrics.exact_match_rate == 1.0
    assert report.passed is True


def test_all_abstain_split_requires_correct_abstentions():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("holdout-positive", split="holdout"),
            _abstain("one", split="adversarial"),
        ],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "mapped_node_iri": "jpmorgan:data:cdao:EquityPrices",
            "confidence": 0.40,
            "status": "mapped",
            "auto_pass": False,
        },
        candidate_version="unsafe-candidate",
        split="adversarial",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.95,
            max_false_auto_pass_rate=0.0,
            max_brier_score=0.10,
        ),
    )

    assert report.metrics.exact_match_rate == 1.0
    assert report.metrics.abstention_recall == 0.0
    assert report.passed is False


def test_abstention_recall_uses_expected_abstentions_as_its_denominator():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("positive"),
            _abstain("correct-abstention"),
            _abstain("missed-abstention", vendor="lseg"),
        ],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "mapped_node_iri": (
                case.expected_target_iri
                if not case.expected_abstain
                else "jpmorgan:data:cdao:EquityPrices"
            ),
            "confidence": 0.9,
            "status": (
                "needs_review"
                if case.case_id == "correct-abstention"
                else "auto_mapped"
            ),
            "auto_pass": not case.expected_abstain,
        },
        candidate_version="candidate-1",
        policy=EvaluationPolicy(min_exact_match_rate=1.0, max_brier_score=1.0),
    )

    assert report.metrics.correct_abstention_cases == 1
    assert report.metrics.predicted_abstain_cases == 1
    assert report.metrics.expected_abstain_cases == 2
    assert report.metrics.abstention_recall == 0.5


def test_match_confidence_calibration_uses_positive_cases_and_skips_abstentions():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("correct"),
            _positive("incorrect", vendor="ice"),
            _abstain("abstain", vendor="spglobal"),
        ],
    )

    def predictor(case):
        if case.case_id == "correct":
            return {
                "mapped_node_iri": case.expected_target_iri,
                "confidence": 0.8,
                "status": "auto_mapped",
            }
        if case.case_id == "incorrect":
            return {
                "mapped_node_iri": "jpmorgan:data:cdao:Wrong",
                "confidence": 0.3,
                "status": "mapped",
            }
        return {
            "confidence": 0.99,
            "status": "needs_review",
            "requires_human_review": True,
        }

    report = evaluate_golden_set(
        golden,
        predictor,
        candidate_version="candidate-1",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            max_brier_score=1.0,
        ),
    )

    assert report.metrics.calibration_mae == pytest.approx(0.25)
    assert report.metrics.brier_score == pytest.approx(0.065)


def test_golden_identity_is_case_insensitive_for_product_reference():
    first = _positive("one", split="train")
    duplicate = first.model_copy(
        update={
            "case_id": "two",
            "vendor_product_ref": first.vendor_product_ref.lower(),
            "split": "holdout",
        }
    )

    with pytest.raises(ValueError, match="duplicate vendor/vendor_product_ref"):
        GoldenSet(version="golden-1", cases=[first, duplicate])


def test_evaluator_only_calls_the_requested_split():
    golden = GoldenSet(
        version="golden-1",
        cases=[
            _positive("train", split="train"),
            _positive("holdout", split="holdout", vendor="ice"),
            _positive("adversarial", split="adversarial", vendor="spglobal"),
        ],
    )
    evaluated_case_ids = []

    report = evaluate_golden_set(
        golden,
        lambda case: (
            evaluated_case_ids.append(case.case_id)
            or {
                "mapped_node_iri": case.expected_target_iri,
                "confidence": 0.99,
                "status": "auto_mapped",
            }
        ),
        candidate_version="candidate-1",
        split="adversarial",
        policy=EvaluationPolicy(min_exact_match_rate=1.0, max_brier_score=1.0),
    )

    assert evaluated_case_ids == ["adversarial"]
    assert report.case_ids == ["adversarial"]


def test_abstaining_positive_prediction_is_not_an_exact_match():
    golden = GoldenSet(version="golden-1", cases=[_positive("one")])

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "proposed_target_iri": "jpmorgan:data:cdao:EquityPrices",
            "confidence": 0.91,
            "requires_human_review": True,
        },
        candidate_version="candidate-1",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            max_brier_score=1.0,
        ),
    )

    assert report.metrics.correct_target_cases == 0
    assert report.metrics.exact_match_rate == 0.0
    assert report.passed is True


def _report(
    *,
    version: str,
    exact: float,
    false_auto_pass: float,
    brier: float,
    passed: bool = True,
):
    return {
        "candidate_version": version,
        "golden_set_version": "golden-1",
        "split": "holdout",
        "case_ids": ["one", "two"],
        "metrics": {
            "total_cases": 2,
            "expected_match_cases": 1,
            "expected_abstain_cases": 1,
            "predicted_abstain_cases": 1,
            "auto_pass_cases": 1,
            "correct_target_cases": 1,
            "correct_abstention_cases": 1,
            "false_auto_pass_cases": 0,
            "exact_match_rate": exact,
            "abstention_recall": 1.0,
            "coverage": 0.5,
            "false_auto_pass_rate": false_auto_pass,
            "calibration_mae": brier,
            "brier_score": brier,
        },
        "policy": {
            "min_cases": 1,
            "min_exact_match_rate": 0.5,
            "max_false_auto_pass_rate": 0.0,
            "max_brier_score": 1.0,
        },
        "passed": passed,
    }


def test_evaluation_metrics_accepts_pre_auto_pass_artifacts():
    payload = _report(
        version="candidate-1",
        exact=1.0,
        false_auto_pass=0.0,
        brier=0.01,
    )
    del payload["metrics"]["auto_pass_cases"]

    metrics = EvaluationMetrics.model_validate(payload["metrics"])

    assert metrics.auto_pass_cases == 0


def _artifact(
    *,
    version: int,
    report: dict,
    approval_ref: str,
    content: str = "prefer exact vendor references",
) -> LearningArtifact:
    return LearningArtifact(
        artifact_id=f"matching-skill-{version}",
        artifact_kind="matching_skill",
        version=version,
        content=content,
        evaluation=report,
        approval=PromotionApproval(
            approved_by="reviewer@example.com",
            approval_ref=approval_ref,
            rationale="Reviewed against the holdout report.",
        ),
    )


def test_promotion_requires_holdout_pass_and_named_approval():
    report = _report(version="candidate-1", exact=1.0, false_auto_pass=0.0, brier=0.01)
    artifact = _artifact(version=1, report=report, approval_ref="MR-1")

    validate_promotion(artifact)

    unapproved = artifact.model_copy(
        update={
            "approval": None,
        }
    )
    with pytest.raises(PromotionRejected, match="no named approval"):
        validate_promotion(unapproved)


def test_promotion_rejects_abstention_only_holdout_report():
    # Defence in depth: a forged/replayed holdout report that never went through
    # evaluate_golden_set (so its positive-case guard never fired) must still be
    # rejected at the promotion boundary. expected_match_cases==0 means the
    # holdout proved abstention only, not matching capability.
    report = _report(version="candidate-1", exact=1.0, false_auto_pass=0.0, brier=0.0)
    report["metrics"]["expected_match_cases"] = 0
    report["metrics"]["correct_target_cases"] = 0
    report["metrics"]["auto_pass_cases"] = 0
    artifact = _artifact(version=1, report=report, approval_ref="MR-1")

    with pytest.raises(PromotionRejected, match="no positive mapping case"):
        validate_promotion(artifact)


def test_promotion_rejects_failed_evaluation_and_non_improvement():
    failed = _artifact(
        version=1,
        report=_report(
            version="candidate-1",
            exact=0.5,
            false_auto_pass=0.0,
            brier=0.5,
            passed=False,
        ),
        approval_ref="MR-2",
    )
    with pytest.raises(PromotionRejected, match="did not pass"):
        validate_promotion(failed)

    current = _artifact(
        version=1,
        report=_report(
            version="candidate-1", exact=1.0, false_auto_pass=0.0, brier=0.01
        ),
        approval_ref="MR-3",
    )
    candidate = _artifact(
        version=2,
        report=_report(
            version="candidate-2", exact=1.0, false_auto_pass=0.0, brier=0.01
        ),
        approval_ref="MR-4",
    )
    with pytest.raises(PromotionRejected, match="strictly improve"):
        validate_promotion(candidate, current=current)
