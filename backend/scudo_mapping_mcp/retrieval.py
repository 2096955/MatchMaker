"""
Multi-path retrieval orchestrator — SDK-inspired, our code.

Why this exists
---------------
The FalkorDB GraphRAG-SDK ships a multi-path retrieval pattern (lexical +
dense + structural arms fused and reranked). We are NOT adopting the full
SDK — too much surface, too much policy. Instead this module replicates the
*shape* (fan-out → dense rescore → structural tilt → top-N) in a tiny
purpose-built orchestrator that talks to the same RetrievalStore seam the
rest of the matcher already uses.

For the demo this is TWO arms:

  Arm 1 — BM25 PRE-FILTER (cheap, lexical, recall-oriented).
      Hits the full taxonomy through ``RetrievalStore.bm25_scores``,
      keeps the top ``_BM25_PREFILTER_TOP_N`` candidates. BM25 is a
      NOMINATOR; its score is never used as similarity.

  Arm 2 — DENSE RESCORE (Opus, or any callable matching the
      ``dense_scorer`` signature). The survivors of the pre-filter +
      negative-precedent drop + candidate_filter get rescored. The
      Opus score becomes ``Candidate.similarity`` — i.e. the value the
      band gate (0.75 centre; PASS cut 0.80) sees.

Future arms (cypher / rel-expansion) are stubbed by comment, not by code,
so this file stays small and demoable.

Invariants
----------
I.  ``Candidate.similarity`` is the DENSE score. Never BM25. Never RRF.
    The band gate (0.75 centre; PASS cut 0.80) was calibrated against a
    [0,1] dense quantity; lifting
    it onto a fused rank score would silently break I4.
II. BM25 is PRE-FILTER ONLY. It nominates ``_BM25_PREFILTER_TOP_N``
    candidates, no more. The dense scorer never sees the full universe.
III.``min_similarity`` is enforced AFTER dense rescore, never against BM25.
IV. The rank-signal tilt (≤ +0.10, capped) applies to the SORT KEY only,
    never to ``Candidate.similarity``. This mirrors I5 in the FalkorDB
    store: structural signals never lift a sub-floor candidate over the
    floor unattended.
V.  ``dense_scorer`` is a per-call dependency injection kwarg, NEVER a
    module global. Thread-safety. Tests pass a deterministic stub; prod
    passes an Opus client.
VI. When ``dense_scorer`` is None we run in DEGRADED MODE: every survivor
    gets a constant 0.5 similarity. This keeps the orchestrator runnable
    in CI / Opus-out smoke gates without polluting the matcher with
    "is the scorer wired" branching.
"""

from __future__ import annotations

from typing import Callable, Optional

from .config import env_subsumption_expand, settings
from .loaders.subsumption import build_child_index, direct_subsumption_neighbors
from .models import Candidate, TaxonomyNode, VendorProductRef
from .opus_dense import DenseScoringUnavailableError
from .store.base import CandidateFilter, RetrievalStore
from .taxonomy_text import maybe_log_taxonomy_text_shadow, taxonomy_bm25_doc


# BM25 pre-filter top-N. Matches the seam's MAX_RESULTS_CEIL so the
# orchestrator can never nominate more candidates than the store would
# itself surface. Kept module-private so the smoke gate can assert it.
_BM25_PREFILTER_TOP_N = 25

# Degraded-mode constant similarity. Picked at 0.5 so it sits squarely
# below the 0.70 FAIL cut — a run with no dense scorer cannot accidentally
# auto-map anything. NB the property that matters is "below FAIL_CUT", not
# "below the 0.80 PASS cut": BORDERLINE scores (0.70-0.80) can still
# auto-map, so clearing only 0.80 would NOT make this safe.
_DEGRADED_SIMILARITY = 0.5


# Type alias for the per-call dense scorer DI kwarg. Takes the query text
# and a list of candidates (with raw labels), returns a NEW list of
# candidates with ``similarity`` set to the dense score. Order preserved.
DenseScorer = Callable[[str, list[Candidate]], list[Candidate]]


