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
from .skill_gate import should_promote

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


def consult_best_skill() -> Optional[dict]:
    """Read the current validated matching skill doc, if one has ever been
    promoted. Fails OPEN (Aurora unreachable/misconfigured/malformed -> None,
    never raises) — same advisory contract as consult_priors. A missing skill
    doc is a normal, expected state (nothing promoted yet), not an error.
    """
    try:
        result = aurora_store._execute(
            "select memory_key, memory_type, payload from scudo.agent_memory "
            "where memory_key = :memory_key",
            [aurora_store._str_param("memory_key", _BEST_SKILL_KEY)],
        )
    except Exception as e:  # noqa: BLE001 — advisory, never blocks a mapping run
        log.warning(
            "consult_best_skill failed, proceeding with no skill doc: %s: %s",
            type(e).__name__,
            e,
        )
        return None

    for rec in result.get("records", []):
        raw_payload = (rec[2] or {}).get("stringValue") if len(rec) > 2 else None
        try:
            return json.loads(raw_payload) if raw_payload else None
        except (ValueError, TypeError):
            return None
    return None


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


def promote_skill(*, skill_text: str, validation_score: float, version: int) -> bool:
    """The whole gated-promotion operation in one call: read the current
    best (fail-open — an unreadable current best is treated as "none," never
    blocking a legitimate promotion), decide via should_promote(), and only
    if it returns True, perform the upsert to skill:matching:best. FAIL LOUD
    on the write itself (unguarded, same contract as record_verified_precedent
    /record_trajectory) — a lost promotion silently strands live agents on a
    stale skill. Returns whether promotion actually happened.
    """
    current = consult_best_skill()
    current_score = current["validation_score"] if current else None
    if not should_promote(validation_score, current_score):
        return False

    payload: dict[str, Any] = {
        "skill_text": skill_text,
        "version": version,
        "validation_score": validation_score,
        "promoted_at": time.time(),
    }
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
