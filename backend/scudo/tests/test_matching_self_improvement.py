from __future__ import annotations

import hashlib
import json

import pytest

from scudo.matching_self_improvement import (
    EVALUATION_PASS_CUT,
    EvaluationMetrics,
    EvaluationPolicy,
    GoldenCase,
    GoldenSet,
    EvaluationAttestation,
    SignedEvaluationEnvelope,
    LearningArtifact,
    LiveSkillPointer,
    MatchingPrediction,
    PromotionApproval,
    ProtectedPromotionReceipt,
    PromotionRejected,
    TrustedEvaluationEvidence,
    evaluate_golden_set,
    issue_evaluation_attestation,
    issue_signed_evaluation_envelope,
    issue_live_pointer,
    promotion_receipt_for,
    verify_promotion_receipt,
    verify_evaluation_attestation,
    verify_signed_evaluation_envelope,
    verify_live_pointer,
    load_golden_set,
    trusted_evidence_for,
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


def test_auto_pass_requires_canonical_pass_cut():
    with pytest.raises(ValueError, match="confidence.*0.80"):
        MatchingPrediction(
            target_iri="jpmorgan:data:cdao:EquityPrices",
            confidence=0.79,
            status="auto_mapped",
            auto_pass=True,
        )

    accepted = MatchingPrediction(
        target_iri="jpmorgan:data:cdao:EquityPrices",
        confidence=EVALUATION_PASS_CUT,
        status="auto_mapped",
        auto_pass=True,
    )
    assert accepted.confidence == 0.80


def test_evaluation_cannot_reproduce_subthreshold_auto_pass():
    golden = GoldenSet(version="cut", cases=[_positive("one")])

    with pytest.raises(ValueError, match="confidence.*0.80"):
        evaluate_golden_set(
            golden,
            lambda case: {
                "mapped_node_iri": case.expected_target_iri,
                "confidence": 0.79,
                "status": "auto_mapped",
            },
            candidate_version="subthreshold",
            artifact_content="unsafe",
            repeat_runs=2,
        )


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


def test_legacy_artifact_still_loads_but_cannot_promote_automatically():
    report = _report(version="candidate-1", exact=1.0, false_auto_pass=0.0, brier=0.01)
    artifact = _artifact(version=1, report=report, approval_ref="MR-1")

    assert artifact.evaluation.golden_set_hash is None
    with pytest.raises(PromotionRejected, match="protected evidence"):
        validate_promotion(artifact)

    protected = _evaluated_artifact(version=1, content="protected skill")
    unapproved = protected.model_copy(
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

    current = _evaluated_artifact(version=1, content="current skill")
    candidate = _evaluated_artifact(version=2, content="candidate skill")
    with pytest.raises(PromotionRejected, match="strictly improve"):
        validate_promotion(
            candidate,
            current=current,
            trusted_evidence=_trusted_evidence(candidate),
        )


def test_auto_publish_precision_counts_only_correct_automatic_publishes():
    golden = GoldenSet(
        version="golden-precision",
        cases=[_positive("correct"), _positive("wrong", vendor="ice")],
    )

    report = evaluate_golden_set(
        golden,
        lambda case: {
            "mapped_node_iri": (
                case.expected_target_iri
                if case.case_id == "correct"
                else "jpmorgan:data:cdao:Wrong"
            ),
            "confidence": 0.95,
            "status": "auto_mapped",
        },
        candidate_version="candidate-precision",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            min_auto_publish_precision=0.75,
            max_brier_score=1.0,
        ),
    )

    assert report.metrics.auto_pass_cases == 2
    assert report.metrics.correct_auto_pass_cases == 1
    assert report.metrics.auto_publish_precision == 0.5
    assert report.passed is False


def test_integrity_hashes_are_canonical_and_ignore_case_order():
    cases = [_positive("one"), _abstain("two")]
    first = GoldenSet(version="golden-hash", cases=cases)
    second = GoldenSet(version="golden-hash", cases=list(reversed(cases)))
    policy = EvaluationPolicy(max_brier_score=1.0)

    first_report = evaluate_golden_set(
        first,
        lambda case: {
            "mapped_node_iri": case.expected_target_iri,
            "confidence": 0.9,
            "status": "auto_mapped" if not case.expected_abstain else "needs_review",
            "requires_human_review": case.expected_abstain,
        },
        candidate_version="candidate-hash",
        policy=policy,
        artifact_content="protected skill text",
        repeat_runs=2,
    )
    second_report = evaluate_golden_set(
        second,
        lambda case: {
            "mapped_node_iri": case.expected_target_iri,
            "confidence": 0.9,
            "status": "auto_mapped" if not case.expected_abstain else "needs_review",
            "requires_human_review": case.expected_abstain,
        },
        candidate_version="candidate-hash",
        policy=policy,
        artifact_content="protected skill text",
        repeat_runs=2,
    )

    assert first_report.golden_set_hash == second_report.golden_set_hash
    assert first_report.case_hashes == second_report.case_hashes
    assert first_report.policy_hash == second_report.policy_hash
    assert first_report.metric_definition_hash == second_report.metric_definition_hash
    assert first_report.artifact_content_hash == second_report.artifact_content_hash
    assert len(first_report.golden_set_hash) == 64


def test_repeated_runs_record_stability_and_detect_variance():
    golden = GoldenSet(version="golden-stability", cases=[_positive("one")])
    calls = 0

    def predictor(case):
        nonlocal calls
        calls += 1
        return {
            "mapped_node_iri": (
                case.expected_target_iri if calls == 1 else "jpmorgan:data:cdao:Changed"
            ),
            "confidence": 0.9,
            "status": "auto_mapped",
        }

    report = evaluate_golden_set(
        golden,
        predictor,
        candidate_version="candidate-unstable",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            max_brier_score=1.0,
        ),
        artifact_content="unstable skill",
        repeat_runs=2,
    )

    assert report.repeat_run_count == 2
    assert len(report.run_hashes) == 2
    assert report.run_hashes[0] != report.run_hashes[1]
    assert report.stable is False
    assert report.passed is False


