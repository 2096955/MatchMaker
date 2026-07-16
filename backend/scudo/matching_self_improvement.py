"""Contracts and evaluation primitives for matching self-improvement.

This module is deliberately offline-safe and persistence-agnostic. It gives
both matching surfaces the same language for:

* versioned, human-labelled golden cases;
* normalized predictions from the agent and deterministic matcher result
  shapes;
* holdout metrics, including abstention and false-auto-pass rates; and
* an explicit evaluation + approval gate for learning artifacts.

The deterministic matcher remains authoritative at request time. Nothing in
this module changes a live mapping or promotes an artifact by itself.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


GoldenSplit = Literal["train", "holdout", "adversarial"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_identity(vendor: str, vendor_product_ref: str) -> tuple[str, str]:
    return (vendor.strip().lower(), vendor_product_ref.strip().lower())


class GoldenCase(BaseModel):
    """One labelled matching example.

    A case is either a positive mapping or an explicit abstention case. The
    input identity may occur only once in a golden set, preventing a product
    from leaking between train and holdout under different labels.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1)
    vendor: str = Field(..., min_length=1)
    vendor_product_ref: str = Field(..., min_length=1)
    product_name: str = ""
    description: str = ""
    expected_target_iri: Optional[str] = None
    expected_abstain: bool = False
    split: GoldenSplit = "holdout"
    taxonomy_group: str = Field(default="unstratified", min_length=1)
    source: str = Field(default="human", min_length=1)
    source_ref: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_label(self) -> "GoldenCase":
        if self.expected_abstain and self.expected_target_iri:
            raise ValueError(
                "expected_abstain cases cannot also define expected_target_iri"
            )
        if not self.expected_abstain and not self.expected_target_iri:
            raise ValueError(
                "positive golden cases require expected_target_iri or "
                "expected_abstain=true"
            )
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return _canonical_identity(self.vendor, self.vendor_product_ref)


