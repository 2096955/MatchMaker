"""
The matcher — a strict COST LADDER.

The ladder is FIRST-MATCH-WINS, ordered from cheapest to most expensive.
Each rung that can settle the case ends the pipeline; the LLM only runs on
the narrow window of cases it can plausibly help with.

  Rung 1 — Scope gate          (deterministic; no retrieval, no model)
  Rung 2 — Precedent reuse     (graph lookup; a confirmed prior decision wins)
  Rung 3 — Falkor match+check  (dense + lexical fusion + structural tilt)
  Rung 4 — Opus LLM specialist (ONLY for borderline candidates — see Gate)
  Rung 5 — Gate                (three bands: pass | borderline | fail)

THREE VERDICT BANDS at the Gate (Rung 5):

  PASS       — similarity >= floor + 0.05  -> AUTO_MAPPED, no specialist
  BORDERLINE — within ±0.05 of the floor   -> consult specialist; if the
                                              specialist concurs and stays
                                              above floor: AUTO_MAPPED.
                                              If the specialist DISAGREES
                                              (picks a different node), the
                                              confidence is CAPPED below
                                              floor and the case lands in
                                              NEEDS_REVIEW — the disagreement
                                              between the sparse ranker and
                                              the LLM is itself a signal.
  FAIL       — similarity <  floor - 0.05  -> NEEDS_REVIEW; specialist is
              (or required validation         NOT consulted (ladder
              fails)                          discipline: the LLM only runs
                                              on cases it can plausibly
                                              resolve, not on rubbish).

M9 SCORER CONTRACT (pinned now to keep determinism through the cutover):

  The Bedrock/Strands specialist slots in HERE (Rung 4) and ONLY here. The
  scorer's responsibility is to SCORE candidates — return a similarity for
  the candidate it picks. The scorer MUST NOT:

    - Return a MappingStatus or otherwise pre-decide the outcome.
    - Bypass ``check_scope`` (I3 — scope is deterministic; the LLM never
      gets a vote on entitlement).
    - Bypass the floor (I4 — floor stays in this file).
    - Bypass ``run_validations`` / ``required_failures`` (I6 — invariants
      stay outside the model).
    - Mutate the store (I5 — write side stays HITL-only via apply_decision).

  In short: scorer -> scores; this file -> status. If the scorer ever
  returns a status, you've recreated the auto-promotion path I5 forbids.

  The specialist seam is a callable
  ``SpecialistScorer = (ref, candidates) -> Optional[Candidate]`` passed
  PER CALL via the ``specialist`` kwarg on ``map_vendor_product``. There
  is intentionally NO module-level mutable global — under concurrent
  requests in the M&V MCP that would race. Default is no-op (None) which
  keeps the matcher fully deterministic.

  CONFIDENCE DISCIPLINE: when the specialist concurs with the sparse
  ranker on the same node, the matcher uses
  ``min(best.similarity, specialist_pick.similarity)`` as the authoritative
  confidence — the LLM can REINFORCE but cannot INFLATE past the
  deterministic anchor. A hallucinating specialist returning 0.99 cannot
  push a 0.78 dense score above the floor.

The store surface, the scope gate, the validations and the result contract
do not change — only the scorer does (Section 11 / M9).
"""
from __future__ import annotations

from typing import Callable, Optional

from .config import settings
from .frames import check_scope
from .models import (
    Candidate,
    MappingResult,
    MappingStatus,
    TaxonomyNode,
    Validation,
    VendorProductRef,
)
from .store import get_store
from .validations import (
    default_field_rules,
    required_failures,
    run_validations,
)

# Specialist seam type (Rung 4 of the cost ladder).
#
# Passed PER CALL to ``map_vendor_product`` — no module global. Default
# behaviour when omitted is "no specialist" (the borderline branch falls
# back to a deterministic floor check).
SpecialistScorer = Callable[
    [VendorProductRef, list[Candidate]],
    Optional[Candidate],
]