def _evaluated_artifact(
    *,
    version: int,
    content: str,
    wrong_target: bool = False,
) -> LearningArtifact:
    golden = GoldenSet(
        version="golden-protected",
        cases=[_positive("one"), _abstain("two")],
    )

    def predictor(case):
        if case.expected_abstain:
            return {"status": "needs_review", "requires_human_review": True}
        return {
            "mapped_node_iri": (
                "jpmorgan:data:cdao:Wrong" if wrong_target else case.expected_target_iri
            ),
            "confidence": 0.9,
            "status": "auto_mapped",
        }

    report = evaluate_golden_set(
        golden,
        predictor,
        candidate_version=f"candidate-{version}",
        policy=EvaluationPolicy(
            min_exact_match_rate=0.0,
            max_brier_score=1.0,
        ),
        artifact_content=content,
        repeat_runs=2,
    )
    return _artifact(
        version=version,
        report=report.model_dump(mode="json"),
        approval_ref=f"AUTO-{version}",
        content=content,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("golden_set_hash", "0" * 64, "golden set hash"),
        ("policy_hash", "0" * 64, "policy hash"),
        ("metric_definition_hash", "0" * 64, "metric definition hash"),
        ("artifact_content_hash", "0" * 64, "artifact content hash"),
        ("stable", False, "unstable"),
        ("split", "adversarial", "holdout"),
    ],
)
def test_promotion_rejects_forged_or_unprotected_report_fields(field, value, message):
    artifact = _evaluated_artifact(version=1, content="protected skill")
    forged_report = artifact.evaluation.model_copy(update={field: value})
    forged = artifact.model_copy(update={"evaluation": forged_report})

    with pytest.raises(PromotionRejected, match=message):
        validate_promotion(forged, trusted_evidence=_trusted_evidence(artifact))