def multi_path_retrieve(
    ref: VendorProductRef,
    store: RetrievalStore,
    max_results: int = 10,
    min_similarity: float = 0.0,
    *,
    candidate_filter: Optional[CandidateFilter] = None,
    dense_scorer: Optional[DenseScorer] = None,
) -> list[Candidate]:
    """Multi-path retrieve: BM25 pre-filter → drops → dense rescore → sort.

    Pipeline (each step is small and named so the smoke gates can pin it):

      1. ``_universe`` — pull every TaxonomyNode from the store. The seam
         already enforces a hard ceiling on results; the universe scan is
         bounded by the size of the taxonomy.
      2. ``_bm25_prefilter`` — score every node via the seam's BM25 and
         keep the top ``_BM25_PREFILTER_TOP_N`` by lexical score. This is
         the only place BM25 is consulted.
      3. ``_drop_negative_precedents`` — strip out any node the human has
         previously rejected for this (vendor, product_id). The store owns
         the negative-precedent set; the orchestrator just asks for it.
      4. ``candidate_filter`` (when provided) — per-candidate validator
         from the matcher. Drops out-of-scope / wrong-class candidates.
      5. ``_dense_rescore`` — call the injected dense_scorer on the
         survivors. Each survivor gets ``Candidate.similarity`` = dense
         score in [0,1]. If no scorer is wired, every survivor falls back
         to ``_DEGRADED_SIMILARITY``.
      6. ``min_similarity`` filter — applied to dense scores only.
      7. ``_sort_with_rank_tilt`` — sort by (dense + capped rank boost).
         The boost is the structural tilt; it influences ORDER but
         ``Candidate.similarity`` stays raw (I).
      8. Return the top ``max_results`` (clamped by the seam's ceiling).
    """
    # Build the candidate universe from the store.
    universe = _universe(store)
    if not universe:
        return []

    # Compose the query text the same way the FalkorDB store does so the
    # BM25 pre-filter is consistent with the deterministic in-store path.
    query_text = f"{ref.name} {ref.description}".strip() or ref.product_id

    # Arm 1 — BM25 PRE-FILTER. Nominates a bounded set; no scoring decisions.
    nominated = _bm25_prefilter(query_text, universe, store)

    # Shadow rollout (P1): log text-on BM25 diff without changing nominations.
    maybe_log_taxonomy_text_shadow(
        query_text, universe, store, nominated, top_n=_BM25_PREFILTER_TOP_N
    )

    # Subsumption expansion (P1): widen nominated set with 1-hop ancestors/descendants.
    if settings.subsumption_expand or env_subsumption_expand():
        nominated = _expand_subsumption(nominated, universe)

    # Structural drop (a) — negative precedents (mirrors the seam's order).
    survivors = _drop_negative_precedents(ref, nominated, store)

    # Structural drop (b) — per-candidate matcher filter, when provided.
    if candidate_filter is not None:
        survivors = [c for c in survivors if candidate_filter(c)]

    if not survivors:
        return []

    # Arm 2 — DENSE rescore. Opus (or stub) is the AUTHORITATIVE similarity.
    survivors = _dense_rescore(query_text, survivors, dense_scorer)

    # Floor on dense similarity, not BM25 (III).
    survivors = [c for c in survivors if c.similarity >= min_similarity]
    if not survivors:
        return []

    # Sort by dense + capped rank-signal tilt (boost touches sort key only).
    ranked = _sort_with_rank_tilt(ref, survivors, store)

    # Clamp result count through the seam's own ceiling so an orchestrator
    # caller can never out-ask the store's bounded contract.
    limit = store.clamp_results(max_results)
    return ranked[:limit]


# ─────────────────────────────────────────────────────────────────────────
# Helpers — each is one named pipeline step. Keeping them module-level
# (rather than nested closures) lets the smoke gates monkeypatch any
# individual step in isolation if a future regression needs pinning.
# ─────────────────────────────────────────────────────────────────────────


def _universe(store: RetrievalStore) -> list[TaxonomyNode]:
    """Pull every TaxonomyNode from the store, deterministic order by IRI.

    The seam already exposes ``list_taxonomy_nodes`` for the M6 bundle
    export path; we reuse it so a future swap to a sharded store stays
    a single change point.
    """
    return list(store.list_taxonomy_nodes() or [])


def _bm25_prefilter(
    query_text: str,
    universe: list[TaxonomyNode],
    store: RetrievalStore,
) -> list[Candidate]:
    """BM25 over labels; return top ``_BM25_PREFILTER_TOP_N`` as Candidates.

    The candidates carry similarity=0.0 here ON PURPOSE — the dense rescore
    is what populates similarity. Returning real BM25 scores would invite
    a downstream caller to treat them as similarities (violating I).
    """
    docs = [(node.iri, taxonomy_bm25_doc(node)) for node in universe]
    bm25 = store.bm25_scores(query_text, docs)
    by_iri = {n.iri: n for n in universe}

    # Sort by BM25 desc, then iri asc for determinism on ties. We always
    # take a top-N slice — BM25 scores of zero are still allowed through
    # if the universe is small, so a tiny taxonomy still gets nominated.
    ranked_iris = sorted(
        by_iri.keys(),
        key=lambda iri: (-bm25.get(iri, 0.0), iri),
    )[:_BM25_PREFILTER_TOP_N]

    return [Candidate(node=by_iri[iri], similarity=0.0) for iri in ranked_iris]


