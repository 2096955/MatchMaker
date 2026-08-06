"""
Calibration — replay a golden set through retrieval and report the evidence.

WHY THIS MODULE EXISTS
----------------------
The 0.80 confidence floor and the ±0.05 borderline band have never been
validated against a golden set (README, "What is NOT done"), and the dense
arm is a string-metric stand-in whose score scale will CHANGE when real
embeddings land. Swapping the retriever without recalibrating fails in one
of two silent ways: everything clears the floor (over-automation pressure
on the reviewer queue) or nothing does (100% human review). The golden
dataset is the calibration instrument; this module is the harness.

WHAT IT DOES — AND DELIBERATELY DOES NOT
----------------------------------------
For each golden pair ``(vendor product -> expected CDAO node)`` it runs
``store.find_similar_products`` ONLY — retrieval, not the full matcher:

  - NO precedent reuse (rung 2 would short-circuit a golden pair that is
    already a confirmed precedent, telling you nothing about retrieval).
  - NO specialist (rung 4 is the thing the calibration protects; it must
    not contaminate the measurement).
  - NO writes of any kind. This module imports no write surface.

Reported per run: precision@1, recall@N (does the true node make the
specialist's anchor window?), MRR, the band each top hit would land in at
CURRENT settings, and a threshold sweep (auto-map rate + auto-map precision
per candidate floor) so a floor can be chosen from evidence.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .config import settings
from .frames import check_scope
from .models import VendorProductRef
from .store import get_store
from .store.base import RetrievalStore


class GoldenPair(BaseModel):
    """One golden mapping: a vendor product and the CDAO node a human
    (or the existing Scudo schema) says it belongs to."""
    vendor: str
    product_id: str = Field(..., min_length=1)
    name: str = ""
    description: str = ""
    expected_node_iri: str = Field(..., min_length=1)


class PairOutcome(BaseModel):
    vendor: str
    product_id: str
    expected_node_iri: str
    skipped: bool = False
    skip_reason: str = ""
    # 1-based rank of the expected node in the retrieved list; None when it
    # did not surface at all (a recall failure — the specialist could never
    # have picked it, because the specialist is anchored to this window).
    rank: Optional[int] = None
    top_iri: Optional[str] = None
    top_similarity: Optional[float] = None
    expected_similarity: Optional[float] = None
    # Band the TOP candidate would land in at current settings — what the
    # gate would have done with this case today.
    band: str = "n/a"


class ThresholdPoint(BaseModel):
    """One row of the floor sweep. ``auto_precision`` is the fraction of
    auto-mapped cases whose top pick was the golden node — the number that
    actually justifies a floor."""
    floor: float
    auto_mapped: int = 0
    auto_correct: int = 0
    auto_precision: Optional[float] = None
    routed_to_review: int = 0


class CalibrationReport(BaseModel):
    total: int
    scored: int
    skipped: int
    max_candidates: int
    current_floor: float
    current_half_width: float
    precision_at_1: Optional[float] = None
    recall_at_n: Optional[float] = None
    mrr: Optional[float] = None
    band_distribution: dict[str, int] = Field(default_factory=dict)
    sweep: list[ThresholdPoint] = Field(default_factory=list)
    outcomes: list[PairOutcome] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


_SWEEP_FLOORS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))  # 0.50..0.95


def _band_for(similarity: float, floor: float, half: float) -> str:
    if similarity >= floor + half:
        return "pass"
    if similarity >= floor - half:
        return "borderline"
    return "fail"


def replay_golden(
    pairs: list[GoldenPair],
    *,
    max_candidates: int = 8,
    store: Optional[RetrievalStore] = None,
    include_outcomes: bool = True,
) -> CalibrationReport:
    """Replay golden pairs through retrieval and report calibration metrics.

    ``store`` is injectable for tests; defaults to the configured backend.
    Out-of-scope vendors are SKIPPED with a reason (the scope gate is not
    suspended for calibration — golden data for an unentitled vendor is a
    data problem to surface, not to score around).
    """
    s = store or get_store()
    floor = settings.confidence_floor
    half = settings.borderline_half_width

    # DECLARE THE WHOLE WORKLOAD, don't dribble it out one pair at a time.
    #
    # This harness is the one caller that knows its ENTIRE workload before
    # it starts — every golden pair is in hand at entry. Scoring them with
    # a per-pair loop threw that away: each call rebuilt the BM25 corpus
    # over the full taxonomy, so a 500-pair replay did ~499 redundant
    # corpus builds. Handing the batch to the store lets it hoist that work
    # once (see RetrievalStore.find_similar_products_batch).
    #
    # Scope is filtered FIRST so out-of-scope pairs never reach retrieval —
    # they are a data problem to surface, not work to schedule. Positions
    # in ``scored_pairs`` line up with ``batch_results`` by construction.
    outcomes: list[PairOutcome] = []
    scored_pairs: list[GoldenPair] = []
    refs: list[VendorProductRef] = []
    for pair in pairs:
        scope = check_scope(VendorProductRef(
            vendor=pair.vendor, product_id=pair.product_id,
        ))
        if not scope.allowed:
            outcomes.append(PairOutcome(
                vendor=pair.vendor, product_id=pair.product_id,
                expected_node_iri=pair.expected_node_iri,
                skipped=True, skip_reason=scope.reason,
            ))
            continue
        scored_pairs.append(pair)
        refs.append(VendorProductRef(
            vendor=pair.vendor, product_id=pair.product_id,
            name=pair.name, description=pair.description,
        ))

    batch_results = s.find_similar_products_batch(
        refs, max_results=max_candidates,
    ) if refs else []

    for pair, candidates in zip(scored_pairs, batch_results):
        rank: Optional[int] = None
        expected_sim: Optional[float] = None
        for i, c in enumerate(candidates, start=1):
            if c.node.iri == pair.expected_node_iri:
                rank = i
                expected_sim = c.similarity
                break
        top = candidates[0] if candidates else None
        outcomes.append(PairOutcome(
            vendor=pair.vendor, product_id=pair.product_id,
            expected_node_iri=pair.expected_node_iri,
            rank=rank,
            top_iri=top.node.iri if top else None,
            top_similarity=top.similarity if top else None,
            expected_similarity=expected_sim,
            band=_band_for(top.similarity, floor, half) if top else "n/a",
        ))

    scored = [o for o in outcomes if not o.skipped]
    n_scored = len(scored)

    report = CalibrationReport(
        total=len(pairs),
        scored=n_scored,
        skipped=len(outcomes) - n_scored,
        max_candidates=max_candidates,
        current_floor=floor,
        current_half_width=half,
        outcomes=outcomes if include_outcomes else [],
    )

    if n_scored:
        hits_at_1 = sum(1 for o in scored if o.rank == 1)
        hits_at_n = sum(1 for o in scored if o.rank is not None)
        report.precision_at_1 = hits_at_1 / n_scored
        report.recall_at_n = hits_at_n / n_scored
        report.mrr = sum(
            (1.0 / o.rank) for o in scored if o.rank
        ) / n_scored
        for o in scored:
            report.band_distribution[o.band] = (
                report.band_distribution.get(o.band, 0) + 1
            )
        for f in _SWEEP_FLOORS:
            point = ThresholdPoint(floor=f)
            for o in scored:
                if o.top_similarity is not None and o.top_similarity >= f:
                    point.auto_mapped += 1
                    if o.rank == 1:
                        point.auto_correct += 1
                else:
                    point.routed_to_review += 1
            if point.auto_mapped:
                point.auto_precision = point.auto_correct / point.auto_mapped
            report.sweep.append(point)

    report.notes = [
        "Retrieval-only replay: precedent reuse and the specialist are "
        "deliberately excluded so the measurement is of the retriever.",
        "recall_at_n is recall into the specialist's anchor window — the "
        "specialist fails closed on off-list picks, so a pair the window "
        "misses can never be auto-resolved at the borderline rung.",
        "Re-run this whole sweep whenever the dense arm changes (embedding "
        "model swap or re-embedding): similarity scales are model-specific "
        "and the current floor was set against the Jaro-Winkler stand-in.",
    ]
    return report