def test_promotion_rejects_missing_protected_evidence_from_legacy_artifact():
    legacy = _artifact(
        version=1,
        report=_report(
            version="candidate-1",
            exact=1.0,
            false_auto_pass=0.0,
            brier=0.01,
        ),
        approval_ref="LEGACY-1",
    )

    with pytest.raises(PromotionRejected, match="protected"):
        validate_promotion(legacy)


def test_promotion_rejects_false_auto_publish_even_if_report_says_passed():
    artifact = _evaluated_artifact(
        version=1,
        content="unsafe skill",
        wrong_target=True,
    )
    forged_report = artifact.evaluation.model_copy(update={"passed": True})
    forged = artifact.model_copy(update={"evaluation": forged_report})

    with pytest.raises(PromotionRejected, match="false auto-publish"):
        validate_promotion(forged, trusted_evidence=_trusted_evidence(artifact))


def test_promotion_rejects_candidate_evaluated_against_different_protection():
    current = _evaluated_artifact(version=1, content="current skill")
    candidate = _evaluated_artifact(version=2, content="candidate skill")
    different_current_report = current.evaluation.model_copy(
        update={"golden_set_hash": "f" * 64}
    )
    different_current = current.model_copy(
        update={"evaluation": different_current_report}
    )

    with pytest.raises(PromotionRejected, match="same protected"):
        validate_promotion(
            candidate,
            current=different_current,
            trusted_evidence=_trusted_evidence(candidate),
        )


def _trusted_evidence(artifact: LearningArtifact) -> TrustedEvaluationEvidence:
    golden = GoldenSet(
        version="golden-protected",
        cases=[_positive("one"), _abstain("two")],
    )
    predictions = (
        {
            "one": MatchingPrediction(
                target_iri="jpmorgan:data:cdao:EquityPrices",
                confidence=0.9,
                status="auto_mapped",
                auto_pass=True,
            ),
            "two": MatchingPrediction(
                confidence=0.0,
                status="needs_review",
                abstained=True,
            ),
        },
    ) * 2
    return trusted_evidence_for(
        golden,
        policy=artifact.evaluation.policy,
        prediction_runs=predictions,
    )


def test_automatic_promotion_requires_authoritative_evidence():
    artifact = _evaluated_artifact(version=1, content="protected skill")

    with pytest.raises(PromotionRejected, match="trusted evaluation evidence"):
        validate_promotion(artifact)

    validate_promotion(artifact, trusted_evidence=_trusted_evidence(artifact))


def test_forged_self_consistent_report_fails_trusted_evidence():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    trusted = _trusted_evidence(artifact)
    forged_case_hashes = dict(artifact.evaluation.case_hashes)
    forged_case_hashes["one"] = "a" * 64
    forged_case_manifest_hash = _canonical_hash(
        [
            {"case_id": case_id, "hash": forged_case_hashes[case_id]}
            for case_id in sorted(forged_case_hashes)
        ]
    )
    forged_golden_hash = _canonical_hash(
        {
            "version": artifact.evaluation.golden_set_version,
            "split": "holdout",
            "cases": [
                {"case_id": case_id, "hash": forged_case_hashes[case_id]}
                for case_id in sorted(forged_case_hashes)
            ],
        }
    )
    forged_report = artifact.evaluation.model_copy(
        update={
            "case_hashes": forged_case_hashes,
            "case_manifest_hash": forged_case_manifest_hash,
            "golden_set_hash": forged_golden_hash,
        }
    )
    forged = artifact.model_copy(update={"evaluation": forged_report})

    with pytest.raises(PromotionRejected, match="trusted evaluation evidence"):
        validate_promotion(forged, trusted_evidence=trusted)


