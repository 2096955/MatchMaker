"""Durable single-host RetrievalStore backed by native SQLite."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .. import opus_dense, retrieval
from ..config import env_use_opus_dense
from ..models import (
    Candidate,
    ConceptualEdge,
    ConceptualGraph,
    ConceptualNode,
    MappingResult,
    MappingStatus,
    Subgraph,
    TaxonomyNode,
    VendorProductRef,
)
from .base import CandidateFilter, RetrievalStore
from .retrieval_scoring import score_candidates
from .scipy_sqlite_schema import (
    initialize_database,
    read_connection,
    schema_is_valid,
    write_transaction,
)
from .snapshot_manager import SnapshotManager
from .taxonomy_snapshot import (
    ImmutableTaxonomyNode,
    TaxonomySnapshot,
    build_taxonomy_snapshot,
    public_taxonomy_node,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _vendor_key(vendor: str) -> str:
    return (vendor or "").strip().lower()


class ScipySQLiteStore(RetrievalStore):
    """Complete operation-local SQLite implementation of the store seam."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._closed = False
        self._taxonomy_write_lock = threading.Lock()
        initialize_database(self._path)
        self._snapshot_manager = SnapshotManager(
            self._read_taxonomy_revision,
            self._load_taxonomy_revision,
        )

    def _read_taxonomy_revision(self) -> int:
        with read_connection(self._path) as conn:
            row = conn.execute(
                "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
            ).fetchone()
        return int(row[0])

    def _load_taxonomy_revision(self) -> tuple[int, list[TaxonomyNode]]:
        with read_connection(self._path) as conn:
            conn.execute("BEGIN")
            revision = int(
                conn.execute(
                    "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
                ).fetchone()[0]
            )
            nodes = self._nodes_from_connection(conn)
            conn.commit()
        return revision, nodes

    @classmethod
    def _nodes_from_connection(cls, conn) -> list[TaxonomyNode]:
        rows = conn.execute(
            "SELECT iri, payload_json FROM taxonomy_nodes WHERE active=1 ORDER BY iri"
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT e.from_iri, e.to_iri, e.edge_kind "
            "FROM taxonomy_edges e "
            "JOIN taxonomy_nodes source ON source.iri=e.from_iri "
            "JOIN taxonomy_nodes target ON target.iri=e.to_iri "
            "WHERE source.active=1 AND target.active=1 "
            "ORDER BY e.edge_kind, e.from_iri, e.to_iri"
        ).fetchall()
        parents: dict[str, list[str]] = {}
        children: dict[str, list[str]] = {}
        superclasses: dict[str, list[str]] = {}
        superproperties: dict[str, list[str]] = {}
        for edge in edge_rows:
            if edge["edge_kind"] == "parent":
                parents.setdefault(edge["to_iri"], []).append(edge["from_iri"])
                children.setdefault(edge["from_iri"], []).append(edge["to_iri"])
            elif edge["edge_kind"] == "superclass":
                superclasses.setdefault(edge["to_iri"], []).append(edge["from_iri"])
            elif edge["edge_kind"] == "superproperty":
                superproperties.setdefault(edge["to_iri"], []).append(edge["from_iri"])
        nodes = []
        for row in rows:
            node = TaxonomyNode.model_validate_json(row["payload_json"])
            node_parents = parents.get(node.iri, [])
            nodes.append(
                node.model_copy(
                    update={
                        "parent_iri": node_parents[0] if node_parents else None,
                        "children_iris": children.get(node.iri, []),
                        "superclass_iris": superclasses.get(node.iri, []),
                        "superproperty_iris": superproperties.get(node.iri, []),
                    }
                )
            )
        return nodes

    def health(self) -> bool:
        if self._closed:
            return False
        try:
            if not self.storage_ready():
                return False
            snapshot = self._snapshot_manager.capture()
            return bool(snapshot.iris) and (
                snapshot.revision == self._read_taxonomy_revision()
            )
        except Exception:
            return False

    def storage_ready(self) -> bool:
        """Return schema/storage liveness without requiring taxonomy data."""

        if self._closed:
            return False
        try:
            with read_connection(self._path) as conn:
                return conn.execute("PRAGMA quick_check").fetchone()[
                    0
                ] == "ok" and schema_is_valid(conn)
        except Exception:
            return False

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ScipySQLiteStore is closed")

    def replace_taxonomy(self, nodes: list[TaxonomyNode]) -> None:
        self._ensure_open()
        if not nodes:
            raise ValueError("empty taxonomy replacement is not allowed")
        with self._taxonomy_write_lock:
            self._replace_taxonomy_locked(nodes)

    def _replace_taxonomy_locked(self, nodes: list[TaxonomyNode]) -> None:
        committed: TaxonomySnapshot
        with write_transaction(self._path) as conn:
            next_revision = (
                int(
                    conn.execute(
                        "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
                    ).fetchone()[0]
                )
                + 1
            )
            committed = build_taxonomy_snapshot(nodes, revision=next_revision)
            ordered = [committed.nodes[iri] for iri in committed.iris]
            for node in ordered:
                self._write_taxonomy_node(conn, node)
            keep = {node.iri for node in ordered}
            if keep:
                placeholders = ",".join("?" for _ in keep)
                conn.execute(
                    f"UPDATE taxonomy_nodes SET active=0 "
                    f"WHERE active=1 AND iri NOT IN ({placeholders})",
                    tuple(sorted(keep)),
                )
            else:
                conn.execute("UPDATE taxonomy_nodes SET active=0 WHERE active=1")
            conn.execute(
                "UPDATE taxonomy_nodes SET active=1, last_seen_revision=? "
                "WHERE iri IN (SELECT iri FROM taxonomy_nodes WHERE active=1)",
                (next_revision,),
            )
            self._rebuild_taxonomy_edges(conn)
            conn.execute(
                "UPDATE store_metadata SET value=CAST(value AS INTEGER)+1 "
                "WHERE key='taxonomy_revision'"
            )
        self._publish_or_refresh(committed, required_iri=None)

    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
        """Safely merge one node in O(N+E); bulk seeds use replace_taxonomy."""

        self._ensure_open()
        with self._taxonomy_write_lock:
            self._upsert_taxonomy_node_locked(node)

    def _upsert_taxonomy_node_locked(self, node: TaxonomyNode) -> None:
        committed: TaxonomySnapshot
        with write_transaction(self._path) as conn:
            current = {
                item.iri: item.model_copy(update={"children_iris": []})
                for item in self._nodes_from_connection(conn)
            }
            current[node.iri] = node
            next_revision = (
                int(
                    conn.execute(
                        "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
                    ).fetchone()[0]
                )
                + 1
            )
            committed = build_taxonomy_snapshot(
                list(current.values()),
                revision=next_revision,
            )
            ordered = [committed.nodes[iri] for iri in committed.iris]
            for item in ordered:
                self._write_taxonomy_node(conn, item)
            self._rebuild_taxonomy_edges(conn)
            conn.execute(
                "UPDATE store_metadata SET value=CAST(value AS INTEGER)+1 "
                "WHERE key='taxonomy_revision'"
            )
        self._publish_or_refresh(committed, required_iri=node.iri)

    def _publish_or_refresh(
        self,
        committed: TaxonomySnapshot,
        *,
        required_iri: str | None,
    ) -> None:
        """Publish own commit or refresh if a later writer already advanced."""

        durable_revision = self._read_taxonomy_revision()
        if durable_revision == committed.revision:
            try:
                self._snapshot_manager.publish(committed)
            except ValueError:
                pass
        self._snapshot_manager.capture()

    @staticmethod
    def _write_taxonomy_node(conn, node: TaxonomyNode) -> None:
        payload_json = (
            public_taxonomy_node(node).model_dump_json()
            if isinstance(node, ImmutableTaxonomyNode)
            else node.model_dump_json()
        )
        revision = (
            int(
                conn.execute(
                    "SELECT value FROM store_metadata WHERE key='taxonomy_revision'"
                ).fetchone()[0]
            )
            + 1
        )
        conn.execute(
            "INSERT INTO taxonomy_nodes "
            "(iri, label, definition, node_kind, parent_iri, business_concept, "
            "asset_class, super_asset_class, temporal_coverage, payload_json, "
            "active, last_seen_revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(iri) DO UPDATE SET label=excluded.label, "
            "definition=excluded.definition, node_kind=excluded.node_kind, "
            "parent_iri=excluded.parent_iri, "
            "business_concept=excluded.business_concept, "
            "asset_class=excluded.asset_class, "
            "super_asset_class=excluded.super_asset_class, "
            "temporal_coverage=excluded.temporal_coverage, "
            "payload_json=excluded.payload_json, active=1, "
            "last_seen_revision=excluded.last_seen_revision",
            (
                node.iri,
                node.label,
                node.definition,
                node.node_kind,
                node.parent_iri,
                node.business_concept,
                node.asset_class,
                node.super_asset_class,
                node.temporal_coverage,
                payload_json,
                revision,
            ),
        )

    @staticmethod
    def _rebuild_taxonomy_edges(conn) -> None:
        conn.execute("DELETE FROM taxonomy_edges")
        rows = conn.execute(
            "SELECT iri, payload_json FROM taxonomy_nodes WHERE active=1 ORDER BY iri"
        ).fetchall()
        edges: set[tuple[str, str, str]] = set()
        for row in rows:
            node = TaxonomyNode.model_validate_json(row["payload_json"])
            if node.parent_iri:
                edges.add((node.parent_iri, node.iri, "parent"))
            for parent in node.superclass_iris:
                edges.add((parent, node.iri, "superclass"))
            for parent in node.superproperty_iris:
                edges.add((parent, node.iri, "superproperty"))
        conn.executemany(
            "INSERT INTO taxonomy_edges(from_iri, to_iri, edge_kind) VALUES (?, ?, ?)",
            sorted(edges),
        )

    def upsert_vendor_product(self, ref: VendorProductRef) -> None:
        self._ensure_open()
        vendor = _vendor_key(ref.vendor)
        signature = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        with write_transaction(self._path) as conn:
            conn.execute(
                "INSERT INTO vendor_products "
                "(vendor, product_id, iri, name, description, raw_json, "
                "vendor_signature, source_content_hash, source_file_audit_id, "
                "temporal_coverage, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(vendor, product_id) DO UPDATE SET iri=excluded.iri, "
                "name=excluded.name, description=excluded.description, "
                "raw_json=excluded.raw_json, "
                "vendor_signature=excluded.vendor_signature, "
                "source_content_hash=excluded.source_content_hash, "
                "source_file_audit_id=excluded.source_file_audit_id, "
                "temporal_coverage=excluded.temporal_coverage, "
                "payload_json=excluded.payload_json",
                (
                    vendor,
                    ref.product_id,
                    ref.iri,
                    ref.name,
                    ref.description,
                    _json(ref.raw),
                    signature,
                    ref.source_content_hash,
                    ref.source_file_audit_id,
                    ref.temporal_coverage,
                    ref.model_dump_json(),
                ),
            )

    def upsert_precedent(
        self,
        *,
        ref: VendorProductRef,
        node: TaxonomyNode,
        decision: str,
        decided_by: str,
        confidence: float,
        provisional: bool = False,
        decided_at_ms: Optional[int] = None,
    ) -> None:
        self._ensure_open()
        decision = decision.strip().lower()
        if decision not in {"approve", "override", "reject"}:
            raise ValueError("decision must be approve, override, or reject")
        vendor = _vendor_key(ref.vendor)
        at_ms = (
            int(decided_at_ms) if decided_at_ms is not None else int(time.time() * 1000)
        )
        with write_transaction(self._path) as conn:
            self._require_active_taxonomy_node(conn, node.iri)
            self._write_vendor_product_in_transaction(conn, ref)
            if decision == "reject":
                conn.execute(
                    "INSERT INTO negative_precedents "
                    "(vendor, product_id, node_iri, decided_by, decided_at_ms, "
                    "confidence, source_content_hash, source_file_audit_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(vendor, product_id, node_iri) DO UPDATE SET "
                    "decided_by=excluded.decided_by, "
                    "decided_at_ms=excluded.decided_at_ms, "
                    "confidence=excluded.confidence, "
                    "source_content_hash=excluded.source_content_hash, "
                    "source_file_audit_id=excluded.source_file_audit_id",
                    (
                        vendor,
                        ref.product_id,
                        node.iri,
                        decided_by,
                        at_ms,
                        confidence,
                        ref.source_content_hash,
                        ref.source_file_audit_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM positive_precedents "
                    "WHERE vendor=? AND product_id=? AND node_iri=?",
                    (vendor, ref.product_id, node.iri),
                )
                return
            conn.execute(
                "DELETE FROM negative_precedents "
                "WHERE vendor=? AND product_id=? AND node_iri=?",
                (vendor, ref.product_id, node.iri),
            )
            conn.execute(
                "INSERT INTO positive_precedents "
                "(vendor, product_id, node_iri, decision, decided_by, "
                "decided_at_ms, confidence, provisional, source_content_hash, "
                "source_file_audit_id, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(vendor, product_id) DO UPDATE SET "
                "node_iri=excluded.node_iri, decision=excluded.decision, "
                "decided_by=excluded.decided_by, "
                "decided_at_ms=excluded.decided_at_ms, "
                "confidence=excluded.confidence, "
                "provisional=excluded.provisional, "
                "source_content_hash=excluded.source_content_hash, "
                "source_file_audit_id=excluded.source_file_audit_id, "
                "description=excluded.description",
                (
                    vendor,
                    ref.product_id,
                    node.iri,
                    decision,
                    decided_by,
                    at_ms,
                    confidence,
                    int(provisional),
                    ref.source_content_hash,
                    ref.source_file_audit_id,
                    ref.description,
                ),
            )

    @staticmethod
    def _require_active_taxonomy_node(conn, node_iri: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM taxonomy_nodes WHERE iri=? AND active=1",
            (node_iri,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"decision target {node_iri!r} is not an active taxonomy node"
            )

    def _write_vendor_product_in_transaction(self, conn, ref: VendorProductRef) -> None:
        vendor = _vendor_key(ref.vendor)
        conn.execute(
            "INSERT INTO vendor_products "
            "(vendor, product_id, iri, name, description, raw_json, "
            "vendor_signature, source_content_hash, source_file_audit_id, "
            "temporal_coverage, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vendor, product_id) DO NOTHING",
            (
                vendor,
                ref.product_id,
                ref.iri,
                ref.name,
                ref.description,
                _json(ref.raw),
                self.vendor_signature(ref.vendor, ref.name, ref.product_id),
                ref.source_content_hash,
                ref.source_file_audit_id,
                ref.temporal_coverage,
                ref.model_dump_json(),
            ),
        )

    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        self._ensure_open()
        with read_connection(self._path) as conn:
            rows = conn.execute(
                "SELECT p.node_iri, COUNT(DISTINCT p.vendor || char(0) || "
                "p.product_id) AS approval_count "
                "FROM positive_precedents p JOIN vendor_products v "
                "ON v.vendor=p.vendor AND v.product_id=p.product_id "
                "WHERE p.provisional=0 AND v.vendor_signature=? "
                "GROUP BY p.node_iri ORDER BY p.node_iri",
                (vendor_signature,),
            ).fetchall()
        return {row["node_iri"]: int(row["approval_count"]) for row in rows}

    def get_negative_precedents(self, vendor: str, product_id: str) -> list[str]:
        self._ensure_open()
        with read_connection(self._path) as conn:
            rows = conn.execute(
                "SELECT node_iri FROM negative_precedents "
                "WHERE vendor=? AND product_id=? ORDER BY node_iri",
                (_vendor_key(vendor), product_id),
            ).fetchall()
        return [row["node_iri"] for row in rows]

    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        self._ensure_open()
        node = self._snapshot_manager.capture().nodes.get(node_iri)
        return public_taxonomy_node(node) if node is not None else None

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        self._ensure_open()
        snapshot = self._snapshot_manager.capture()
        return [public_taxonomy_node(snapshot.nodes[iri]) for iri in snapshot.iris]

    def taxonomy_size(self) -> int:
        """Use the current immutable snapshot without copying every node."""
        self._ensure_open()
        return len(self._snapshot_manager.capture().iris)

    @staticmethod
    def _node_from_row(conn, row) -> TaxonomyNode:
        node = TaxonomyNode.model_validate_json(row["payload_json"])
        edges = conn.execute(
            "SELECT from_iri, to_iri, edge_kind FROM taxonomy_edges "
            "WHERE from_iri=? OR to_iri=? ORDER BY edge_kind, from_iri, to_iri",
            (node.iri, node.iri),
        ).fetchall()
        parent_edges = [edge for edge in edges if edge["edge_kind"] == "parent"]
        parents = sorted(
            edge["from_iri"] for edge in parent_edges if edge["to_iri"] == node.iri
        )
        children = sorted(
            edge["to_iri"] for edge in parent_edges if edge["from_iri"] == node.iri
        )
        superclasses = sorted(
            edge["from_iri"]
            for edge in edges
            if edge["edge_kind"] == "superclass" and edge["to_iri"] == node.iri
        )
        superproperties = sorted(
            edge["from_iri"]
            for edge in edges
            if edge["edge_kind"] == "superproperty" and edge["to_iri"] == node.iri
        )
        return node.model_copy(
            update={
                "parent_iri": parents[0] if parents else None,
                "children_iris": children,
                "superclass_iris": superclasses,
                "superproperty_iris": superproperties,
            }
        )

    def find_similar_products(
        self,
        ref: VendorProductRef,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter: Optional[CandidateFilter] = None,
    ) -> list[Candidate]:
        self._ensure_open()
        if env_use_opus_dense():
            scorer = opus_dense.make_opus_dense_scorer(
                query_desc=ref.description or "",
            )
            return retrieval.multi_path_retrieve(
                ref,
                self,
                max_results,
                min_similarity,
                candidate_filter=candidate_filter,
                dense_scorer=scorer,
            )
        snapshot = self._snapshot_manager.capture()
        if not snapshot.iris:
            return []
        rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))
        boosts = self.rank_signals_for(
            self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        )
        candidates = score_candidates(
            store=self,
            ref=ref,
            nodes=[snapshot.nodes[iri] for iri in snapshot.iris],
            rejected_iris=rejected,
            boosts=boosts,
            max_results=max_results,
            min_similarity=min_similarity,
            candidate_filter=candidate_filter,
            dense_scorer=opus_dense.opus_dense_score,
        )
        return [
            Candidate(
                node=public_taxonomy_node(candidate.node),
                similarity=candidate.similarity,
            )
            for candidate in candidates
        ]

    def get_ontology_neighbourhood(
        self, node_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> Subgraph:
        self._ensure_open()
        depth_cap = self.clamp_depth(max_depth)
        node_cap = self.clamp_nodes(max_nodes)
        snapshot = self._snapshot_manager.capture()
        root = snapshot.nodes.get(node_iri)
        if root is None:
            return Subgraph(root_iri=node_iri)
        typed = (
            snapshot.property_parent
            if root.node_kind == "property"
            else snapshot.class_concept_parent
        )
        seen = {node_iri}
        frontier = [node_iri]
        edges: set[tuple[str, str]] = set()
        for _depth in range(depth_cap):
            next_frontier: list[str] = []
            for current in sorted(frontier):
                for child in typed.neighbors(current, direction="downward"):
                    if len(seen) >= node_cap and child not in seen:
                        continue
                    edges.add((current, child))
                    if child not in seen:
                        seen.add(child)
                        next_frontier.append(child)
            frontier = next_frontier
            if not frontier or len(seen) >= node_cap:
                break
        nodes = [public_taxonomy_node(snapshot.nodes[iri]) for iri in sorted(seen)]
        return Subgraph(
            root_iri=node_iri,
            nodes=nodes,
            edges=sorted(edges),
        )

    def get_precedent_mapping(
        self, vendor: str, product_id: str
    ) -> Optional[MappingResult]:
        self._ensure_open()
        with read_connection(self._path) as conn:
            row = conn.execute(
                "SELECT v.iri, v.payload_json, p.node_iri, t.label, p.decision, "
                "p.confidence, p.source_content_hash, p.source_file_audit_id "
                "FROM positive_precedents p JOIN vendor_products v "
                "ON v.vendor=p.vendor AND v.product_id=p.product_id "
                "JOIN taxonomy_nodes t ON t.iri=p.node_iri "
                "WHERE p.vendor=? AND p.product_id=? AND p.provisional=0 "
                "AND t.active=1",
                (_vendor_key(vendor), product_id),
            ).fetchone()
        if row is None:
            return None
        ref = VendorProductRef.model_validate_json(row["payload_json"])
        status = (
            MappingStatus.OVERRIDDEN
            if row["decision"] == "override"
            else MappingStatus.APPROVED
        )
        return MappingResult(
            vendor_product_iri=row["iri"],
            vendor=ref.vendor,
            product_id=ref.product_id,
            product_name=ref.name,
            mapped_node_iri=row["node_iri"],
            mapped_node_label=row["label"],
            confidence=row["confidence"],
            status=status,
            rationale="precedent",
            source_content_hash=row["source_content_hash"],
            source_file_audit_id=row["source_file_audit_id"],
        )

    def list_confirmed_precedents(self) -> list[dict]:
        self._ensure_open()
        with read_connection(self._path) as conn:
            rows = conn.execute(
                "SELECT v.payload_json, p.node_iri, t.label, p.decision, "
                "p.decided_by, p.decided_at_ms, p.confidence, p.description, "
                "p.source_content_hash, p.source_file_audit_id "
                "FROM positive_precedents p JOIN vendor_products v "
                "ON v.vendor=p.vendor AND v.product_id=p.product_id "
                "JOIN taxonomy_nodes t ON t.iri=p.node_iri "
                "WHERE p.provisional=0 ORDER BY p.vendor, p.product_id"
            ).fetchall()
        out = []
        for row in rows:
            ref = VendorProductRef.model_validate_json(row["payload_json"])
            out.append(
                {
                    "vendor": ref.vendor,
                    "product_id": ref.product_id,
                    "product_name": ref.name,
                    "description": row["description"],
                    "mapped_node_iri": row["node_iri"],
                    "mapped_node_label": row["label"],
                    "decision": row["decision"],
                    "decided_by": row["decided_by"],
                    "decided_at_ms": int(row["decided_at_ms"]),
                    "confidence": float(row["confidence"]),
                    "source_content_hash": row["source_content_hash"],
                    "source_file_audit_id": row["source_file_audit_id"],
                }
            )
        return out

    def upsert_conceptual_node(self, node: ConceptualNode) -> None:
        self._ensure_open()
        with write_transaction(self._path) as conn:
            self._require_active_taxonomy_node(
                conn,
                node.attaches_to_concept_iri,
            )
            conn.execute(
                "INSERT INTO conceptual_nodes "
                "(iri, concept_iri, kind, label, payload_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(iri) DO UPDATE SET concept_iri=excluded.concept_iri, "
                "kind=excluded.kind, label=excluded.label, "
                "payload_json=excluded.payload_json",
                (
                    node.iri,
                    node.attaches_to_concept_iri,
                    node.kind.value,
                    node.label,
                    node.model_dump_json(),
                ),
            )

    def upsert_conceptual_edge(self, edge: ConceptualEdge) -> None:
        self._ensure_open()
        with write_transaction(self._path) as conn:
            conn.execute(
                "INSERT INTO conceptual_edges "
                "(from_iri, to_iri, kind, label, payload_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_iri, to_iri, kind) DO UPDATE SET "
                "label=excluded.label, payload_json=excluded.payload_json",
                (
                    edge.from_iri,
                    edge.to_iri,
                    edge.kind.value,
                    edge.label,
                    edge.model_dump_json(),
                ),
            )

    def get_conceptual_graph(
        self, concept_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> ConceptualGraph:
        self._ensure_open()
        node_cap = self.clamp_nodes(max_nodes)
        self.clamp_depth(max_depth)
        with read_connection(self._path) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM conceptual_nodes "
                "WHERE concept_iri=? ORDER BY iri LIMIT ?",
                (concept_iri, node_cap),
            ).fetchall()
            nodes = [ConceptualNode.model_validate_json(row[0]) for row in rows]
            if not nodes:
                return ConceptualGraph(root_concept_iri=concept_iri)
            iris = {node.iri for node in nodes}
            placeholders = ",".join("?" for _ in iris)
            edge_rows = conn.execute(
                f"SELECT payload_json FROM conceptual_edges "
                f"WHERE from_iri IN ({placeholders}) AND to_iri IN ({placeholders}) "
                f"ORDER BY from_iri, to_iri, kind",
                (*sorted(iris), *sorted(iris)),
            ).fetchall()
        return ConceptualGraph(
            root_concept_iri=concept_iri,
            nodes=nodes,
            edges=[ConceptualEdge.model_validate_json(row[0]) for row in edge_rows],
        )
