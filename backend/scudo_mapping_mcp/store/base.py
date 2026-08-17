"""
The seam.

This is the single interface the matcher and the MCP tools talk to. It is
defined as *retrieval operations*, never as query strings — that is the one
property that makes FalkorDB-local and Neptune-prod interchangeable. Cypher
lives inside FalkorDBStore; SPARQL lives inside NeptuneStore; neither ever
leaks above this line.

Every operation enforces depth / breadth / cardinality limits at the boundary.
The caller cannot ask for a 6-hop traversal or 10,000 rows; the store clamps it.
This is "appropriate context, not maximum context" applied to graph retrieval.

DIAGRAM 2 — what the seam owns:
  Structural pass = negative-precedent drop + rank-signal tilt (today)
                  + optional ``candidate_filter`` callable (forward seam)
                  + distance check (NOT YET — pending anchor data)
  Dense           = ``RetrievalStore.bm25_scores`` consumers' counterpart;
                    today a Jaro-Winkler stand-in inside FalkorDBStore.
                    Production target: GraphRAG Toolkit vector index.
  Lexical         = ``bm25_scores`` (pure-Python BM25). Production target:
                    LlamaIndex BM25Retriever.
  Fusion          = ``reciprocal_rank_fusion`` over [dense_ranks, lexical_ranks].
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import Callable, Optional

from ..models import (
    Candidate,
    ConceptualEdge,
    ConceptualGraph,
    ConceptualNode,
    MappingResult,
    Subgraph,
    TaxonomyNode,
    VendorProductRef,
)

# Per-candidate filter callable signature. Returning False drops the
# candidate from the retrieved set BEFORE ranking is finalised. The
# matcher (matching.py) provides this filter; the store applies it.
# This keeps validation logic out of the store and makes the seam
# stay validation-agnostic.
CandidateFilter = Callable[[Candidate], bool]

# Hard ceilings enforced regardless of what the caller passes.
MAX_RESULTS_CEIL = 25
MAX_DEPTH_CEIL = 3
MAX_NODES_CEIL = 100


def _clamp(value: int, ceiling: int, floor: int = 1) -> int:
    return max(floor, min(value, ceiling))


class RetrievalStore(ABC):
    """Bounded, parameterised retrieval. No raw query passthrough is exposed."""

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def health(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    # --- write side (index build / seed only; never used by the agent) ---
    @abstractmethod
    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None: ...

    def replace_taxonomy(self, nodes: list[TaxonomyNode]) -> None:
        """Replace a complete taxonomy when a backend has atomic support.

        Legacy backends retain their existing node-at-a-time behavior. Durable
        backends override this method to publish the seed in one transaction.
        """
        if not nodes:
            raise ValueError("empty taxonomy replacement is not allowed")
        for node in nodes:
            self.upsert_taxonomy_node(node)

    @abstractmethod
    def upsert_vendor_product(self, ref: VendorProductRef) -> None: ...

    # --- HITL write side (M4 new — Section 10a) --------------------------
    @abstractmethod
    def upsert_precedent(
        self,
        *,
        ref: VendorProductRef,
        node: TaxonomyNode,
        decision: str,  # "approve" | "override" | "reject"
        decided_by: str,
        confidence: float,
        provisional: bool = False,
        decided_at_ms: Optional[int] = None,
    ) -> None:
        """Record a HITL decision as a precedent edge.

        ``approve``/``override`` write a POSITIVE precedent
        ``(VendorProduct)-[:MAPPED_TO {decision, decided_by, decided_at,
        confidence, provisional}]->(TaxonomyNode)`` that future
        ``get_precedent_mapping`` calls reuse for replay-safety.

        ``reject`` writes a NEGATIVE precedent
        ``(VendorProduct)-[:NOT_MAPPED_TO {...}]->(TaxonomyNode)`` so the
        ``(vendor, product_id, node)`` triple is filtered out of subsequent
        candidate lists and flagged for attention.

        ``provisional=True`` is reserved for write-back of auto_mapped runs
        that have NOT been human-confirmed (Section 10a caveat); such edges
        MUST be filtered out of ``get_precedent_mapping`` to satisfy invariant
        I5 (never auto-promote against the weak oracle).

        ``decided_at_ms`` lets a bundle importer PRESERVE the original
        decision timestamp (epoch milliseconds, UTC). When ``None``, the
        backend uses its own "now" — the path HITL decisions take.

        Implementations MUST also persist ``ref.source_content_hash`` and
        ``ref.source_file_audit_id`` (when non-None) on the precedent edge.
        ``get_precedent_mapping`` and ``list_confirmed_precedents`` MUST
        read them back. This is the M8 federated-audit chain: decision-time
        source provenance lives on the edge, not just on the in-memory
        result, so the bundle round-trip and a subsequent precedent reuse
        both replay the exact frame identity the matcher consulted.
        """

    @abstractmethod
    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        """Return ``{node_iri: approval_count}`` for one vendor signature.

        DERIVED from CONFIRMED MAPPED_TO precedent edges, NOT a stored counter.
        Count = number of distinct ``VendorProduct`` rows that (a) carry this
        ``vendor_signature`` and (b) have a confirmed MAPPED_TO edge to the
        node. Two consequences flow from deriving instead of counting:

          - Idempotent by construction. Replaying the same HITL decision
            (same upsert_precedent call) is a no-op because MERGE collapses
            duplicate edges; the count cannot drift.
          - Atomic with the precedent. Step "write precedent" and step
            "update signal" are the SAME step — there is no second write to
            fail between them.

        Batch read so the candidate scorer makes at most one round-trip per
        ``find_similar_products`` call.
        """

    @abstractmethod
    def get_negative_precedents(self, vendor: str, product_id: str) -> list[str]:
        """Return CDAO node IRIs rejected for ``(vendor, product_id)``."""

    # --- read side (the agent's entire surface) --------------------------
    @abstractmethod
    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        """A node plus its immediate parent and children. Single hop only."""

    @abstractmethod
    def find_similar_products(
        self,
        ref: VendorProductRef,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter: Optional[CandidateFilter] = None,
    ) -> list[Candidate]:
        """Top-N candidate CDAO nodes for a vendor product. Read-only.

        IMPLEMENTATIONS MUST apply, in this order, before returning:
          (a) Negative-precedent drop — exclude any node the human has
              previously rejected for ``(ref.vendor, ref.product_id)``.
          (b) ``candidate_filter(c)`` if provided — drop any candidate
              for which it returns False. This is where the matcher's
              per-candidate validation filters (``candidate_passes_scope``,
              ``candidate_passes_data_class``) plug in.

        Together with the dense + lexical fusion and the rank-signal tilt,
        these constitute the "structural check" half of Diagram 2.

        ``identifier_resolves`` is NOT applied here — it is a product-level
        sanity check that lives at the matcher's gate (a failure means the
        store returned a node it cannot resolve, which is a data-integrity
        issue, not a per-candidate one).
        """

    @abstractmethod
    def get_ontology_neighbourhood(
        self, node_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> Subgraph:
        """A bounded subgraph around a node. Depth and node count are clamped."""

    @abstractmethod
    def get_precedent_mapping(
        self, vendor: str, product_id: str
    ) -> Optional[MappingResult]:
        """A prior CONFIRMED mapping for this exact product, if one exists.

        Implementations MUST exclude provisional edges (Section 10a caveat:
        unattended auto_mapped runs are never treated as ground truth).
        """

    # --- M6 — bundle export surface --------------------------------------
    @abstractmethod
    def list_confirmed_precedents(self) -> list[dict]:
        """Return every CONFIRMED MAPPED_TO edge as a flat record.

        Used by ``bundle.export_bundle`` to materialise MappingPatterns.
        Implementations MUST exclude provisional edges (I5). Each record
        has the keys: ``vendor``, ``product_id``, ``product_name``,
        ``description``, ``mapped_node_iri``, ``mapped_node_label``,
        ``decision``, ``decided_by``, ``decided_at_ms``, ``confidence``.
        ``bundle.export_bundle`` is responsible for shaping each record
        into a typed ``MappingPattern`` (it owns the signature derivation
        and the ISO-8601 timestamp conversion).
        """

    @abstractmethod
    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        """Return every TaxonomyNode in the store, deterministic order by IRI.

        Used by ``bundle._taxonomy_hash`` so the bundle records a fingerprint
        of the seed at export time. An importer can detect divergence before
        re-seeding.
        """

    def taxonomy_size(self) -> int:
        """Return current taxonomy cardinality for readiness checks."""
        return len(self.list_taxonomy_nodes())

    # --- M10 — conceptual enrichment layer (ADDITIVE, Section 10c) -------
    # Governance/lineage metadata that hangs off an already-mapped CDAO
    # Concept. Never fed into matching.py's cost ladder (I5); never
    # queried above this seam as raw Cypher/SPARQL (I2).
    @abstractmethod
    def upsert_conceptual_node(self, node: ConceptualNode) -> None:
        """Write (or MERGE-update) one conceptual-layer node."""

    @abstractmethod
    def upsert_conceptual_edge(self, edge: ConceptualEdge) -> None:
        """Write (or MERGE-update) one conceptual-layer edge.

        Implementations MAY assume both ``edge.from_iri`` and ``edge.to_iri``
        were already upserted via ``upsert_conceptual_node`` — this method
        is not responsible for creating missing endpoint nodes.
        """

    @abstractmethod
    def get_conceptual_graph(
        self, concept_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> ConceptualGraph:
        """All conceptual-layer nodes/edges attached to one CDAO Concept.

        Read-only. Depth and node count are clamped exactly like
        ``get_ontology_neighbourhood`` (``clamp_depth`` / ``clamp_nodes``).
        Returns an empty ``ConceptualGraph`` (not an error) when nothing is
        attached to ``concept_iri`` yet.
        """

    # Shared clamping helpers so both backends behave identically at the edge.
    @staticmethod
    def clamp_results(n: int) -> int:
        return _clamp(n, MAX_RESULTS_CEIL)

    @staticmethod
    def clamp_depth(n: int) -> int:
        return _clamp(n, MAX_DEPTH_CEIL)

    @staticmethod
    def clamp_nodes(n: int) -> int:
        return _clamp(n, MAX_NODES_CEIL)

    # Shared canonicalisation so FalkorDB and Neptune key rank signals the
    # same way. Whitespace normalised; vendor and (name|product_id) joined.
    #
    # VENDOR IS LOWER-CASED — and must stay that way. The other two keys
    # derived from the vendor already canonicalise it:
    #
    #     models.mds_iri       f"{vendor.strip().lower()}::{product_id.strip()}"
    #     verdict.input_hash   f"{vendor.strip().lower()}::{product_id.strip()}"
    #
    # This function used to ``.strip()`` the vendor without lower-casing it,
    # so "LSEG" and "lseg" produced the SAME IRI and the SAME seal identity
    # but DIFFERENT signatures. Identity converged while rank signals forked:
    # ``rank_signals_for`` bucketed the two casings apart, so the precedent
    # ordering boost silently split and under-performed — no error, no failing
    # test, just degraded matching. Pinned by tests/test_vendor_signature_casing.py.
    @staticmethod
    def vendor_signature(vendor: str, name: str, product_id: str) -> str:
        base = (name or product_id or "").strip().lower()
        base = " ".join(base.split())
        return f"{(vendor or '').strip().lower()}::{base}"

    # Per-approval rank-signal weight and overall cap. Single swap point for
    # the scorer's tuning surface (I10): retuning happens here and propagates
    # to every backend that calls compute_rank_boost().
    RANK_BOOST_PER_APPROVAL = 0.02
    RANK_BOOST_CAP = 0.10

    # Reciprocal Rank Fusion constant. RRF combines multiple rankings via
    # sum(1 / (k + rank)) per item. k=60 is the canonical default that
    # prevents top-1 items from dominating the fused score — keeps lexical
    # and "dense" arms equally weighted so a weak-semantic-but-strong-
    # lexical hit (e.g., a ticker match) can still surface.
    RRF_K = 60

    @classmethod
    def compute_rank_boost(cls, boosts: dict, node_iri: str) -> float:
        """Return the additive rank-signal boost for one candidate node.

        Used by ``find_similar_products`` to TILT candidate ORDERING. The boost
        MUST NOT lift a base similarity past the 0.80 PASS cut unattended
        (Section 10a CAVEAT / I5) — the matcher achieves this by sorting on
        ``base + boost`` but emitting ``Candidate.similarity = base``, so the
        floor still sees the raw oracle score.
        """
        count = int(boosts.get(node_iri, 0) or 0)
        return min(cls.RANK_BOOST_CAP, cls.RANK_BOOST_PER_APPROVAL * count)

    @classmethod
    def compute_rank_boost_scaled_for_dense(
        cls,
        boosts: dict,
        node_iri: str,
    ) -> float:
        """Rank-signal boost, scaled for a dense-score sort key in [0, 1].

        The legacy ``find_similar_products`` path sorts by a fused RRF score
        whose top entry is ~1/(RRF_K+1) ≈ 0.0164. Multiplying the raw boost
        (cap 0.10) by that magnitude keeps the structural tilt commensurate
        with the fused ranking signal. Under Opus-dense, the sort key is the
        raw dense similarity in [0, 1] — orders of magnitude larger than
        RRF — so the same scaling would render the boost effectively
        invisible (≤ 0.00164 against a top hit near 1.0).

        This method returns the RAW boost (≤ ``RANK_BOOST_CAP`` == 0.10),
        which is the correct magnitude to add to a dense [0, 1] sort key:
        the tilt is a perceptible nudge but bounded so an under-similar
        node can never overtake a clearly-better one. The matcher still
        emits ``Candidate.similarity = raw dense score``, so the 0.80
        confidence floor never sees the boost (I5 preserved).
        """
        count = int(boosts.get(node_iri, 0) or 0)
        return min(cls.RANK_BOOST_CAP, cls.RANK_BOOST_PER_APPROVAL * count)

    @classmethod
    def max_useful_boost_approvals(cls) -> int:
        """How many approvals saturate the cap.

        Useful for tests asserting "5 approvals max out the boost"; the
        constant is derived from the seam's own tuning so it tracks any
        retune of ``RANK_BOOST_PER_APPROVAL`` / ``RANK_BOOST_CAP``.
        """
        # ceil(cap / per_approval), but per-approval > 0 always.
        per = cls.RANK_BOOST_PER_APPROVAL
        return int(cls.RANK_BOOST_CAP / per) if per > 0 else 0

    # ──────────────────────────────────────────────────────────────────
    # Falkor fusion — the "dense + lexical + structural" pass
    # ──────────────────────────────────────────────────────────────────
    # Implemented on the seam so both backends share the SAME deterministic
    # logic and the M9 swap (real embeddings replacing Jaro-Winkler) is a
    # single-method substitution.
    #
    # Today the "dense" arm is Jaro-Winkler over labels — it's the
    # deterministic stand-in for real semantic embeddings. BM25 sits
    # alongside as the lexical sidecar that recovers ticker / RIC / ISIN /
    # acronym matches embeddings are weak on. RRF blends them so neither
    # dominates: a strong lexical hit with weak semantic still surfaces,
    # and vice versa.

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        """Lowercase, split on whitespace + most punctuation, but PRESERVE
        periods inside tokens so dotted identifiers (RICs like ``AAPL.O``,
        decimal numbers like ``1.5``) stay intact. Leading and trailing
        periods are stripped per-token so a trailing sentence period
        doesn't sneak into the indexed token.
        """
        raw = re.split(r"[^a-z0-9.]+", (text or "").lower())
        return [t.strip(".") for t in raw if t.strip(".")]

    @classmethod
    def bm25_scores(
        cls,
        query: str,
        docs: list[tuple[str, str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> dict[str, float]:
        """Return ``{doc_id: bm25_score}`` for the query against ``docs``.

        ``docs`` is ``[(id, text), ...]``. Pure-Python BM25 implementation
        — no scipy / no rank_bm25 dependency. ``k1`` and ``b`` are the
        canonical tuning constants.
        """
        if not docs:
            return {}
        tokenised = [(d_id, cls._tokenise(text)) for d_id, text in docs]
        avgdl = sum(len(toks) for _, toks in tokenised) / max(len(tokenised), 1)
        # Document frequency (number of docs containing each term).
        df: dict[str, int] = {}
        for _, toks in tokenised:
            for term in set(toks):
                df[term] = df.get(term, 0) + 1
        N = len(tokenised)
        idf = {term: math.log(1 + (N - n + 0.5) / (n + 0.5)) for term, n in df.items()}
        q_terms = cls._tokenise(query)
        scores: dict[str, float] = {}
        for d_id, toks in tokenised:
            if not toks:
                scores[d_id] = 0.0
                continue
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            dl = len(toks)
            score = 0.0
            for q in q_terms:
                if q not in tf:
                    continue
                f = tf[q]
                num = f * (k1 + 1)
                denom = f + k1 * (1 - b + b * (dl / avgdl)) if avgdl > 0 else 1
                score += idf.get(q, 0.0) * (num / denom)
            scores[d_id] = score
        return scores

    @classmethod
    def reciprocal_rank_fusion(
        cls,
        rankings: list[dict[str, float]],
    ) -> dict[str, float]:
        """RRF: combine multiple ranked-score maps into one fused map.

        For each ranker, items are ranked by score (descending). The fused
        score for item ``x`` is ``sum_over_rankers(1 / (k + rank_in_ranker))``
        where rank is 1-indexed. Items absent from a ranker contribute 0
        from that ranker — they're not penalised, they just don't gain.
        """
        k = cls.RRF_K
        fused: dict[str, float] = {}
        for ranker in rankings:
            ranked = sorted(ranker.items(), key=lambda kv: kv[1], reverse=True)
            for rank, (item_id, _) in enumerate(ranked, start=1):
                fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + rank)
        return fused
