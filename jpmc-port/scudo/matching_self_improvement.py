"""Explicit adapter to the generated canonical self-improvement contract.

``_matching_self_improvement_canonical.py`` is copied byte-for-byte from
``backend/scudo/matching_self_improvement.py``. The parity test fails on any
drift. Importing that private module avoids recursion between the two packages
named ``scudo`` and keeps the JPMC package standalone.
"""

from __future__ import annotations

from . import _matching_self_improvement_canonical as _canonical

EVALUATION_PASS_CUT = _canonical.EVALUATION_PASS_CUT
EvaluationMetrics = _canonical.EvaluationMetrics
EvaluationPolicy = _canonical.EvaluationPolicy
EvaluationReport = _canonical.EvaluationReport
EvaluationAttestation = _canonical.EvaluationAttestation
GoldenCase = _canonical.GoldenCase
GoldenSet = _canonical.GoldenSet
LearningArtifact = _canonical.LearningArtifact
LiveSkillPointer = _canonical.LiveSkillPointer
MatchingPrediction = _canonical.MatchingPrediction
AuthoritativeMonitoringOutcome = _canonical.AuthoritativeMonitoringOutcome
MonitoringEvaluation = _canonical.MonitoringEvaluation
MonitoringObservation = _canonical.MonitoringObservation
MonitoringOutcome = _canonical.MonitoringOutcome
MonitoringPolicy = _canonical.MonitoringPolicy
SignedMonitoringEnvelope = _canonical.SignedMonitoringEnvelope
PromotionApproval = _canonical.PromotionApproval
ProtectedPromotionReceipt = _canonical.ProtectedPromotionReceipt
PromotionRejected = _canonical.PromotionRejected
SignedEvaluationEnvelope = _canonical.SignedEvaluationEnvelope
TrustedCasePrediction = _canonical.TrustedCasePrediction
TrustedEvaluationEvidence = _canonical.TrustedEvaluationEvidence
TrustedEvaluationManifest = _canonical.TrustedEvaluationManifest
TrustedPredictionRun = _canonical.TrustedPredictionRun
evaluate_golden_set = _canonical.evaluate_golden_set
evaluate_monitoring_window = _canonical.evaluate_monitoring_window
monitoring_source_record_digest = _canonical.monitoring_source_record_digest
evaluation_report_digest = _canonical.evaluation_report_digest
issue_evaluation_attestation = _canonical.issue_evaluation_attestation
issue_live_pointer = _canonical.issue_live_pointer
issue_signed_monitoring_envelope = _canonical.issue_signed_monitoring_envelope
issue_signed_evaluation_envelope = _canonical.issue_signed_evaluation_envelope
learning_artifact_digest = _canonical.learning_artifact_digest
load_golden_set = _canonical.load_golden_set
promotion_receipt_for = _canonical.promotion_receipt_for
trusted_evidence_for = _canonical.trusted_evidence_for
trusted_manifest_for = _canonical.trusted_manifest_for
validate_manual_promotion = _canonical.validate_manual_promotion
validate_promotion = _canonical.validate_promotion
verify_evaluation_attestation = _canonical.verify_evaluation_attestation
verify_live_pointer = _canonical.verify_live_pointer
verify_signed_monitoring_envelope = _canonical.verify_signed_monitoring_envelope
verify_signed_evaluation_envelope = _canonical.verify_signed_evaluation_envelope
verify_promotion_receipt = _canonical.verify_promotion_receipt

__all__ = list(_canonical.__all__)
