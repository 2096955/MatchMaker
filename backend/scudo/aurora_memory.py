"""Real CONSULT/DISTILL memory for the Orchestrator pipeline, backed by the
single Aurora PostgreSQL cluster (``scudo.agent_memory``).

Replaces two previously-fabricated behaviours (see
docs/superpowers/specs/2026-07-07-aurora-memory-rights-model-zone-tool-design.md):

  CONSULT — ``consult_priors`` reads a real precedent + any already-promoted
    rules for a (vendor, vendor_product_ref), in place of the ``has_precedent``
    flag that used to fabricate a canned PrecedentMapping.
  DISTILL — ``record_verified_precedent`` upserts a durable precedent row on a
    verified auto-pass, in place of the ``InMemoryPublishSink`` that used to
    evaporate every outcome at the end of the Lambda invocation.

Rule DISTILL (adversarial-verify-gated rule promotion) is an offline nightly
routine — see the spec — reusing ~/.claude/skills/self-improving-agent's
existing distill_lessons.py + adversarial-verify.js wholesale. Not buildable
synchronously in a Lambda (the Workflow tool's subagent refuters only exist in
an interactive session). This module only provides the read side of that
contract (rule rows, once promoted, are picked up by consult_priors above).

CONSULT fails OPEN (advisory — a missing prior must never block a mapping
request). DISTILL fails LOUD (a lost precedent write defeats the entire
point) — same fail-loud contract as aurora_store.py, whose primitives this
module reuses directly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import aurora_store
from .matching_self_improvement import (
    EvaluationAttestation,
    EvaluationReport,
    LearningArtifact,
    LiveSkillPointer,
    PromotionApproval,
    PromotionRejected,
    TrustedEvaluationEvidence,
    issue_live_pointer,
    learning_artifact_digest,
    promotion_receipt_for,
    validate_manual_promotion,
    validate_promotion,
    verify_promotion_receipt,
    verify_live_pointer,
)

log = logging.getLogger(__name__)


@dataclass
class Priors:
    """What CONSULT hands back to the bundle assembler."""

    precedent: Optional[dict] = None
    rules: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ProtectedRollbackPlan:
    """Verified immutable inputs for one rollback pointer transition."""

    current_pointer: LiveSkillPointer
    rollback_pointer: LiveSkillPointer
    sequence_payload: dict


def _precedent_key(vendor: str, vendor_product_ref: str) -> str:
    return f"precedent:{vendor}:{vendor_product_ref}"


def _rule_prefix(vendor: str) -> str:
    return f"rule:{vendor}:"


def consult_priors(*, vendor: str, vendor_product_ref: str) -> Priors:
    """Read the real precedent (if any) + promoted rules for this vendor.

    Fails OPEN: any error (Aurora unreachable, env not configured, malformed
    row) is logged and treated as "no priors" rather than raised — CONSULT is
    advisory context for the mapping specialist, never a hard dependency for
    the request to proceed. Same philosophy as hydrate.py's "cold start is a
    WARN, not a failure".
    """
    try:
        result = aurora_store._execute(
            "select memory_key, memory_type, payload from scudo.agent_memory "
            "where memory_key = :precedent_key or memory_key like :rule_prefix",
            [
                aurora_store._str_param(
                    "precedent_key", _precedent_key(vendor, vendor_product_ref)
                ),
                aurora_store._str_param("rule_prefix", _rule_prefix(vendor) + "%"),
            ],
        )
    except Exception as e:  # noqa: BLE001 — CONSULT is advisory, never blocks
        log.warning(
            "consult_priors failed, proceeding with no priors: %s: %s",
            type(e).__name__,
            e,
        )
        return Priors()

    priors = Priors()
    for rec in result.get("records", []):
        memory_type = (rec[1] or {}).get("stringValue", "") if len(rec) > 1 else ""
        raw_payload = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
        except (ValueError, TypeError):
            continue
        if memory_type == "precedent":
            priors.precedent = payload
        elif memory_type == "rule":
            priors.rules.append(payload)
    return priors


def record_verified_precedent(
    *,
    vendor: str,
    vendor_product_ref: str,
    target_iri: str,
    confidence: float,
    rationale: str,
    source_outcome_ref: str,
) -> None:
    """Upsert a durable precedent row for a verified auto-pass outcome.

    FAIL LOUD (raises on any error, including missing Aurora config) — a lost
    precedent write silently defeats the entire point of this feature, so the
    caller's request must fail rather than mask it. Matches this session's
    db.py Aurora fail-fast precedent.
    """
    payload: dict[str, Any] = {
        "target_iri": target_iri,
        "confidence": confidence,
        "rationale": rationale,
        "source_outcome_ref": source_outcome_ref,
        "decided_at": time.time(),
    }
    aurora_store._execute(
        "insert into scudo.agent_memory (memory_key, memory_type, updated_at_ms, payload) "
        "values (:memory_key, :memory_type, :updated_at_ms, :payload::jsonb) "
        "on conflict (memory_key) do update set "
        "payload = excluded.payload, updated_at_ms = excluded.updated_at_ms",
        [
            aurora_store._str_param(
                "memory_key", _precedent_key(vendor, vendor_product_ref)
            ),
            aurora_store._str_param("memory_type", "precedent"),
            aurora_store._str_param("updated_at_ms", str(int(time.time() * 1000))),
            aurora_store._json_param("payload", payload),
        ],
    )


# ──────────────────────────────────────────────────────────────────────────
# Part D — SkillOpt-inspired matching skill memory
#
# microsoft/SkillOpt (verified real: PyPI `skillopt`, arXiv:2605.23904) trains
# a "skill document" — a compact natural-language instruction blob — as the
# trainable state of a frozen agent, via a held-out validation gate; the
# deployed artifact runs with ZERO inference-time model calls. The `skillopt`
# package itself is NOT installed/vendored in this repo (verified), so the
# live half here is a plain-text CONSULT read only — no import of the
# package anywhere in this module. The training loop (rollout/reflect/
# validate/promote) is the OFFLINE half; see skillopt_sleep_runner.py.
#
# Two singleton rows in the SAME scudo.agent_memory table (no new table):
#   memory_key='skill:matching:current' — latest candidate, may be unvalidated
#   memory_key='skill:matching:best'    — validated, deployment-ready; the
#                                          ONLY one live agents ever read
_BEST_SKILL_KEY = "skill:matching:best"
_PROMOTION_SEQUENCE_PREFIX = "skill:matching:promotion:"


def _read_live_pointer() -> Optional[LiveSkillPointer]:
    pointer_payload = _read_memory_payload(_BEST_SKILL_KEY)
    if not pointer_payload:
        return None
    raw_pointer = pointer_payload.get("pointer")
    if not raw_pointer or not verify_live_pointer(raw_pointer):
        return None
    try:
        pointer = LiveSkillPointer.model_validate(raw_pointer)
    except Exception:
        return None
    sequence_payload = _read_memory_payload(
        f"{_PROMOTION_SEQUENCE_PREFIX}{pointer.sequence}"
    )
    if not sequence_payload or sequence_payload.get("committed_pointer") != raw_pointer:
        return None
    return pointer


def _resolve_verified_current_artifact(
    *,
    fail_closed: bool,
) -> tuple[Optional[LiveSkillPointer], Optional[LearningArtifact]]:
    """Resolve pointer → sequence → immutable artifact through every live gate."""

    pointer_payload = _read_memory_payload(_BEST_SKILL_KEY)
    if not pointer_payload:
        return None, None
    pointer = _read_live_pointer()
    if pointer is None:
        if fail_closed:
            raise PromotionRejected("current live pointer is malformed or uncommitted")
        return None, None
    artifact_payload = _read_memory_payload(pointer.artifact_key)
    artifact = _artifact_from_skill_payload(artifact_payload or {})
    valid = bool(
        artifact_payload
        and artifact is not None
        and artifact_payload.get("status") == "approved"
        and artifact_payload.get("immutable") is True
        and artifact.version == pointer.artifact_version
        and learning_artifact_digest(artifact) == pointer.artifact_digest
        and _has_protected_promotion_receipt(artifact_payload, artifact)
        and artifact.live_eligible
    )
    if not valid:
        if fail_closed:
            raise PromotionRejected("current protected artifact failed verification")
        return None, None
    return pointer, artifact


def _has_protected_promotion_receipt(
    payload: dict,
    artifact: LearningArtifact,
) -> bool:
    return verify_promotion_receipt(
        artifact,
        payload.get("protected_promotion_receipt") or {},
        evaluation_public_key_pem=os.environ.get("SCUDO_EVALUATION_PUBLIC_KEY"),
    )


def _read_memory_payload(memory_key: str) -> Optional[dict]:
    result = aurora_store._execute(
        "select memory_key, memory_type, payload from scudo.agent_memory "
        "where memory_key = :memory_key",
        [aurora_store._str_param("memory_key", memory_key)],
    )
    for rec in result.get("records", []):
        record_key = (rec[0] or {}).get("stringValue") if rec else None
        if record_key != memory_key:
            continue
        raw_payload = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            payload = json.loads(raw_payload) if raw_payload else None
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def consult_best_skill() -> Optional[dict]:
    """Read the current approved matching skill doc, if one exists.

    A row is live-readable only when it carries a valid evaluated
    ``LearningArtifact`` and a named ``PromotionApproval``. Older scalar-score
    rows are deliberately quarantined by returning ``None``; an artifact must
    not influence prompts merely because it was written to Aurora.

    Fails OPEN (Aurora unreachable/misconfigured/malformed -> None, never
    raises) — same advisory contract as consult_priors. A missing or
    quarantined skill doc is a normal state.

    Threat boundary: the signed pointer chain plus conditional advancement
    prevents stale/replayed writes through this API. An attacker with direct
    database write access could replace both the singleton pointer and its
    referenced immutable rows; preventing that requires an external append-only
    ledger or database audit control beyond this Data API persistence contract.
    """
    try:
        pointer, artifact = _resolve_verified_current_artifact(fail_closed=False)
    except Exception as e:  # noqa: BLE001 — advisory, never blocks a mapping run
        log.warning(
            "consult_best_skill failed, proceeding with no skill doc: %s",
            type(e).__name__,
        )
        return None

    if pointer is None or artifact is None:
        return None
    return _read_memory_payload(pointer.artifact_key)


def _trajectory_key(bundle_ref: str) -> str:
    return f"trajectory:{bundle_ref}"


def record_trajectory(
    *,
    bundle_ref: str,
    vendor: str,
    vendor_product_ref: str,
    target_iri: str,
    confidence: float,
    rationale: str,
    outcome: str = "published",
    status: str = "auto_mapped",
    band: Optional[str] = None,
    auto_pass: bool = True,
    verifier_score: Optional[int] = None,
    matcher_version: str = "unknown",
    ontology_snapshot: str = "",
    rubric_version: str = "",
    prompt_version: str = "",
    skill_version: Optional[int] = None,
    source_content_hash: Optional[str] = None,
    source_file_audit_id: Optional[str] = None,
    surface: str = "agent",
    input_snapshot: Optional[dict] = None,
    decision_snapshot: Optional[dict] = None,
) -> None:
    """Record a verified mapping outcome as SkillOpt-style rollout evidence,
    for an offline harvest step (skillopt_sleep_runner.py) to later mine into
    skill-doc edits. FAIL LOUD — a lost trajectory silently starves the
    offline loop, same contract as record_verified_precedent.
    """
    payload: dict[str, Any] = {
        "vendor": vendor,
        "vendor_product_ref": vendor_product_ref,
        "target_iri": target_iri,
        "confidence": confidence,
        "rationale": rationale,
        "decided_at": time.time(),
        "outcome": outcome,
        "status": status,
        "band": band,
        "auto_pass": auto_pass,
        "verifier_score": verifier_score,
        "matcher_version": matcher_version,
        "ontology_snapshot": ontology_snapshot,
        "rubric_version": rubric_version,
        "prompt_version": prompt_version,
        "skill_version": skill_version,
        "source_content_hash": source_content_hash,
        "source_file_audit_id": source_file_audit_id,
        "surface": surface,
        "input_snapshot": input_snapshot or {},
        "decision_snapshot": decision_snapshot or {},
    }
    aurora_store._execute(
        "insert into scudo.agent_memory (memory_key, memory_type, updated_at_ms, payload) "
        "values (:memory_key, :memory_type, :updated_at_ms, :payload::jsonb) "
        "on conflict (memory_key) do update set "
        "payload = excluded.payload, updated_at_ms = excluded.updated_at_ms",
        [
            aurora_store._str_param("memory_key", _trajectory_key(bundle_ref)),
            aurora_store._str_param("memory_type", "trajectory"),
            aurora_store._str_param("updated_at_ms", str(int(time.time() * 1000))),
            aurora_store._json_param("payload", payload),
        ],
    )


def record_engine_trajectory(
    *,
    bundle_ref: str,
    vendor: str,
    vendor_product_ref: str,
    mapping_result: Any,
    matcher_version: str,
    ontology_snapshot: str = "",
    source_content_hash: Optional[str] = None,
    source_file_audit_id: Optional[str] = None,
) -> None:
    """Record a deterministic-engine result in the shared trajectory shape.

    The engine package does not depend on Aurora. Callers that already own the
    persistence boundary can use this adapter to preserve the same evidence
    contract as the agent path without changing the engine's authoritative
    decision logic.
    """

    from .matching_self_improvement import MatchingPrediction

    prediction = MatchingPrediction.from_result(mapping_result)
    record_trajectory(
        bundle_ref=bundle_ref,
        vendor=vendor,
        vendor_product_ref=vendor_product_ref,
        target_iri=prediction.target_iri or "",
        confidence=prediction.confidence,
        rationale=prediction.rationale,
        outcome="auto_mapped" if prediction.auto_pass else "needs_review",
        status=prediction.status,
        band=prediction.band,
        auto_pass=prediction.auto_pass,
        matcher_version=matcher_version,
        ontology_snapshot=ontology_snapshot,
        source_content_hash=source_content_hash,
        source_file_audit_id=source_file_audit_id,
        surface="matching_engine",
        decision_snapshot=prediction.model_dump(mode="json"),
    )


def harvest_trajectories(limit: int = 100) -> list[dict]:
    """Read recorded trajectories (newest first) for the offline HARVEST
    step. Fails OPEN — a failed harvest just means "nothing to mine this
    cycle," never blocks or crashes the (already offline, non-request-path)
    caller.
    """
    try:
        result = aurora_store._execute(
            "select memory_key, memory_type, payload from scudo.agent_memory "
            "where memory_type = :memory_type order by updated_at_ms desc "
            "limit :limit",
            [
                aurora_store._str_param("memory_type", "trajectory"),
                {"name": "limit", "value": {"longValue": int(limit)}},
            ],
        )
    except Exception as e:  # noqa: BLE001 — harvest is best-effort, never blocks
        log.warning(
            "harvest_trajectories failed, proceeding with none: %s: %s",
            type(e).__name__,
            e,
        )
        return []

    trajectories: list[dict] = []
    for rec in result.get("records", []):
        raw_payload = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            payload = json.loads(raw_payload) if raw_payload else None
        except (ValueError, TypeError):
            continue
        if payload is not None:
            trajectories.append(payload)
    return trajectories


def _artifact_from_skill_payload(payload: dict) -> Optional[LearningArtifact]:
    try:
        artifact_payload = {
            "artifact_id": payload["artifact_id"],
            "artifact_kind": payload.get("artifact_kind", "matching_skill"),
            "version": payload["version"],
            "content": payload.get("skill_text") or payload.get("content", ""),
            "source_trajectory_refs": payload.get("source_trajectory_refs", []),
            "evaluation": payload["evaluation"],
            "approval": payload["approval"],
        }
        if payload.get("created_at"):
            artifact_payload["created_at"] = payload["created_at"]
        return LearningArtifact.model_validate(artifact_payload)
    except Exception:
        return None


def _artifact_payload_matches(existing: dict, candidate: dict) -> bool:
    if any(
        existing.get(key) != candidate.get(key)
        for key in (
            "artifact_id",
            "artifact_kind",
            "skill_text",
            "version",
            "source_trajectory_refs",
        )
    ):
        return False
    existing_evaluation = existing.get("evaluation")
    candidate_evaluation = candidate.get("evaluation")
    existing_approval = existing.get("approval")
    candidate_approval = candidate.get("approval")
    if not all(
        isinstance(value, dict)
        for value in (
            existing_evaluation,
            candidate_evaluation,
            existing_approval,
            candidate_approval,
        )
    ):
        return False
    existing_evaluation = dict(existing_evaluation)
    candidate_evaluation = dict(candidate_evaluation)
    existing_approval = dict(existing_approval)
    candidate_approval = dict(candidate_approval)
    # A caller supplying an evaluation dict without evaluated_at receives a
    # model default on each retry. The same applies to approval.approved_at.
    # Those generated timestamps are not substantive retry differences.
    existing_evaluation.pop("evaluated_at", None)
    candidate_evaluation.pop("evaluated_at", None)
    existing_approval.pop("approved_at", None)
    candidate_approval.pop("approved_at", None)
    return (
        existing_evaluation == candidate_evaluation
        and existing_approval == candidate_approval
    )


def _artifact_comparison_payload(candidate: LearningArtifact) -> dict:
    return {
        "artifact_id": candidate.artifact_id,
        "artifact_kind": candidate.artifact_kind,
        "skill_text": candidate.content,
        "version": candidate.version,
        "source_trajectory_refs": candidate.source_trajectory_refs,
        "evaluation": candidate.evaluation.model_dump(mode="json"),
        "approval": candidate.approval.model_dump(mode="json"),
    }


def next_skill_version(*, minimum: int = 1) -> int:
    """Allocate a version beyond immutable artifacts and the historic pointer.

    The live pointer can be absent or quarantined while prior immutable
    artifacts still exist. Conversely, an older best pointer can predate
    immutable artifacts. Both sources must contribute to version allocation
    so a new artifact never reuses or regresses a known version.
    """
    result = aurora_store._execute(
        "select memory_key, memory_type, payload from scudo.agent_memory "
        "where memory_type = :artifact_memory_type "
        "or memory_key = :best_skill_key",
        [
            aurora_store._str_param("artifact_memory_type", "skill_artifact"),
            aurora_store._str_param("best_skill_key", _BEST_SKILL_KEY),
        ],
    )
    highest_version = 0
    for rec in result.get("records", []):
        raw_payload = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            payload = json.loads(raw_payload) if raw_payload else None
            version = payload.get("version") if isinstance(payload, dict) else None
            if isinstance(version, int) and not isinstance(version, bool):
                highest_version = max(highest_version, version)
        except (ValueError, TypeError):
            continue
    return max(highest_version + 1, max(int(minimum), 1))


def preflight_skill_promotion(
    *,
    skill_text: str,
    version: int,
    evaluation: EvaluationReport | dict | None,
    approval: PromotionApproval | dict | None,
    source_trajectory_refs: Optional[list[str]] = None,
    artifact_id: Optional[str] = None,
) -> Optional[LearningArtifact]:
    """Validate a candidate against the live promotion boundary without writes.

    The scheduler dry-run uses this exact preflight so its decision stays
    aligned with a real promotion while leaving both immutable artifacts and
    the live pointer untouched.
    """
    if evaluation is None or approval is None:
        log.warning(
            "refusing skill promotion without holdout evaluation and named approval"
        )
        return None
    try:
        evaluation_model = (
            evaluation
            if isinstance(evaluation, EvaluationReport)
            else EvaluationReport.model_validate(evaluation)
        )
        approval_model = (
            approval
            if isinstance(approval, PromotionApproval)
            else PromotionApproval.model_validate(approval)
        )
        candidate = LearningArtifact(
            artifact_id=artifact_id or f"matching-skill-{version}",
            artifact_kind="matching_skill",
            version=version,
            content=skill_text,
            source_trajectory_refs=source_trajectory_refs or [],
            evaluation=evaluation_model,
            approval=approval_model,
        )
    except Exception as exc:  # noqa: BLE001 — invalid candidate is not live
        log.warning(
            "refusing malformed skill promotion candidate: %s",
            type(exc).__name__,
        )
        return None

    # Manual persistence is quarantine-only, so it must neither depend on nor
    # compare against the protected live pointer.
    current_artifact = None
    try:
        # Legacy/manual preflight remains quarantined: it can maintain existing
        # persistence workflows but cannot mint a protected promotion receipt,
        # so consult_best_skill will never inject its output into live prompts.
        validate_manual_promotion(candidate, current=current_artifact)
    except PromotionRejected as exc:
        log.info("skill promotion rejected: %s", exc)
        return None

    artifact_key = f"skill:matching:artifact:{candidate.version}"
    existing = _read_memory_payload(artifact_key)
    if existing and not _artifact_payload_matches(
        existing, _artifact_comparison_payload(candidate)
    ):
        log.warning(
            "refusing skill promotion because immutable artifact version %s "
            "already belongs to a different candidate",
            candidate.version,
        )
        return None
    return candidate


def promote_skill(
    *,
    skill_text: str,
    validation_score: float,
    version: int,
    evaluation: EvaluationReport | dict | None = None,
    approval: PromotionApproval | dict | None = None,
    source_trajectory_refs: Optional[list[str]] = None,
    artifact_id: Optional[str] = None,
) -> bool:
    """Persist a legacy/manual candidate under quarantine.

    ``validation_score`` remains accepted for compatibility with the older
    sleep-runner interface and is stored as a diagnostic field. It is not a
    promotion decision. A candidate must carry a passed holdout
    ``EvaluationReport`` and a named ``PromotionApproval``. The artifact body
    is written under an immutable versioned key before the live pointer is
    updated. This compatibility API never updates the live best pointer and
    therefore returns ``False`` even after a successful immutable write.

    Returns ``False`` for a rejected candidate and fails loud on Aurora write
    errors. Newly recorded candidates therefore cannot influence live prompts
    without a complete, approved artifact.
    """
    candidate = preflight_skill_promotion(
        skill_text=skill_text,
        version=version,
        evaluation=evaluation,
        approval=approval,
        source_trajectory_refs=source_trajectory_refs,
        artifact_id=artifact_id,
    )
    if candidate is None:
        return False

    payload: dict[str, Any] = {
        "status": "quarantined",
        "artifact_id": candidate.artifact_id,
        "artifact_kind": candidate.artifact_kind,
        "skill_text": skill_text,
        "version": version,
        "validation_score": validation_score,
        "created_at": candidate.created_at,
        "promoted_at": time.time(),
        "immutable": True,
        "source_trajectory_refs": candidate.source_trajectory_refs,
        "evaluation": candidate.evaluation.model_dump(mode="json"),
        "approval": candidate.approval.model_dump(mode="json"),
    }
    artifact_key = f"skill:matching:artifact:{version}"
    artifact_payload = {
        **payload,
        "artifact_key": artifact_key,
    }
    artifact_write = aurora_store._execute(
        "insert into scudo.agent_memory (memory_key, memory_type, updated_at_ms, payload) "
        "values (:memory_key, :memory_type, :updated_at_ms, :payload::jsonb) "
        "on conflict (memory_key) do nothing",
        [
            aurora_store._str_param("memory_key", artifact_key),
            aurora_store._str_param("memory_type", "skill_artifact"),
            aurora_store._str_param("updated_at_ms", str(int(time.time() * 1000))),
            aurora_store._json_param("payload", artifact_payload),
        ],
    )
    if artifact_write.get("numberOfRecordsUpdated") == 0:
        existing = _read_memory_payload(artifact_key)
        if not existing or not _artifact_payload_matches(existing, artifact_payload):
            log.warning(
                "refusing skill promotion because immutable artifact version %s "
                "already belongs to a different candidate",
                version,
            )
            return False

    return False


def promote_protected_skill(
    *,
    skill_text: str,
    version: int,
    evaluation: EvaluationReport | dict,
    approval: PromotionApproval | dict,
    trusted_evidence: TrustedEvaluationEvidence,
    evaluation_attestation: Optional[EvaluationAttestation],
    signed_evaluation_envelope: Optional[Any] = None,
    evaluation_public_key_pem: Optional[str] = None,
    source_trajectory_refs: Optional[list[str]] = None,
    artifact_id: Optional[str] = None,
    signing_key: Optional[str] = None,
) -> bool:
    """Validate, sign, write immutably, then advance the live pointer."""

    evaluation_model = (
        evaluation
        if isinstance(evaluation, EvaluationReport)
        else EvaluationReport.model_validate(evaluation)
    )
    if isinstance(approval, PromotionApproval):
        approval_model = approval
    else:
        approval_payload = dict(approval)
        approval_payload.setdefault("approved_at", evaluation_model.evaluated_at)
        approval_model = PromotionApproval.model_validate(approval_payload)
    candidate = LearningArtifact(
        artifact_id=artifact_id or f"matching-skill-{version}",
        artifact_kind="matching_skill",
        version=version,
        content=skill_text,
        created_at=evaluation_model.evaluated_at,
        source_trajectory_refs=source_trajectory_refs or [],
        evaluation=evaluation_model,
        approval=approval_model,
    )
    predecessor_pointer, current = _resolve_verified_current_artifact(fail_closed=True)
    if (
        predecessor_pointer is not None
        and current is not None
        and candidate.version == current.version
        and learning_artifact_digest(candidate) == predecessor_pointer.artifact_digest
    ):
        return True
    validate_promotion(
        candidate,
        current=current,
        trusted_evidence=trusted_evidence,
    )
    receipt = promotion_receipt_for(
        candidate,
        trusted_evidence=trusted_evidence,
        evaluation_attestation=evaluation_attestation,
        signed_evaluation_envelope=signed_evaluation_envelope,
        signing_key=signing_key,
        evaluation_public_key_pem=evaluation_public_key_pem,
    )
    artifact_digest = learning_artifact_digest(candidate)
    if (
        predecessor_pointer
        and candidate.version <= predecessor_pointer.artifact_version
    ):
        return False
    artifact_key = f"skill:matching:artifact:{version}"
    pointer = issue_live_pointer(
        artifact_key=artifact_key,
        artifact_version=candidate.version,
        artifact_digest=artifact_digest,
        predecessor_version=(
            predecessor_pointer.artifact_version if predecessor_pointer else None
        ),
        predecessor_digest=(
            predecessor_pointer.artifact_digest if predecessor_pointer else None
        ),
        sequence=(predecessor_pointer.sequence + 1 if predecessor_pointer else 1),
        signing_key=signing_key,
    )
    artifact_payload: dict[str, Any] = {
        "status": "approved",
        "artifact_id": candidate.artifact_id,
        "artifact_kind": candidate.artifact_kind,
        "skill_text": candidate.content,
        "version": candidate.version,
        "created_at": candidate.created_at,
        "immutable": True,
        "source_trajectory_refs": candidate.source_trajectory_refs,
        "evaluation": candidate.evaluation.model_dump(mode="json"),
        "approval": candidate.approval.model_dump(mode="json"),
        "protected_promotion_receipt": receipt.model_dump(mode="json"),
    }
    artifact_payload["artifact_key"] = artifact_key
    artifact_sql = (
        "insert into scudo.agent_memory "
        "(memory_key, memory_type, updated_at_ms, payload) values "
        "(:artifact_key, 'skill_artifact', :artifact_updated_at_ms, "
        ":artifact_payload::jsonb) on conflict (memory_key) do nothing"
    )
    artifact_params = [
        aurora_store._str_param("artifact_key", artifact_key),
        aurora_store._long_param("artifact_updated_at_ms", int(time.time() * 1000)),
        aurora_store._json_param("artifact_payload", artifact_payload),
    ]
    pointer_payload = {"pointer": pointer.model_dump(mode="json")}
    sequence_key = f"{_PROMOTION_SEQUENCE_PREFIX}{pointer.sequence}"
    sequence_payload = {
        "committed_pointer": pointer.model_dump(mode="json"),
        "committed": True,
    }
    sequence_sql = (
        "insert into scudo.agent_memory "
        "(memory_key, memory_type, updated_at_ms, payload) values "
        "(:sequence_key, 'skill_promotion_sequence', :updated_at_ms, "
        ":sequence_payload::jsonb) on conflict (memory_key) do nothing"
    )
    sequence_params = [
        aurora_store._str_param("sequence_key", sequence_key),
        aurora_store._long_param("updated_at_ms", int(time.time() * 1000)),
        aurora_store._json_param("sequence_payload", sequence_payload),
    ]
    if predecessor_pointer is None:
        sql = (
            "/* compare-and-swap */ "
            "insert into scudo.agent_memory "
            "(memory_key, memory_type, updated_at_ms, payload) values "
            "(:memory_key, :memory_type, :updated_at_ms, :payload::jsonb) "
            "on conflict (memory_key) do nothing"
        )
        params = [
            aurora_store._str_param("memory_key", _BEST_SKILL_KEY),
            aurora_store._str_param("memory_type", "skill_doc"),
            aurora_store._long_param("updated_at_ms", int(time.time() * 1000)),
            aurora_store._json_param("payload", pointer_payload),
        ]
    else:
        sql = (
            "/* compare-and-swap */ "
            "update scudo.agent_memory set payload = :payload::jsonb, "
            "updated_at_ms = :updated_at_ms where memory_key = :memory_key "
            "and (payload->'pointer'->>'sequence')::bigint = :expected_sequence "
            "and (payload->'pointer'->>'artifact_version')::bigint = "
            ":expected_version and payload->'pointer'->>'artifact_digest' = "
            ":expected_digest"
        )
        params = [
            aurora_store._json_param("payload", pointer_payload),
            aurora_store._long_param("updated_at_ms", int(time.time() * 1000)),
            aurora_store._str_param("memory_key", _BEST_SKILL_KEY),
            aurora_store._long_param("expected_sequence", predecessor_pointer.sequence),
            aurora_store._long_param(
                "expected_version", predecessor_pointer.artifact_version
            ),
            aurora_store._str_param(
                "expected_digest", predecessor_pointer.artifact_digest
            ),
        ]
    with aurora_store.transaction() as tx:
        tx.execute(artifact_sql, artifact_params, expected_rows=1)
        tx.execute(sequence_sql, sequence_params, expected_rows=1)
        tx.execute(sql, params, expected_rows=1)
    return True


def migrate_legacy_best_skill(
    *,
    legacy_payload: dict,
    operator_migration_ref: str,
    skill_text: str,
    version: int,
    evaluation: EvaluationReport | dict,
    approval: PromotionApproval | dict,
    trusted_evidence: TrustedEvaluationEvidence,
    evaluation_attestation: EvaluationAttestation,
    signing_key: Optional[str] = None,
    signed_evaluation_envelope: Optional[Any] = None,
    evaluation_public_key_pem: Optional[str] = None,
) -> bool:
    """Atomically replace one exact legacy row with a protected genesis."""

    if not operator_migration_ref.strip():
        raise ValueError("operator_migration_ref is required")
    legacy_version = legacy_payload.get("version")
    if not isinstance(legacy_version, int) or version <= legacy_version:
        raise PromotionRejected("protected migration version must exceed legacy")
    evaluation_model = (
        evaluation
        if isinstance(evaluation, EvaluationReport)
        else EvaluationReport.model_validate(evaluation)
    )
    approval_model = (
        approval
        if isinstance(approval, PromotionApproval)
        else PromotionApproval.model_validate(approval)
    )
    candidate = LearningArtifact(
        artifact_id=f"matching-skill-{version}",
        artifact_kind="matching_skill",
        version=version,
        content=skill_text,
        created_at=evaluation_model.evaluated_at,
        evaluation=evaluation_model,
        approval=approval_model,
    )
    validate_promotion(candidate, trusted_evidence=trusted_evidence)
    receipt = promotion_receipt_for(
        candidate,
        trusted_evidence=trusted_evidence,
        evaluation_attestation=evaluation_attestation,
        signed_evaluation_envelope=signed_evaluation_envelope,
        signing_key=signing_key,
        evaluation_public_key_pem=evaluation_public_key_pem,
    )
    artifact_key = f"skill:matching:artifact:{version}"
    artifact_digest = learning_artifact_digest(candidate)
    pointer = issue_live_pointer(
        artifact_key=artifact_key,
        artifact_version=version,
        artifact_digest=artifact_digest,
        predecessor_version=None,
        predecessor_digest=None,
        sequence=1,
        signing_key=signing_key,
    )
    archive_key = f"skill:matching:legacy-migration:{operator_migration_ref}"
    archive_payload = {
        "operator_migration_ref": operator_migration_ref,
        "legacy_payload": legacy_payload,
        "candidate_digest": artifact_digest,
        "status": "quarantined",
        "immutable": True,
    }
    artifact_payload = {
        "artifact_id": candidate.artifact_id,
        "artifact_kind": candidate.artifact_kind,
        "skill_text": candidate.content,
        "version": candidate.version,
        "created_at": candidate.created_at,
        "source_trajectory_refs": candidate.source_trajectory_refs,
        "evaluation": candidate.evaluation.model_dump(mode="json"),
        "approval": candidate.approval.model_dump(mode="json"),
        "status": "approved",
        "immutable": True,
        "protected_promotion_receipt": receipt.model_dump(mode="json"),
    }
    sequence_payload = {
        "committed": True,
        "committed_pointer": pointer.model_dump(mode="json"),
    }
    pointer_payload = {"pointer": pointer.model_dump(mode="json")}
    try:
        existing_archive = _read_memory_payload(archive_key)
    except RuntimeError as exc:
        # Some transaction-focused callers supply the transaction boundary
        # directly without configuring the non-transactional read client.
        if "is not set" not in str(exc):
            raise
        existing_archive = None
    if existing_archive is not None:
        if existing_archive != archive_payload:
            raise PromotionRejected(
                "operator migration reference already belongs to another migration"
            )
        return bool(
            _read_memory_payload(artifact_key) == artifact_payload
            and _read_memory_payload(f"{_PROMOTION_SEQUENCE_PREFIX}1")
            == sequence_payload
            and _read_memory_payload(_BEST_SKILL_KEY) == pointer_payload
        )
    with aurora_store.transaction() as tx:
        tx.execute(
            "select payload from scudo.agent_memory where memory_key = :key "
            "and payload = :legacy::jsonb for update",
            [
                aurora_store._str_param("key", _BEST_SKILL_KEY),
                aurora_store._json_param("legacy", legacy_payload),
            ],
        )
        tx.execute(
            "insert into scudo.agent_memory "
            "(memory_key,memory_type,updated_at_ms,payload) values "
            "(:key,'skill_legacy_migration',:ts,:payload::jsonb) "
            "on conflict (memory_key) do nothing",
            [
                aurora_store._str_param("key", archive_key),
                aurora_store._long_param("ts", int(time.time() * 1000)),
                aurora_store._json_param("payload", archive_payload),
            ],
            expected_rows=1,
        )
        tx.execute(
            "insert into scudo.agent_memory "
            "(memory_key,memory_type,updated_at_ms,payload) values "
            "(:key,'skill_artifact',:ts,:payload::jsonb) "
            "on conflict (memory_key) do nothing",
            [
                aurora_store._str_param("key", artifact_key),
                aurora_store._long_param("ts", int(time.time() * 1000)),
                aurora_store._json_param("payload", artifact_payload),
            ],
            expected_rows=1,
        )
        tx.execute(
            "insert into scudo.agent_memory "
            "(memory_key,memory_type,updated_at_ms,payload) values "
            "(:key,'skill_promotion_sequence',:ts,:payload::jsonb) "
            "on conflict (memory_key) do nothing",
            [
                aurora_store._str_param("key", f"{_PROMOTION_SEQUENCE_PREFIX}1"),
                aurora_store._long_param("ts", int(time.time() * 1000)),
                aurora_store._json_param("payload", sequence_payload),
            ],
            expected_rows=1,
        )
        tx.execute(
            "update scudo.agent_memory set memory_type='skill_doc', "
            "updated_at_ms=:ts,payload=:pointer::jsonb where memory_key=:key "
            "and payload=:legacy::jsonb",
            [
                aurora_store._long_param("ts", int(time.time() * 1000)),
                aurora_store._json_param("pointer", pointer_payload),
                aurora_store._str_param("key", _BEST_SKILL_KEY),
                aurora_store._json_param("legacy", legacy_payload),
            ],
            expected_rows=1,
        )
    return True


def _prepare_protected_skill_rollback(
    *,
    operator_rollback_ref: str,
    reason: str,
    expected_sequence: int,
    signing_key: Optional[str] = None,
) -> Optional[ProtectedRollbackPlan]:
    """Verify rollback ancestry and prepare writes without committing them."""

    if not operator_rollback_ref.strip() or not reason.strip():
        raise ValueError("operator rollback reference and reason are required")
    current_pointer, current = _resolve_verified_current_artifact(fail_closed=True)
    if current_pointer is None or current is None:
        return None
    if current_pointer.sequence != expected_sequence:
        return None
    if (
        current_pointer.predecessor_version is None
        or current_pointer.predecessor_digest is None
    ):
        return None
    predecessor_key = f"skill:matching:artifact:{current_pointer.predecessor_version}"
    predecessor_payload = _read_memory_payload(predecessor_key)
    predecessor = _artifact_from_skill_payload(predecessor_payload or {})
    historical_result = aurora_store._execute(
        "select memory_key, memory_type, payload from scudo.agent_memory "
        "where memory_type = 'skill_promotion_sequence' "
        "and payload->'committed_pointer'->>'transition_kind' = 'promote' "
        "and (payload->'committed_pointer'->>'artifact_version')::bigint = "
        ":artifact_version "
        "and payload->'committed_pointer'->>'artifact_digest' = :artifact_digest "
        "order by updated_at_ms asc limit 1",
        [
            aurora_store._long_param(
                "artifact_version", current_pointer.predecessor_version
            ),
            aurora_store._str_param(
                "artifact_digest", current_pointer.predecessor_digest
            ),
        ],
    )
    historical_records = historical_result.get("records", [])
    if not historical_records:
        return None
    try:
        historical_key = (historical_records[0][0] or {}).get("stringValue", "")
        historical_type = (historical_records[0][1] or {}).get("stringValue", "")
        historical_payload = json.loads(
            (historical_records[0][2] or {}).get("stringValue", "")
        )
        historical_pointer = LiveSkillPointer.model_validate(
            historical_payload["committed_pointer"]
        )
    except Exception:
        return None
    if (
        predecessor is None
        or learning_artifact_digest(predecessor) != current_pointer.predecessor_digest
        or not _has_protected_promotion_receipt(predecessor_payload, predecessor)
        or historical_pointer.artifact_version != predecessor.version
        or historical_pointer.artifact_digest != current_pointer.predecessor_digest
        or historical_pointer.artifact_key != predecessor_key
        or historical_pointer.transition_kind != "promote"
        or historical_type != "skill_promotion_sequence"
        or historical_key
        != f"{_PROMOTION_SEQUENCE_PREFIX}{historical_pointer.sequence}"
        or not verify_live_pointer(historical_pointer, signing_key=signing_key)
    ):
        return None
    rollback_pointer = issue_live_pointer(
        artifact_key=predecessor_key,
        artifact_version=predecessor.version,
        artifact_digest=current_pointer.predecessor_digest,
        predecessor_version=historical_pointer.predecessor_version,
        predecessor_digest=historical_pointer.predecessor_digest,
        sequence=current_pointer.sequence + 1,
        transition_kind="rollback",
        signing_key=signing_key,
    )
    sequence_payload = {
        "committed": True,
        "operator_rollback_ref": operator_rollback_ref,
        "reason": reason,
        "rolled_back_from": {
            "artifact_key": current_pointer.artifact_key,
            "artifact_version": current_pointer.artifact_version,
            "artifact_digest": current_pointer.artifact_digest,
        },
        "committed_pointer": rollback_pointer.model_dump(mode="json"),
    }
    return ProtectedRollbackPlan(
        current_pointer=current_pointer,
        rollback_pointer=rollback_pointer,
        sequence_payload=sequence_payload,
    )


def _execute_protected_skill_rollback(
    tx: aurora_store.Transaction,
    plan: ProtectedRollbackPlan,
) -> None:
    """Execute a prepared rollback in the caller's transaction."""

    tx.execute(
        "insert into scudo.agent_memory "
        "(memory_key,memory_type,updated_at_ms,payload) values "
        "(:key,'skill_promotion_sequence',:ts,:payload::jsonb) "
        "on conflict (memory_key) do nothing",
        [
            aurora_store._str_param(
                "key",
                f"{_PROMOTION_SEQUENCE_PREFIX}{plan.rollback_pointer.sequence}",
            ),
            aurora_store._long_param("ts", int(time.time() * 1000)),
            aurora_store._json_param("payload", plan.sequence_payload),
        ],
        expected_rows=1,
    )
    tx.execute(
        "update scudo.agent_memory set payload=:payload::jsonb,updated_at_ms=:ts "
        "where memory_key=:key and "
        "(payload->'pointer'->>'sequence')::bigint=:expected_sequence "
        "and payload->'pointer'->>'artifact_digest'=:expected_digest",
        [
            aurora_store._json_param(
                "payload",
                {"pointer": plan.rollback_pointer.model_dump(mode="json")},
            ),
            aurora_store._long_param("ts", int(time.time() * 1000)),
            aurora_store._str_param("key", _BEST_SKILL_KEY),
            aurora_store._long_param(
                "expected_sequence", plan.current_pointer.sequence
            ),
            aurora_store._str_param(
                "expected_digest", plan.current_pointer.artifact_digest
            ),
        ],
        expected_rows=1,
    )