def map_vendor_product(
    ref: VendorProductRef,
    max_candidates: int = 8,
    *,
    specialist: Optional[SpecialistScorer] = None,
) -> MappingResult:
    rules = default_field_rules()

    # M8 federated-audit fields — copied from the ref onto EVERY return path
    # so the MappingResult carries the source identity the matcher actually
    # saw (sha256 of the frame body + S3 audit-id). Decided once here so the
    # five return paths below stay readable.
    source_content_hash = ref.source_content_hash
    source_file_audit_id = ref.source_file_audit_id

    # Rung 1 — Scope gate (deterministic, fail-closed).
    scope = check_scope(ref)
    if not scope.allowed:
        return MappingResult(
            vendor_product_iri=ref.iri, vendor=ref.vendor, product_id=ref.product_id,
            product_name=ref.name, status=MappingStatus.OUT_OF_SCOPE,
            rationale=scope.reason,
            band="n/a",
            field_normalisation=rules,
            validations=run_validations(
                ref, None, scope_allowed=False, has_store_node=False,
            ),
            source_content_hash=source_content_hash,
            source_file_audit_id=source_file_audit_id,
        )

    store = get_store()

    # Rung 2 — Precedent reuse. A CONFIRMED prior mapping wins immediately
    # (replay-safe; the store filters out provisional edges per Section
    # 10a / I5). Precedent reuse is OUTSIDE the band model — the case was
    # already settled by a human, so band="n/a".
    precedent = store.get_precedent_mapping(ref.vendor, ref.product_id)
    if precedent is not None:
        # Augment with a self-describing artifact if the precedent predates M5.
        # Crucially, do NOT re-run the deterministic validations against the
        # precedent's candidate: validations gate AUTO-MAPPING, not
        # human-confirmed precedents (Section 10c). Running them here would
        # let a now-unresolvable identifier emit identifier_resolves=fail
        # against an APPROVED status — an internally contradictory artifact.
        if not precedent.field_normalisation:
            precedent.field_normalisation = rules
        if not precedent.validations:
            precedent.validations = [
                Validation(
                    name="precedent_human_confirmed",
                    status="pass",
                    required=False,
                    detail=(
                        "Status comes from a prior HITL decision; deterministic "
                        "validations are not re-run on precedent reuse."
                    ),
                ),
            ]
        # PREFER persisted DECISION-TIME provenance over the current call's
        # ref. The store now writes source_content_hash / source_file_audit_id
        # onto the precedent edge (see upsert_precedent), and
        # get_precedent_mapping reads them back — so a HITL approval made
        # against frame X is auditable as "approved against X" even if the
        # frame has been re-landed as X' since. Fall back to the current
        # call's ref only when the persisted value is missing (old edges
        # written before this seam extension, or HITL approvals made via
        # the mock/inline path with no upstream provenance).
        if precedent.source_content_hash is None:
            precedent.source_content_hash = source_content_hash
        if precedent.source_file_audit_id is None:
            precedent.source_file_audit_id = source_file_audit_id
        return precedent

    # Rung 3 — Falkor match+check. The store's find_similar_products
    # internally fuses the dense arm (Jaro-Winkler stand-in for embeddings)
    # with a BM25 lexical sidecar via RRF, then applies the structural
    # rank-signal tilt to the sort key (NOT the similarity).
    candidates = store.find_similar_products(ref, max_results=max_candidates)

    # 3a. Subtract nodes the human has already rejected for this exact pair
    #     (Section 10a — negative precedent). Stops the same dead-end from
    #     re-appearing run after run.
    rejected = set(store.get_negative_precedents(ref.vendor, ref.product_id))
    if rejected:
        candidates = [c for c in candidates if c.node.iri not in rejected]

    if not candidates:
        return MappingResult(
            vendor_product_iri=ref.iri, vendor=ref.vendor, product_id=ref.product_id,
            product_name=ref.name, status=MappingStatus.NEEDS_REVIEW,
            candidates=[], confidence=0.0,
            band="n/a",
            rationale=(
                "No candidate above the similarity threshold (after rejected-node "
                "filter); needs a human."
                if rejected else
                "No candidate above the similarity threshold; needs a human."
            ),
            field_normalisation=rules,
            validations=run_validations(
                ref, None, scope_allowed=True, has_store_node=False,
            ),
            source_content_hash=source_content_hash,
            source_file_audit_id=source_file_audit_id,
        )

    best = candidates[0]

    # 4. Validations on the best candidate. A failing REQUIRED validation
    #    forces needs_review even at high similarity (Section 10c). Required
    #    failure is by definition outside the band model — the ladder treats
    #    it as a hard FAIL: NO specialist consultation, straight to review.
    store_node = store.get_taxonomy_node(best.node.iri)
    vresults = run_validations(
        ref, best.node,
        scope_allowed=True,
        has_store_node=store_node is not None,
    )
    req_fails = required_failures(vresults)

    floor = settings.confidence_floor
    half = settings.borderline_half_width
    pass_threshold = floor + half
    borderline_threshold = floor - half

    # Disagreement bookkeeping — populated only when the borderline
    # specialist picks a DIFFERENT node from the sparse ranker. The
    # primary mapped_node_iri stays anchored to candidates[0] in that
    # case so a reviewer can always trace the surfaced pick back to a
    # deterministically-retrieved candidate.
    alternative_iri: Optional[str] = None
    alternative_label: Optional[str] = None

    # Rung 5 — Gate. Three-band decision.
    if req_fails:
        # Hard FAIL: required-validation failure. The specialist is NOT
        # consulted (an LLM can't override a deterministic invariant — that
        # would re-open the I5/I6 hole the ladder exists to prevent).
        band: str = "fail"
        status = MappingStatus.NEEDS_REVIEW
        confidence = best.similarity
        mapped_node_iri: Optional[str] = best.node.iri
        mapped_node_label: Optional[str] = best.node.label
        names = ", ".join(v.name for v in req_fails)
        rationale = (
            f"Required validation(s) failed: {names}. "
            f"Best candidate '{best.node.label}' at {best.similarity:.2f}. "
            "Specialist not consulted (deterministic invariant)."
        )
    elif best.similarity >= pass_threshold:
        # PASS band: clearly above floor. Auto-map without specialist —
        # paying the LLM here would be pure cost with no upside.
        band = "pass"
        status = MappingStatus.AUTO_MAPPED
        confidence = best.similarity
        mapped_node_iri = best.node.iri
        mapped_node_label = best.node.label
        rationale = (
            f"PASS band — best candidate '{best.node.label}' at "
            f"{best.similarity:.2f} >= pass threshold {pass_threshold:.2f}; "
            "all required validations passed."
        )
    elif best.similarity >= borderline_threshold:
        # BORDERLINE band: consult the specialist (Rung 4). Three sub-cases.
        specialist_pick = specialist(ref, candidates) if specialist else None

        if specialist_pick is None:
            # Specialist absent / abstained. Fall back to the deterministic
            # floor on the sparse ranker's pick.
            band = "borderline"
            mapped_node_iri = best.node.iri
            mapped_node_label = best.node.label
            confidence = best.similarity
            if best.similarity >= floor:
                status = MappingStatus.AUTO_MAPPED
                rationale = (
                    f"BORDERLINE band — best candidate '{best.node.label}' "
                    f"at {best.similarity:.2f} ≥ floor {floor:.2f}; "
                    "specialist abstained."
                )
            else:
                status = MappingStatus.NEEDS_REVIEW
                rationale = (
                    f"BORDERLINE band — best candidate '{best.node.label}' "
                    f"at {best.similarity:.2f} < floor {floor:.2f}; "
                    "specialist abstained."
                )
        elif specialist_pick.node.iri == best.node.iri:
            # Specialist CONCURS with the sparse ranker. Cap confidence at
            # min(best, specialist) so a hallucinating LLM CANNOT inflate
            # past the deterministic anchor (I5). The specialist can
            # REINFORCE — not override — the dense score.
            band = "borderline"
            mapped_node_iri = specialist_pick.node.iri
            mapped_node_label = specialist_pick.node.label
            confidence = min(best.similarity, specialist_pick.similarity)
            if confidence >= floor:
                status = MappingStatus.AUTO_MAPPED
                rationale = (
                    f"BORDERLINE band — specialist CONCURS with sparse "
                    f"ranker on '{specialist_pick.node.label}'; capped "
                    f"confidence min(dense={best.similarity:.2f}, "
                    f"specialist={specialist_pick.similarity:.2f}) = "
                    f"{confidence:.2f} >= floor {floor:.2f}."
                )
            else:
                status = MappingStatus.NEEDS_REVIEW
                rationale = (
                    f"BORDERLINE band — specialist concurs on "
                    f"'{specialist_pick.node.label}' but capped confidence "
                    f"{confidence:.2f} < floor {floor:.2f}."
                )
        else:
            # Specialist DISAGREES — picked a different node from the
            # sparse ranker. The disagreement IS the signal: cap
            # confidence clearly into the fail band and route to review.
            # We surface the SPARSE RANKER's pick as the primary (so it
            # matches candidates[0] — a reviewer can always trace it) and
            # carry the specialist's alternative on the result for the
            # reviewer to compare. No information loss.
            band = "borderline"
            status = MappingStatus.NEEDS_REVIEW
            mapped_node_iri = best.node.iri
            mapped_node_label = best.node.label
            alternative_iri = specialist_pick.node.iri
            alternative_label = specialist_pick.node.label
            # Cap into the fail band so even if the borderline window is
            # later widened, this case stays below the new floor.
            confidence = min(best.similarity, borderline_threshold - 0.01)
            rationale = (
                f"BORDERLINE band — DISAGREEMENT: sparse ranker chose "
                f"'{best.node.label}' at {best.similarity:.2f}; specialist "
                f"chose '{specialist_pick.node.label}' at "
                f"{specialist_pick.similarity:.2f}. Confidence capped to "
                f"{confidence:.2f} pending human review."
            )
    else:
        # FAIL band: clearly below floor. Specialist is NOT consulted —
        # the ladder's discipline is that the LLM only runs on cases it
        # can plausibly resolve, not on long-tail rubbish.
        band = "fail"
        status = MappingStatus.NEEDS_REVIEW
        confidence = best.similarity
        mapped_node_iri = best.node.iri
        mapped_node_label = best.node.label
        rationale = (
            f"FAIL band — best candidate '{best.node.label}' at "
            f"{best.similarity:.2f} < borderline threshold "
            f"{borderline_threshold:.2f}. Specialist not consulted "
            "(below the resolvable window)."
        )

    return MappingResult(
        vendor_product_iri=ref.iri, vendor=ref.vendor, product_id=ref.product_id,
        product_name=ref.name,
        mapped_node_iri=mapped_node_iri, mapped_node_label=mapped_node_label,
        confidence=confidence, status=status,
        band=band,
        alternative_mapped_node_iri=alternative_iri,
        alternative_mapped_node_label=alternative_label,
        candidates=candidates, rationale=rationale,
        field_normalisation=rules,
        validations=vresults,
        source_content_hash=source_content_hash,
        source_file_audit_id=source_file_audit_id,
    )
