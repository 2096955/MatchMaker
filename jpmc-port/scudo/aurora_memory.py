"""Aurora CONSULT/DISTILL memory — precedents, rules, skill, trajectories.

Live path only. Offline SkillOpt sleep/promote stays outside Lambda (never
import skillopt_sleep_runner / skillopt_adapter here).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import aurora_store, local_state
from .matching_self_improvement import (
    EvaluationAttestation,
    EvaluationReport,
    LearningArtifact,
    LiveSkillPointer,
    MatchingPrediction,
    PromotionApproval,
    SignedEvaluationEnvelope,
    TrustedEvaluationEvidence,
    issue_live_pointer,
    learning_artifact_digest,
    promotion_receipt_for,
    validate_promotion,
    verify_live_pointer,
    verify_promotion_receipt,
)
from .schemas import Band, MappingResult

log = logging.getLogger("scudo.aurora_memory")
_BEST_SKILL_KEY = "skill:matching:best"
_PROMOTION_SEQUENCE_PREFIX = "skill:matching:promotion:"


def _has_protected_promotion_receipt(
    payload: dict,
    artifact: LearningArtifact,
) -> bool:
    return verify_promotion_receipt(
        artifact,
        payload.get("protected_promotion_receipt") or {},
        evaluation_public_key_pem=os.environ.get("SCUDO_EVALUATION_PUBLIC_KEY"),
    )


@dataclass
class Priors:
    precedent: Optional[dict] = None
    rules: list[dict] = field(default_factory=list)


def _norm_vendor(vendor: str) -> str:
    """Canonical vendor key — case-insensitive across teach → next-run CONSULT."""
    return (vendor or "").strip().lower()


def _precedent_key(vendor: str, product_ref: str) -> str:
    return f"precedent:{_norm_vendor(vendor)}:{product_ref}"


def _rule_prefix(vendor: str) -> str:
    return f"rule:{_norm_vendor(vendor)}:"


def _trajectory_key(bundle_ref: str) -> str:
    return f"trajectory:{bundle_ref}"


def _write_memory(memory_key: str, memory_type: str, payload: dict) -> None:
    if local_state.is_local():

        def update(memory):
            memory[memory_key] = {
                "memory_type": memory_type,
                "payload": dict(payload),
                "updated_at_ms": int(time.time() * 1000),
            }
            return True

        local_state.atomic_memory_update(update)
        return
    aurora_store._execute(
        "insert into scudo.agent_memory (memory_key, memory_type, updated_at_ms, payload) "
        "values (:k, :t, :ts, :p::jsonb) "
        "on conflict (memory_key) do update set memory_type = excluded.memory_type, "
        "updated_at_ms = excluded.updated_at_ms, payload = excluded.payload",
        [
            aurora_store._str_param("k", memory_key),
            aurora_store._str_param("t", memory_type),
            aurora_store._str_param("ts", str(int(time.time() * 1000))),
            aurora_store._json_param("p", payload),
        ],
    )


def _read_memory_payload(memory_key: str) -> Optional[dict]:
    if local_state.is_local():
        row = local_state.read_memory(memory_key)
        return dict(row["payload"]) if row else None
    result = aurora_store._execute(
        "select payload::text from scudo.agent_memory where memory_key = :k",
        [aurora_store._str_param("k", memory_key)],
    )
    records = result.get("records") or []
    if not records:
        return None
    raw = records[0][0].get("stringValue")
    return json.loads(raw) if raw else None


def _artifact_from_skill_payload(payload: dict) -> Optional[LearningArtifact]:
    try:
        artifact_payload = payload.get("artifact") or payload
        return LearningArtifact.model_validate(artifact_payload)
    except Exception:
        return None


def consult_priors(*, vendor: str, vendor_product_ref: str) -> Priors:
    """Fail-open CONSULT: precedent + promoted rules for vendor."""
    try:
        if local_state.is_local():
            priors = Priors()
            pk = _precedent_key(vendor, vendor_product_ref)
            memory = local_state.memory_snapshot()
            row = memory.get(pk)
            if row and row.get("memory_type") == "precedent":
                priors.precedent = dict(row["payload"])
            prefix = _rule_prefix(vendor)
            for key, row in memory.items():
                if key.startswith(prefix) and row.get("memory_type") == "rule":
                    priors.rules.append(dict(row["payload"]))
            return priors

        result = aurora_store._execute(
            "select memory_key, memory_type, payload::text from scudo.agent_memory "
            "where memory_key = :precedent_key or memory_key like :rule_prefix",
            [
                aurora_store._str_param(
                    "precedent_key", _precedent_key(vendor, vendor_product_ref)
                ),
                aurora_store._str_param("rule_prefix", _rule_prefix(vendor) + "%"),
            ],
        )
    except Exception as e:
        log.warning("consult_priors failed open: %s: %s", type(e).__name__, e)
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


def consult_best_skill() -> tuple[Optional[str], Optional[int]]:
    """Fail-open skill CONSULT — only approved immutable LearningArtifacts."""
    try:
        payload = _read_memory_payload(_BEST_SKILL_KEY)
    except Exception as e:
        log.warning("consult_best_skill failed open: %s", type(e).__name__)
        return None, None
    if not payload:
        return None, None
    pointer_payload = payload.get("pointer")
    if not pointer_payload or not verify_live_pointer(pointer_payload):
        return None, None
    try:
        pointer = LiveSkillPointer.model_validate(pointer_payload)
        sequence_payload = _read_memory_payload(
            f"{_PROMOTION_SEQUENCE_PREFIX}{pointer.sequence}"
        )
        if (
            not sequence_payload
            or sequence_payload.get("committed_pointer") != pointer_payload
        ):
            return None, None
        artifact_payload = _read_memory_payload(pointer.artifact_key)
    except Exception:
        return None, None
    if not artifact_payload:
        return None, None
    artifact = _artifact_from_skill_payload(artifact_payload)
    if (
        artifact is not None
        and artifact.live_eligible
        and artifact_payload.get("status") == "approved"
        and artifact_payload.get("immutable") is True
        and artifact.version == pointer.artifact_version
        and learning_artifact_digest(artifact) == pointer.artifact_digest
        and _has_protected_promotion_receipt(artifact_payload, artifact)
    ):
        return artifact.content, artifact.version
    return None, None


def read_quarantined_skill() -> tuple[Optional[str], Optional[int]]:
    """Read legacy/unattested content for migration tooling, never live prompts."""

    try:
        payload = _read_memory_payload(_BEST_SKILL_KEY)
    except Exception:
        return None, None
    artifact_payload = (payload or {}).get("artifact") or payload or {}
    content = artifact_payload.get("content") or artifact_payload.get("skill_text")
    version = artifact_payload.get("version")
    if isinstance(content, str) and content.strip() and isinstance(version, int):
        return content, version
    return None, None


def promote_protected_skill(
    *,
    skill_text: str,
    version: int,
    evaluation: EvaluationReport | dict,
    approval: PromotionApproval | dict,
    trusted_evidence: TrustedEvaluationEvidence,
    evaluation_attestation: Optional[EvaluationAttestation],
    signed_evaluation_envelope: Optional[SignedEvaluationEnvelope] = None,
    evaluation_public_key_pem: Optional[str] = None,
    source_trajectory_refs: Optional[list[str]] = None,
    artifact_id: Optional[str] = None,
    signing_key: Optional[str] = None,
    expected_sequence: Optional[int] = None,
    inject_failure: bool = False,
) -> bool:
    """Atomically advance local protected state; remote JPMC remains read-only."""

    if not local_state.is_local():
        raise RuntimeError("JPMC protected writer is available only for local state")
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
    validate_promotion(candidate, trusted_evidence=trusted_evidence)
    receipt = promotion_receipt_for(
        candidate,
        trusted_evidence=trusted_evidence,
        evaluation_attestation=evaluation_attestation,
        signed_evaluation_envelope=signed_evaluation_envelope,
        signing_key=signing_key,
        evaluation_public_key_pem=evaluation_public_key_pem,
    )

    def transition(memory):
        current_row = memory.get(_BEST_SKILL_KEY)
        current_payload = dict(current_row.get("payload") or {}) if current_row else {}
        current_pointer = None
        current_artifact = None
        if current_row:
            raw_pointer = current_payload.get("pointer")
            if not raw_pointer or not verify_live_pointer(raw_pointer):
                return False
            try:
                current_pointer = LiveSkillPointer.model_validate(raw_pointer)
            except Exception:
                return False
            sequence_row = memory.get(
                f"{_PROMOTION_SEQUENCE_PREFIX}{current_pointer.sequence}"
            )
            artifact_row = memory.get(current_pointer.artifact_key)
            if (
                not sequence_row
                or (sequence_row.get("payload") or {}).get("committed_pointer")
                != raw_pointer
                or not artifact_row
            ):
                return False
            artifact_payload = dict(artifact_row.get("payload") or {})
            current_artifact = _artifact_from_skill_payload(artifact_payload)
            if (
                current_artifact is None
                or artifact_payload.get("status") != "approved"
                or artifact_payload.get("immutable") is not True
                or current_artifact.version != current_pointer.artifact_version
                or learning_artifact_digest(current_artifact)
                != current_pointer.artifact_digest
                or not _has_protected_promotion_receipt(
                    artifact_payload, current_artifact
                )
            ):
                return False
        observed_sequence = current_pointer.sequence if current_pointer else 0
        if expected_sequence is not None and expected_sequence != observed_sequence:
            return False
        if current_pointer and version <= current_pointer.artifact_version:
            return False
        try:
            validate_promotion(
                candidate,
                current=current_artifact,
                trusted_evidence=trusted_evidence,
            )
        except Exception:
            return False
        artifact_key = f"skill:matching:artifact:{version}"
        artifact_digest = learning_artifact_digest(candidate)
        pointer = issue_live_pointer(
            artifact_key=artifact_key,
            artifact_version=version,
            artifact_digest=artifact_digest,
            predecessor_version=(
                current_pointer.artifact_version if current_pointer else None
            ),
            predecessor_digest=(
                current_pointer.artifact_digest if current_pointer else None
            ),
            sequence=observed_sequence + 1,
            signing_key=signing_key,
        )
        artifact_payload = {
            "memory_type": "skill_artifact",
            "payload": {
                "artifact": candidate.model_dump(mode="json"),
                "status": "approved",
                "immutable": True,
                "protected_promotion_receipt": receipt.model_dump(mode="json"),
            },
            "updated_at_ms": int(time.time() * 1000),
        }
        sequence_key = f"{_PROMOTION_SEQUENCE_PREFIX}{pointer.sequence}"
        sequence_payload = {
            "memory_type": "skill_promotion_sequence",
            "payload": {
                "committed": True,
                "committed_pointer": pointer.model_dump(mode="json"),
            },
            "updated_at_ms": int(time.time() * 1000),
        }

        def immutable_equal(existing, candidate):
            return existing.get("memory_type") == candidate.get(
                "memory_type"
            ) and existing.get("payload") == candidate.get("payload")

        if artifact_key in memory and not immutable_equal(
            memory[artifact_key], artifact_payload
        ):
            return False
        if sequence_key in memory and not immutable_equal(
            memory[sequence_key], sequence_payload
        ):
            return False
        new_memory = dict(memory)
        new_memory[artifact_key] = artifact_payload
        new_memory[sequence_key] = sequence_payload
        new_memory[_BEST_SKILL_KEY] = {
            "memory_type": "skill_pointer",
            "payload": {"pointer": pointer.model_dump(mode="json")},
            "updated_at_ms": int(time.time() * 1000),
        }
        if inject_failure:
            return False
        memory.clear()
        memory.update(new_memory)
        return True

    return local_state.atomic_memory_update(transition)


def _resolve_verified_artifact_from_memory(
    memory: dict[str, dict],
    *,
    signing_key: Optional[str] = None,
) -> tuple[LiveSkillPointer, LearningArtifact]:
    current_row = memory.get(_BEST_SKILL_KEY)
    raw_pointer = ((current_row or {}).get("payload") or {}).get("pointer")
    if not raw_pointer or not verify_live_pointer(raw_pointer, signing_key=signing_key):
        raise RuntimeError("current live pointer is malformed")
    pointer = LiveSkillPointer.model_validate(raw_pointer)
    sequence_row = memory.get(f"{_PROMOTION_SEQUENCE_PREFIX}{pointer.sequence}")
    artifact_row = memory.get(pointer.artifact_key)
    artifact_payload = dict((artifact_row or {}).get("payload") or {})
    artifact = _artifact_from_skill_payload(artifact_payload)
    if (
        not sequence_row
        or (sequence_row.get("payload") or {}).get("committed_pointer") != raw_pointer
        or artifact is None
        or artifact_payload.get("status") != "approved"
        or artifact_payload.get("immutable") is not True
        or artifact.version != pointer.artifact_version
        or learning_artifact_digest(artifact) != pointer.artifact_digest
        or not _has_protected_promotion_receipt(artifact_payload, artifact)
    ):
        raise RuntimeError("current protected artifact failed verification")
    return pointer, artifact


def _apply_protected_rollback_to_memory(
    memory: dict[str, dict],
    *,
    operator_rollback_ref: str,
    reason: str,
    expected_sequence: int,
    signing_key: Optional[str] = None,
) -> None:
    current_pointer, _ = _resolve_verified_artifact_from_memory(
        memory, signing_key=signing_key
    )
    if current_pointer.sequence != expected_sequence:
        raise RuntimeError("rollback pointer sequence mismatch")
    if (
        current_pointer.predecessor_version is None
        or current_pointer.predecessor_digest is None
    ):
        raise RuntimeError("rollback predecessor is unavailable")
    predecessor_key = f"skill:matching:artifact:{current_pointer.predecessor_version}"
    predecessor_payload = dict((memory.get(predecessor_key) or {}).get("payload") or {})
    predecessor = _artifact_from_skill_payload(predecessor_payload)
    historical_pointer = None
    for key, row in memory.items():
        if not key.startswith(_PROMOTION_SEQUENCE_PREFIX):
            continue
        raw_historical = (row.get("payload") or {}).get("committed_pointer")
        try:
            candidate_pointer = LiveSkillPointer.model_validate(raw_historical)
        except Exception:
            continue
        if (
            row.get("memory_type") == "skill_promotion_sequence"
            and key == f"{_PROMOTION_SEQUENCE_PREFIX}{candidate_pointer.sequence}"
            and verify_live_pointer(candidate_pointer, signing_key=signing_key)
            and candidate_pointer.transition_kind == "promote"
            and candidate_pointer.artifact_version
            == current_pointer.predecessor_version
            and candidate_pointer.artifact_digest == current_pointer.predecessor_digest
            and candidate_pointer.artifact_key == predecessor_key
            and (
                historical_pointer is None
                or candidate_pointer.sequence < historical_pointer.sequence
            )
        ):
            historical_pointer = candidate_pointer
    if (
        predecessor is None
        or historical_pointer is None
        or predecessor_payload.get("status") != "approved"
        or predecessor_payload.get("immutable") is not True
        or learning_artifact_digest(predecessor) != current_pointer.predecessor_digest
        or not _has_protected_promotion_receipt(predecessor_payload, predecessor)
    ):
        raise RuntimeError("rollback predecessor failed verification")
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
    sequence_key = f"{_PROMOTION_SEQUENCE_PREFIX}{rollback_pointer.sequence}"
    if sequence_key in memory:
        raise RuntimeError("rollback sequence already exists")
    pointer_dump = rollback_pointer.model_dump(mode="json")
    memory[sequence_key] = {
        "memory_type": "skill_promotion_sequence",
        "payload": {
            "committed": True,
            "operator_rollback_ref": operator_rollback_ref,
            "reason": reason,
            "rolled_back_from": {
                "artifact_key": current_pointer.artifact_key,
                "artifact_version": current_pointer.artifact_version,
                "artifact_digest": current_pointer.artifact_digest,
            },
            "committed_pointer": pointer_dump,
        },
        "updated_at_ms": int(time.time() * 1000),
    }
    memory[_BEST_SKILL_KEY] = {
        "memory_type": "skill_pointer",
        "payload": {"pointer": pointer_dump},
        "updated_at_ms": int(time.time() * 1000),
    }


def rollback_protected_skill(
    *,
    operator_rollback_ref: str,
    reason: str,
    expected_sequence: int,
    signing_key: Optional[str] = None,
) -> bool:
    """Atomically advance local state to the current artifact's predecessor."""

    if not operator_rollback_ref.strip() or not reason.strip():
        raise ValueError("operator rollback reference and reason are required")
    if not local_state.is_local():
        raise RuntimeError("JPMC protected writer is available only for local state")

    def transition(memory):
        try:
            _apply_protected_rollback_to_memory(
                memory,
                operator_rollback_ref=operator_rollback_ref,
                reason=reason,
                expected_sequence=expected_sequence,
                signing_key=signing_key,
            )
        except (RuntimeError, ValueError):
            return False
        return True

    return local_state.atomic_memory_update(transition)