def test_fabricated_perfect_metrics_and_run_hashes_fail_recomputation():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    wrong_prediction = MatchingPrediction(
        target_iri="jpmorgan:data:cdao:Wrong",
        confidence=0.99,
        status="auto_mapped",
        auto_pass=True,
    )
    evidence = trusted_evidence_for(
        GoldenSet(
            version="golden-protected",
            cases=[_positive("one"), _abstain("two")],
        ),
        policy=artifact.evaluation.policy,
        prediction_runs=(
            {
                "one": wrong_prediction,
                "two": MatchingPrediction(
                    confidence=0.0,
                    status="needs_review",
                    abstained=True,
                ),
            },
        )
        * 2,
    )
    copied_hash_report = artifact.evaluation.model_copy(
        update={
            "golden_set_hash": evidence.manifest.golden_set_hash,
            "case_manifest_hash": evidence.manifest.case_manifest_hash,
            "policy_hash": evidence.manifest.policy_hash,
            "metric_definition_hash": evidence.manifest.metric_definition_hash,
            "passed": True,
        }
    )
    forged = artifact.model_copy(update={"evaluation": copied_hash_report})

    with pytest.raises(PromotionRejected, match="recomputed trusted evaluation"):
        validate_promotion(forged, trusted_evidence=evidence)


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def test_promotion_recomputes_policy_and_metric_consistency():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    trusted = _trusted_evidence(artifact)
    forged_metrics = artifact.evaluation.metrics.model_copy(
        update={
            "correct_target_cases": 0,
            "exact_match_rate": 1.0,
            "brier_score": 0.9,
        }
    )
    forged_report = artifact.evaluation.model_copy(
        update={"metrics": forged_metrics, "passed": True}
    )
    forged = artifact.model_copy(update={"evaluation": forged_report})

    with pytest.raises(PromotionRejected, match="metric consistency|policy"):
        validate_promotion(forged, trusted_evidence=trusted)


def test_forged_calibration_mae_is_rejected_by_recomputation():
    current = _evaluated_artifact(version=1, content="current skill")
    candidate = _evaluated_artifact(version=2, content="candidate skill")
    worse_metrics = candidate.evaluation.metrics.model_copy(
        update={
            "exact_match_rate": 1.0,
            "calibration_mae": current.evaluation.metrics.calibration_mae + 0.01,
            "brier_score": max(0.0, current.evaluation.metrics.brier_score - 0.001),
        }
    )
    worse_report = candidate.evaluation.model_copy(update={"metrics": worse_metrics})
    worse = candidate.model_copy(update={"evaluation": worse_report})

    with pytest.raises(PromotionRejected, match="recomputed trusted evaluation"):
        validate_promotion(
            worse,
            current=current,
            trusted_evidence=_trusted_evidence(candidate),
        )


def test_matching_prediction_rejects_contradictory_direct_state():
    with pytest.raises(ValueError, match="auto_pass.*abstained"):
        MatchingPrediction(
            target_iri="jpmorgan:data:cdao:EquityPrices",
            confidence=0.9,
            status="auto_mapped",
            auto_pass=True,
            abstained=True,
        )


@pytest.mark.parametrize(
    ("prediction", "message"),
    [
        (
            {"confidence": 0.9, "status": "auto_mapped", "auto_pass": True},
            "requires target_iri",
        ),
        (
            {
                "target_iri": "target",
                "confidence": 0.9,
                "status": "rejected",
                "auto_pass": True,
            },
            "publish-compatible",
        ),
        (
            {
                "target_iri": "target",
                "confidence": 0.9,
                "status": "auto_mapped",
                "band": "fail",
                "auto_pass": True,
            },
            "incompatible confidence band",
        ),
        (
            {
                "target_iri": "target",
                "confidence": 0.9,
                "status": "mapped",
                "band": "low",
                "auto_pass": True,
            },
            "requires pass or high band",
        ),
    ],
)
def test_matching_prediction_rejects_unsafe_auto_pass_states(prediction, message):
    with pytest.raises(ValueError, match=message):
        MatchingPrediction.model_validate(prediction)


