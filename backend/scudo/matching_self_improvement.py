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

import base64
import hmac
import json
import os
from hashlib import sha256
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


GoldenSplit = Literal["train", "holdout", "adversarial"]
PredictionStatus = Literal[
    "mapped",
    "auto_mapped",
    "published",
    "approved",
    "needs_review",
    "out_of_scope",
    "rejected",
    "retry",
    "hitl",
]
PredictionBand = Literal["pass", "high", "borderline", "medium", "fail", "low", "n/a"]
EVALUATION_PASS_CUT = 0.80
HASH_PATTERN = r"^[0-9a-f]{64}$"
METRIC_DEFINITION = {
    "version": 1,
    "auto_publish_precision": "correct_auto_pass_cases / auto_pass_cases",
    "false_auto_pass_rate": "false_auto_pass_cases / auto_pass_cases",
    "exact_match_rate": "correct_target_cases / expected_match_cases",
    "abstention_recall": "correct_abstention_cases / expected_abstain_cases",
    "calibration": "positive expected-match cases only",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_identity(vendor: str, vendor_product_ref: str) -> tuple[str, str]:
    return (vendor.strip().lower(), vendor_product_ref.strip().lower())


def _canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


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
    status: PredictionStatus = "needs_review"
    band: Optional[PredictionBand] = None
    abstained: bool = False
    auto_pass: bool = False
    rationale: str = ""

    @model_validator(mode="after")
    def _validate_decision_state(self) -> "MatchingPrediction":
        publication_final = self.status in {"auto_mapped", "published", "approved"}
        if publication_final and not self.auto_pass:
            raise ValueError("publication-final status requires auto_pass=true")
        if self.auto_pass and self.abstained:
            raise ValueError("auto_pass and abstained cannot both be true")
        if self.auto_pass and not self.target_iri:
            raise ValueError("auto_pass requires target_iri")
        if self.auto_pass and self.confidence < EVALUATION_PASS_CUT:
            raise ValueError(
                f"auto_pass requires confidence >= {EVALUATION_PASS_CUT:.2f}"
            )
        if self.auto_pass and self.status not in {
            "mapped",
            "auto_mapped",
            "published",
            "approved",
        }:
            raise ValueError("auto_pass requires a publish-compatible status")
        if self.auto_pass:
            if self.status == "mapped" and self.band not in {"pass", "high"}:
                raise ValueError("mapped auto_pass requires pass or high band")
            if self.status in {
                "auto_mapped",
                "published",
                "approved",
            } and self.band not in {
                None,
                "pass",
                "high",
            }:
                raise ValueError("auto_pass has an incompatible confidence band")
        return self

    @classmethod
    def from_result(
        cls,
        result: Any,
        *,
        vendor: str = "",
        product_id: str = "",
    ) -> "MatchingPrediction":
        """Adapt either ``scudo.schemas.MappingResult`` or the engine result.

        The two result models intentionally remain separate. This adapter is
        the only shared surface needed by the evaluator. ``vendor`` and
        ``product_id`` are retained as no-op compatibility keywords for the
        former JPMC duplicate; identity belongs to the surrounding trajectory,
        not this normalized prediction.
        """

        if isinstance(result, cls):
            return result
        if isinstance(result, BaseModel):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            raise TypeError(
                "matching result must be a Pydantic model, dict, or MatchingPrediction"
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


class MonitoringPolicy(BaseModel):
    """Fixed post-promotion rollback thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["monitor-v1"] = "monitor-v1"
    min_total_samples: Literal[20] = 20
    min_auto_pass_samples: Literal[20] = 20
    min_auto_publish_precision: Literal[1.0] = 1.0
    max_false_auto_pass_rate: Literal[0.0] = 0.0
    require_no_publish_gate_violations: Literal[True] = True

    @property
    def digest(self) -> str:
        return _canonical_json_hash(self.model_dump(mode="json"))


class AuthoritativeMonitoringOutcome(BaseModel):
    """Outcome asserted only by the separate monitoring authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_iri: Optional[str] = None
    abstain: bool = False
    publish_gate_violations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_authoritative_outcome(self) -> "AuthoritativeMonitoringOutcome":
        if (self.target_iri is not None) == self.abstain:
            raise ValueError(
                "exactly one authoritative target or authoritative abstain is required"
            )
        return self


class MonitoringObservation(BaseModel):
    """One immutable prediction/outcome join issued by the authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_event_id: str = Field(..., min_length=1)
    source_record_digest: str = Field(..., pattern=HASH_PATTERN)
    observed_at: datetime
    artifact_key: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_digest: str = Field(..., pattern=HASH_PATTERN)
    pointer_sequence: int = Field(..., ge=1)
    prediction: MatchingPrediction
    authoritative_outcome: AuthoritativeMonitoringOutcome

    @model_validator(mode="after")
    def _validate_observed_at(self) -> "MonitoringObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("monitoring observation timestamp must be timezone-aware")
        return self


def monitoring_source_record_digest(record: dict[str, Any]) -> str:
    """Hash the immutable canonical source record, excluding its asserted digest."""

    normalized = MonitoringObservation.model_validate(
        {
            **record,
            "source_record_digest": "0" * 64,
        }
    ).model_dump(mode="json", exclude={"source_record_digest"})
    return _canonical_json_hash(normalized)


class SignedMonitoringEnvelope(BaseModel):
    """Authority-signed immutable observations for one monitoring window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    audience: str = Field(..., min_length=1)
    deployment_id: str = Field(..., min_length=1)
    key_id: str = Field(..., min_length=1)
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    observation_start: datetime
    observation_end: datetime
    window_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$")
    artifact_key: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_digest: str = Field(..., pattern=HASH_PATTERN)
    pointer_sequence: int = Field(..., ge=1)
    policy_version: Literal["monitor-v1"] = "monitor-v1"
    observations: tuple[MonitoringObservation, ...]
    signature: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_observations(self) -> "SignedMonitoringEnvelope":
        timestamps = (
            self.issued_at,
            self.not_before,
            self.expires_at,
            self.observation_start,
            self.observation_end,
        )
        if any(
            value.tzinfo is None or value.utcoffset() is None for value in timestamps
        ):
            raise ValueError("monitoring timestamps must be timezone-aware")
        if not (
            self.not_before <= self.issued_at <= self.expires_at
            and self.observation_start <= self.observation_end
            and self.observation_end <= self.issued_at
        ):
            raise ValueError("monitoring envelope temporal ordering is invalid")
        event_ids = [item.source_event_id for item in self.observations]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("monitoring source event IDs must be unique")
        expected = (
            self.artifact_key,
            self.artifact_version,
            self.artifact_digest,
            self.pointer_sequence,
        )
        if any(
            (
                item.artifact_key,
                item.artifact_version,
                item.artifact_digest,
                item.pointer_sequence,
            )
            != expected
            for item in self.observations
        ):
            raise ValueError("monitoring observation artifact binding mismatch")
        if any(
            not self.observation_start <= item.observed_at <= self.observation_end
            for item in self.observations
        ):
            raise ValueError("monitoring observation falls outside signed period")
        return self

    @property
    def policy_digest(self) -> str:
        return MonitoringPolicy().digest

    @property
    def input_digest(self) -> str:
        return _canonical_json_hash(self.model_dump(mode="json", exclude={"signature"}))


def _signed_monitoring_payload(envelope: SignedMonitoringEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json", exclude={"signature"})


def issue_signed_monitoring_envelope(
    *,
    window_id: str,
    artifact_key: str,
    artifact_version: int,
    artifact_digest: str,
    pointer_sequence: int,
    observations: Iterable[MonitoringObservation | dict[str, Any]],
    private_key_pem: str,
    audience: str,
    deployment_id: str,
    key_id: str,
    issued_at: datetime,
    not_before: datetime,
    expires_at: datetime,
    observation_start: datetime,
    observation_end: datetime,
) -> SignedMonitoringEnvelope:
    """Authority/test helper; private material must not enter monitor runtime."""

    unsigned = SignedMonitoringEnvelope(
        audience=audience,
        deployment_id=deployment_id,
        key_id=key_id,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        observation_start=observation_start,
        observation_end=observation_end,
        window_id=window_id,
        artifact_key=artifact_key,
        artifact_version=artifact_version,
        artifact_digest=artifact_digest,
        pointer_sequence=pointer_sequence,
        observations=tuple(
            item
            if isinstance(item, MonitoringObservation)
            else MonitoringObservation.model_validate(item)
            for item in observations
        ),
        signature="pending",
    )
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("monitoring private key must be Ed25519")
    canonical = json.dumps(
        _signed_monitoring_payload(unsigned),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return unsigned.model_copy(
        update={
            "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii")
        }
    )


def verify_signed_monitoring_envelope(
    envelope: SignedMonitoringEnvelope | dict[str, Any],
    *,
    public_key_pem: str,
) -> bool:
    """Verify an authority envelope using only the monitoring public key."""

    try:
        validated = (
            envelope
            if isinstance(envelope, SignedMonitoringEnvelope)
            else SignedMonitoringEnvelope.model_validate(envelope)
        )
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        canonical = json.dumps(
            _signed_monitoring_payload(validated),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        public_key.verify(base64.b64decode(validated.signature), canonical)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


class MonitoringEvaluation(BaseModel):
    """Metrics derived exclusively from monitoring samples."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(..., ge=0)
    auto_pass_count: int = Field(..., ge=0)
    correct_auto_pass_count: int = Field(..., ge=0)
    false_auto_pass_count: int = Field(..., ge=0)
    publish_gate_violation_count: int = Field(..., ge=0)
    auto_publish_precision: float = Field(..., ge=0.0, le=1.0)
    false_auto_pass_rate: float = Field(..., ge=0.0, le=1.0)
    sufficient_samples: bool
    breached: bool
    breach_reasons: tuple[str, ...] = ()


class MonitoringOutcome(BaseModel):
    """Immutable audit decision for one post-promotion monitoring window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: str = Field(..., min_length=1)
    artifact_key: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_digest: str = Field(..., pattern=HASH_PATTERN)
    pointer_sequence: int = Field(..., ge=1)
    input_digest: str = Field(..., pattern=HASH_PATTERN)
    policy_digest: str = Field(..., pattern=HASH_PATTERN)
    observations: tuple[MonitoringObservation, ...]
    policy: MonitoringPolicy
    evaluation: MonitoringEvaluation
    action: Literal["insufficient_samples", "retain", "rollback"]
    persisted: bool
    rollback_succeeded: bool = False
    reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_persistence_lifecycle(self) -> "MonitoringOutcome":
        if self.persisted == (self.action == "insufficient_samples"):
            raise ValueError("only complete monitoring outcomes may be persisted")
        if self.rollback_succeeded != (self.action == "rollback"):
            raise ValueError("rollback success must match rollback action")
        return self


def evaluate_monitoring_window(
    observations: Iterable[MonitoringObservation | dict[str, Any]],
) -> MonitoringEvaluation:
    """Derive metrics under the non-overridable protected monitoring policy."""

    policy = MonitoringPolicy()
    normalized = [
        observation
        if isinstance(observation, MonitoringObservation)
        else MonitoringObservation.model_validate(observation)
        for observation in observations
    ]
    event_ids = [item.source_event_id for item in normalized]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("monitoring source event IDs must be unique")
    auto_passes = [item for item in normalized if item.prediction.auto_pass]
    correct = sum(
        item.authoritative_outcome.target_iri is not None
        and not item.authoritative_outcome.abstain
        and item.prediction.target_iri == item.authoritative_outcome.target_iri
        for item in auto_passes
    )
    false = len(auto_passes) - correct
    gate_violations = sum(
        bool(item.authoritative_outcome.publish_gate_violations) for item in normalized
    )
    precision = _expected_rate(correct, len(auto_passes), empty=1.0)
    false_rate = _expected_rate(false, len(auto_passes))
    sufficient = (
        len(normalized) >= policy.min_total_samples
        and len(auto_passes) >= policy.min_auto_pass_samples
    )
    reasons: list[str] = []
    if sufficient and precision < policy.min_auto_publish_precision:
        reasons.append("auto_publish_precision")
    if sufficient and false_rate > policy.max_false_auto_pass_rate:
        reasons.append("false_auto_pass_rate")
    if sufficient and policy.require_no_publish_gate_violations and gate_violations:
        reasons.append("publish_gate_violations")
    return MonitoringEvaluation(
        sample_count=len(normalized),
        auto_pass_count=len(auto_passes),
        correct_auto_pass_count=correct,
        false_auto_pass_count=false,
        publish_gate_violation_count=gate_violations,
        auto_publish_precision=precision,
        false_auto_pass_rate=false_rate,
        sufficient_samples=sufficient,
        breached=bool(reasons),
        breach_reasons=tuple(reasons),
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
    min_auto_publish_precision: float = Field(default=1.0, ge=0.0, le=1.0)
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
    auto_pass_cases: int = Field(default=0, ge=0)
    correct_auto_pass_cases: int = Field(default=0, ge=0)
    correct_target_cases: int = Field(..., ge=0)
    correct_abstention_cases: int = Field(..., ge=0)
    false_auto_pass_cases: int = Field(..., ge=0)
    exact_match_rate: float = Field(..., ge=0.0, le=1.0)
    abstention_recall: float = Field(..., ge=0.0, le=1.0)
    coverage: float = Field(..., ge=0.0, le=1.0)
    false_auto_pass_rate: float = Field(..., ge=0.0, le=1.0)
    auto_publish_precision: float = Field(default=0.0, ge=0.0, le=1.0)
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
    case_hashes: dict[str, str] = Field(default_factory=dict)
    case_manifest_hash: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    golden_set_hash: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    policy_hash: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    metric_definition_hash: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    artifact_content_hash: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    repeat_run_count: int = Field(default=1, ge=1)
    run_hashes: list[str] = Field(default_factory=list)
    stable: Optional[bool] = None


class TrustedEvaluationManifest(BaseModel):
    """Protection evidence supplied independently by the promotion boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    golden_set_version: str = Field(..., min_length=1)
    split: Literal["holdout", "adversarial"]
    case_ids: list[str] = Field(..., min_length=1)
    case_manifest_hash: str = Field(..., pattern=HASH_PATTERN)
    golden_set_hash: str = Field(..., pattern=HASH_PATTERN)
    policy_hash: str = Field(..., pattern=HASH_PATTERN)
    metric_definition_hash: str = Field(..., pattern=HASH_PATTERN)


class TrustedCasePrediction(BaseModel):
    """One immutable case prediction supplied by the protected runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(..., min_length=1)
    prediction: MatchingPrediction


class TrustedPredictionRun(BaseModel):
    """One complete immutable run over the protected cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    predictions: tuple[TrustedCasePrediction, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_unique_cases(self) -> "TrustedPredictionRun":
        case_ids = [item.case_id for item in self.predictions]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("trusted prediction run contains duplicate case_id values")
        return self


class TrustedEvaluationEvidence(BaseModel):
    """Boundary-owned golden inputs and repeated per-case predictions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    golden_set: GoldenSet
    split: Literal["holdout", "adversarial"] = "holdout"
    policy: EvaluationPolicy
    prediction_runs: tuple[TrustedPredictionRun, ...] = Field(..., min_length=2)

    @property
    def manifest(self) -> TrustedEvaluationManifest:
        return _manifest_from_evidence(self)


class ProtectedPromotionReceipt(BaseModel):
    """Persistable receipt binding an artifact to protected recomputation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_version: Literal[1] = 1
    report_digest: str = Field(..., pattern=HASH_PATTERN)
    evidence_digest: str = Field(..., pattern=HASH_PATTERN)
    manifest_digest: str = Field(..., pattern=HASH_PATTERN)
    artifact_id: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_kind: Literal[
        "matching_skill", "prompt", "retrieval_weights", "matcher_variant"
    ]
    artifact_content_hash: str = Field(..., pattern=HASH_PATTERN)
    artifact_digest: str = Field(..., pattern=HASH_PATTERN)
    evaluation_attestation_digest: str = Field(..., pattern=HASH_PATTERN)
    evaluation_attestation: dict[str, Any]
    signature: str = Field(..., pattern=HASH_PATTERN)


class EvaluationAttestation(BaseModel):
    """Evaluator-authenticated binding of evidence to recomputed output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attestation_version: Literal[1] = 1
    evidence_digest: str = Field(..., pattern=HASH_PATTERN)
    report_digest: str = Field(..., pattern=HASH_PATTERN)
    manifest_digest: str = Field(..., pattern=HASH_PATTERN)
    evaluator_id: str = Field(..., min_length=1)
    evaluator_version: str = Field(..., min_length=1)
    artifact_content_hash: str = Field(..., pattern=HASH_PATTERN)
    artifact_id: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_kind: Literal[
        "matching_skill", "prompt", "retrieval_weights", "matcher_variant"
    ]
    signature: str = Field(..., pattern=HASH_PATTERN)


class SignedEvaluationEnvelope(BaseModel):
    """Serializable output from the separate protected evaluator process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal[1] = 1
    candidate_content: str = Field(..., min_length=1)
    artifact_id: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_kind: Literal[
        "matching_skill", "prompt", "retrieval_weights", "matcher_variant"
    ]
    evidence: TrustedEvaluationEvidence
    report: EvaluationReport
    manifest: TrustedEvaluationManifest
    adversarial_evidence: TrustedEvaluationEvidence
    adversarial_report: EvaluationReport
    adversarial_manifest: TrustedEvaluationManifest
    evaluator_id: str = Field(..., min_length=1)
    evaluator_version: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1)


class LiveSkillPointer(BaseModel):
    """Signed monotonic reference to one immutable skill artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pointer_version: Literal[1] = 1
    artifact_key: str = Field(..., min_length=1)
    artifact_version: int = Field(..., ge=1)
    artifact_digest: str = Field(..., pattern=HASH_PATTERN)
    predecessor_version: Optional[int] = Field(default=None, ge=1)
    predecessor_digest: Optional[str] = Field(default=None, pattern=HASH_PATTERN)
    sequence: int = Field(..., ge=1)
    transition_kind: Literal["promote", "rollback"] = "promote"
    signature: str = Field(..., pattern=HASH_PATTERN)


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
        and candidate.auto_publish_precision >= baseline.auto_publish_precision
        and candidate.abstention_recall >= baseline.abstention_recall
        and candidate.false_auto_pass_rate <= baseline.false_auto_pass_rate
        and candidate.calibration_mae <= baseline.calibration_mae
        and candidate.brier_score <= baseline.brier_score
    )
    strict = (
        candidate.exact_match_rate > baseline.exact_match_rate
        or candidate.auto_publish_precision > baseline.auto_publish_precision
        or candidate.abstention_recall > baseline.abstention_recall
        or candidate.false_auto_pass_rate < baseline.false_auto_pass_rate
        or candidate.calibration_mae < baseline.calibration_mae
        or candidate.brier_score < baseline.brier_score
    )
    return no_regression and strict


def _expected_rate(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _require_metric_consistency(metrics: EvaluationMetrics) -> None:
    counts_valid = (
        metrics.total_cases
        == metrics.expected_match_cases + metrics.expected_abstain_cases
        and metrics.predicted_abstain_cases <= metrics.total_cases
        and metrics.auto_pass_cases <= metrics.total_cases
        and metrics.predicted_abstain_cases + metrics.auto_pass_cases
        <= metrics.total_cases
        and metrics.correct_target_cases <= metrics.expected_match_cases
        and metrics.correct_abstention_cases <= metrics.expected_abstain_cases
        and metrics.correct_auto_pass_cases + metrics.false_auto_pass_cases
        == metrics.auto_pass_cases
    )
    expected_rates = {
        "exact_match_rate": _expected_rate(
            metrics.correct_target_cases,
            metrics.expected_match_cases,
            empty=1.0,
        ),
        "abstention_recall": _expected_rate(
            metrics.correct_abstention_cases,
            metrics.expected_abstain_cases,
        ),
        "coverage": _expected_rate(
            metrics.total_cases - metrics.predicted_abstain_cases,
            metrics.total_cases,
        ),
        "false_auto_pass_rate": _expected_rate(
            metrics.false_auto_pass_cases,
            metrics.auto_pass_cases,
        ),
        "auto_publish_precision": _expected_rate(
            metrics.correct_auto_pass_cases,
            metrics.auto_pass_cases,
            empty=1.0,
        ),
    }
    rates_valid = all(
        abs(getattr(metrics, name) - expected) <= 1e-9
        for name, expected in expected_rates.items()
    )
    if not counts_valid or not rates_valid:
        raise PromotionRejected("evaluation metric consistency check failed")


def _require_policy_compliance(
    metrics: EvaluationMetrics,
    policy: EvaluationPolicy,
) -> None:
    passed = (
        metrics.total_cases >= policy.min_cases
        and metrics.exact_match_rate >= policy.min_exact_match_rate
        and (
            metrics.auto_pass_cases == 0
            or metrics.auto_publish_precision >= policy.min_auto_publish_precision
        )
        and (
            metrics.expected_abstain_cases == 0
            or metrics.abstention_recall >= policy.min_abstention_recall
        )
        and metrics.false_auto_pass_rate <= policy.max_false_auto_pass_rate
        and metrics.brier_score <= policy.max_brier_score
    )
    if not passed:
        raise PromotionRejected("candidate metrics do not satisfy evaluation policy")


def validate_promotion(
    candidate: LearningArtifact,
    *,
    current: Optional[LearningArtifact] = None,
    trusted_evidence: Optional[TrustedEvaluationEvidence] = None,
) -> None:
    """Validate protected automatic promotion against boundary-owned evidence."""

    if candidate.approval is None:
        raise PromotionRejected("candidate has no named approval")
    if not candidate.evaluation.passed:
        raise PromotionRejected("candidate evaluation did not pass its policy")
    if candidate.evaluation.split != "holdout":
        raise PromotionRejected("promotion requires a holdout evaluation")
    report = candidate.evaluation
    if report.metrics.expected_match_cases < 1:
        raise PromotionRejected(
            "holdout evaluation has no positive mapping case; an abstention-only "
            "holdout cannot demonstrate matching capability"
        )
    protected_fields = {
        "golden set hash": report.golden_set_hash,
        "policy hash": report.policy_hash,
        "metric definition hash": report.metric_definition_hash,
        "artifact content hash": report.artifact_content_hash,
    }
    missing = [name for name, value in protected_fields.items() if not value]
    if missing:
        raise PromotionRejected(
            "automatic promotion requires protected evidence: " + ", ".join(missing)
        )
    if trusted_evidence is None:
        raise PromotionRejected(
            "automatic promotion requires trusted evaluation evidence supplied "
            "by the promotion boundary"
        )
    trusted_manifest = _manifest_from_evidence(trusted_evidence)
    if report.artifact_content_hash != _canonical_json_hash(candidate.content):
        raise PromotionRejected("artifact content hash mismatch")
    if report.policy_hash != _canonical_json_hash(
        report.policy.model_dump(mode="json")
    ):
        raise PromotionRejected("policy hash mismatch")
    if report.metric_definition_hash != _canonical_json_hash(METRIC_DEFINITION):
        raise PromotionRejected("metric definition hash mismatch")
    expected_case_manifest_hash = _canonical_json_hash(
        [
            {"case_id": case_id, "hash": report.case_hashes.get(case_id)}
            for case_id in sorted(report.case_ids)
        ]
    )
    if report.case_manifest_hash != expected_case_manifest_hash:
        raise PromotionRejected("case manifest hash mismatch")
    expected_golden_hash = _canonical_json_hash(
        {
            "version": report.golden_set_version,
            "split": report.split,
            "cases": [
                {"case_id": case_id, "hash": report.case_hashes.get(case_id)}
                for case_id in sorted(report.case_ids)
            ],
        }
    )
    if (
        set(report.case_hashes) != set(report.case_ids)
        or report.golden_set_hash != expected_golden_hash
    ):
        raise PromotionRejected("golden set hash mismatch")
    report_protection = (
        report.golden_set_version,
        report.split,
        sorted(report.case_ids),
        report.case_manifest_hash,
        report.golden_set_hash,
        report.policy_hash,
        report.metric_definition_hash,
    )
    trusted_protection = (
        trusted_manifest.golden_set_version,
        trusted_manifest.split,
        sorted(trusted_manifest.case_ids),
        trusted_manifest.case_manifest_hash,
        trusted_manifest.golden_set_hash,
        trusted_manifest.policy_hash,
        trusted_manifest.metric_definition_hash,
    )
    if report_protection != trusted_protection:
        raise PromotionRejected("report does not match trusted evaluation evidence")
    if report.stable is not True or report.repeat_run_count < 2:
        raise PromotionRejected(
            "promotion report is unstable or lacks repeated-run evidence"
        )
    if (
        len(report.run_hashes) != report.repeat_run_count
        or len(set(report.run_hashes)) != 1
    ):
        raise PromotionRejected("promotion report is unstable")
    if report.metrics.false_auto_pass_cases:
        raise PromotionRejected("promotion report contains a false auto-publish")
    _require_metric_consistency(report.metrics)
    _require_policy_compliance(report.metrics, report.policy)
    recomputed = _report_from_trusted_evidence(
        trusted_evidence,
        candidate_version=report.candidate_version,
        artifact_content=candidate.content,
        baseline_version=report.baseline_version,
    )
    if _evaluation_envelope(report) != _evaluation_envelope(recomputed):
        raise PromotionRejected(
            "candidate report does not match recomputed trusted evaluation"
        )
    # Defence in depth: the promotion boundary must not trust a passed=True
    # report it did not compute. An abstention-only holdout cannot demonstrate
    # matching capability, so reject it here regardless of how the report was
    # produced (a forged or replayed report bypasses evaluate_golden_set).
    if current is None:
        return
    if candidate.version <= current.version:
        raise PromotionRejected(
            f"candidate version {candidate.version} is not newer than "
            f"current version {current.version}"
        )
    current_is_protected = all(
        (
            current.evaluation.golden_set_hash,
            current.evaluation.policy_hash,
            current.evaluation.metric_definition_hash,
            current.evaluation.case_manifest_hash,
        )
    )
    if current_is_protected:
        candidate_protection = (
            report.golden_set_hash,
            report.policy_hash,
            report.metric_definition_hash,
            report.case_manifest_hash,
            report.split,
        )
        current_protection = (
            current.evaluation.golden_set_hash,
            current.evaluation.policy_hash,
            current.evaluation.metric_definition_hash,
            current.evaluation.case_manifest_hash,
            current.evaluation.split,
        )
        if candidate_protection != current_protection:
            raise PromotionRejected(
                "candidate and current artifact were not evaluated against the same "
                "protected dataset, policy, metric definition, and split"
            )
    if not _strictly_improves(candidate.evaluation.metrics, current.evaluation.metrics):
        raise PromotionRejected(
            "candidate does not strictly improve the current artifact without "
            "regressing auto-publish precision, exact-match rate, abstention recall, "
            "false-auto-pass rate, calibration MAE, or Brier score"
        )


def validate_manual_promotion(
    candidate: LearningArtifact,
    *,
    current: Optional[LearningArtifact] = None,
) -> None:
    """Quarantined compatibility gate; never creates a protected receipt."""

    if candidate.approval is None:
        raise PromotionRejected("candidate has no named approval")
    report = candidate.evaluation
    if not report.passed or report.split != "holdout":
        raise PromotionRejected("manual candidate did not pass a holdout evaluation")
    if report.metrics.expected_match_cases < 1:
        raise PromotionRejected("manual holdout has no positive mapping case")
    legacy_policy_passed = (
        report.metrics.total_cases >= report.policy.min_cases
        and report.metrics.exact_match_rate >= report.policy.min_exact_match_rate
        and (
            report.metrics.expected_abstain_cases == 0
            or report.metrics.abstention_recall >= report.policy.min_abstention_recall
        )
        and report.metrics.false_auto_pass_rate
        <= report.policy.max_false_auto_pass_rate
        and report.metrics.brier_score <= report.policy.max_brier_score
    )
    if not legacy_policy_passed:
        raise PromotionRejected("manual candidate metrics do not satisfy legacy policy")
    if current is None:
        return
    if candidate.version <= current.version:
        raise PromotionRejected("manual candidate version is not newer")
    if not _strictly_improves(report.metrics, current.evaluation.metrics):
        raise PromotionRejected("manual candidate does not strictly improve current")


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
    correct_auto_passes = 0
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
            case.expected_abstain or prediction.target_iri != case.expected_target_iri
        ):
            false_auto_passes += 1
        elif prediction.auto_pass:
            correct_auto_passes += 1

        if not case.expected_abstain:
            target_correct = (
                not prediction.abstained
                and prediction.target_iri == case.expected_target_iri
            )
            expected_confidence = 1.0 if target_correct else 0.0
            calibration_errors.append(abs(prediction.confidence - expected_confidence))
            brier_scores.append((prediction.confidence - expected_confidence) ** 2)

    return EvaluationMetrics(
        total_cases=len(selected),
        expected_match_cases=expected_matches,
        expected_abstain_cases=expected_abstains,
        predicted_abstain_cases=predicted_abstains,
        auto_pass_cases=auto_passes,
        correct_auto_pass_cases=correct_auto_passes,
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
        auto_publish_precision=(
            1.0 if auto_passes == 0 else _safe_rate(correct_auto_passes, auto_passes)
        ),
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
    artifact_content: Optional[str] = None,
    repeat_runs: int = 1,
) -> EvaluationReport:
    """Evaluate a candidate only on the requested, leakage-checked split."""

    cases = golden_set.cases_for_split(split)
    if len(cases) < 1:
        raise ValueError(f"golden set has no {split} cases")
    selected_policy = policy or EvaluationPolicy()
    if repeat_runs < 1:
        raise ValueError("repeat_runs must be at least 1")
    if len(cases) < selected_policy.min_cases:
        raise ValueError(
            f"{split} evaluation has {len(cases)} cases but policy requires "
            f"{selected_policy.min_cases}"
        )
    # A holdout run gates promotion, so it must prove matching capability, not
    # only correct abstention. Scoped here (not at GoldenSet construction) so an
    # abstention-only holdout set can still load and run --split adversarial.
    if split == "holdout" and not any(not case.expected_abstain for case in cases):
        raise ValueError(
            "holdout evaluation must include at least one positive mapping case; "
            "an abstention-only holdout cannot demonstrate matching capability"
        )

    prediction_runs: list[dict[str, MatchingPrediction]] = []
    run_hashes: list[str] = []
    for _ in range(repeat_runs):
        run_predictions: dict[str, MatchingPrediction] = {}
        for case in cases:
            run_predictions[case.case_id] = MatchingPrediction.from_result(
                predictor(case)
            )
        prediction_runs.append(run_predictions)
        run_hashes.append(
            _canonical_json_hash(
                {
                    case_id: prediction.model_dump(mode="json")
                    for case_id, prediction in sorted(run_predictions.items())
                }
            )
        )
    predictions = prediction_runs[0]

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
            metrics.auto_pass_cases == 0
            or metrics.auto_publish_precision
            >= selected_policy.min_auto_publish_precision
        )
        and (
            metrics.expected_abstain_cases == 0
            or metrics.abstention_recall >= selected_policy.min_abstention_recall
        )
        and metrics.false_auto_pass_rate <= selected_policy.max_false_auto_pass_rate
        and metrics.brier_score <= selected_policy.max_brier_score
        and len(set(run_hashes)) == 1
    )
    case_hashes = {
        case.case_id: _canonical_json_hash(case.model_dump(mode="json"))
        for case in cases
    }
    case_manifest_hash = _canonical_json_hash(
        [
            {"case_id": case_id, "hash": case_hashes[case_id]}
            for case_id in sorted(case_hashes)
        ]
    )
    golden_set_hash = _canonical_json_hash(
        {
            "version": golden_set.version,
            "split": split,
            "cases": [
                {"case_id": case_id, "hash": case_hashes[case_id]}
                for case_id in sorted(case_hashes)
            ],
        }
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
        case_hashes=case_hashes,
        case_manifest_hash=case_manifest_hash,
        golden_set_hash=golden_set_hash,
        policy_hash=_canonical_json_hash(selected_policy.model_dump(mode="json")),
        metric_definition_hash=_canonical_json_hash(METRIC_DEFINITION),
        artifact_content_hash=(
            _canonical_json_hash(artifact_content)
            if artifact_content is not None
            else None
        ),
        repeat_run_count=repeat_runs,
        run_hashes=run_hashes,
        stable=len(set(run_hashes)) == 1,
    )


def trusted_manifest_for(
    golden_set: GoldenSet,
    *,
    policy: EvaluationPolicy,
    split: Literal["holdout", "adversarial"] = "holdout",
) -> TrustedEvaluationManifest:
    """Build boundary-owned protection evidence from authoritative inputs."""

    cases = golden_set.cases_for_split(split)
    if not cases:
        raise ValueError(f"golden set has no {split} cases")
    case_hashes = {
        case.case_id: _canonical_json_hash(case.model_dump(mode="json"))
        for case in cases
    }
    case_manifest = [
        {"case_id": case_id, "hash": case_hashes[case_id]}
        for case_id in sorted(case_hashes)
    ]
    return TrustedEvaluationManifest(
        golden_set_version=golden_set.version,
        split=split,
        case_ids=sorted(case_hashes),
        case_manifest_hash=_canonical_json_hash(case_manifest),
        golden_set_hash=_canonical_json_hash(
            {
                "version": golden_set.version,
                "split": split,
                "cases": case_manifest,
            }
        ),
        policy_hash=_canonical_json_hash(policy.model_dump(mode="json")),
        metric_definition_hash=_canonical_json_hash(METRIC_DEFINITION),
    )


def trusted_evidence_for(
    golden_set: GoldenSet,
    *,
    policy: EvaluationPolicy,
    prediction_runs: Iterable[dict[str, MatchingPrediction]],
    split: Literal["holdout", "adversarial"] = "holdout",
) -> TrustedEvaluationEvidence:
    """Freeze authoritative cases and complete repeated prediction runs."""

    expected_case_ids = {case.case_id for case in golden_set.cases_for_split(split)}
    runs: list[TrustedPredictionRun] = []
    for predictions in prediction_runs:
        if set(predictions) != expected_case_ids:
            raise ValueError(
                "trusted prediction run case_ids must exactly match protected cases"
            )
        runs.append(
            TrustedPredictionRun(
                predictions=tuple(
                    TrustedCasePrediction(
                        case_id=case_id,
                        prediction=MatchingPrediction.model_validate(
                            predictions[case_id]
                        ),
                    )
                    for case_id in sorted(predictions)
                )
            )
        )
    return TrustedEvaluationEvidence(
        golden_set=GoldenSet(
            version=golden_set.version,
            cases=sorted(
                golden_set.cases,
                key=lambda case: case.case_id,
            ),
            created_at=golden_set.created_at,
            source=golden_set.source,
        ),
        split=split,
        policy=EvaluationPolicy.model_validate(policy.model_dump(mode="json")),
        prediction_runs=tuple(runs),
    )


def _manifest_from_evidence(
    evidence: TrustedEvaluationEvidence,
) -> TrustedEvaluationManifest:
    return trusted_manifest_for(
        evidence.golden_set,
        policy=evidence.policy,
        split=evidence.split,
    )


def _report_from_trusted_evidence(
    evidence: TrustedEvaluationEvidence,
    *,
    candidate_version: str,
    artifact_content: str,
    baseline_version: Optional[str],
) -> EvaluationReport:
    case_order = [
        case.case_id for case in evidence.golden_set.cases_for_split(evidence.split)
    ]
    ordered_predictions: list[MatchingPrediction] = []
    for run in evidence.prediction_runs:
        by_case = {item.case_id: item.prediction for item in run.predictions}
        ordered_predictions.extend(by_case[case_id] for case_id in case_order)
    prediction_iterator = iter(ordered_predictions)
    return evaluate_golden_set(
        evidence.golden_set,
        lambda _case: next(prediction_iterator),
        candidate_version=candidate_version,
        split=evidence.split,
        policy=evidence.policy,
        baseline_version=baseline_version,
        artifact_content=artifact_content,
        repeat_runs=len(evidence.prediction_runs),
    )


def _evaluation_envelope(report: EvaluationReport) -> dict[str, Any]:
    return report.model_dump(mode="json", exclude={"evaluated_at"})


def evaluation_report_digest(report: EvaluationReport) -> str:
    """Canonical digest persisted in protected promotion receipts."""

    return _canonical_json_hash(_evaluation_envelope(report))


def learning_artifact_digest(artifact: LearningArtifact) -> str:
    """Canonical digest of every persisted artifact field."""

    return _canonical_json_hash(artifact.model_dump(mode="json"))


def _evaluation_key(signing_key: Optional[str]) -> Optional[bytes]:
    value = (
        signing_key
        if signing_key is not None
        else os.getenv("SCUDO_EVALUATION_SIGNING_KEY")
    )
    return value.encode("utf-8") if value else None


def _attestation_payload(
    *,
    evidence_digest: str,
    report_digest: str,
    manifest_digest: str,
    evaluator_id: str,
    evaluator_version: str,
    artifact_content_hash: str,
    artifact_id: str,
    artifact_version: int,
    artifact_kind: str,
) -> dict[str, Any]:
    return {
        "attestation_version": 1,
        "evidence_digest": evidence_digest,
        "report_digest": report_digest,
        "manifest_digest": manifest_digest,
        "evaluator_id": evaluator_id,
        "evaluator_version": evaluator_version,
        "artifact_content_hash": artifact_content_hash,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "artifact_kind": artifact_kind,
    }


def issue_evaluation_attestation(
    report: EvaluationReport,
    *,
    trusted_evidence: TrustedEvaluationEvidence,
    artifact_content: str,
    artifact_id: str,
    artifact_version: int,
    artifact_kind: str,
    evaluator_id: str,
    evaluator_version: str,
    signing_key: Optional[str] = None,
    promotion_key: Optional[str] = None,
) -> EvaluationAttestation:
    evaluation_key = _evaluation_key(signing_key)
    selected_promotion_key = _promotion_key(promotion_key)
    if evaluation_key is None:
        raise PromotionRejected("SCUDO_EVALUATION_SIGNING_KEY is required")
    if selected_promotion_key is None:
        raise PromotionRejected("SCUDO_SKILL_PROMOTION_KEY is required")
    if hmac.compare_digest(evaluation_key, selected_promotion_key):
        raise PromotionRejected("evaluation and promotion keys must be distinct")
    recomputed = _report_from_trusted_evidence(
        trusted_evidence,
        candidate_version=report.candidate_version,
        artifact_content=artifact_content,
        baseline_version=report.baseline_version,
    )
    recomputed = recomputed.model_copy(update={"evaluated_at": report.evaluated_at})
    if _evaluation_envelope(recomputed) != _evaluation_envelope(report):
        raise PromotionRejected("attested report does not match trusted evidence")
    manifest = _manifest_from_evidence(trusted_evidence)
    payload = _attestation_payload(
        evidence_digest=_canonical_json_hash(trusted_evidence.model_dump(mode="json")),
        report_digest=evaluation_report_digest(report),
        manifest_digest=_canonical_json_hash(manifest.model_dump(mode="json")),
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        artifact_content_hash=_canonical_json_hash(artifact_content),
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        artifact_kind=artifact_kind,
    )
    return EvaluationAttestation(
        **payload,
        signature=_sign_receipt(payload, evaluation_key),
    )


def _signed_envelope_payload(envelope: SignedEvaluationEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json", exclude={"signature"})


def issue_signed_evaluation_envelope(
    *,
    candidate_content: str,
    artifact_id: str,
    artifact_version: int,
    artifact_kind: str,
    trusted_evidence: TrustedEvaluationEvidence,
    adversarial_evidence: TrustedEvaluationEvidence,
    candidate_version: str,
    baseline_version: Optional[str],
    evaluator_id: str,
    evaluator_version: str,
    private_key_pem: str,
) -> SignedEvaluationEnvelope:
    """Recompute and sign protected evaluation with evaluator-private Ed25519."""

    if trusted_evidence.split != "holdout":
        raise PromotionRejected("primary protected evidence must be holdout")
    if adversarial_evidence.split != "adversarial":
        raise PromotionRejected("adversarial protected evidence is required")
    report = _report_from_trusted_evidence(
        trusted_evidence,
        candidate_version=candidate_version,
        artifact_content=candidate_content,
        baseline_version=baseline_version,
    )
    manifest = _manifest_from_evidence(trusted_evidence)
    adversarial_report = _report_from_trusted_evidence(
        adversarial_evidence,
        candidate_version=candidate_version,
        artifact_content=candidate_content,
        baseline_version=baseline_version,
    )
    if not adversarial_report.passed:
        raise PromotionRejected("candidate failed adversarial evaluation")
    adversarial_manifest = _manifest_from_evidence(adversarial_evidence)
    unsigned = SignedEvaluationEnvelope(
        candidate_content=candidate_content,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        artifact_kind=artifact_kind,
        evidence=trusted_evidence,
        report=report,
        manifest=manifest,
        adversarial_evidence=adversarial_evidence,
        adversarial_report=adversarial_report,
        adversarial_manifest=adversarial_manifest,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        signature="pending",
    )
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("evaluation private key must be Ed25519")
    canonical = json.dumps(
        _signed_envelope_payload(unsigned),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return unsigned.model_copy(
        update={
            "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii")
        }
    )


def verify_signed_evaluation_envelope(
    envelope: SignedEvaluationEnvelope | dict[str, Any],
    *,
    public_key_pem: str,
) -> bool:
    try:
        validated = (
            envelope
            if isinstance(envelope, SignedEvaluationEnvelope)
            else SignedEvaluationEnvelope.model_validate(envelope)
        )
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        canonical = json.dumps(
            _signed_envelope_payload(validated),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        public_key.verify(base64.b64decode(validated.signature), canonical)
        recomputed = _report_from_trusted_evidence(
            validated.evidence,
            candidate_version=validated.report.candidate_version,
            artifact_content=validated.candidate_content,
            baseline_version=validated.report.baseline_version,
        )
        recomputed_adversarial = _report_from_trusted_evidence(
            validated.adversarial_evidence,
            candidate_version=validated.adversarial_report.candidate_version,
            artifact_content=validated.candidate_content,
            baseline_version=validated.adversarial_report.baseline_version,
        )
        return (
            _evaluation_envelope(recomputed) == _evaluation_envelope(validated.report)
            and _manifest_from_evidence(validated.evidence) == validated.manifest
            and validated.evidence.split == "holdout"
            and validated.adversarial_evidence.split == "adversarial"
            and validated.adversarial_report.passed
            and _evaluation_envelope(recomputed_adversarial)
            == _evaluation_envelope(validated.adversarial_report)
            and _manifest_from_evidence(validated.adversarial_evidence)
            == validated.adversarial_manifest
        )
    except (ValueError, TypeError, InvalidSignature):
        return False


def verify_evaluation_attestation(
    report: EvaluationReport,
    attestation: EvaluationAttestation | dict[str, Any],
    *,
    trusted_evidence: TrustedEvaluationEvidence,
    artifact_content: str,
    artifact_id: str,
    artifact_version: int,
    artifact_kind: str,
    signing_key: Optional[str] = None,
    promotion_key: Optional[str] = None,
) -> bool:
    evaluation_key = _evaluation_key(signing_key)
    selected_promotion_key = _promotion_key(promotion_key)
    if (
        evaluation_key is None
        or selected_promotion_key is None
        or hmac.compare_digest(evaluation_key, selected_promotion_key)
    ):
        return False
    try:
        validated = (
            attestation
            if isinstance(attestation, EvaluationAttestation)
            else EvaluationAttestation.model_validate(attestation)
        )
    except Exception:
        return False
    manifest = _manifest_from_evidence(trusted_evidence)
    payload = _attestation_payload(
        evidence_digest=_canonical_json_hash(trusted_evidence.model_dump(mode="json")),
        report_digest=evaluation_report_digest(report),
        manifest_digest=_canonical_json_hash(manifest.model_dump(mode="json")),
        evaluator_id=validated.evaluator_id,
        evaluator_version=validated.evaluator_version,
        artifact_content_hash=_canonical_json_hash(artifact_content),
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        artifact_kind=artifact_kind,
    )
    return (
        payload["evidence_digest"] == validated.evidence_digest
        and payload["report_digest"] == validated.report_digest
        and payload["manifest_digest"] == validated.manifest_digest
        and payload["artifact_content_hash"] == validated.artifact_content_hash
        and payload["artifact_id"] == validated.artifact_id
        and payload["artifact_version"] == validated.artifact_version
        and payload["artifact_kind"] == validated.artifact_kind
        and hmac.compare_digest(
            _sign_receipt(payload, evaluation_key),
            validated.signature,
        )
    )


def _receipt_signed_payload(
    *,
    report_digest: str,
    evidence_digest: str,
    manifest_digest: str,
    artifact_id: str,
    artifact_version: int,
    artifact_kind: str,
    artifact_content_hash: str,
    artifact_digest: str,
    evaluation_attestation_digest: str,
    evaluation_attestation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "receipt_version": 1,
        "report_digest": report_digest,
        "evidence_digest": evidence_digest,
        "manifest_digest": manifest_digest,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "artifact_kind": artifact_kind,
        "artifact_content_hash": artifact_content_hash,
        "artifact_digest": artifact_digest,
        "evaluation_attestation_digest": evaluation_attestation_digest,
        "evaluation_attestation": evaluation_attestation,
    }


def _promotion_key(signing_key: Optional[str]) -> Optional[bytes]:
    value = (
        signing_key
        if signing_key is not None
        else os.getenv("SCUDO_SKILL_PROMOTION_KEY")
    )
    if not value:
        return None
    return value.encode("utf-8")


def _sign_receipt(payload: dict[str, Any], key: bytes) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hmac.new(key, canonical, sha256).hexdigest()


def promotion_receipt_for(
    candidate: LearningArtifact,
    *,
    trusted_evidence: TrustedEvaluationEvidence,
    evaluation_attestation: Optional[EvaluationAttestation] = None,
    signed_evaluation_envelope: Optional[SignedEvaluationEnvelope] = None,
    signing_key: Optional[str] = None,
    evaluation_signing_key: Optional[str] = None,
    evaluation_public_key_pem: Optional[str] = None,
) -> ProtectedPromotionReceipt:
    """Create a boundary-owned receipt after protected validation succeeds."""

    validate_promotion(candidate, trusted_evidence=trusted_evidence)
    key = _promotion_key(signing_key)
    if key is None:
        raise PromotionRejected("SCUDO_SKILL_PROMOTION_KEY is required")
    envelope_valid = bool(
        signed_evaluation_envelope is not None
        and evaluation_public_key_pem
        and verify_signed_evaluation_envelope(
            signed_evaluation_envelope,
            public_key_pem=evaluation_public_key_pem,
        )
        and signed_evaluation_envelope.candidate_content == candidate.content
        and signed_evaluation_envelope.artifact_id == candidate.artifact_id
        and signed_evaluation_envelope.artifact_version == candidate.version
        and signed_evaluation_envelope.artifact_kind == candidate.artifact_kind
        and _evaluation_envelope(signed_evaluation_envelope.report)
        == _evaluation_envelope(candidate.evaluation)
    )
    legacy_attestation_valid = bool(
        evaluation_attestation is not None
        and verify_evaluation_attestation(
            candidate.evaluation,
            evaluation_attestation,
            trusted_evidence=trusted_evidence,
            artifact_content=candidate.content,
            artifact_id=candidate.artifact_id,
            artifact_version=candidate.version,
            artifact_kind=candidate.artifact_kind,
            signing_key=evaluation_signing_key,
            promotion_key=signing_key,
        )
    )
    if not envelope_valid and not legacy_attestation_valid:
        raise PromotionRejected("valid evaluation attestation is required")
    manifest = _manifest_from_evidence(trusted_evidence)
    report_digest = evaluation_report_digest(candidate.evaluation)
    evidence_digest = _canonical_json_hash(trusted_evidence.model_dump(mode="json"))
    manifest_digest = _canonical_json_hash(manifest.model_dump(mode="json"))
    content_hash = _canonical_json_hash(candidate.content)
    artifact_digest = learning_artifact_digest(candidate)
    authority_payload = (
        signed_evaluation_envelope.model_dump(mode="json")
        if envelope_valid and signed_evaluation_envelope is not None
        else evaluation_attestation.model_dump(mode="json")
    )
    attestation_digest = _canonical_json_hash(authority_payload)
    attestation_payload = authority_payload
    signed_payload = _receipt_signed_payload(
        report_digest=report_digest,
        evidence_digest=evidence_digest,
        manifest_digest=manifest_digest,
        artifact_id=candidate.artifact_id,
        artifact_version=candidate.version,
        artifact_kind=candidate.artifact_kind,
        artifact_content_hash=content_hash,
        artifact_digest=artifact_digest,
        evaluation_attestation_digest=attestation_digest,
        evaluation_attestation=attestation_payload,
    )
    return ProtectedPromotionReceipt(
        **signed_payload,
        signature=_sign_receipt(signed_payload, key),
    )


def verify_promotion_receipt(
    candidate: LearningArtifact,
    receipt: ProtectedPromotionReceipt | dict[str, Any],
    *,
    signing_key: Optional[str] = None,
    evaluation_signing_key: Optional[str] = None,
    evaluation_public_key_pem: Optional[str] = None,
) -> bool:
    """Authenticate and bind a persisted receipt to its exact artifact."""

    key = _promotion_key(signing_key)
    evaluation_key = _evaluation_key(evaluation_signing_key)
    if key is None:
        return False
    if evaluation_public_key_pem is None and (
        evaluation_key is None or hmac.compare_digest(key, evaluation_key)
    ):
        return False
    try:
        validated = (
            receipt
            if isinstance(receipt, ProtectedPromotionReceipt)
            else ProtectedPromotionReceipt.model_validate(receipt)
        )
    except Exception:
        return False
    content_hash = _canonical_json_hash(candidate.content)
    artifact_digest = learning_artifact_digest(candidate)
    authority_payload = validated.evaluation_attestation
    authority_valid = False
    if evaluation_public_key_pem is not None:
        try:
            envelope = SignedEvaluationEnvelope.model_validate(authority_payload)
            authority_valid = (
                verify_signed_evaluation_envelope(
                    envelope, public_key_pem=evaluation_public_key_pem
                )
                and envelope.candidate_content == candidate.content
                and envelope.artifact_id == candidate.artifact_id
                and envelope.artifact_version == candidate.version
                and envelope.artifact_kind == candidate.artifact_kind
                and _evaluation_envelope(envelope.report)
                == _evaluation_envelope(candidate.evaluation)
            )
        except Exception:
            authority_valid = False
    else:
        try:
            embedded_attestation = EvaluationAttestation.model_validate(
                authority_payload
            )
            unsigned_attestation = _attestation_payload(
                evidence_digest=embedded_attestation.evidence_digest,
                report_digest=embedded_attestation.report_digest,
                manifest_digest=embedded_attestation.manifest_digest,
                evaluator_id=embedded_attestation.evaluator_id,
                evaluator_version=embedded_attestation.evaluator_version,
                artifact_content_hash=embedded_attestation.artifact_content_hash,
                artifact_id=embedded_attestation.artifact_id,
                artifact_version=embedded_attestation.artifact_version,
                artifact_kind=embedded_attestation.artifact_kind,
            )
            authority_valid = bool(
                evaluation_key is not None
                and embedded_attestation.report_digest == validated.report_digest
                and embedded_attestation.evidence_digest == validated.evidence_digest
                and embedded_attestation.manifest_digest == validated.manifest_digest
                and embedded_attestation.artifact_content_hash == content_hash
                and embedded_attestation.artifact_id == candidate.artifact_id
                and embedded_attestation.artifact_version == candidate.version
                and embedded_attestation.artifact_kind == candidate.artifact_kind
                and hmac.compare_digest(
                    _sign_receipt(unsigned_attestation, evaluation_key),
                    embedded_attestation.signature,
                )
            )
        except Exception:
            authority_valid = False
    if (
        candidate.evaluation.artifact_content_hash != content_hash
        or validated.artifact_content_hash != content_hash
        or validated.report_digest != evaluation_report_digest(candidate.evaluation)
        or validated.artifact_id != candidate.artifact_id
        or validated.artifact_version != candidate.version
        or validated.artifact_kind != candidate.artifact_kind
        or validated.artifact_digest != artifact_digest
        or validated.evaluation_attestation_digest
        != _canonical_json_hash(authority_payload)
        or not authority_valid
    ):
        return False
    signed_payload = _receipt_signed_payload(
        report_digest=validated.report_digest,
        evidence_digest=validated.evidence_digest,
        manifest_digest=validated.manifest_digest,
        artifact_id=validated.artifact_id,
        artifact_version=validated.artifact_version,
        artifact_kind=validated.artifact_kind,
        artifact_content_hash=validated.artifact_content_hash,
        artifact_digest=validated.artifact_digest,
        evaluation_attestation_digest=validated.evaluation_attestation_digest,
        evaluation_attestation=validated.evaluation_attestation,
    )
    expected = _sign_receipt(signed_payload, key)
    return hmac.compare_digest(expected, validated.signature)


def _pointer_payload(
    *,
    artifact_key: str,
    artifact_version: int,
    artifact_digest: str,
    predecessor_version: Optional[int],
    predecessor_digest: Optional[str],
    sequence: int,
    transition_kind: Literal["promote", "rollback"],
) -> dict[str, Any]:
    return {
        "pointer_version": 1,
        "artifact_key": artifact_key,
        "artifact_version": artifact_version,
        "artifact_digest": artifact_digest,
        "predecessor_version": predecessor_version,
        "predecessor_digest": predecessor_digest,
        "sequence": sequence,
        "transition_kind": transition_kind,
    }


def issue_live_pointer(
    *,
    artifact_key: str,
    artifact_version: int,
    artifact_digest: str,
    predecessor_version: Optional[int],
    predecessor_digest: Optional[str],
    sequence: int,
    transition_kind: Literal["promote", "rollback"] = "promote",
    signing_key: Optional[str] = None,
) -> LiveSkillPointer:
    key = _promotion_key(signing_key)
    if key is None:
        raise PromotionRejected("SCUDO_SKILL_PROMOTION_KEY is required")
    payload = _pointer_payload(
        artifact_key=artifact_key,
        artifact_version=artifact_version,
        artifact_digest=artifact_digest,
        predecessor_version=predecessor_version,
        predecessor_digest=predecessor_digest,
        sequence=sequence,
        transition_kind=transition_kind,
    )
    return LiveSkillPointer(
        **payload,
        signature=_sign_receipt(payload, key),
    )


def verify_live_pointer(
    pointer: LiveSkillPointer | dict[str, Any],
    *,
    signing_key: Optional[str] = None,
) -> bool:
    key = _promotion_key(signing_key)
    if key is None:
        return False
    try:
        validated = (
            pointer
            if isinstance(pointer, LiveSkillPointer)
            else LiveSkillPointer.model_validate(pointer)
        )
    except Exception:
        return False
    payload = _pointer_payload(
        artifact_key=validated.artifact_key,
        artifact_version=validated.artifact_version,
        artifact_digest=validated.artifact_digest,
        predecessor_version=validated.predecessor_version,
        predecessor_digest=validated.predecessor_digest,
        sequence=validated.sequence,
        transition_kind=validated.transition_kind,
    )
    return hmac.compare_digest(
        _sign_receipt(payload, key),
        validated.signature,
    )


__all__ = [
    "EVALUATION_PASS_CUT",
    "AuthoritativeMonitoringOutcome",
    "MonitoringEvaluation",
    "MonitoringObservation",
    "MonitoringOutcome",
    "MonitoringPolicy",
    "SignedMonitoringEnvelope",
    "EvaluationAttestation",
    "EvaluationMetrics",
    "EvaluationPolicy",
    "EvaluationReport",
    "GoldenCase",
    "GoldenSet",
    "LearningArtifact",
    "LiveSkillPointer",
    "MatchingPrediction",
    "PromotionApproval",
    "ProtectedPromotionReceipt",
    "PromotionRejected",
    "SignedEvaluationEnvelope",
    "TrustedCasePrediction",
    "TrustedEvaluationEvidence",
    "TrustedEvaluationManifest",
    "TrustedPredictionRun",
    "evaluation_report_digest",
    "issue_evaluation_attestation",
    "issue_signed_monitoring_envelope",
    "issue_signed_evaluation_envelope",
    "issue_live_pointer",
    "learning_artifact_digest",
    "evaluate_golden_set",
    "evaluate_monitoring_window",
    "monitoring_source_record_digest",
    "load_golden_set",
    "promotion_receipt_for",
    "trusted_evidence_for",
    "trusted_manifest_for",
    "validate_manual_promotion",
    "validate_promotion",
    "verify_promotion_receipt",
    "verify_evaluation_attestation",
    "verify_signed_monitoring_envelope",
    "verify_signed_evaluation_envelope",
    "verify_live_pointer",
]