class GoldenSet(BaseModel):
    """A versioned, leakage-checked collection of labelled cases."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1)
    cases: list[GoldenCase] = Field(..., min_length=1)
    created_at: str = Field(default_factory=_utc_now)
    source: str = Field(default="curated", min_length=1)

    @model_validator(mode="after")
    def _validate_unique_cases(self) -> "GoldenSet":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("golden set contains duplicate case_id values")

        identities = [case.identity for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "golden set contains duplicate vendor/vendor_product_ref identities; "
                "this would leak cases across splits"
            )
        if not any(case.split == "holdout" for case in self.cases):
            raise ValueError("golden set must contain at least one holdout case")
        return self

    def cases_for_split(self, split: GoldenSplit) -> list[GoldenCase]:
        return [case for case in self.cases if case.split == split]

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        version: str,
        source: str = "curated",
    ) -> "GoldenSet":
        rows: list[GoldenCase] = []
        with Path(path).open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                try:
                    payload = json.loads(text)
                    rows.append(GoldenCase.model_validate(payload))
                except Exception as exc:  # noqa: BLE001 - add file context
                    raise ValueError(
                        f"invalid golden-set row at {path}:{line_no}: {exc}"
                    ) from exc
        if not rows:
            raise ValueError(f"golden set {path} contains no cases")
        return cls(version=version, cases=rows, source=source)


def load_golden_set(
    path: str | Path, *, version: str, source: str = "curated"
) -> GoldenSet:
    """Load and validate a JSONL golden set."""

    return GoldenSet.from_jsonl(path, version=version, source=source)


class MatchingPrediction(BaseModel):
    """Normalized result shape shared by both matching implementations."""

    model_config = ConfigDict(extra="forbid")

    target_iri: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = Field(default="needs_review", min_length=1)
    band: Optional[str] = None
    abstained: bool = False
    auto_pass: bool = False
    rationale: str = ""

    @classmethod
    def from_result(cls, result: Any) -> "MatchingPrediction":
        """Adapt either ``scudo.schemas.MappingResult`` or the engine result.

        The two result models intentionally remain separate. This adapter is
        the only shared surface needed by the evaluator.
        """

        if isinstance(result, cls):
            return result
        if isinstance(result, BaseModel):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            raise TypeError(
                "matching result must be a Pydantic model, dict, or "
                "MatchingPrediction"
            )

        target = payload.get("mapped_node_iri")
        if target is None:
            target = payload.get("proposed_target_iri")

        raw_status = payload.get("status")
        status = getattr(raw_status, "value", raw_status) or ""
        status = str(status).lower()
        requires_review = bool(payload.get("requires_human_review", False))
        band = payload.get("band")
        band = getattr(band, "value", band)

        auto_pass = bool(payload.get("auto_pass", False))
        auto_pass = auto_pass or status in {
            "auto_mapped",
            "published",
            "approved",
        }
        # Agent MappingResult has no status field; a pass/high result without a
        # human-review flag is its auto-publish candidate.
        if not status and band in {"high", "pass"} and not requires_review:
            auto_pass = True

        abstained = bool(payload.get("abstained", False))
        abstained = abstained or requires_review or not target
        abstained = abstained or status in {
            "needs_review",
            "out_of_scope",
            "rejected",
            "retry",
            "hitl",
        }
        if not status and band in {"medium", "low", "borderline", "fail", "n/a"}:
            abstained = True

        return cls(
            target_iri=target,
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            status=status or ("needs_review" if abstained else "mapped"),
            band=str(band) if band is not None else None,
            abstained=abstained,
            auto_pass=auto_pass and not abstained,
            rationale=str(payload.get("rationale", "") or ""),
        )


class EvaluationPolicy(BaseModel):
    """Promotion thresholds for an evaluation run.

    Defaults are conservative. Callers can use a stricter policy for a
    production gate or a small policy in unit tests with a deliberately tiny
    fixture.
    """

    model_config = ConfigDict(extra="forbid")

    min_cases: int = Field(default=1, ge=1)
    min_exact_match_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    min_abstention_recall: float = Field(default=1.0, ge=0.0, le=1.0)
    max_false_auto_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_brier_score: float = Field(default=0.10, ge=0.0, le=1.0)


class EvaluationMetrics(BaseModel):
    """Metrics that make a matching change reviewable."""

    model_config = ConfigDict(extra="forbid")

    total_cases: int = Field(..., ge=0)
    expected_match_cases: int = Field(..., ge=0)
    expected_abstain_cases: int = Field(..., ge=0)
    predicted_abstain_cases: int = Field(..., ge=0)
    auto_pass_cases: int = Field(..., ge=0)
    correct_target_cases: int = Field(..., ge=0)
    correct_abstention_cases: int = Field(..., ge=0)
    false_auto_pass_cases: int = Field(..., ge=0)
    exact_match_rate: float = Field(..., ge=0.0, le=1.0)
    abstention_recall: float = Field(..., ge=0.0, le=1.0)
    coverage: float = Field(..., ge=0.0, le=1.0)
    false_auto_pass_rate: float = Field(..., ge=0.0, le=1.0)
    calibration_mae: float = Field(..., ge=0.0, le=1.0)
    brier_score: float = Field(..., ge=0.0, le=1.0)


class EvaluationReport(BaseModel):
    """Immutable-in-practice record of one candidate evaluation."""

    model_config = ConfigDict(extra="forbid")

    candidate_version: str = Field(..., min_length=1)
    golden_set_version: str = Field(..., min_length=1)
    split: Literal["holdout", "adversarial"] = "holdout"
    evaluated_at: str = Field(default_factory=_utc_now)
    case_ids: list[str] = Field(..., min_length=1)
    metrics: EvaluationMetrics
    by_vendor: dict[str, EvaluationMetrics] = Field(default_factory=dict)
    by_taxonomy_group: dict[str, EvaluationMetrics] = Field(default_factory=dict)
    policy: EvaluationPolicy
    passed: bool
    baseline_version: Optional[str] = None


class PromotionApproval(BaseModel):
    """Named approval required before a learning artifact can affect prompts."""

    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(..., min_length=1)
    approval_ref: str = Field(..., min_length=1)
    approved_at: str = Field(default_factory=_utc_now)
    rationale: str = Field(..., min_length=1)


class LearningArtifact(BaseModel):
    """A versioned artifact kept separate from ordinary trajectory memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(..., min_length=1)
    artifact_kind: Literal[
        "matching_skill", "prompt", "retrieval_weights", "matcher_variant"
    ]
    version: int = Field(..., ge=1)
    content: str = Field(..., min_length=1)
    created_at: str = Field(default_factory=_utc_now)
    source_trajectory_refs: list[str] = Field(default_factory=list)
    evaluation: EvaluationReport
    approval: PromotionApproval

    @property
    def live_eligible(self) -> bool:
        return self.evaluation.passed and self.approval is not None


