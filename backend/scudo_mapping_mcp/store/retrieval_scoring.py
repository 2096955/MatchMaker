"""Shared candidate scoring composition for local retrieval stores."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from ..config import env_dense_backend
from ..models import Candidate, TaxonomyNode, VendorProductRef
from ..opus_dense import opus_dense_score
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
    if dense_backend == "opus":
        nominated_iris = {
            iri
            for iri, _score in sorted(
                bm25.items(),
                key=lambda item: (-item[1], item[0]),
            )[:_MAX_OPUS_NOMINEES]
        }
        eligible = [node for node in eligible if node.iri in nominated_iris]
        dense_scores = {
            node.iri: dense_scorer(
                query_label=ref.name or ref.product_id,
                query_desc=ref.description or "",
                candidate_label=node.label or "",
                candidate_desc=candidate_description(node),
            )
            for node in eligible
        }
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
        sort_key = (
            similarity + raw_boost
            if dense_backend == "opus"
            else fused.get(node.iri, 0.0) + raw_boost * rrf_top
        )
        scored.append((candidate, sort_key))
    scored.sort(key=lambda pair: (-pair[1], pair[0].node.iri))
    return [candidate for candidate, _sort_key in scored[:limit]]
