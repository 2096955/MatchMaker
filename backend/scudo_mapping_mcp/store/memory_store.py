"""In-memory store with REAL scoring — the local-demo backend.

DEV/DEMO ONLY. ``STORE_BACKEND=memory`` selects this store so the full
matching ladder runs on a laptop with no FalkorDB container and no Neptune:
seed_taxonomy() populates the node set, ingest drops vendor rows into the
working set, and ``find_similar_products`` computes the SAME dense
(Jaro-Winkler) + lexical (BM25) + RRF + rank-signal composition as
``FalkorDBStore`` — not the preset-score oracle the test FakeStore uses.

Inheritance: FakeStore supplies the write/precedent/negative surfaces
(faithful mirrors of the FalkorDB semantics — that's what the smoke suite
proves); this class replaces only the retrieval scoring so similarity values
are computed, not preset. ``Candidate.similarity`` is the RAW DENSE SCORE
(arb-review §5.2) — RRF and boost drive the sort key only, exactly as in
``FalkorDBStore.find_similar_products``.

The SCUDO_DENSE_BACKEND=opus branch is honoured here too, so a local demo
with Bedrock credentials exercises the same Opus-dense path the deployed
Match&Verify uses.
"""

from __future__ import annotations

from typing import Optional

from ..config import env_dense_backend
from ..models import Candidate, VendorProductRef
from ..taxonomy_text import (
    maybe_log_taxonomy_text_shadow,
    taxonomy_bm25_doc,
    taxonomy_candidate_desc,
    taxonomy_dense_text,
)
from ..tests.fake_store import FakeStore
from .falkordb_store import _jaro_winkler


class MemoryStore(FakeStore):
    """FakeStore write surfaces + FalkorDBStore scoring composition."""

    def find_similar_products(
        self,
        ref: VendorProductRef,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter=None,
    ) -> list[Candidate]:
        limit = self.clamp_results(max_results)
        if not self._nodes:
            return []
        query_text = f"{ref.name} {ref.description}".strip() or ref.product_id

        # Structural filter (a) — negative-precedent drop, up front.
        rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))

        dense_backend = env_dense_backend()

        dense_scores: dict[str, float] = {}
        labels: dict[str, str] = {}
        parents: dict[str, Optional[str]] = {}

        if dense_backend == "opus":
            from ..opus_dense import opus_dense_score

            query_label = ref.name or ref.product_id
            query_desc = ref.description or ""
            for iri, node in self._nodes.items():
                if iri in rejected:
                    continue
                labels[iri] = node.label or ""
                parents[iri] = node.parent_iri
                dense_scores[iri] = opus_dense_score(
                    query_label=query_label,
                    query_desc=query_desc,
                    candidate_label=node.label or "",
                    candidate_desc=taxonomy_candidate_desc(node),
                )
        else:
            for iri, node in self._nodes.items():
                if iri in rejected:
                    continue
                labels[iri] = node.label or ""
                parents[iri] = node.parent_iri
                dense_scores[iri] = _jaro_winkler(
                    query_text,
                    taxonomy_dense_text(node),
                )

        # Arm 2 — BM25 lexical sidecar. SORT-only, same as FalkorDBStore.
        bm25 = self.bm25_scores(
            query_text,
            [(iri, taxonomy_bm25_doc(self._nodes[iri])) for iri in labels],
        )

        # Shadow rollout (Phase E step 2): the DEFAULT legacy sidecar now
        # reports the text-on BM25 nomination diff too — previously only the
        # multi-path route did, so shadow mode produced zero signal in
        # default environments. Observation only; nominations unchanged.
        self._maybe_log_bm25_shadow(query_text, bm25, limit)

        # RRF fuses the two RANKINGS into the sort score. Never similarity.
        fused_rank_score = self.reciprocal_rank_fusion([dense_scores, bm25])

        sig = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        boosts = self.rank_signals_for(sig)
        rrf_top = 1.0 / (self.RRF_K + 1)

        scored: list[tuple[Candidate, float]] = []
        for iri in labels:
            similarity = dense_scores[iri]
            if similarity < min_similarity:
                continue
            # CRITICAL: Candidate.similarity is the RAW DENSE SCORE
            # (arb-review §5.2). No rerank, no boost, no fusion.
            candidate = Candidate(
                node=self._nodes[iri],
                similarity=round(similarity, 4),
            )
            if candidate_filter is not None and not candidate_filter(candidate):
                continue
            raw_boost = self.compute_rank_boost(boosts, iri)
            if dense_backend == "opus":
                sort_key = similarity + raw_boost
            else:
                rank_score = fused_rank_score.get(iri, 0.0)
                sort_key = rank_score + raw_boost * rrf_top
            scored.append((candidate, sort_key))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in scored[:limit]]

    def _maybe_log_bm25_shadow(
        self, query_text: str, bm25: dict[str, float], top_n: int
    ) -> None:
        """Report the text-on BM25 nomination diff for the legacy sidecar.

        Production nomination here is the BM25 arm's top-N under the LIVE
        text flag (same (-score, iri) ordering as ``shadow_bm25_top_iris``).
        Observation only — never touches scores, sort keys or results.
        """
        universe = [self._nodes[iri] for iri in bm25 if iri in self._nodes]
        if not universe:
            return
        ranked = sorted(bm25.keys(), key=lambda iri: (-bm25.get(iri, 0.0), iri))
        nominated = [
            Candidate(node=self._nodes[iri], similarity=0.0)
            for iri in ranked[:top_n]
            if iri in self._nodes
        ]
        maybe_log_taxonomy_text_shadow(
            query_text, universe, self, nominated, top_n=top_n
        )
