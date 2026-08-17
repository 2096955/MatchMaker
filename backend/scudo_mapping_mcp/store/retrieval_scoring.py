"""Shared candidate scoring composition for local retrieval stores."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from collections.abc import Callable, Mapping, Sequence

from ..config import env_dense_backend
from ..models import Candidate, TaxonomyNode, VendorProductRef
from ..opus_dense import (
    begin_dense_batch,
    dense_batch_refusal_error,
    opus_dense_score,
    opus_dense_score_strict,
    record_dense_batch_failure,
    record_dense_batch_success,
)
from ..taxonomy_text import (
    taxonomy_bm25_doc,
    taxonomy_candidate_desc,
    taxonomy_dense_text,
)
from .base import CandidateFilter, RetrievalStore
from .falkordb_store import _jaro_winkler

ShadowHook = Callable[[str, dict[str, float], int], None]
CandidateDescription = Callable[[TaxonomyNode], str]
DenseScorer = Callable[..., float]
_MAX_OPUS_NOMINEES = 25
log = logging.getLogger(__name__)


def _dense_fallback_enabled() -> bool:
    """Mirrors opus_dense._fallback_enabled; read per call, never cached."""
    return (os.getenv("SCUDO_DENSE_FALLBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def score_candidates(
    *,
    store: RetrievalStore,
    ref: VendorProductRef,
    nodes: Sequence[TaxonomyNode],
    rejected_iris: set[str],
    boosts: Mapping[str, int],
    max_results: int,
    min_similarity: float,
    candidate_filter: CandidateFilter | None,
    shadow_hook: ShadowHook | None = None,
    candidate_description: CandidateDescription = taxonomy_candidate_desc,
    dense_scorer: DenseScorer = opus_dense_score,
) -> list[Candidate]:
    """Compose dense, BM25, RRF and boosts without changing raw similarity."""

    limit = store.clamp_results(max_results)
    ordered_nodes = sorted(nodes, key=lambda node: node.iri)
    eligible = [node for node in ordered_nodes if node.iri not in rejected_iris]
    if not eligible:
        return []
    query_text = f"{ref.name} {ref.description}".strip() or ref.product_id
    dense_backend = env_dense_backend()
    bm25 = store.bm25_scores(
        query_text,
        [(node.iri, taxonomy_bm25_doc(node)) for node in eligible],
    )
    if shadow_hook is not None:
        shadow_hook(query_text, bm25, limit)
    effective_backend = dense_backend
    if dense_backend == "opus":
        nominated_iris = {
            iri
            for iri, _score in sorted(
                bm25.items(),
                key=lambda item: (-item[1], item[0]),
            )[:_MAX_OPUS_NOMINEES]
        }
        eligible = [node for node in eligible if node.iri in nominated_iris]
        # ALL-OR-NOTHING. Calls run concurrently for latency, but the batch is
        # committed atomically: either every returned similarity is
        # model-scored or all of them use the precomputed Jaro-Winkler
        # baseline. Never a mix.
        #
        # A mixed list ranks two incomparable scales against each other, and
        # because the workers share the process-global breaker, WHICH
        # candidates ended up on which scale depended on thread timing — a
        # review measured the published band moving between 0.84/pass and
        # 0.77/borderline on identical inputs. Order is keyed by IRI, not by
        # completion order.
        _q_label = ref.name or ref.product_id
        _q_desc = ref.description or ""
        # Computed BEFORE any model call, for every nominee, so a failure part
        # way through has a complete alternative to fall back to. Not used to
        # filter nominees: BM25 remains the only nominator, so Opus can still
        # recover a semantically strong candidate with weak lexical overlap.
        jaro_scores = {
            node.iri: _jaro_winkler(query_text, taxonomy_dense_text(node))
            for node in eligible
        }

        # Use the STRICT scorer, not the caller-supplied one. The default
        # dense_scorer is opus_dense_score, which makes its OWN per-candidate
        # fallback decision — so with a real (network-level) failure the batch
        # silently kept model scores for the survivors and jaro values for the
        # rest. Measured before this line existed: similarities came back
        # [1.0, 0.9333, 0.91, 0.91] with 0.91 the model value, i.e. two scales
        # in one ranking. The strict seam raises instead, so the batch decides.
        #
        # An injected test double still wins: tests monkeypatch the store's
        # opus_dense_score to script model behaviour, and that must keep
        # working, so only the real scorer is swapped for its strict twin.
        # Wrap ANY scorer so it cannot make its own fallback decision. The
        # previous `is opus_dense_score` identity test was fragile: a
        # monkeypatched or decorated scorer failed the check and silently
        # restored per-candidate fallback, which meant a mutation test
        # (_strict = dense_scorer) still left 25 of 26 tests passing. Wrapping
        # keeps injected test doubles working — a double that returns a score
        # is untouched, one that raises now aborts the batch, which is the
        # behaviour under test.
        def _strict(**kwargs):
            if dense_scorer is opus_dense_score:
                return opus_dense_score_strict(
                    kwargs["query_label"],
                    kwargs["query_desc"],
                    kwargs["candidate_label"],
                    kwargs["candidate_desc"],
                )
            return dense_scorer(**kwargs)

        def _score_one(node):
            return node.iri, _strict(
                query_label=_q_label,
                query_desc=_q_desc,
                candidate_label=node.label or "",
                candidate_desc=candidate_description(node),
            )

        decision = begin_dense_batch()
        if not decision.attempt_opus:
            if not _dense_fallback_enabled():
                raise dense_batch_refusal_error(decision)
            dense_scores = jaro_scores
            effective_backend = "jaro_winkler"
        else:
            try:
                if len(eligible) > 1:
                    with ThreadPoolExecutor(
                        max_workers=min(8, len(eligible))
                    ) as executor:
                        futures = [
                            executor.submit(_score_one, node) for node in eligible
                        ]
                        opus_scores = {}
                        for future in futures:
                            iri, score = future.result()
                            opus_scores[iri] = score
                else:
                    opus_scores = dict(_score_one(node) for node in eligible)
            except Exception as exc:  # noqa: BLE001 - one decision per match
                record_dense_batch_failure(decision)
                if not _dense_fallback_enabled():
                    # Fallback disabled means the caller wants the loud error;
                    # quietly serving Jaro-Winkler would change the configured
                    # contract behind their back.
                    raise
                log.warning(
                    "opus dense batch failed (%s); scoring all %d nominees "
                    "with jaro_winkler for this match",
                    type(exc).__name__,
                    len(eligible),
                )
                dense_scores = jaro_scores
                effective_backend = "jaro_winkler"
            else:
                record_dense_batch_success(decision)
                dense_scores = opus_scores
    else:
        dense_scores = {
            node.iri: _jaro_winkler(query_text, taxonomy_dense_text(node))
            for node in eligible
        }
    fused = store.reciprocal_rank_fusion([dense_scores, bm25])
    rrf_top = 1.0 / (store.RRF_K + 1)
    scored: list[tuple[Candidate, float]] = []
    for node in eligible:
        similarity = dense_scores[node.iri]
        if similarity < min_similarity:
            continue
        candidate = Candidate(node=node, similarity=round(similarity, 4))
        if candidate_filter is not None and not candidate_filter(candidate):
            continue
        raw_boost = store.compute_rank_boost(dict(boosts), node.iri)
        # Rank by the arm that ACTUALLY produced these scores. Using the
        # configured arm would sort Jaro-Winkler fallback values with the opus
        # rule, so the scores and the ordering would disagree.
        #
        # NOT equivalence with SCUDO_DENSE_BACKEND=jaro_winkler: an earlier
        # comment claimed that and it is false. BM25 has already narrowed the
        # pool to _MAX_OPUS_NOMINEES before the fallback decision, so a node
        # with weak lexical overlap but strong string similarity is present in
        # the jaro arm and absent here — measured on a 40-node fixture. That is
        # inherent to nominating for the model arm, not a defect: the contract
        # is one scale per list, not identity with a differently-nominated run.
        sort_key = (
            similarity + raw_boost
            if effective_backend == "opus"
            else fused.get(node.iri, 0.0) + raw_boost * rrf_top
        )
        scored.append((candidate, sort_key))
    scored.sort(key=lambda pair: (-pair[1], pair[0].node.iri))
    return [candidate for candidate, _sort_key in scored[:limit]]
