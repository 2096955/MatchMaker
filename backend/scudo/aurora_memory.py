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
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import aurora_store
from .matching_self_improvement import (
    EvaluationReport,
    LearningArtifact,
    PromotionApproval,
    PromotionRejected,
    validate_promotion,
)

log = logging.getLogger(__name__)


@dataclass
class Priors:
    """What CONSULT hands back to the bundle assembler."""

    precedent: Optional[dict] = None
    rules: list[dict] = field(default_factory=list)


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
    """
    try:
        payload = _read_memory_payload(_BEST_SKILL_KEY)
    except Exception as e:  # noqa: BLE001 — advisory, never blocks a mapping run
        log.warning(
            "consult_best_skill failed, proceeding with no skill doc: %s",
            type(e).__name__,
        )
        return None

    if not payload:
        return None
    artifact = _artifact_from_skill_payload(payload)
    if artifact is None:
        log.warning(
            "quarantining malformed best matching skill payload"
        )
        return None
    if (
        payload.get("status") != "approved"
        or payload.get("immutable") is not True
        or not artifact.live_eligible
    ):
        return None
    return payload


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
    """Allocate a version beyond every immutable matching-skill artifact.

    The live pointer can be absent or quarantined while prior immutable
    artifacts still exist. Those artifacts remain the authoritative version
    history, so version allocation must not rely on the pointer alone.
    """
    result = aurora_store._execute(
        "select memory_key, memory_type, payload from scudo.agent_memory "
        "where memory_type = :memory_type",
        [aurora_store._str_param("memory_type", "skill_artifact")],
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

    current = consult_best_skill()
    current_artifact = _artifact_from_skill_payload(current) if current else None
    try:
        validate_promotion(candidate, current=current_artifact)
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
    """Promote a matching skill only across the full offline boundary.

    ``validation_score`` remains accepted for compatibility with the older
    sleep-runner interface and is stored as a diagnostic field. It is not a
    promotion decision. A candidate must carry a passed holdout
    ``EvaluationReport`` and a named ``PromotionApproval``. The artifact body
    is written under an immutable versioned key before the live pointer is
    updated.

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
        "status": "approved",
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

    aurora_store._execute(
        "insert into scudo.agent_memory (memory_key, memory_type, updated_at_ms, payload) "
        "values (:memory_key, :memory_type, :updated_at_ms, :payload::jsonb) "
        "on conflict (memory_key) do update set "
        "payload = excluded.payload, updated_at_ms = excluded.updated_at_ms",
        [
            aurora_store._str_param("memory_key", _BEST_SKILL_KEY),
            aurora_store._str_param("memory_type", "skill_doc"),
            aurora_store._str_param("updated_at_ms", str(int(time.time() * 1000))),
            aurora_store._json_param("payload", payload),
        ],
    )
    return True