def record_verified_precedent(
    *,
    vendor: str,
    vendor_product_ref: str,
    source_iri: str,
    target_iri: str,
    confidence: float,
    rationale: str = "",
    source_outcome_ref: str = "",
) -> None:
    """Fail-loud DISTILL of a verified auto-pass."""
    _write_memory(
        _precedent_key(vendor, vendor_product_ref),
        "precedent",
        {
            "source_iri": source_iri,
            "target_iri": target_iri,
            "confidence": confidence,
            "rationale": rationale,
            "source_outcome_ref": source_outcome_ref,
            "decided_at": time.time(),
        },
    )


def record_trajectory(
    *,
    vendor: str,
    vendor_product_ref: str,
    mapping_result: Any,
    mapping_object: dict,
    bundle_ref: str = "",
) -> None:
    """Fail-loud trajectory evidence for offline harvest (never imported by sleep runner)."""
    prediction = MatchingPrediction.from_result(
        mapping_result, vendor=vendor, product_id=vendor_product_ref
    )
    key = _trajectory_key(
        bundle_ref or f"{vendor}:{vendor_product_ref}:{int(time.time() * 1000)}"
    )
    _write_memory(
        key,
        "trajectory",
        {
            "prediction": prediction.model_dump(mode="json"),
            "mapping_object": mapping_object,
            "vendor": vendor,
            "vendor_product_ref": vendor_product_ref,
            "recorded_at_ms": int(time.time() * 1000),
        },
    )


