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


from .. import opus_dense
from ..models import Candidate, VendorProductRef
from ..taxonomy_text import (
    maybe_log_taxonomy_text_shadow,
    taxonomy_candidate_desc,
)
from ..tests.fake_store import FakeStore
from .retrieval_scoring import score_candidates


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
        if not self._nodes:
            return []
        rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))
        sig = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        boosts = self.rank_signals_for(sig)
        return score_candidates(
            store=self,
            ref=ref,
            nodes=list(self._nodes.values()),
            rejected_iris=rejected,
            boosts=boosts,
            max_results=max_results,
            min_similarity=min_similarity,
            candidate_filter=candidate_filter,
            shadow_hook=self._maybe_log_bm25_shadow,
            candidate_description=taxonomy_candidate_desc,
            dense_scorer=opus_dense.opus_dense_score,
        )

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
