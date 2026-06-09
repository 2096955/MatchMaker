"""
M4 (new) — HITL decision write-back + rank signal (Section 10a).

Per the brief:

  - approve  -> POSITIVE precedent at the suggested node, confidence preserved.
  - override -> POSITIVE precedent at the human-chosen node, confidence 1.0.
  - reject   -> NEGATIVE precedent so the (vendor, product_id, node) triple is
                not re-surfaced as an auto-map and is flagged for attention.

  - approve and override also bump a lightweight (vendor_signature, node)
    rank signal used by find_similar_products on subsequent runs. Reject does
    NOT feed the signal.

Invariants this module obeys (Sections 3 + 16):

  I5  one-shot + escalate, not unbounded looping — apply_decision is HITL-only;
      no auto-promotion of weak-oracle guesses lives in this module.
  I6  invariants outside the model — the decision verb is a Python literal,
      not a prompt parameter; rank-signal rules are arithmetic.
  I8  deterministic identity — precedent keys flow through mds_iri via
      VendorProductRef.iri; same input -> same edge.

The HITL endpoint (POST /api/mapping/decision) is the only intended caller.
The MCP tool surface stays READ-ONLY (Sections 8, 16); this is NOT exposed
there by design.
"""
from __future__ import annotations

from typing import Optional

from .frames import check_scope
from .models import MappingResult, MappingStatus, VendorProductRef
from .store import get_store

_DECISIONS = {"approve", "override", "reject"}
_DECISION_TO_STATUS = {
    "approve": MappingStatus.APPROVED,
    "override": MappingStatus.OVERRIDDEN,
    "reject": MappingStatus.REJECTED,
}


def apply_decision(
    ref: VendorProductRef,
    *,
    decision: str,
    decided_by: str,
    node_iri: str,
    suggested_confidence: Optional[float] = None,
) -> MappingResult:
    """Record a HITL decision and return the resulting MappingResult.

    Args:
        ref: The vendor product the decision is about.
        decision: ``'approve' | 'override' | 'reject'`` (case-insensitive).
        decided_by: The human's identity (email/login). Recorded as provenance
            on the precedent edge — required, not optional, for audit.
        node_iri: The CDAO node the decision is about. For ``approve`` this is
            the previously-suggested node; for ``override`` the chosen
            alternative; for ``reject`` the node that was rejected.
        suggested_confidence: REQUIRED for ``approve`` — the matcher's
            similarity score, preserved on the precedent edge. Ignored for
            ``override`` (forced to 1.0) and ``reject`` (0.0).

    Raises:
        ValueError: invalid decision verb, missing decided_by/node_iri/
            suggested_confidence, unknown node, or out-of-scope vendor.

    Operational ordering:
        1. Input validation (cheap, fail fast).
        2. I3 scope gate — enforced HERE (not just at the HTTP route) so a
           direct Python caller cannot write an out-of-scope precedent (I6:
           invariants outside the model AND outside the caller).
        3. Atomic upsert_precedent (single Cypher statement on the FalkorDB
           backend; the store seam owns the SINGLE-POSITIVE-PRECEDENT
           invariant).

    The rank signal is DERIVED from precedent edges (see
    ``RetrievalStore.rank_signals_for``), so the precedent write IS the
    rank-signal update — there is no second store call to fail between, and
    a replay of the same decision is idempotent on every dimension (the
    edge is MERGEd; the derived count cannot drift).
    """
    d = (decision or "").strip().lower()
    if d not in _DECISIONS:
        raise ValueError(
            f"decision must be one of {sorted(_DECISIONS)!r}, got {decision!r}"
        )
    if not (decided_by or "").strip():
        raise ValueError("decided_by is required")
    if not (node_iri or "").strip():
        raise ValueError("node_iri is required")

    # Approve must carry the matcher's score (the precedent's confidence is
    # the replay-trace of the auto-mapping decision the human confirmed).
    if d == "approve" and suggested_confidence is None:
        raise ValueError(
            "suggested_confidence is required for decision='approve'"
        )

    # I3 scope gate, fail-closed — same guard as the matcher uses, so a
    # direct caller cannot route around the HTTP layer's _validate_vendor.
    scope = check_scope(ref)
    if not scope.allowed:
        raise ValueError(f"out of scope: {scope.reason}")

    store = get_store()

    # Reject the decision early if the node doesn't exist — the precedent
    # edge would otherwise reference an unresolvable IRI.
    node = store.get_taxonomy_node(node_iri.strip())
    if node is None:
        raise ValueError(f"unknown taxonomy node {node_iri!r}")

    if d == "override":
        confidence = 1.0
    elif d == "reject":
        confidence = 0.0
    else:
        # approve — preserve the matcher's similarity for replay traceability.
        try:
            confidence = float(suggested_confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"suggested_confidence must be numeric, got {suggested_confidence!r}"
            ) from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"suggested_confidence must be in [0.0, 1.0], got {confidence}"
            )

    # Atomic precedent write — the FalkorDB backend runs this as ONE Cypher
    # statement; the rank signal is derived from the very edge being written,
    # so there is no second store call to update. A retry / replay of the
    # exact same decision is a no-op on the graph (MERGE collapses; derived
    # count is unchanged).
    store.upsert_precedent(
        ref=ref, node=node,
        decision=d, decided_by=decided_by.strip(),
        confidence=confidence, provisional=False,
    )

    return MappingResult(
        vendor_product_iri=ref.iri,
        vendor=ref.vendor,
        product_id=ref.product_id,
        product_name=ref.name,
        mapped_node_iri=node.iri if d != "reject" else None,
        mapped_node_label=node.label if d != "reject" else None,
        confidence=confidence,
        status=_DECISION_TO_STATUS[d],
        rationale=f"HITL decision '{d}' by {decided_by} on node {node.iri!r}.",
    )