class PromotionRejected(ValueError):
    """Raised when an artifact fails the offline-to-live promotion boundary."""


def _strictly_improves(
    candidate: EvaluationMetrics, baseline: EvaluationMetrics
) -> bool:
    """Require no regression in any load-bearing metric and one improvement."""

    no_regression = (
        candidate.exact_match_rate >= baseline.exact_match_rate
        and candidate.abstention_recall >= baseline.abstention_recall
        and candidate.false_auto_pass_rate <= baseline.false_auto_pass_rate
        and candidate.brier_score <= baseline.brier_score
    )
    strict = (
        candidate.exact_match_rate > baseline.exact_match_rate
        or candidate.abstention_recall > baseline.abstention_recall
        or candidate.false_auto_pass_rate < baseline.false_auto_pass_rate
        or candidate.brier_score < baseline.brier_score
    )
    return no_regression and strict


def validate_promotion(
    candidate: LearningArtifact,
    *,
    current: Optional[LearningArtifact] = None,
) -> None:
    """Raise unless the candidate is evaluated, approved, and better."""

    if candidate.approval is None:
        raise PromotionRejected("candidate has no named approval")
    if not candidate.evaluation.passed:
        raise PromotionRejected("candidate evaluation did not pass its policy")
    if candidate.evaluation.split != "holdout":
        raise PromotionRejected("promotion requires a holdout evaluation")
    if current is None:
        return
    if candidate.version <= current.version:
        raise PromotionRejected(
            f"candidate version {candidate.version} is not newer than "
            f"current version {current.version}"
        )
    if not _strictly_improves(candidate.evaluation.metrics, current.evaluation.metrics):
        raise PromotionRejected(
            "candidate does not strictly improve the current artifact without "
            "regressing exact-match rate, false-auto-pass rate, or Brier score"
        )


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics_for(
    cases: Iterable[GoldenCase],
    predictions: dict[str, MatchingPrediction],
) -> EvaluationMetrics:
    selected = list(cases)
    expected_matches = sum(not case.expected_abstain for case in selected)
    expected_abstains = len(selected) - expected_matches
    predicted_abstains = 0
    auto_passes = 0
    correct_targets = 0
    correct_abstentions = 0
    false_auto_passes = 0
    calibration_errors: list[float] = []
    brier_scores: list[float] = []

    for case in selected:
        prediction = predictions[case.case_id]
        if prediction.abstained:
            predicted_abstains += 1
        if prediction.auto_pass:
            auto_passes += 1

        if (
            not case.expected_abstain
            and not prediction.abstained
            and prediction.target_iri == case.expected_target_iri
        ):
            correct_targets += 1
        if case.expected_abstain and prediction.abstained:
            correct_abstentions += 1
        if prediction.auto_pass and (
            case.expected_abstain
            or prediction.target_iri != case.expected_target_iri
        ):
            false_auto_passes += 1

        if not case.expected_abstain:
            target_correct = (
                not prediction.abstained
                and prediction.target_iri == case.expected_target_iri
            )
            expected_confidence = 1.0 if target_correct else 0.0
            calibration_errors.append(
                abs(prediction.confidence - expected_confidence)
            )
            brier_scores.append((prediction.confidence - expected_confidence) ** 2)

    return EvaluationMetrics(
        total_cases=len(selected),
        expected_match_cases=expected_matches,
        expected_abstain_cases=expected_abstains,
        predicted_abstain_cases=predicted_abstains,
        auto_pass_cases=auto_passes,
        correct_target_cases=correct_targets,
        correct_abstention_cases=correct_abstentions,
        false_auto_pass_cases=false_auto_passes,
        exact_match_rate=(
            1.0
            if expected_matches == 0
            else _safe_rate(correct_targets, expected_matches)
        ),
        abstention_recall=_safe_rate(correct_abstentions, expected_abstains),
        coverage=_safe_rate(len(selected) - predicted_abstains, len(selected)),
        false_auto_pass_rate=_safe_rate(false_auto_passes, auto_passes),
        calibration_mae=_safe_rate(
            int(round(sum(calibration_errors) * 1_000_000)), len(calibration_errors)
        )
        / 1_000_000,
        brier_score=_safe_rate(
            int(round(sum(brier_scores) * 1_000_000)), len(brier_scores)
        )
        / 1_000_000,
    )