def test_matching_prediction_accepts_valid_engine_and_agent_pass_states():
    engine = MatchingPrediction(
        target_iri="target",
        confidence=0.9,
        status="auto_mapped",
        auto_pass=True,
    )
    agent = MatchingPrediction(
        target_iri="target",
        confidence=0.9,
        status="mapped",
        band="high",
        auto_pass=True,
    )

    assert engine.auto_pass
    assert agent.auto_pass


@pytest.mark.parametrize("status", ["auto_mapped", "published", "approved"])
def test_publication_final_status_requires_auto_pass(status):
    with pytest.raises(ValueError, match="publication-final.*auto_pass"):
        MatchingPrediction(
            target_iri="target",
            confidence=0.99,
            status=status,
            auto_pass=False,
        )


def test_wrong_published_auto_pass_false_cannot_enter_twenty_case_evaluation():
    golden = GoldenSet(
        version="published-freeze",
        cases=[
            GoldenCase(
                case_id=f"case-{index}",
                vendor="vendor",
                vendor_product_ref=f"PRODUCT-{index}",
                expected_target_iri="target",
            )
            for index in range(20)
        ],
    )

    def predictor(case):
        if case.case_id == "case-19":
            return MatchingPrediction(
                target_iri="wrong",
                confidence=0.99,
                status="published",
                auto_pass=False,
            )
        return MatchingPrediction(
            target_iri="target",
            confidence=0.99,
            status="auto_mapped",
            auto_pass=True,
        )

    with pytest.raises(ValueError, match="publication-final.*auto_pass"):
        evaluate_golden_set(
            golden,
            predictor,
            candidate_version="candidate-published-freeze",
            policy=EvaluationPolicy(max_brier_score=1.0),
        )


def test_protected_candidate_can_replace_legacy_without_metric_downgrade():
    candidate = _evaluated_artifact(version=2, content="protected skill")
    legacy_report = _report(
        version="candidate-1",
        exact=0.5,
        false_auto_pass=0.0,
        brier=0.5,
    )
    legacy = _artifact(
        version=1,
        report=legacy_report,
        approval_ref="LEGACY",
        content="legacy skill",
    )

    validate_promotion(
        candidate,
        current=legacy,
        trusted_evidence=_trusted_evidence(candidate),
    )

    stronger_legacy_report = _report(
        version="candidate-1",
        exact=1.0,
        false_auto_pass=0.0,
        brier=0.0,
    )
    stronger_legacy_report["metrics"]["calibration_mae"] = 0.0
    stronger_legacy = _artifact(
        version=1,
        report=stronger_legacy_report,
        approval_ref="LEGACY-STRONG",
        content="legacy strong skill",
    )
    with pytest.raises(PromotionRejected, match="strictly improve"):
        validate_promotion(
            candidate,
            current=stronger_legacy,
            trusted_evidence=_trusted_evidence(candidate),
        )


def test_trusted_evidence_factory_is_deterministic():
    created_at = "2026-08-12T00:00:00+00:00"
    golden = GoldenSet(
        version="manifest-1",
        cases=[_positive("one"), _abstain("two")],
        created_at=created_at,
    )
    policy = EvaluationPolicy(max_brier_score=1.0)

    predictions = (
        {
            "one": MatchingPrediction(
                target_iri="jpmorgan:data:cdao:EquityPrices",
                confidence=0.9,
                status="auto_mapped",
                auto_pass=True,
            ),
            "two": MatchingPrediction(abstained=True),
        },
    ) * 2
    first = trusted_evidence_for(
        golden, policy=policy, split="holdout", prediction_runs=predictions
    )
    second = trusted_evidence_for(
        GoldenSet(
            version="manifest-1",
            cases=list(reversed(golden.cases)),
            created_at=created_at,
        ),
        policy=policy,
        split="holdout",
        prediction_runs=predictions,
    )

    assert first == second