def rollback_protected_skill(
    *,
    operator_rollback_ref: str,
    reason: str,
    expected_sequence: int,
    signing_key: Optional[str] = None,
) -> bool:
    """Advance pointer sequence to the predecessor artifact without mutation."""

    plan = _prepare_protected_skill_rollback(
        operator_rollback_ref=operator_rollback_ref,
        reason=reason,
        expected_sequence=expected_sequence,
        signing_key=signing_key,
    )
    if plan is None:
        return False
    with aurora_store.transaction() as tx:
        _execute_protected_skill_rollback(tx, plan)
    return True


def persist_monitoring_outcome(*, outcome, signing_key: Optional[str] = None) -> None:
    """Claim evidence, recheck live state, decide, and optionally roll back."""

    monitor_key = f"monitor:{outcome.window_id}"
    finalized_payload = {
        "status": "finalized",
        "input_digest": outcome.input_digest,
        "policy_digest": outcome.policy_digest,
        "outcome": outcome.model_dump(mode="json"),
    }
    plan = None
    if outcome.action == "rollback":
        plan = _prepare_protected_skill_rollback(
            operator_rollback_ref=f"auto-monitor:{outcome.window_id}",
            reason=f"{outcome.reason}; input_digest={outcome.input_digest}",
            expected_sequence=outcome.pointer_sequence,
            signing_key=signing_key,
        )
        if plan is None:
            raise RuntimeError("automatic monitoring rollback failed verification")
    pending_payload = {
        "status": "pending",
        "input_digest": outcome.input_digest,
        "policy_digest": outcome.policy_digest,
    }
    with aurora_store.transaction() as tx:
        now = int(time.time() * 1000)
        pending_payload = {
            "status": "pending",
            "input_digest": outcome.input_digest,
            "policy_digest": outcome.policy_digest,
        }
        tx.execute(
            "insert into scudo.agent_memory "
            "(memory_key,memory_type,updated_at_ms,payload) values "
            "(:key,'promotion_monitor_decision',:ts,:payload::jsonb) "
            "on conflict (memory_key) do nothing",
            [
                aurora_store._str_param("key", monitor_key),
                aurora_store._long_param("ts", now),
                aurora_store._json_param("payload", pending_payload),
            ],
            expected_rows=1,
        )
        for observation in outcome.observations:
            observation_key = (
                "monitor-observation:"
                f"{outcome.artifact_digest}:{outcome.pointer_sequence}:"
                f"{observation.source_event_id}"
            )
            claim_payload = {
                "artifact_key": outcome.artifact_key,
                "artifact_version": outcome.artifact_version,
                "artifact_digest": outcome.artifact_digest,
                "pointer_sequence": outcome.pointer_sequence,
                "source_event_id": observation.source_event_id,
                "window_id": outcome.window_id,
                "input_digest": outcome.input_digest,
                "authoritative_outcome": observation.authoritative_outcome.model_dump(
                    mode="json"
                ),
                "immutable": True,
            }
            tx.execute(
                "insert into scudo.agent_memory "
                "(memory_key,memory_type,updated_at_ms,payload) values "
                "(:key,'monitoring_observation_claim',:ts,:payload::jsonb) "
                "on conflict (memory_key) do nothing",
                [
                    aurora_store._str_param("key", observation_key),
                    aurora_store._long_param("ts", now),
                    aurora_store._json_param("payload", claim_payload),
                ],
                expected_rows=1,
            )
        pointer_result = tx.execute(
            "select payload from scudo.agent_memory where memory_key=:key for update",
            [aurora_store._str_param("key", _BEST_SKILL_KEY)],
        )
        records = pointer_result.get("records", [])
        try:
            locked_pointer = LiveSkillPointer.model_validate(
                json.loads(records[0][0]["stringValue"])["pointer"]
            )
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError("monitoring live pointer lock failed") from exc
        if (
            not verify_live_pointer(locked_pointer, signing_key=signing_key)
            or locked_pointer.artifact_key != outcome.artifact_key
            or locked_pointer.artifact_version != outcome.artifact_version
            or locked_pointer.artifact_digest != outcome.artifact_digest
            or locked_pointer.sequence != outcome.pointer_sequence
        ):
            raise RuntimeError(
                "monitoring window does not match the signed live pointer"
            )
        if plan is not None:
            _execute_protected_skill_rollback(tx, plan)
        tx.execute(
            "update scudo.agent_memory set payload=:payload::jsonb,updated_at_ms=:ts "
            "where memory_key=:key and payload->>'status'='pending' "
            "and payload->>'input_digest'=:input_digest",
            [
                aurora_store._json_param("payload", finalized_payload),
                aurora_store._long_param("ts", now),
                aurora_store._str_param("key", monitor_key),
                aurora_store._str_param("input_digest", outcome.input_digest),
            ],
            expected_rows=1,
        )