def evaluate_golden_set(
    golden_set: GoldenSet,
    predictor: Callable[[GoldenCase], Any],
    *,
    candidate_version: str,
    split: Literal["holdout", "adversarial"] = "holdout",
    policy: Optional[EvaluationPolicy] = None,
    baseline_version: Optional[str] = None,
) -> EvaluationReport:
    """Evaluate a candidate only on the requested, leakage-checked split."""

    cases = golden_set.cases_for_split(split)
    if len(cases) < 1:
        raise ValueError(f"golden set has no {split} cases")
    selected_policy = policy or EvaluationPolicy()
    if len(cases) < selected_policy.min_cases:
        raise ValueError(
            f"{split} evaluation has {len(cases)} cases but policy requires "
            f"{selected_policy.min_cases}"
        )

    predictions: dict[str, MatchingPrediction] = {}
    for case in cases:
        predictions[case.case_id] = MatchingPrediction.from_result(predictor(case))

    metrics = _metrics_for(cases, predictions)
    by_vendor: dict[str, EvaluationMetrics] = {}
    by_taxonomy_group: dict[str, EvaluationMetrics] = {}
    vendors: dict[str, list[GoldenCase]] = defaultdict(list)
    taxonomy_groups: dict[str, list[GoldenCase]] = defaultdict(list)
    for case in cases:
        vendors[case.vendor.strip().lower()].append(case)
        taxonomy_groups[case.taxonomy_group].append(case)
    for vendor, vendor_cases in vendors.items():
        by_vendor[vendor] = _metrics_for(vendor_cases, predictions)
    for group, group_cases in taxonomy_groups.items():
        by_taxonomy_group[group] = _metrics_for(group_cases, predictions)
    passed = (
        metrics.total_cases >= selected_policy.min_cases
        and metrics.exact_match_rate >= selected_policy.min_exact_match_rate
        and (
            metrics.expected_abstain_cases == 0
            or metrics.abstention_recall >= selected_policy.min_abstention_recall
        )
        and metrics.false_auto_pass_rate
        <= selected_policy.max_false_auto_pass_rate
        and metrics.brier_score <= selected_policy.max_brier_score
    )
    return EvaluationReport(
        candidate_version=candidate_version,
        golden_set_version=golden_set.version,
        split=split,
        case_ids=[case.case_id for case in cases],
        metrics=metrics,
        by_vendor=by_vendor,
        by_taxonomy_group=by_taxonomy_group,
        policy=selected_policy,
        passed=passed,
        baseline_version=baseline_version,
    )


__all__ = [
    "EvaluationMetrics",
    "EvaluationPolicy",
    "EvaluationReport",
    "GoldenCase",
    "GoldenSet",
    "LearningArtifact",
    "MatchingPrediction",
    "PromotionApproval",
    "PromotionRejected",
    "evaluate_golden_set",
    "load_golden_set",
    "validate_promotion",
]