def test_protected_receipt_binds_artifact_report_and_evidence():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)
    attestation = issue_evaluation_attestation(
        artifact.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        evaluator_id="test-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="test-promotion-key",
    )

    receipt = promotion_receipt_for(
        artifact,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        signing_key="test-promotion-key",
        evaluation_signing_key="evaluation-key",
    )

    assert isinstance(receipt, ProtectedPromotionReceipt)
    assert len(receipt.report_digest) == 64
    assert len(receipt.evidence_digest) == 64
    assert receipt.artifact_id == artifact.artifact_id
    assert receipt.artifact_version == artifact.version
    assert receipt.artifact_kind == artifact.artifact_kind
    assert receipt.artifact_content_hash == artifact.evaluation.artifact_content_hash
    assert verify_promotion_receipt(
        artifact,
        receipt,
        signing_key="test-promotion-key",
        evaluation_signing_key="evaluation-key",
    )


def test_protected_receipt_rejects_substitution_forgery_and_missing_key():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)
    attestation = issue_evaluation_attestation(
        artifact.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        evaluator_id="test-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="test-promotion-key",
    )
    receipt = promotion_receipt_for(
        artifact,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        signing_key="test-promotion-key",
        evaluation_signing_key="evaluation-key",
    )

    substituted = receipt.model_copy(update={"evidence_digest": "f" * 64})
    forged = receipt.model_copy(update={"signature": "0" * 64})
    tampered_artifact = artifact.model_copy(update={"content": "tampered skill"})

    assert not verify_promotion_receipt(
        artifact,
        substituted,
        signing_key="test-promotion-key",
    )
    assert not verify_promotion_receipt(
        artifact,
        forged,
        signing_key="test-promotion-key",
    )
    assert not verify_promotion_receipt(
        artifact,
        receipt,
        signing_key=None,
    )
    assert not verify_promotion_receipt(
        tampered_artifact,
        receipt,
        signing_key="test-promotion-key",
    )


def test_evaluation_attestation_requires_distinct_dedicated_key():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)

    attestation = issue_evaluation_attestation(
        artifact.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )

    assert isinstance(attestation, EvaluationAttestation)
    assert verify_evaluation_attestation(
        artifact.evaluation,
        attestation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )
    assert not verify_evaluation_attestation(
        artifact.evaluation,
        attestation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        signing_key=None,
        promotion_key="promotion-key",
    )
    with pytest.raises(PromotionRejected, match="distinct"):
        issue_evaluation_attestation(
            artifact.evaluation,
            trusted_evidence=evidence,
            artifact_content=artifact.content,
            artifact_id=artifact.artifact_id,
            artifact_version=artifact.version,
            artifact_kind=artifact.artifact_kind,
            evaluator_id="protected-evaluator",
            evaluator_version="1",
            signing_key="same-key",
            promotion_key="same-key",
        )


def test_promotion_receipt_requires_evaluator_attestation_and_full_artifact_digest():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)
    attestation = issue_evaluation_attestation(
        artifact.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )

    with pytest.raises(PromotionRejected, match="evaluation attestation"):
        promotion_receipt_for(
            artifact,
            trusted_evidence=evidence,
            signing_key="promotion-key",
            evaluation_signing_key="evaluation-key",
        )

    receipt = promotion_receipt_for(
        artifact,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        signing_key="promotion-key",
        evaluation_signing_key="evaluation-key",
    )
    metadata_tampered = artifact.model_copy(
        update={"source_trajectory_refs": ["forged-trajectory"]}
    )
    assert not verify_promotion_receipt(
        metadata_tampered,
        receipt,
        signing_key="promotion-key",
        evaluation_signing_key="evaluation-key",
    )


