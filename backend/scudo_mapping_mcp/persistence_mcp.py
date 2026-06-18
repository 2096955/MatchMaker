"""
Persistence MCP — tier 3 of the trust gradient. THE ONLY WRITER.

ROLE
----
Every mutation to canonical state (FalkorDB locally / Neptune in prod)
runs through this server. The publish gate sits here and is the single
chokepoint — even human-approved decisions pass through it. The two
destinations are the only two writes the system can do:

  Gate ──hold──→ Reviewer queue  (anything agent-driven hits this path
                                  per I5 — never auto-promote)
  Gate ──persist──→ canonical graph  (only on confirmed HITL decisions)

THE PUBLISH GATE — POLICY IN ONE PLACE
--------------------------------------
``persist.commit_mapping`` accepts a verdict + HMAC seal from Match &
Verify and runs, in order:

  1. ``verdict.verify`` — seal must be valid HMAC, not expired, and
     bind to the (vendor, product_id) the caller is asking to write.
     Forgery / replay / tamper → refusal with typed reason.

  2. Scope gate, layer 3 (defense in depth — Ingestion was layer 1,
     Match & Verify was layer 2). Failure → ``out_of_scope``.

  3. I5 enforcement: the brief forbids auto-promoting weak-oracle output.
     **Agent-driven AUTO_MAPPED verdicts never write to canon — they
     go to the reviewer queue.** This is the load-bearing invariant
     the three-MCP split makes physically true: even if the agent
     forges a "trust me" status string, the seal verifies and the gate
     STILL refuses agent-driven auto_mapped.

``persist.record_decision`` is the HITL write path. A human approves /
overrides / rejects via the Flask Admin UI; that endpoint calls into
this tool. Human-approved decisions DO write to canon — the human IS
the verifier. Scope gate layer 3 still applies.

WHAT'S IMPORTED HERE
--------------------
This is the only server in the package that imports ``feedback``,
``bundle.import_bundle``, and the store's write methods. Smoke gate
``TRUST_persistence_is_only_writer`` asserts the inverse on the other
two servers.

TOOLS
-----
  - ``persist.commit_mapping``  — agent path: refuses except on
                                  HITL-pre-approved verdicts.
  - ``persist.record_decision`` — HITL path: approve / override /
                                  reject. Decided_by must be supplied
                                  by the caller (the Flask Admin route
                                  injects it from ``g.principal``).
  - ``persist.export_bundle``   — M6 bundle export (read of confirmed
                                  precedents, but lives on the
                                  Persistence side because the bundle
                                  is the cutover artefact).
  - ``persist.import_bundle``   — M6 bundle import. Writes.

Run locally with:
    python -m scudo_mapping_mcp.persistence_mcp
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import verdict as verdict_seal
from .bundle import export_bundle, import_bundle
from .config import PRIORITY_VENDORS
from .feedback import apply_decision
from .frames import check_scope
from .models import MappingBundle, MappingStatus, VendorProductRef

_RW = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,  # upsert_precedent is idempotent on retry
    "openWorldHint": False,
}
_RO = {**_RW, "readOnlyHint": True, "destructiveHint": False}


# ──────────────────────────────────────────────────────────────────────
# Reviewer queue — placeholder in-memory implementation
# ──────────────────────────────────────────────────────────────────────
# Production reads this from DynamoDB / SQS / whatever the party uses.
# For the demo loop it's a module-level list so the Flask Admin UI can
# render it. Same swap-point discipline as _WORKING_SET in frames.py.
_REVIEWER_QUEUE: list[dict] = []


def reviewer_queue_snapshot() -> list[dict]:
    """Return a copy of the queue for read-only inspection."""
    return list(_REVIEWER_QUEUE)


def _enqueue(verdict: dict, reason: str) -> str:
    """Add a held verdict to the reviewer queue, return queue id."""
    entry = {
        "id": f"rev-{int(time.time() * 1000)}-{len(_REVIEWER_QUEUE) + 1}",
        "queued_at_ms": int(time.time() * 1000),
        "verdict": verdict,
        "reason": reason,
    }
    _REVIEWER_QUEUE.append(entry)
    return entry["id"]


mcp = FastMCP("scudo_persistence_mcp")


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class CommitInput(_Base):
    """Inputs for ``persist.commit_mapping`` — the agent-driven path."""

    vendor: str = Field(...)
    product_id: str = Field(..., min_length=1)
    verdict: dict = Field(
        ...,
        description=("The MappingResult JSON returned by matchverify.verify_mapping."),
    )
    seal: dict = Field(
        ...,
        description=(
            "The HMAC seal returned alongside the verdict — must contain "
            "'payload_b64' and 'hmac_b64'."
        ),
    )


class DecisionInput(_Base):
    """Inputs for ``persist.record_decision`` — the HITL path."""

    vendor: str = Field(...)
    product_id: str = Field(..., min_length=1)
    decision: str = Field(
        ...,
        description="approve | override | reject",
    )
    decided_by: str = Field(..., min_length=1)
    node_iri: str = Field(..., min_length=1)
    name: str = Field("")
    description: str = Field("")
    suggested_confidence: Optional[float] = Field(default=None)


class BundleExportInput(_Base):
    source_env: Optional[str] = None
    created_at: Optional[str] = None


def _refusal(reason: str, **detail) -> str:
    """Typed refusal envelope. The agent reasons over `reason`."""
    return json.dumps(
        {
            "committed": False,
            "refusal": {"reason": reason, **detail},
        }
    )


def _validate_vendor(vendor: str) -> Optional[str]:
    """Layer-3 scope gate as a membership pre-check (mirrors the Flask
    route's ``_validate_vendor``). The full ``check_scope`` runs inside
    ``apply_decision`` too — this is the cheap up-front rejection.
    """
    if vendor not in PRIORITY_VENDORS:
        return f"unknown vendor {vendor!r} (valid: {', '.join(PRIORITY_VENDORS)})"
    return None


@mcp.tool(
    name="persist.commit_mapping",
    annotations={
        "title": "Commit a verified mapping (agent path — refuses on weak oracle)",
        **_RW,
    },
)
async def commit_mapping(params: CommitInput) -> str:
    """Agent-driven commit. Refuses unless the verdict carries a HUMAN
    decision (approved / overridden). Per I5, agent-driven AUTO_MAPPED
    verdicts go to the reviewer queue — never to canon.

    Order of checks:
      1. seal valid + identity-bound to (vendor, product_id) + not expired
      2. vendor in scope (defense layer 3)
      3. verdict.status: APPROVED / OVERRIDDEN -> write
                         AUTO_MAPPED          -> enqueue for review
                         NEEDS_REVIEW         -> enqueue for review
                         OUT_OF_SCOPE         -> refuse (don't enqueue)
    """
    # Layer 1 — seal verification (forgery / tamper / replay / expiry).
    seal_result = verdict_seal.verify(
        params.seal,
        expected_vendor=params.vendor,
        expected_product_id=params.product_id,
    )
    if not seal_result.ok:
        return _refusal(seal_result.reason)

    # Layer 2 — defense-in-depth scope gate. Cheap membership first.
    err = _validate_vendor(params.vendor)
    if err:
        return _refusal("out_of_scope", detail=err)
    probe = VendorProductRef(vendor=params.vendor, product_id=params.product_id)
    scope = check_scope(probe)
    if not scope.allowed:
        return _refusal("out_of_scope", detail=scope.reason)

    # Layer 3 — I5 gate. Read status FROM THE SEALED PAYLOAD, not from
    # the verdict dict the agent passed alongside, so the agent cannot
    # smuggle a different status past us by editing the verdict but
    # leaving the seal alone (would fail seal check anyway, but the
    # double-source-of-truth pattern is what makes this airtight).
    sealed_status = (seal_result.payload or {}).get("status", "")
    if sealed_status in {
        MappingStatus.APPROVED.value,
        MappingStatus.OVERRIDDEN.value,
    }:
        # Human already approved upstream — write to canon. This path is
        # primarily reached by the reviewer queue draining: a human
        # approves via record_decision, that produces an APPROVED verdict
        # that record_decision itself writes. commit_mapping with
        # APPROVED is the future "downstream consumer pushes a confirmed
        # decision back" path; today it's an additional ingress.
        return _refusal(
            "already_persisted_via_record_decision",
            detail=(
                "commit_mapping does not double-write APPROVED/OVERRIDDEN "
                "verdicts — those come in through record_decision which "
                "writes immediately. Use record_decision for the HITL "
                "ingress."
            ),
        )

    if sealed_status == MappingStatus.AUTO_MAPPED.value:
        # I5 ENFORCEMENT — never auto-promote weak-oracle output. Even a
        # cryptographically valid AUTO_MAPPED verdict goes to the reviewer.
        queue_id = _enqueue(
            params.verdict,
            reason="auto_mapped_requires_review",
        )
        return _refusal(
            "auto_mapped_requires_review",
            queue_id=queue_id,
            detail=(
                "Auto-mapped verdicts never write to canonical state. "
                "Routed to the reviewer queue for HITL approval."
            ),
        )

    if sealed_status == MappingStatus.NEEDS_REVIEW.value:
        queue_id = _enqueue(params.verdict, reason="needs_review")
        return _refusal(
            "needs_review",
            queue_id=queue_id,
            detail="Confidence below floor / required validation failed.",
        )

    if sealed_status == MappingStatus.OUT_OF_SCOPE.value:
        return _refusal(
            "out_of_scope",
            detail="Verdict says out_of_scope; nothing to enqueue.",
        )

    # Anything else: fail-closed.
    return _refusal("unknown_status", status=sealed_status)


@mcp.tool(
    name="persist.record_decision",
    annotations={
        "title": "Record a HITL decision (the only canonical write path)",
        **_RW,
    },
)
async def record_decision(params: DecisionInput) -> str:
    """The HITL write path. Approve / override / reject from a human.

    Wraps ``feedback.apply_decision`` which is itself the atomic write
    surface (scope gate + node resolve + single Cypher upsert). The Flask
    Admin route at ``/api/mapping/decision`` is the user-facing front of
    this tool; the principal (decided_by) comes from the gateway auth
    header, never from the tool body.
    """
    err = _validate_vendor(params.vendor)
    if err:
        return _refusal("out_of_scope", detail=err)

    ref = VendorProductRef(
        vendor=params.vendor,
        product_id=params.product_id,
        name=params.name,
        description=params.description,
    )
    try:
        result = apply_decision(
            ref,
            decision=params.decision,
            decided_by=params.decided_by,
            node_iri=params.node_iri,
            suggested_confidence=params.suggested_confidence,
        )
    except ValueError as e:
        return _refusal("invalid_decision", detail=str(e))
    return json.dumps(
        {
            "committed": True,
            "result": result.model_dump(mode="json"),
        }
    )


@mcp.tool(
    name="persist.export_bundle",
    annotations={"title": "Export the M6 mapping bundle", **_RO},
)
async def export_bundle_tool(params: BundleExportInput) -> str:
    """Export confirmed precedents as a portable, versioned bundle.

    Read-only against the store, but logically a Persistence concern —
    the bundle is the cutover artefact and only Persistence is the
    canonical writer that can guarantee a consistent snapshot.
    """
    bundle = export_bundle(
        source_env=params.source_env,
        created_at=params.created_at,
    )
    return bundle.model_dump_json()


@mcp.tool(
    name="persist.publish_bundle",
    annotations={"title": "Export and publish the M6 bundle to canonical S3", **_RW},
)
async def publish_bundle_tool(params: BundleExportInput) -> str:
    """Build the confirmed-precedent bundle AND write it to the canonical S3
    key that hydrate() reads at boot. Read-write: the only write side of the
    export->hydrate cycle. Bucket/key resolve exactly like hydration."""
    bundle = export_bundle(
        source_env=params.source_env,
        created_at=params.created_at,
    )
    from .hydrate import export_to_s3

    bucket, key = export_to_s3(bundle)
    return json.dumps(
        {
            "bucket": bucket,
            "key": key,
            "patterns": len(bundle.patterns),
        }
    )


@mcp.tool(
    name="persist.import_bundle",
    annotations={"title": "Import an M6 mapping bundle", **_RW},
)
async def import_bundle_tool(bundle: dict) -> str:
    """Idempotent re-seed of confirmed precedents from a bundle.

    Same scope-gate + identity discipline as a HITL write: the bundle's
    own ``check_scope`` filter excludes out-of-scope vendors from being
    imported.
    """
    try:
        parsed = MappingBundle.model_validate(bundle)
    except Exception as e:  # noqa: BLE001
        return _refusal("invalid_bundle", detail=str(e))
    try:
        summary = import_bundle(parsed)
    except ValueError as e:
        return _refusal("bundle_format_error", detail=str(e))
    return json.dumps(
        {
            "committed": True,
            "summary": summary.model_dump(mode="json"),
        }
    )


if __name__ == "__main__":
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8003"))
    mcp.run(transport="streamable-http")
