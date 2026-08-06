"""
FalkorDB backend — the local / prototype store.

PRODUCTION SWAP-POINTS (per Diagram 2):

  Dense arm    -> Claude Opus 4.8 as a SEMANTIC JUDGEMENT scorer (WS-B).
                  The Titan-Embed plan is PARKED until the Foundation
                  amendment for Bedrock model access lands; Opus is
                  already accessible to the M and V task roles, so we
                  use it as the dense quantity ``Candidate.similarity``
                  carries. The swap is gated by SCUDO_DENSE_BACKEND
                  (see opus_dense.py): "opus" wires the Bedrock invoke,
                  "jaro_winkler" (default in tests) keeps the legacy
                  deterministic stand-in. CRITICAL INVARIANT (arb-review
                  §5.2): ``Candidate.similarity`` IS the raw dense
                  score the backend returned — no rerank, no fusion,
                  no boost contamination. The 0.80 confidence floor
                  sees exactly that value.

  Lexical arm  -> LlamaIndex ``BM25Retriever`` (or FalkorDB-native
                  full-text index). TODAY: the pure-Python BM25 on
                  ``RetrievalStore.bm25_scores`` is the deliberate
                  stand-in; functionally equivalent at demo scale, no
                  external dependency.

  Structural   -> Distance check via SciPy sparse adjacency over the
                  taxonomy (BFS / shortest-path against an anchor
                  derived from precedent neighbourhood or vendor class
                  baseline). NOT IMPLEMENTED — deferred until an anchor
                  exists (warm-up phase: no precedents yet, no class
                  tags). Negative-precedent drop and rank-signal tilt
                  ship today.

Note on the AWS GraphRAG Toolkit: its FalkorDB graph store currently supports
semantic (vector) search only, NOT traversal-based search. Our bounded surface
needs real graph walks (get_taxonomy_node, get_ontology_neighbourhood), so
this implementation talks to FalkorDB *natively* via Cypher and will continue
to do so. The toolkit slots into the dense arm only.

INTEGRATION TEST DEBT: the fusion path here (dense + lexical + RRF +
structural) is unit-tested at the seam (`bm25_scores`,
`reciprocal_rank_fusion`) and end-to-end through the FakeStore which does NOT
fuse. There is no test against a real FalkorDB container; the first time the
real backend exercises this code path is at integration. This is a known gap.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candidate, MappingResult, MappingStatus, Subgraph, TaxonomyNode, VendorProductRef
from .base import RetrievalStore


def _jaro_winkler(s1: str, s2: str) -> float:
    """Pure-Python Jaro-Winkler similarity in [0, 1]. Deterministic, no deps."""
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    match_dist = max(len(s1), len(s2)) // 2 - 1
    s1_m = [False] * len(s1)
    s2_m = [False] * len(s2)
    matches = 0
    for i, c in enumerate(s1):
        lo = max(0, i - match_dist)
        hi = min(i + match_dist + 1, len(s2))
        for j in range(lo, hi):
            if not s2_m[j] and s2[j] == c:
                s1_m[i] = s2_m[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    k = 0
    transpositions = 0
    for i in range(len(s1)):
        if s1_m[i]:
            while not s2_m[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
    transpositions //= 2
    jaro = (matches / len(s1) + matches / len(s2) + (matches - transpositions) / matches) / 3
    prefix = 0
    for c1, c2 in zip(s1, s2):
        if c1 == c2 and prefix < 4:
            prefix += 1
        else:
            break
    return jaro + prefix * 0.1 * (1 - jaro)


class FalkorDBStore(RetrievalStore):
    def __init__(self, url: str, graph_name: str):
        # Lazy import so the package loads even where falkordb isn't installed.
        from falkordb import FalkorDB  # type: ignore

        host, port = self._parse_url(url)
        self._db = FalkorDB(host=host, port=port)
        self._g = self._db.select_graph(graph_name)

    @staticmethod
    def _parse_url(url: str) -> tuple[str, int]:
        # falkordb://host:port  ->  (host, port);  falkordb://  ->  (localhost, 6379)
        rest = url.replace("falkordb://", "").strip()
        if not rest:
            return "localhost", 6379
        host, _, port = rest.partition(":")
        return host or "localhost", int(port or 6379)

    def _ro(self, cypher: str, params: dict | None = None):
        # Read-only query: FalkorDB rejects writes, a real guard on the read path.
        return self._g.ro_query(cypher, params or {}).result_set

    # --- lifecycle -------------------------------------------------------
    def health(self) -> bool:
        try:
            self._ro("RETURN 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        try:
            self._db.connection.close()
        except Exception:
            pass

    # --- write side (seed / index build only) ----------------------------
    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
        self._g.query(
            "MERGE (t:TaxonomyNode {iri:$iri}) SET t.label=$label, t.parent_iri=$parent",
            {"iri": node.iri, "label": node.label, "parent": node.parent_iri or ""},
        )
        if node.parent_iri:
            self._g.query(
                "MATCH (p:TaxonomyNode {iri:$p}),(c:TaxonomyNode {iri:$c}) "
                "MERGE (p)-[:HAS_CHILD]->(c)",
                {"p": node.parent_iri, "c": node.iri},
            )

    def upsert_vendor_product(self, ref: VendorProductRef) -> None:
        # Ingest is the canonical source for the VendorProduct's label-bearing
        # fields, so SET overwrites on every call (a re-ingest reflecting
        # updated vendor metadata is intended to win). Crucially, this MUST
        # include ``signature`` — otherwise rank_signals_for cannot find this
        # row in the property-equality MATCH and the derived count is 0 in
        # any real ingest-then-approve deployment (the upsert_precedent path
        # would only set signature ON CREATE, which would not fire on the
        # already-existing iri-keyed node).
        signature = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        self._g.query(
            "MERGE (v:VendorProduct {iri:$iri}) "
            "SET v.vendor=$vendor, v.product_id=$pid, v.name=$name, "
            "    v.description=$desc, v.signature=$sig",
            {
                "iri": ref.iri, "vendor": ref.vendor, "pid": ref.product_id,
                "name": ref.name, "desc": ref.description,
                "sig": signature,
            },
        )

    # --- read side -------------------------------------------------------
    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        rows = self._ro(
            "MATCH (t:TaxonomyNode {iri:$iri}) "
            "OPTIONAL MATCH (t)-[:HAS_CHILD]->(c:TaxonomyNode) "
            "OPTIONAL MATCH (p:TaxonomyNode)-[:HAS_CHILD]->(t) "
            "RETURN t.iri, t.label, p.iri, collect(DISTINCT c.iri)",
            {"iri": node_iri},
        )
        if not rows:
            return None
        iri, label, parent, children = rows[0]
        return TaxonomyNode(
            iri=iri, label=label, parent_iri=parent or None,
            children_iris=[c for c in (children or []) if c],
        )

    def find_similar_products_batch(
        self,
        refs,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter=None,
    ) -> list[list[Candidate]]:
        """Batch retrieval with the BM25 corpus hoisted out of the loop.

        Declaring the whole workload lets the corpus half of BM25 be built
        ONCE for the taxonomy instead of once per vendor product (see
        ``RetrievalStore.find_similar_products_batch`` for why the eager
        per-ref call cannot do this).

        The shared index covers the FULL node set, so it is passed down
        only for refs with NO negative precedents: a rejected node is
        dropped from the corpus before scoring, which shifts idf and avgdl
        and therefore the BM25 ranking. Refs carrying rejections build
        their own index, exactly as before. Result: identical output to the
        per-ref loop, fewer corpus builds.

        One taxonomy read is shared across the batch as well — the eager
        path issues the same MATCH once per product.
        """
        rows = self._ro(
            "MATCH (t:TaxonomyNode) RETURN t.iri, t.label, t.parent_iri"
        )
        shared = self.build_bm25_index(
            [(iri, label or "") for iri, label, _ in rows]
        ) if rows else None
        out: list[list[Candidate]] = []
        for ref in refs:
            has_rejects = bool(
                self.get_negative_precedents(ref.vendor, ref.product_id)
            )
            out.append(self.find_similar_products(
                ref,
                max_results=max_results,
                min_similarity=min_similarity,
                candidate_filter=candidate_filter,
                _shared_index=None if has_rejects else shared,
            ))
        return out

    def find_similar_products(
        self,
        ref: VendorProductRef,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter=None,
        _shared_index=None,
    ) -> list[Candidate]:
        """Falkor match-and-check: dense (Jaro-Winkler) + lexical (BM25) +
        structural (negative-precedent drop, optional candidate_filter,
        rank-signal tilt). See Diagram 2.

        DESIGN: dense stays AUTHORITATIVE for ``Candidate.similarity``.
        BM25 is a SORT-only sidecar. The 0.80 floor was calibrated against
        Jaro-Winkler's [0,1] string similarity; making the floor gate
        against a max-normalised RRF score (which always yields 1.0 for
        the top hit) would silently break I4 — any query with at least one
        weakly-similar candidate would auto-map. So:

          - Dense (Jaro-Winkler) score IS the Candidate.similarity. The
            floor sees the same calibrated quantity it was tuned against.
          - RRF fuses dense-rank with BM25-rank to produce the SORT KEY.
            BM25 thus surfaces exact-token hits into the top-N retrieved
            set; if their dense score is low, the matcher will still gate
            them to NEEDS_REVIEW (correctly) and a reviewer confirms.
          - The structural rank-signal tilt (≤+0.10) adds to the same SORT
            KEY, never to the similarity.

        Net effect: lexical recovery improves RECALL (right candidates in
        the top-N) without breaking the floor's PRECISION discipline.

        OPUS-DENSE FEATURE FLAG: when ``settings.use_opus_dense`` is True,
        this method delegates to ``retrieval.multi_path_retrieve`` — the
        new SDK-inspired multi-path retrieval surface. The flag is OFF by
        default so the legacy Jaro-Winkler + BM25 + RRF path (below) is
        the one the 86 smoke gates exercise unchanged. Agent B owns the
        retrieval module; if it isn't on the path or raises, we surface
        that loudly (do NOT silently fall back — a flag-on misconfig
        should fail visibly, not regress silently to the legacy path).
        """
        # Flag-gated delegation to the new multi-path retrieval pipeline.
        # Imported lazily so the module-load surface stays cheap and the
        # legacy path doesn't pay an import cost it never uses.
        #
        # CANONICAL OPUS SURFACE — opus_dense.make_opus_dense_scorer.
        # The newer scudo_mapping_mcp.dense_scorer module is DEPRECATED
        # (see its module docstring); opus_dense is the per-pair scorer
        # wired through here and on the legacy SCUDO_DENSE_BACKEND=opus
        # path. Closes ARB finding A1 (flag-on no longer hardwires
        # dense_scorer=None — every match got 0.5 → NEEDS_REVIEW) and
        # A2 (one canonical surface).
        from ..config import settings as _settings
        if _settings.use_opus_dense:
            from .. import retrieval as _retrieval  # type: ignore
            from .. import opus_dense as _opus_dense  # type: ignore
            scorer = _opus_dense.make_opus_dense_scorer(
                query_desc=ref.description or "",
            )
            return _retrieval.multi_path_retrieve(
                ref,
                self,
                max_results,
                min_similarity,
                candidate_filter=candidate_filter,
                dense_scorer=scorer,
            )

        limit = self.clamp_results(max_results)
        rows = self._ro("MATCH (t:TaxonomyNode) RETURN t.iri, t.label, t.parent_iri")
        if not rows:
            return []
        query_text = f"{ref.name} {ref.description}".strip() or ref.product_id

        # Structural filter (a) — negative-precedent drop. Done up front so
        # rejected nodes never enter the scoring loop, which would only
        # waste compute. The matcher used to apply this AFTER retrieval;
        # owning it on the seam matches Diagram 2 (negative-precedents are
        # part of the structural box) and saves the scoring work.
        rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))

        # Arm 1 — dense (Jaro-Winkler OR Opus) over labels. AUTHORITATIVE similarity.
        # Backend is selected by SCUDO_DENSE_BACKEND (see opus_dense.py):
        #   "jaro_winkler" (default): the legacy deterministic stand-in.
        #   "opus":                   Bedrock invoke on Claude Opus 4.8.
        # In both cases the returned value is in [0, 1] and lands on
        # Candidate.similarity unmodified — arb-review §5.2 invariant.
        import os as _os
        dense_backend = (_os.getenv("SCUDO_DENSE_BACKEND") or "jaro_winkler").strip().lower()

        dense_scores: dict[str, float] = {}
        labels: dict[str, str] = {}
        parents: dict[str, Optional[str]] = {}

        if dense_backend == "opus":
            # Lazy import so the package still loads when boto3 is absent.
            from ..opus_dense import opus_dense_score
            query_label = ref.name or ref.product_id
            query_desc = ref.description or ""
            for iri, label, parent in rows:
                if iri in rejected:
                    continue
                labels[iri] = label or ""
                parents[iri] = parent or None
                dense_scores[iri] = opus_dense_score(
                    query_label=query_label,
                    query_desc=query_desc,
                    candidate_label=label or "",
                    candidate_desc="",
                )
        else:
            for iri, label, parent in rows:
                if iri in rejected:
                    continue
                labels[iri] = label or ""
                parents[iri] = parent or None
                dense_scores[iri] = _jaro_winkler(query_text, label or "")

        # Arm 2 — BM25 (lexical sidecar). Recovers exact-token hits the
        # dense arm misses on tickers / RICs / acronyms. SORT-only.
        # Reuse the batch-hoisted corpus when one was supplied (see
        # find_similar_products_batch); otherwise build it for this ref
        # alone — the eager path. Both branches run the SAME arithmetic via
        # Bm25Index.score, so the only difference is how many times the
        # corpus half was computed.
        bm25_scores = (
            _shared_index.score(query_text)
            if _shared_index is not None
            else self.bm25_scores(
                query_text,
                [(iri, labels[iri]) for iri in labels],
            )
        )

        # RRF fuses the two RANKINGS into a single ordering score. This
        # only drives the SORT — it does NOT replace dense as similarity.
        fused_rank_score = self.reciprocal_rank_fusion(
            [dense_scores, bm25_scores],
        )

        # Structural pass — rank-signal tilt applied to the SORT ONLY.
        # The boost magnitude is rescaled per backend so that it stays
        # commensurate with the sort key but never absorbed into
        # ``Candidate.similarity``. INVARIANT: boost cap ≤ 0.10 of the
        # similarity scale, and never above 0.10 absolute.
        #
        #   Jaro-Winkler backend: sort key is the RRF fused-rank score
        #     whose top contribution is rrf_top = 1/(RRF_K+1) ≈ 0.0164.
        #     The raw boost (cap 0.10) is scaled by rrf_top so a saturated
        #     boost contributes ≤ 0.00164 to the sort key — well under
        #     10% of the top RRF magnitude.
        #
        #   Opus backend: sort key starts from the dense similarity in
        #     [0, 1]. The raw boost (cap 0.10) is applied directly, so a
        #     saturated boost contributes ≤ 0.10 — perceptible but
        #     bounded at 10% of full scale and incapable of lifting a
        #     sub-floor candidate past 0.80 + 0.10 = 0.90.
        sig = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        boosts = self.rank_signals_for(sig)
        rrf_top = 1.0 / (self.RRF_K + 1)  # ~0.0164 — the maximum fused contrib

        scored: list[tuple[Candidate, float]] = []
        for iri in labels:
            similarity = dense_scores[iri]
            if similarity < min_similarity:
                continue
            # CRITICAL: Candidate.similarity is the RAW DENSE SCORE
            # (arb-review §5.2). No rerank, no boost, no fusion.
            candidate = Candidate(
                node=TaxonomyNode(
                    iri=iri, label=labels[iri],
                    parent_iri=parents[iri],
                ),
                similarity=round(similarity, 4),
            )
            # Structural filter (b) — per-candidate filter from the matcher.
            # The matcher composes this from
            # validations.candidate_passes_scope and
            # validations.candidate_passes_data_class. Both are pass-by-default
            # in the warm-up phase (no per-(vendor, node) scope rules and no
            # class tags yet); the filter drops nothing until that data lands.
            if candidate_filter is not None and not candidate_filter(candidate):
                continue
            raw_boost = self.compute_rank_boost(boosts, iri)
            if dense_backend == "opus":
                # Sort by raw dense + raw capped boost. Boost cap is
                # ALREADY ≤ 0.10 — no further scaling needed against a
                # [0, 1] dense quantity.
                sort_key = similarity + raw_boost
            else:
                # Legacy fused-rank sort. Scale boost into RRF's range
                # so a strong rank signal nudges the sort order without
                # obliterating fusion's contribution.
                rank_score = fused_rank_score.get(iri, 0.0)
                sort_key = rank_score + raw_boost * rrf_top
            scored.append((candidate, sort_key))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in scored[:limit]]

    def get_ontology_neighbourhood(
        self, node_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> Subgraph:
        depth = self.clamp_depth(max_depth)
        cap = self.clamp_nodes(max_nodes)
        rows = self._ro(
            f"MATCH p=(root:TaxonomyNode {{iri:$iri}})-[:HAS_CHILD*1..{depth}]->(n:TaxonomyNode) "
            "RETURN nodes(p) AS ns LIMIT $cap",
            {"iri": node_iri, "cap": cap},
        )
        seen: dict[str, TaxonomyNode] = {}
        edges: set[tuple[str, str]] = set()
        for (ns,) in rows:
            prev = None
            for n in ns:
                props = n.properties
                iri = props["iri"]
                seen.setdefault(iri, TaxonomyNode(iri=iri, label=props.get("label", "")))
                if prev is not None:
                    edges.add((prev, iri))
                prev = iri
        return Subgraph(root_iri=node_iri, nodes=list(seen.values()), edges=list(edges))

    def get_precedent_mapping(self, vendor: str, product_id: str) -> Optional[MappingResult]:
        # CONFIRMED edges only — exclude provisional (Section 10a caveat I5).
        # ORDER BY decided_at DESC LIMIT 1 is defence-in-depth: upsert_precedent
        # already maintains a single-positive-precedent invariant per
        # VendorProduct, but if that ever drifts, the most-recent decision wins.
        rows = self._ro(
            "MATCH (v:VendorProduct {vendor:$vendor, product_id:$pid})"
            "-[r:MAPPED_TO]->(t:TaxonomyNode) "
            "WHERE COALESCE(r.provisional, false) = false "
            "RETURN v.iri, v.name, t.iri, t.label, r.confidence, r.decision, "
            "       r.decided_at, r.source_content_hash, r.source_file_audit_id "
            "ORDER BY r.decided_at DESC "
            "LIMIT 1",
            {"vendor": vendor, "pid": product_id},
        )
        if not rows:
            return None
        (v_iri, v_name, t_iri, t_label, conf, decision, _,
         src_hash, src_audit) = rows[0]
        status = (
            MappingStatus.OVERRIDDEN
            if (decision or "").strip().lower() == "override"
            else MappingStatus.APPROVED
        )
        return MappingResult(
            vendor_product_iri=v_iri, vendor=vendor, product_id=product_id,
            product_name=v_name or "",
            mapped_node_iri=t_iri, mapped_node_label=t_label,
            confidence=float(conf) if conf is not None else 1.0,
            status=status, rationale="precedent",
            source_content_hash=src_hash or None,
            source_file_audit_id=src_audit or None,
        )

    # --- HITL write side (M4 new) ----------------------------------------
    def upsert_precedent(
        self,
        *,
        ref: VendorProductRef,
        node: TaxonomyNode,
        decision: str,
        decided_by: str,
        confidence: float,
        provisional: bool = False,
        decided_at_ms=None,
    ) -> None:
        """Atomic precedent write.

        Implemented as ONE Cypher statement per branch so the graph is never
        observed mid-mutation by another reader. The branches differ only in
        which prior edges are cleared:

          approve/override -> wipe ALL existing MAPPED_TO from this
                              VendorProduct (single-positive-precedent
                              invariant; an override implicitly supersedes
                              any prior approval), and clear any negative
                              precedent on the new target.
          reject           -> wipe any MAPPED_TO on the same (v, t) (so a
                              prior approval for THIS node is reversed) and
                              leave other positive precedents untouched.

        VendorProduct labels are written ON CREATE only so a HITL fallback
        ref (where name=product_id) cannot overwrite a richer prior row.
        """
        d = (decision or "").strip().lower()
        if d not in {"approve", "override", "reject"}:
            raise ValueError(
                f"decision must be 'approve' | 'override' | 'reject', got {decision!r}"
            )

        # Denormalise the vendor signature onto VendorProduct so
        # rank_signals_for can derive the count by GROUP-BY without
        # round-tripping or computing it server-side. Signature is set ON
        # CREATE only — same discipline as the label-bearing fields, so a
        # HITL fallback ref (where name=product_id) cannot stomp a richer
        # prior row's name and therefore cannot drift its signature either.
        signature = self.vendor_signature(ref.vendor, ref.name, ref.product_id)

        params = {
            "v": ref.iri, "t": node.iri,
            "vendor": ref.vendor, "pid": ref.product_id,
            "name": ref.name, "desc": ref.description,
            "sig": signature,
            "label": node.label or "",
            "decision": d, "by": decided_by,
            "conf": float(confidence), "prov": bool(provisional),
            "at": int(decided_at_ms) if decided_at_ms is not None else None,
            # M8 federated-audit fields persisted on the edge. None on the
            # mock / inline paths — Cypher stores null cleanly and the read
            # path treats null as "no upstream provenance for this decision".
            "src_hash": ref.source_content_hash,
            "src_audit": ref.source_file_audit_id,
        }
        # Preserve the bundle's original timestamp on import, or use the
        # backend's "now" for live HITL decisions. timestamp() returns
        # epoch ms on FalkorDB/RedisGraph (the seam contract for decided_at_ms).
        at_expr = "$at" if decided_at_ms is not None else "timestamp()"

        if d == "reject":
            cypher = (
                # MERGE both endpoints; label-bearing fields on CREATE only.
                "MERGE (v:VendorProduct {iri:$v}) "
                "ON CREATE SET v.vendor=$vendor, v.product_id=$pid, "
                "              v.name=$name, v.description=$desc, "
                "              v.signature=$sig "
                "WITH v "
                "MERGE (t:TaxonomyNode {iri:$t}) "
                "ON CREATE SET t.label=$label "
                # Clear any prior positive precedent for THIS exact target.
                "WITH v, t "
                "OPTIONAL MATCH (v)-[r_pos:MAPPED_TO]->(t) DELETE r_pos "
                # Record (or upsert) the negative precedent.
                "WITH v, t "
                "MERGE (v)-[r:NOT_MAPPED_TO]->(t) "
                "SET r.decision=$decision, r.decided_by=$by, r.confidence=$conf, "
                f"    r.provisional=$prov, r.decided_at={at_expr}, "
                "    r.source_content_hash=$src_hash, "
                "    r.source_file_audit_id=$src_audit"
            )
        else:  # approve / override
            cypher = (
                "MERGE (v:VendorProduct {iri:$v}) "
                "ON CREATE SET v.vendor=$vendor, v.product_id=$pid, "
                "              v.name=$name, v.description=$desc, "
                "              v.signature=$sig "
                "WITH v "
                "MERGE (t:TaxonomyNode {iri:$t}) "
                "ON CREATE SET t.label=$label "
                # SINGLE-POSITIVE-PRECEDENT INVARIANT: wipe ALL existing
                # MAPPED_TO from this VendorProduct so an override to a new
                # node does not leave a stale approval to the old one. The
                # rank signal is derived from these very edges, so this
                # delete IS the rank-signal decrement for the old target.
                "WITH v, t "
                "OPTIONAL MATCH (v)-[r_old:MAPPED_TO]->() DELETE r_old "
                # Clear any prior negative on the chosen target (the human
                # has now affirmatively chosen it).
                "WITH v, t "
                "OPTIONAL MATCH (v)-[r_neg:NOT_MAPPED_TO]->(t) DELETE r_neg "
                # MERGE the new positive precedent — also IS the rank-signal
                # increment for the new target. Step "write precedent" and
                # step "update signal" are the same step; nothing to crash
                # between (closes findings #1 + #2).
                "WITH v, t "
                "MERGE (v)-[r:MAPPED_TO]->(t) "
                "SET r.decision=$decision, r.decided_by=$by, r.confidence=$conf, "
                f"    r.provisional=$prov, r.decided_at={at_expr}, "
                "    r.source_content_hash=$src_hash, "
                "    r.source_file_audit_id=$src_audit"
            )
        self._g.query(cypher, params)

    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        # DERIVED from CONFIRMED MAPPED_TO edges grouped by v.signature.
        # No stored counter; the count IS the number of distinct
        # VendorProducts whose signature matches and that have a confirmed
        # edge to the node. Provisional edges are excluded (I5).
        rows = self._ro(
            "MATCH (v:VendorProduct {signature:$sig})-[r:MAPPED_TO]->(t:TaxonomyNode) "
            "WHERE COALESCE(r.provisional, false) = false "
            "RETURN t.iri, count(DISTINCT v) AS c",
            {"sig": vendor_signature},
        )
        return {iri: int(c or 0) for iri, c in rows if iri}

    def get_negative_precedents(self, vendor: str, product_id: str) -> list[str]:
        rows = self._ro(
            "MATCH (:VendorProduct {vendor:$vendor, product_id:$pid})"
            "-[:NOT_MAPPED_TO]->(t:TaxonomyNode) "
            "RETURN t.iri",
            {"vendor": vendor, "pid": product_id},
        )
        return [r[0] for r in rows if r and r[0]]

    # --- M6 — bundle export surface --------------------------------------
    def list_confirmed_precedents(self) -> list[dict]:
        rows = self._ro(
            "MATCH (v:VendorProduct)-[r:MAPPED_TO]->(t:TaxonomyNode) "
            "WHERE COALESCE(r.provisional, false) = false "
            "RETURN v.vendor, v.product_id, v.name, v.description, "
            "       t.iri, t.label, "
            "       r.decision, r.decided_by, r.decided_at, r.confidence, "
            "       r.source_content_hash, r.source_file_audit_id "
            "ORDER BY v.vendor, v.product_id"
        )
        out: list[dict] = []
        for row in rows:
            (vendor, pid, name, desc, t_iri, t_label,
             decision, by, at, conf, src_hash, src_audit) = row
            out.append({
                "vendor": vendor or "",
                "product_id": pid or "",
                "product_name": name or "",
                "description": desc or "",
                "mapped_node_iri": t_iri or "",
                "mapped_node_label": t_label or "",
                "decision": (decision or "").strip().lower(),
                "decided_by": by or "",
                "decided_at_ms": int(at) if at is not None else 0,
                "confidence": float(conf) if conf is not None else 1.0,
                "source_content_hash": src_hash or None,
                "source_file_audit_id": src_audit or None,
            })
        return out

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        rows = self._ro(
            "MATCH (t:TaxonomyNode) "
            "RETURN t.iri, t.label, t.parent_iri "
            "ORDER BY t.iri"
        )
        return [
            TaxonomyNode(iri=iri, label=label or "",
                         parent_iri=(parent or None))
            for iri, label, parent in rows if iri
        ]