def _expand_subsumption(
    nominated: list[Candidate],
    universe: list[TaxonomyNode],
) -> list[Candidate]:
    """Add 1-hop subsumption neighbours to the BM25 survivor set."""
    by_iri = {n.iri: n for n in universe}
    child_index = build_child_index(universe)
    seen = {c.node.iri for c in nominated}
    out = list(nominated)
    for c in nominated:
        for n_iri in direct_subsumption_neighbors(c.node, child_index):
            if n_iri in seen or n_iri not in by_iri:
                continue
            seen.add(n_iri)
            out.append(Candidate(node=by_iri[n_iri], similarity=0.0))
    return out


def _drop_negative_precedents(
    ref: VendorProductRef,
    candidates: list[Candidate],
    store: RetrievalStore,
) -> list[Candidate]:
    """Strip out any node the human has previously rejected.

    Mirrors the FalkorDB store's order: negative drop happens BEFORE the
    expensive dense rescore so rejected nodes never consume an Opus call.
    """
    rejected = set(store.get_negative_precedents(ref.vendor, ref.product_id))
    if not rejected:
        return candidates
    return [c for c in candidates if c.node.iri not in rejected]


def _dense_rescore(
    query_text: str,
    survivors: list[Candidate],
    dense_scorer: Optional[DenseScorer],
) -> list[Candidate]:
    """Apply the dense scorer (Opus) to survivors; fall back to constant.

    The dense scorer is per-call DI — never a module global — so each
    invocation can plug a different model / stub. When None, every
    survivor receives ``_DEGRADED_SIMILARITY`` (= 0.5, below the floor)
    so a misconfigured run cannot auto-map anything.

    FAIL-CLOSED ON SCORER ERROR (ARB B2). If the injected dense scorer
    raises — Bedrock outage, throttle, malformed response, anything —
    we DO NOT propagate the exception up through the store seam to the
    matcher. Instead every survivor receives ``_DEGRADED_SIMILARITY``
    (= 0.5, below the floor), and the matcher routes the case to
    NEEDS_REVIEW via the normal floor gate. This is the I5-style
    "never auto-promote on a degraded dense arm" discipline: a single
    Bedrock blip costs us a reviewer-queue entry, not a 500.
    The error is logged at WARNING for ops visibility.
    """
    if dense_scorer is None:
        return [
            Candidate(node=c.node, similarity=_DEGRADED_SIMILARITY) for c in survivors
        ]
    try:
        rescored = dense_scorer(query_text, survivors)
    except DenseScoringUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed swallows by design
        import logging

        logging.getLogger(__name__).warning(
            "dense_scorer raised %s: %s — falling back to degraded "
            "similarity (every survivor → review queue)",
            type(exc).__name__,
            exc,
        )
        return [
            Candidate(node=c.node, similarity=_DEGRADED_SIMILARITY) for c in survivors
        ]
    # Defensive: clamp into the Pydantic [0,1] range. A scorer that hands
    # back 1.1 by mistake would otherwise blow up Candidate validation.
    out: list[Candidate] = []
    for c in rescored:
        sim = max(0.0, min(1.0, float(c.similarity)))
        out.append(Candidate(node=c.node, similarity=round(sim, 4)))
    return out


def _sort_with_rank_tilt(
    ref: VendorProductRef,
    survivors: list[Candidate],
    store: RetrievalStore,
) -> list[Candidate]:
    """Sort by (dense_similarity + capped rank boost), return Candidates.

    The rank boost is the structural-pass tilt: a small additive nudge
    derived from confirmed precedents for the same vendor signature. The
    boost touches the SORT KEY only — ``Candidate.similarity`` stays the
    raw dense score (IV).
    """
    sig = store.vendor_signature(ref.vendor, ref.name, ref.product_id)
    boosts = store.rank_signals_for(sig)
    scored: list[tuple[Candidate, float]] = []
    for c in survivors:
        boost = store.compute_rank_boost(boosts, c.node.iri)
        sort_key = c.similarity + boost
        scored.append((c, sort_key))
    # Stable sort on (-sort_key, iri) so identical sort keys break ties
    # deterministically — same discipline as _bm25_prefilter.
    scored.sort(key=lambda pair: (-pair[1], pair[0].node.iri))
    return [c for c, _ in scored]
