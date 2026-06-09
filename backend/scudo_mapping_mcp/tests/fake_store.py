"""
In-memory implementation of the RetrievalStore seam, for tests.

Faithfully mirrors the FalkorDB backend's semantics — clamping, rank-signal
boost, provisional-edge exclusion, negative precedents — so the matcher's
behaviour can be exercised without a FalkorDB container. Real-store behaviour
(I/O, transactions, ro_query) is out of scope; correctness of the contract is in.
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    Candidate,
    MappingResult,
    MappingStatus,
    Subgraph,
    TaxonomyNode,
    VendorProductRef,
)
from ..store.base import RetrievalStore


class FakeStore(RetrievalStore):
    def __init__(self, nodes: Optional[list[TaxonomyNode]] = None) -> None:
        self._nodes: dict[str, TaxonomyNode] = {n.iri: n for n in (nodes or [])}
        self._scores: dict[str, float] = {}
        # (vendor, product_id) -> MappingResult (confirmed positive precedent).
        # Single-positive-precedent invariant: at most one entry per pair.
        self._precedents: dict[tuple[str, str], MappingResult] = {}
        # Same key, holds PROVISIONAL precedents. Stored separately so the
        # filter in get_precedent_mapping is what hides them — not a missing
        # write — keeping the FakeStore faithful to the FalkorDB
        # store-and-filter semantics (the smoke test asserts the filter).
        self._provisional: dict[tuple[str, str], MappingResult] = {}
        # (vendor, product_id) -> set of rejected node IRIs.
        self._negatives: dict[tuple[str, str], set[str]] = {}
        # Audit metadata for confirmed positive precedents — kept separately
        # because MappingResult doesn't carry decided_by / decided_at /
        # description / source_*. Keyed identically to ``_precedents`` so
        # M6's ``list_confirmed_precedents`` has the full edge to reconstitute.
        # Each value:
        #   {"decision": str, "decided_by": str,
        #    "decided_at_ms": int, "description": str,
        #    "source_content_hash": Optional[str],
        #    "source_file_audit_id": Optional[str]}
        # NOTE: vendor_signature is NOT stored here — it lives in
        # ``self._vp_signatures`` keyed by (vendor, product_id), set ON
        # CREATE / overwritten by ingest, and ``rank_signals_for`` reads
        # from THAT dict (not this one).
        self._precedent_meta: dict[tuple[str, str], dict] = {}
        # Monotonic counter substituting for wall-clock time — tests must
        # not call time.time()/Date.now() so the FakeStore stays
        # deterministic and replay-safe.
        self._now_counter: int = 1_700_000_000_000  # seed: ~2023-11-15Z in ms
        # ON CREATE-only signature per VendorProduct, mirroring the
        # FalkorDB property write. Survives reject so it remains the
        # canonical key for rank_signals_for derivation.
        self._vp_signatures: dict[tuple[str, str], str] = {}

    def _fake_now_ms(self) -> int:
        self._now_counter += 1
        return self._now_counter

    # --- test helpers ---------------------------------------------------
    def set_score(self, node_iri: str, score: float) -> None:
        self._scores[node_iri] = score

    # --- lifecycle ------------------------------------------------------
    def health(self) -> bool:
        return True

    def close(self) -> None:
        pass

    # --- write side (seed) ----------------------------------------------
    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
        self._nodes[node.iri] = node

    def upsert_vendor_product(self, ref: VendorProductRef) -> None:
        # Mirror FalkorDB's signature-write: ingest is authoritative for the
        # VendorProduct's label, so the signature follows current name on
        # every upsert. Without this, ingest-then-approve would derive a
        # rank of 0 because rank_signals_for filters on _vp_signatures —
        # exactly the production bug r2 finding #1 surfaced.
        key = (ref.vendor, ref.product_id)
        self._vp_signatures[key] = self.vendor_signature(
            ref.vendor, ref.name, ref.product_id,
        )

    # --- HITL write side ------------------------------------------------
    def upsert_precedent(self, *, ref, node, decision, decided_by,
                         confidence, provisional=False, decided_at_ms=None):
        key = (ref.vendor, ref.product_id)
        d = decision.strip().lower()
        # Stamp the edge timestamp (epoch ms) so list_confirmed_precedents
        # and the M6 bundle export carry it through to provenance.
        at_ms = int(decided_at_ms) if decided_at_ms is not None else \
            self._fake_now_ms()
        # ON CREATE: capture the VendorProduct's signature once, mirroring
        # FalkorDB's `ON CREATE SET v.signature=$sig`. Subsequent upserts
        # for the same (vendor, product_id) keep the first signature so
        # rank_signals_for derivation is stable under HITL fallback refs
        # whose name is the product_id.
        if key not in self._vp_signatures:
            self._vp_signatures[key] = self.vendor_signature(
                ref.vendor, ref.name, ref.product_id,
            )
        if d == "reject":
            self._negatives.setdefault(key, set()).add(node.iri)
            # Reject only undoes a positive precedent on THIS node, mirroring
            # FalkorDB's `(v)-[r_pos:MAPPED_TO]->(t) DELETE r_pos` step.
            cur = self._precedents.get(key)
            if cur is not None and cur.mapped_node_iri == node.iri:
                self._precedents.pop(key, None)
                self._precedent_meta.pop(key, None)
            self._provisional.pop(key, None)
            return

        status = (
            MappingStatus.OVERRIDDEN if d == "override" else MappingStatus.APPROVED
        )
        result = MappingResult(
            vendor_product_iri=ref.iri, vendor=ref.vendor,
            product_id=ref.product_id, product_name=ref.name,
            mapped_node_iri=node.iri, mapped_node_label=node.label,
            confidence=confidence, status=status,
            rationale=f"precedent: HITL '{d}' by {decided_by}",
        )
        if provisional:
            # Store-and-filter, exactly like FalkorDB. get_precedent_mapping
            # ignores this dict so an unattended auto_mapped run never
            # short-circuits a future call (I5).
            self._provisional[key] = result
            return

        # Approve / override:
        # SINGLE-POSITIVE-PRECEDENT INVARIANT — the new positive overwrites
        # any prior positive on a different node (an override implicitly
        # supersedes the earlier approval). Clear any negative on the new
        # target so the human's affirmative choice takes effect.
        if key in self._negatives:
            self._negatives[key].discard(node.iri)
        self._precedents[key] = result
        self._precedent_meta[key] = {
            "decision": d,
            "decided_by": decided_by,
            "decided_at_ms": at_ms,
            "description": ref.description or "",
            # M8 federated-audit fields, persisted on the precedent.
            "source_content_hash": ref.source_content_hash,
            "source_file_audit_id": ref.source_file_audit_id,
        }

    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        # DERIVED from CONFIRMED precedents, matching the FalkorDB query:
        # count = distinct VendorProducts whose signature matches and that
        # currently have a positive precedent to the node.
        out: dict[str, int] = {}
        for key, mr in self._precedents.items():
            if self._vp_signatures.get(key) != vendor_signature:
                continue
            iri = mr.mapped_node_iri
            if not iri:
                continue
            out[iri] = out.get(iri, 0) + 1
        return out

    def get_negative_precedents(self, vendor: str, product_id: str) -> list[str]:
        return sorted(self._negatives.get((vendor, product_id), set()))

    # --- M6 — bundle export surface ------------------------------------
    def list_confirmed_precedents(self) -> list[dict]:
        out: list[dict] = []
        for key in sorted(self._precedents.keys()):
            r = self._precedents[key]
            meta = self._precedent_meta.get(key, {})
            out.append({
                "vendor": r.vendor,
                "product_id": r.product_id,
                "product_name": r.product_name or "",
                "description": meta.get("description", ""),
                "mapped_node_iri": r.mapped_node_iri or "",
                "mapped_node_label": r.mapped_node_label or "",
                "decision": meta.get("decision", "approve"),
                "decided_by": meta.get("decided_by", ""),
                "decided_at_ms": int(meta.get("decided_at_ms") or 0),
                "confidence": float(r.confidence or 0.0),
                # M8 federated-audit fields — carried through the bundle so
                # an importer in another env can replay the decision-time
                # source identity onto the new edge.
                "source_content_hash": meta.get("source_content_hash"),
                "source_file_audit_id": meta.get("source_file_audit_id"),
            })
        return out

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        return [self._nodes[iri] for iri in sorted(self._nodes)]

    # --- read side ------------------------------------------------------
    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        return self._nodes.get(node_iri)

    def find_similar_products(self, ref, max_results=10, min_similarity=0.0):
        limit = self.clamp_results(max_results)
        sig = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        boosts = self.rank_signals_for(sig)
        # Same separation as FalkorDBStore: boost tilts SORT only;
        # Candidate.similarity stays the raw oracle score so the 0.80 floor
        # sees the unmodified value.
        scored: list[tuple[Candidate, float]] = []
        for iri, node in self._nodes.items():
            base = self._scores.get(iri, 0.5)
            if base < min_similarity:
                continue
            boost = self.compute_rank_boost(boosts, iri)
            sort_key = min(1.0, base + boost)
            scored.append((
                Candidate(node=node, similarity=round(base, 4)),
                sort_key,
            ))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [c for c, _ in scored[:limit]]

    def get_ontology_neighbourhood(self, node_iri, max_depth=2, max_nodes=50):
        _ = self.clamp_depth(max_depth), self.clamp_nodes(max_nodes)
        return Subgraph(root_iri=node_iri)

    def get_precedent_mapping(self, vendor, product_id):
        # Mirror FalkorDBStore: construct a fresh MappingResult on READ with
        # rationale="precedent" rather than returning the rationale that was
        # written at decision time. Keeps the two backends' read shapes
        # behaviourally identical so callers (the matcher) don't branch.
        stored = self._precedents.get((vendor, product_id))
        if stored is None:
            return None
        meta = self._precedent_meta.get((vendor, product_id), {})
        return MappingResult(
            vendor_product_iri=stored.vendor_product_iri,
            vendor=stored.vendor,
            product_id=stored.product_id,
            product_name=stored.product_name,
            mapped_node_iri=stored.mapped_node_iri,
            mapped_node_label=stored.mapped_node_label,
            confidence=stored.confidence,
            status=stored.status,
            rationale="precedent",
            # M8: replay the decision-time provenance back to the matcher.
            source_content_hash=meta.get("source_content_hash"),
            source_file_audit_id=meta.get("source_file_audit_id"),
        )