def test_attestation_for_content_a_cannot_promote_content_b():
    artifact_a = _evaluated_artifact(version=1, content="content A")
    evidence = _trusted_evidence(artifact_a)
    attestation = issue_evaluation_attestation(
        artifact_a.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact_a.content,
        artifact_id=artifact_a.artifact_id,
        artifact_version=artifact_a.version,
        artifact_kind=artifact_a.artifact_kind,
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )
    report_b = artifact_a.evaluation.model_copy(
        update={
            "artifact_content_hash": hashlib.sha256(
                json.dumps(
                    "content B",
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
        }
    )
    artifact_b = artifact_a.model_copy(
        update={"content": "content B", "evaluation": report_b}
    )

    with pytest.raises(PromotionRejected, match="attestation|identity|content"):
        promotion_receipt_for(
            artifact_b,
            trusted_evidence=evidence,
            evaluation_attestation=attestation,
            signing_key="promotion-key",
            evaluation_signing_key="evaluation-key",
        )


def test_receipt_verification_rejects_equal_keys():
    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)
    attestation = issue_evaluation_attestation(
        artifact.evaluation,
        trusted_evidence=evidence,
        artifact_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        evaluator_id="protected-evaluator",
        evaluator_version="1",
        signing_key="evaluation-key",
        promotion_key="promotion-key",
    )
    receipt = promotion_receipt_for(
        artifact,
        trusted_evidence=evidence,
        evaluation_attestation=attestation,
        signing_key="promotion-key",
        evaluation_signing_key="evaluation-key",
    )

    assert not verify_promotion_receipt(
        artifact,
        receipt,
        signing_key="same-key",
        evaluation_signing_key="same-key",
    )


def test_ed25519_signed_envelope_verifies_with_public_key_only():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    artifact = _evaluated_artifact(version=1, content="protected skill")
    evidence = _trusted_evidence(artifact)
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    adversarial_golden = GoldenSet(
        version="adversarial",
        cases=[
            _positive("required-holdout"),
            _positive("adversarial", split="adversarial", vendor="ice"),
        ],
    )
    adversarial_evidence = trusted_evidence_for(
        adversarial_golden,
        policy=artifact.evaluation.policy,
        split="adversarial",
        prediction_runs=(
            {
                "adversarial": MatchingPrediction(
                    target_iri="jpmorgan:data:cdao:EquityPrices",
                    confidence=0.9,
                    status="auto_mapped",
                    auto_pass=True,
                )
            },
        )
        * 2,
    )

    envelope = issue_signed_evaluation_envelope(
        candidate_content=artifact.content,
        artifact_id=artifact.artifact_id,
        artifact_version=artifact.version,
        artifact_kind=artifact.artifact_kind,
        trusted_evidence=evidence,
        adversarial_evidence=adversarial_evidence,
        candidate_version=artifact.evaluation.candidate_version,
        baseline_version=artifact.evaluation.baseline_version,
        evaluator_id="separate-evaluator",
        evaluator_version="1",
        private_key_pem=private_pem,
    )

    assert isinstance(envelope, SignedEvaluationEnvelope)
    assert verify_signed_evaluation_envelope(envelope, public_key_pem=public_pem)
    tampered = envelope.model_copy(update={"candidate_content": "tampered"})
    assert not verify_signed_evaluation_envelope(tampered, public_key_pem=public_pem)


def test_live_pointer_signature_binds_monotonic_chain():
    pointer = issue_live_pointer(
        artifact_key="skill:matching:artifact:2",
        artifact_version=2,
        artifact_digest="a" * 64,
        predecessor_version=1,
        predecessor_digest="b" * 64,
        sequence=2,
        signing_key="promotion-key",
    )

    assert isinstance(pointer, LiveSkillPointer)
    assert verify_live_pointer(pointer, signing_key="promotion-key")
    assert not verify_live_pointer(
        pointer.model_copy(update={"sequence": 1}),
        signing_key="promotion-key",
    )