def _teaching_key(ticket: str) -> str:
    return f"teaching:{ticket or int(time.time() * 1000)}"


def _rule_candidate_key(vendor: str, ticket: str) -> str:
    slug = (ticket or str(int(time.time() * 1000))).replace(" ", "_")
    return f"rule-candidate:{_norm_vendor(vendor)}:{slug}"


def record_rule_candidate(
    *,
    vendor: str,
    text: str,
    polarity: str = "prefer",
    ticket: str = "",
    source: str = "user_teaching",
) -> str:
    """Fail-loud: quarantine a lesson for protected offline incorporation."""
    vendor = _norm_vendor(vendor)
    if not vendor or not text.strip():
        raise ValueError("record_rule_candidate requires vendor and non-empty text")
    key = _rule_candidate_key(vendor, ticket)
    _write_memory(
        key,
        "rule_candidate",
        {
            "text": text.strip(),
            "polarity": polarity,
            "ticket": ticket,
            "source": source,
            "decided_at": time.time(),
        },
    )
    return key


def consult_teaching_notes(*, limit: int = 20) -> str:
    """Compatibility surface: unpromoted teaching is never live prompt context."""
    return ""


def learn_from_teaching(
    *,
    ticket: str,
    decision: str,
    vendor: str,
    vendor_product_ref: str,
    source_iri: str = "",
    target_iri: str = "",
    lesson: str = "",
    confidence: float = 1.0,
    mapping_object: Optional[dict] = None,
) -> dict:
    """Fail-loud DISTILL: every user teaching updates agent memory.

    approve / correct → exact precedent + rule candidate + teaching episode
    reject            → rule candidate + teaching episode (no precedent)
    """
    decision_n = (decision or "").strip().lower()
    if decision_n not in {"approve", "reject", "correct"}:
        raise ValueError("decision must be approve|reject|correct")
    vendor = _norm_vendor(vendor)
    if not vendor or not vendor_product_ref:
        raise ValueError("vendor and vendor_product_ref required for teaching")

    mr = ((mapping_object or {}).get("mapping_result") or {}) if mapping_object else {}
    source_iri = source_iri or mr.get("vendor_product_iri") or ""
    target_iri = target_iri or mr.get("proposed_target_iri") or ""
    if decision_n in {"approve", "correct"} and not target_iri:
        raise ValueError(f"{decision_n} teaching requires target_iri")

    lesson_text = (lesson or "").strip()
    if not lesson_text:
        if decision_n == "reject":
            lesson_text = f"Reject mapping {source_iri or vendor_product_ref} → {target_iri or '?'}"
        else:
            lesson_text = f"User {decision_n}d mapping {source_iri or vendor_product_ref} → {target_iri}"

    polarity = "avoid" if decision_n == "reject" else "prefer"
    if decision_n == "reject" and target_iri:
        rule_text = f"AVOID mapping to {target_iri}. {lesson_text}"
    elif target_iri:
        rule_text = f"PREFER {target_iri} for {vendor_product_ref}. {lesson_text}"
    else:
        rule_text = lesson_text

    # 1) Teaching episode — always (audit + consult_teaching_notes)
    _write_memory(
        _teaching_key(ticket),
        "teaching",
        {
            "ticket": ticket,
            "decision": decision_n,
            "vendor": vendor,
            "vendor_product_ref": vendor_product_ref,
            "source_iri": source_iri,
            "target_iri": target_iri,
            "lesson": lesson_text,
            "confidence": float(confidence),
            "recorded_at_ms": int(time.time() * 1000),
        },
    )

    # 2) Generalized lesson candidate — quarantined until protected promotion.
    record_rule_candidate(
        vendor=vendor,
        text=rule_text,
        polarity=polarity,
        ticket=ticket or f"{decision_n}-{vendor_product_ref}",
        source="user_teaching",
    )

    # 3) Precedent — only when user affirms/corrects a target
    precedent_written = False
    if decision_n in {"approve", "correct"}:
        record_verified_precedent(
            vendor=vendor,
            vendor_product_ref=vendor_product_ref,
            source_iri=source_iri or f"mds.{vendor}:taught",
            target_iri=target_iri,
            confidence=float(confidence),
            rationale=lesson_text,
            source_outcome_ref=ticket or "user_teaching",
        )
        precedent_written = True

    # 4) Trajectory evidence when a mapping_object is present — FAIL-LOUD
    trajectory_written = False
    if mapping_object and mapping_object.get("mapping_result"):
        raw = dict(mapping_object["mapping_result"])
        if not (raw.get("rationale") or "").strip():
            raw["rationale"] = lesson_text
        if not raw.get("band"):
            conf = float(raw.get("confidence") or confidence or 0.0)
            raw["band"] = Band.for_confidence(conf).value
        if decision_n in {"approve", "correct"} and target_iri:
            raw["proposed_target_iri"] = target_iri
        if not raw.get("vendor_product_iri"):
            raw["vendor_product_iri"] = source_iri or f"mds.{vendor}:taught"
        mr_obj = MappingResult.model_validate(raw)
        record_trajectory(
            vendor=vendor,
            vendor_product_ref=vendor_product_ref,
            mapping_result=mr_obj,
            mapping_object=mapping_object,
            bundle_ref=f"teach:{ticket}",
        )
        trajectory_written = True

    return {
        "learned": True,
        "decision": decision_n,
        "precedent_written": precedent_written,
        "rule_candidate_written": True,
        "rule_written": False,
        "trajectory_written": trajectory_written,
        "ticket": ticket,
        "lesson": lesson_text,
    }
