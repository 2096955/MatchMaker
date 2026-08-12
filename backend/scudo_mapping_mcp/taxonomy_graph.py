"""Deterministic bounded SciPy evidence over typed taxonomy hierarchies.

The analyzer is read-only and score-blind. Invalid topology is reported, but
all usable structural evidence is withheld so callers cannot reason from a
known-defective graph.
"""

from __future__ import annotations

from collections import deque
from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, Field
from scipy import sparse
from scipy.sparse import csgraph

from .taxonomy_graph_models import TaxonomyNode

HierarchyType = Literal["class_concept", "property"]
RelationType = Literal["parent", "child", "superclass", "superproperty"]

ABSOLUTE_MAX_NODES = 100
MAX_IRI_LIST_ITEMS = 25
MAX_IRI_LENGTH = 512
DEFAULT_MAX_NODES = 100
DEFAULT_MAX_DEPTH = 8
PAGERANK_DAMPING = 0.85
PAGERANK_TOLERANCE = 1e-10
PAGERANK_MAX_ITERATIONS = 200
MAX_RELATION_ITEMS = 25
MAX_DIRECTED_EDGES = 500
MAX_DIAGNOSTICS = 100


class TaxonomyGraphBoundError(ValueError):
    """The requested graph operation exceeds a hard safety bound."""


class TaxonomyGraphInputError(ValueError):
    """An analyzer boundary argument is malformed or ambiguous."""


class TopologyIssue(BaseModel):
    source_iri: str
    relation: RelationType
    target_iri: str
    detail: str


class CyclicStrongComponent(BaseModel):
    hierarchy_type: HierarchyType
    node_iris: list[str]


class TopologyDiagnostics(BaseModel):
    missing_references: list[TopologyIssue] = Field(default_factory=list)
    asymmetric_declarations: list[TopologyIssue] = Field(default_factory=list)
    cyclic_sccs: list[CyclicStrongComponent] = Field(default_factory=list)
    diagnostics_truncated: bool = False


class AnchorPathEvidence(BaseModel):
    anchor_iri: str
    compatible_hierarchy: bool
    distance: Optional[int] = None
    path_iris: list[str] = Field(default_factory=list)
    within_max_depth: bool = False
    ancestry_truncated: bool = False
    explanation: Optional[str] = None


class CandidateGraphEvidence(BaseModel):
    candidate_iri: str
    present: bool
    hierarchy_type: Optional[HierarchyType] = None
    degree: Optional[int] = None
    child_count: Optional[int] = None
    branch_ambiguity: Optional[bool] = None
    component_id: Optional[int] = None
    component_size: Optional[int] = None
    orphan: Optional[bool] = None
    anchor_paths: list[AnchorPathEvidence] = Field(default_factory=list)


class CandidatePairEvidence(BaseModel):
    first_candidate_iri: str
    second_candidate_iri: str
    compatible_hierarchy: bool
    hierarchy_type: Optional[HierarchyType] = None
    lowest_common_ancestor_iris: list[str] = Field(default_factory=list)
    selected_lowest_common_ancestor_iri: Optional[str] = None
    first_to_lca_distance: Optional[int] = None
    second_to_lca_distance: Optional[int] = None
    separation: Optional[int] = None
    same_component: Optional[bool] = None
    ancestry_truncated: bool = False
    explanation: Optional[str] = None

    @property
    def lowest_common_ancestor_iri(self) -> Optional[str]:
        """Compatibility alias for the deterministic selected LCA."""
        return self.selected_lowest_common_ancestor_iri


class AffinityScore(BaseModel):
    candidate_iri: str
    score: float


class NodeAffinityScore(BaseModel):
    iri: str
    score: float


class PrecedentAffinityEvidence(BaseModel):
    hierarchy_type: HierarchyType
    confirmed_anchor_iris: list[str]
    candidates: list[AffinityScore]
    all_nodes: list[NodeAffinityScore]
    tolerance: float
    iterations: int


class TaxonomyGraphEvidence(BaseModel):
    node_count: int
    index_iris: list[str]
    evidence_valid: bool
    diagnostics: TopologyDiagnostics
    component_count: Optional[int] = None
    orphan_iris: list[str] = Field(default_factory=list)
    candidates: list[CandidateGraphEvidence] = Field(default_factory=list)
    candidate_pairs: list[CandidatePairEvidence] = Field(default_factory=list)
    affinity_converged: Optional[bool] = None
    affinity: Optional[PrecedentAffinityEvidence] = None


class _TypedGraph:
    hierarchy_type: HierarchyType
    iris: list[str]
    index: dict[str, int]
    parents: dict[str, set[str]]
    children: dict[str, set[str]]
    undirected: dict[str, set[str]]
    matrix: sparse.csr_matrix
    component_count: int
    component_labels: np.ndarray
    component_sizes: dict[int, int]

    def __init__(
        self,
        hierarchy_type: HierarchyType,
        iris: list[str],
        parents: dict[str, set[str]],
        children: dict[str, set[str]],
    ) -> None:
        self.hierarchy_type = hierarchy_type
        self.iris = iris
        self.index = {iri: position for position, iri in enumerate(iris)}
        self.parents = parents
        self.children = children
        self.undirected = {iri: set(parents[iri]) | set(children[iri]) for iri in iris}
        rows = [
            self.index[source]
            for source in iris
            for _target in sorted(self.undirected[source])
        ]
        columns = [
            self.index[target]
            for source in iris
            for target in sorted(self.undirected[source])
        ]
        self.matrix = sparse.csr_matrix(
            (np.ones(len(rows), dtype=np.float64), (rows, columns)),
            shape=(len(iris), len(iris)),
        )
        if iris:
            self.component_count, self.component_labels = csgraph.connected_components(
                self.matrix, directed=False, return_labels=True
            )
        else:
            self.component_count = 0
            self.component_labels = np.array([], dtype=int)
        self.component_sizes = {
            component: int(np.count_nonzero(self.component_labels == component))
            for component in range(self.component_count)
        }


def _validated_iri_list(name: str, values: Optional[list[str]]) -> list[str]:
    raw = values or []
    if len(raw) > MAX_IRI_LIST_ITEMS:
        raise TaxonomyGraphInputError(
            f"{name} accepts at most {MAX_IRI_LIST_ITEMS} IRIs"
        )
    normalized: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise TaxonomyGraphInputError(f"{name} IRIs must be strings")
        iri = value.strip()
        if not iri:
            raise TaxonomyGraphInputError(f"{name} IRIs must be nonempty")
        if len(iri) > MAX_IRI_LENGTH:
            raise TaxonomyGraphInputError(
                f"{name} IRIs must be at most {MAX_IRI_LENGTH} characters"
            )
        normalized.append(iri)
    if len(set(normalized)) != len(normalized):
        raise TaxonomyGraphInputError(
            f"{name} IRIs must be unique after whitespace normalization"
        )
    return normalized


def _hierarchy_type(node: TaxonomyNode) -> HierarchyType:
    return "property" if node.node_kind == "property" else "class_concept"


def _issue(
    source: str, relation: RelationType, target: str, detail: str
) -> TopologyIssue:
    return TopologyIssue(
        source_iri=source,
        relation=relation,
        target_iri=target,
        detail=detail,
    )


def _cyclic_sccs(
    hierarchy_type: HierarchyType,
    iris: list[str],
    directed_edges: set[tuple[str, str]],
) -> list[CyclicStrongComponent]:
    if not iris:
        return []
    index = {iri: position for position, iri in enumerate(iris)}
    rows = [index[source] for source, _target in sorted(directed_edges)]
    columns = [index[target] for _source, target in sorted(directed_edges)]
    matrix = sparse.csr_matrix(
        (np.ones(len(rows), dtype=np.int8), (rows, columns)),
        shape=(len(iris), len(iris)),
    )
    count, labels = csgraph.connected_components(
        matrix, directed=True, connection="strong", return_labels=True
    )
    self_loops = {source for source, target in directed_edges if source == target}
    result = []
    for component in range(count):
        members = sorted(iri for iri in iris if int(labels[index[iri]]) == component)
        if len(members) > 1 or any(iri in self_loops for iri in members):
            result.append(
                CyclicStrongComponent(hierarchy_type=hierarchy_type, node_iris=members)
            )
    return sorted(result, key=lambda item: (item.hierarchy_type, item.node_iris))


def _shortest_path(
    adjacency: dict[str, set[str]], start: str, target: str, max_depth: int
) -> list[str]:
    if start == target:
        return [start]
    queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
    seen = {start}
    while queue:
        iri, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for neighbour in sorted(adjacency[iri]):
            if neighbour in seen:
                continue
            next_path = path + [neighbour]
            if neighbour == target:
                return next_path
            seen.add(neighbour)
            queue.append((neighbour, next_path))
    return []


def _ancestor_distances(
    parents: dict[str, set[str]], start: str, max_depth: int
) -> dict[str, int]:
    distances = {start: 0}
    queue = deque([start])
    while queue:
        iri = queue.popleft()
        if distances[iri] >= max_depth:
            continue
        for parent in sorted(parents[iri]):
            candidate_distance = distances[iri] + 1
            if parent not in distances or candidate_distance < distances[parent]:
                distances[parent] = candidate_distance
                queue.append(parent)
    return distances


def _is_ancestor(parents: dict[str, set[str]], ancestor: str, descendant: str) -> bool:
    queue = deque([descendant])
    seen = {descendant}
    while queue:
        current = queue.popleft()
        for parent in sorted(parents[current]):
            if parent == ancestor:
                return True
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    return False


def _lowest_common_ancestors(
    parents: dict[str, set[str]],
    first: str,
    second: str,
) -> tuple[list[str], dict[str, int], dict[str, int]]:
    full_depth = len(parents)
    first_distances = _ancestor_distances(parents, first, full_depth)
    second_distances = _ancestor_distances(parents, second, full_depth)
    common = set(first_distances) & set(second_distances)
    lowest = sorted(
        candidate
        for candidate in common
        if not any(
            candidate != other and _is_ancestor(parents, candidate, other)
            for other in common
        )
    )
    return lowest, first_distances, second_distances


def _pagerank(
    graph: _TypedGraph,
    candidate_iris: list[str],
    anchor_iris: list[str],
    tolerance: float,
    max_iterations: int,
) -> tuple[bool, Optional[PrecedentAffinityEvidence]]:
    seed = np.zeros(len(graph.iris), dtype=np.float64)
    for iri in anchor_iris:
        seed[graph.index[iri]] = 1.0 / len(anchor_iris)
    rank = seed.copy()
    degrees = np.asarray(graph.matrix.sum(axis=1)).ravel()
    inverse = np.divide(
        1.0,
        degrees,
        out=np.zeros_like(degrees),
        where=degrees != 0,
    )
    transition = sparse.diags(inverse) @ graph.matrix
    dangling = degrees == 0
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        dangling_mass = float(rank[dangling].sum())
        next_rank = (
            (1.0 - PAGERANK_DAMPING) * seed
            + PAGERANK_DAMPING * (transition.T @ rank)
            + PAGERANK_DAMPING * dangling_mass * seed
        )
        if np.linalg.norm(next_rank - rank, ord=1) <= tolerance:
            rank = next_rank
            converged = True
            break
        rank = next_rank
    if not converged:
        return False, None
    total = float(rank.sum())
    if total <= 0.0:
        return False, None
    rank /= total
    return True, PrecedentAffinityEvidence(
        hierarchy_type=graph.hierarchy_type,
        confirmed_anchor_iris=anchor_iris,
        candidates=[
            AffinityScore(
                candidate_iri=iri,
                score=round(float(rank[graph.index[iri]]), 15),
            )
            for iri in candidate_iris
            if iri in graph.index
        ],
        all_nodes=[
            NodeAffinityScore(iri=iri, score=round(float(rank[graph.index[iri]]), 15))
            for iri in graph.iris
        ],
        tolerance=tolerance,
        iterations=iterations,
    )


def analyse_taxonomy(
    nodes: list[TaxonomyNode],
    *,
    candidate_iris: list[str],
    anchor_iris: Optional[list[str]] = None,
    confirmed_precedent_iris: Optional[list[str]] = None,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    affinity_tolerance: float = PAGERANK_TOLERANCE,
    affinity_max_iterations: int = PAGERANK_MAX_ITERATIONS,
) -> TaxonomyGraphEvidence:
    """Return typed structural evidence or diagnostics-only on invalid topology."""
    candidates = _validated_iri_list("candidate_iris", candidate_iris)
    anchors = _validated_iri_list("anchor_iris", anchor_iris)
    precedents = _validated_iri_list(
        "confirmed_precedent_iris", confirmed_precedent_iris
    )
    if max_nodes < 1 or max_nodes > ABSOLUTE_MAX_NODES:
        raise TaxonomyGraphBoundError(
            f"max_nodes must be between 1 and {ABSOLUTE_MAX_NODES}"
        )
    if len(nodes) > max_nodes:
        raise TaxonomyGraphBoundError(
            f"taxonomy snapshot with {len(nodes)} nodes exceeds max_nodes={max_nodes}"
        )
    if max_depth < 1 or max_depth > ABSOLUTE_MAX_NODES:
        raise TaxonomyGraphBoundError(
            f"max_depth must be between 1 and {ABSOLUTE_MAX_NODES}"
        )
    if not 0.0 < affinity_tolerance <= 1.0:
        raise TaxonomyGraphInputError(
            "affinity_tolerance must be greater than 0 and at most 1"
        )
    if affinity_max_iterations < 1 or affinity_max_iterations > 10_000:
        raise TaxonomyGraphBoundError(
            "affinity_max_iterations must be between 1 and 10000"
        )

    by_iri: dict[str, TaxonomyNode] = {}
    declared_edge_count = 0
    for node in nodes:
        iri = node.iri.strip()
        if not iri or len(iri) > MAX_IRI_LENGTH:
            raise TaxonomyGraphInputError(
                "taxonomy node IRIs must be nonempty and at most 512 characters"
            )
        if iri in by_iri:
            raise TaxonomyGraphInputError(
                "taxonomy snapshot contains duplicate normalized IRIs"
            )
        by_iri[iri] = node
        relation_lists = (
            node.children_iris,
            node.superclass_iris,
            node.superproperty_iris,
        )
        if any(len(values) > MAX_RELATION_ITEMS for values in relation_lists):
            raise TaxonomyGraphBoundError(
                f"each relationship list is bounded to {MAX_RELATION_ITEMS} items"
            )
        declared_edge_count += int(bool(node.parent_iri))
        declared_edge_count += len(node.superclass_iris)
        declared_edge_count += len(node.superproperty_iris)
    if declared_edge_count > MAX_DIRECTED_EDGES:
        raise TaxonomyGraphBoundError(
            f"normalized directed edges exceed {MAX_DIRECTED_EDGES}"
        )
    index_iris = sorted(by_iri)
    types = {iri: _hierarchy_type(node) for iri, node in by_iri.items()}
    typed_iris = {
        hierarchy_type: sorted(
            iri for iri in index_iris if types[iri] == hierarchy_type
        )
        for hierarchy_type in ("class_concept", "property")
    }
    parents = {
        hierarchy_type: {iri: set() for iri in typed_iris[hierarchy_type]}
        for hierarchy_type in typed_iris
    }
    children = {
        hierarchy_type: {iri: set() for iri in typed_iris[hierarchy_type]}
        for hierarchy_type in typed_iris
    }
    directed_edges: dict[HierarchyType, set[tuple[str, str]]] = {
        "class_concept": set(),
        "property": set(),
    }
    missing: list[TopologyIssue] = []
    asymmetric: list[TopologyIssue] = []

    def add_edge(
        source: str,
        relation: RelationType,
        target_raw: str,
        expected_type: HierarchyType,
    ) -> bool:
        target = target_raw.strip()
        if target not in by_iri:
            missing.append(
                _issue(
                    source,
                    relation,
                    target,
                    "referenced IRI is absent from the snapshot",
                )
            )
            return False
        if types[source] != expected_type or types[target] != expected_type:
            missing.append(
                _issue(
                    source,
                    relation,
                    target,
                    f"relation crosses typed hierarchy boundary; expected {expected_type}",
                )
            )
            return False
        child, parent = (target, source) if relation == "child" else (source, target)
        parents[expected_type][child].add(parent)
        children[expected_type][parent].add(child)
        directed_edges[expected_type].add((child, parent))
        return True

    for iri in index_iris:
        node = by_iri[iri]
        hierarchy_type = types[iri]
        if hierarchy_type == "class_concept":
            if node.superproperty_iris:
                for target in node.superproperty_iris:
                    add_edge(iri, "superproperty", target, "property")
            if node.parent_iri:
                add_edge(iri, "parent", node.parent_iri, "class_concept")
            for child_raw in node.children_iris:
                child = child_raw.strip()
                if child not in by_iri:
                    missing.append(
                        _issue(
                            iri,
                            "child",
                            child,
                            "reverse child IRI is absent from the snapshot",
                        )
                    )
                elif types[child] != "class_concept":
                    missing.append(
                        _issue(
                            iri,
                            "child",
                            child,
                            "reverse child crosses typed hierarchy boundary",
                        )
                    )
                else:
                    child_node = by_iri[child]
                    reciprocal = (
                        child_node.parent_iri and child_node.parent_iri.strip() == iri
                    ) or iri in {value.strip() for value in child_node.superclass_iris}
                    if not reciprocal:
                        asymmetric.append(
                            _issue(
                                iri,
                                "child",
                                child,
                                "child does not reciprocally declare this parent or superclass",
                            )
                        )
            for superclass_raw in node.superclass_iris:
                superclass = superclass_raw.strip()
                add_edge(iri, "superclass", superclass, "class_concept")
        else:
            if node.parent_iri:
                add_edge(iri, "parent", node.parent_iri, "property")
            for child_raw in node.children_iris:
                child = child_raw.strip()
                if child not in by_iri:
                    missing.append(
                        _issue(
                            iri,
                            "child",
                            child,
                            "reverse child IRI is absent from the snapshot",
                        )
                    )
                elif types[child] != "property":
                    missing.append(
                        _issue(
                            iri,
                            "child",
                            child,
                            "reverse child crosses typed hierarchy boundary",
                        )
                    )
                else:
                    child_node = by_iri[child]
                    reciprocal = (
                        child_node.parent_iri and child_node.parent_iri.strip() == iri
                    ) or iri in {
                        value.strip() for value in child_node.superproperty_iris
                    }
                    if not reciprocal:
                        asymmetric.append(
                            _issue(
                                iri,
                                "child",
                                child,
                                "property child has no canonical parent or superproperty declaration",
                            )
                        )
            for superclass in node.superclass_iris:
                add_edge(iri, "superclass", superclass, "class_concept")
            for superproperty in node.superproperty_iris:
                add_edge(iri, "superproperty", superproperty, "property")

    cyclic_sccs = [
        component
        for hierarchy_type in ("class_concept", "property")
        for component in _cyclic_sccs(
            hierarchy_type,
            typed_iris[hierarchy_type],
            directed_edges[hierarchy_type],
        )
    ]
    sorted_missing = sorted(
        missing,
        key=lambda item: (item.source_iri, item.relation, item.target_iri),
    )
    sorted_asymmetric = sorted(
        asymmetric,
        key=lambda item: (item.source_iri, item.relation, item.target_iri),
    )
    all_diagnostics_count = (
        len(sorted_missing) + len(sorted_asymmetric) + len(cyclic_sccs)
    )
    diagnostics = TopologyDiagnostics(
        missing_references=sorted_missing[:MAX_DIAGNOSTICS],
        asymmetric_declarations=sorted_asymmetric[
            : max(0, MAX_DIAGNOSTICS - len(sorted_missing[:MAX_DIAGNOSTICS]))
        ],
        cyclic_sccs=cyclic_sccs[
            : max(
                0,
                MAX_DIAGNOSTICS
                - len(sorted_missing[:MAX_DIAGNOSTICS])
                - len(
                    sorted_asymmetric[
                        : max(
                            0,
                            MAX_DIAGNOSTICS - len(sorted_missing[:MAX_DIAGNOSTICS]),
                        )
                    ]
                ),
            )
        ],
        diagnostics_truncated=all_diagnostics_count > MAX_DIAGNOSTICS,
    )
    evidence_valid = not (
        diagnostics.missing_references
        or diagnostics.asymmetric_declarations
        or diagnostics.cyclic_sccs
    )
    if not evidence_valid:
        return TaxonomyGraphEvidence(
            node_count=len(index_iris),
            index_iris=index_iris,
            evidence_valid=False,
            diagnostics=diagnostics,
        )

    graphs: dict[HierarchyType, _TypedGraph] = {
        hierarchy_type: _TypedGraph(
            hierarchy_type,
            typed_iris[hierarchy_type],
            parents[hierarchy_type],
            children[hierarchy_type],
        )
        for hierarchy_type in ("class_concept", "property")
    }
    component_offsets = {
        "class_concept": 0,
        "property": graphs["class_concept"].component_count,
    }
    component_count = sum(graph.component_count for graph in graphs.values())
    orphan_iris = sorted(
        iri
        for graph in graphs.values()
        for iri in graph.iris
        if not graph.undirected[iri]
    )
    candidate_evidence = []
    for candidate in candidates:
        if candidate not in by_iri:
            candidate_evidence.append(
                CandidateGraphEvidence(candidate_iri=candidate, present=False)
            )
            continue
        hierarchy_type = types[candidate]
        graph = graphs[hierarchy_type]
        candidate_paths = []
        for anchor in anchors:
            compatible = anchor in by_iri and types[anchor] == hierarchy_type
            path = (
                _shortest_path(graph.undirected, candidate, anchor, max_depth)
                if compatible
                else []
            )
            full_path = (
                _shortest_path(graph.undirected, candidate, anchor, len(graph.iris))
                if compatible
                else []
            )
            ancestry_truncated = bool(full_path and not path)
            candidate_paths.append(
                AnchorPathEvidence(
                    anchor_iri=anchor,
                    compatible_hierarchy=compatible,
                    distance=len(path) - 1 if path else None,
                    path_iris=path,
                    within_max_depth=bool(path),
                    ancestry_truncated=ancestry_truncated,
                    explanation=(
                        f"path exists beyond max_depth={max_depth}"
                        if ancestry_truncated
                        else (
                            "anchor belongs to an incompatible hierarchy"
                            if not compatible
                            else None
                        )
                    ),
                )
            )
        local_component = int(graph.component_labels[graph.index[candidate]])
        candidate_evidence.append(
            CandidateGraphEvidence(
                candidate_iri=candidate,
                present=True,
                hierarchy_type=hierarchy_type,
                degree=len(graph.undirected[candidate]),
                child_count=len(graph.children[candidate]),
                branch_ambiguity=len(graph.children[candidate]) > 1,
                component_id=component_offsets[hierarchy_type] + local_component,
                component_size=graph.component_sizes[local_component],
                orphan=not graph.undirected[candidate],
                anchor_paths=candidate_paths,
            )
        )

    candidate_pairs = []
    present_candidates = [iri for iri in candidates if iri in by_iri]
    for first_index, first in enumerate(present_candidates):
        for second in present_candidates[first_index + 1 :]:
            if types[first] != types[second]:
                candidate_pairs.append(
                    CandidatePairEvidence(
                        first_candidate_iri=first,
                        second_candidate_iri=second,
                        compatible_hierarchy=False,
                    )
                )
                continue
            hierarchy_type = types[first]
            graph = graphs[hierarchy_type]
            lowest, first_distances, second_distances = _lowest_common_ancestors(
                graph.parents, first, second
            )
            selected = (
                min(
                    lowest,
                    key=lambda iri: (
                        first_distances[iri] + second_distances[iri],
                        max(first_distances[iri], second_distances[iri]),
                        iri,
                    ),
                )
                if lowest
                else None
            )
            first_distance = first_distances[selected] if selected else None
            second_distance = second_distances[selected] if selected else None
            candidate_pairs.append(
                CandidatePairEvidence(
                    first_candidate_iri=first,
                    second_candidate_iri=second,
                    compatible_hierarchy=True,
                    hierarchy_type=hierarchy_type,
                    lowest_common_ancestor_iris=lowest,
                    selected_lowest_common_ancestor_iri=selected,
                    first_to_lca_distance=first_distance,
                    second_to_lca_distance=second_distance,
                    separation=(
                        first_distance + second_distance
                        if first_distance is not None and second_distance is not None
                        else None
                    ),
                    same_component=(
                        bool(
                            graph.component_labels[graph.index[first]]
                            == graph.component_labels[graph.index[second]]
                        )
                    ),
                    ancestry_truncated=False,
                    explanation=None,
                )
            )

    affinity_converged: Optional[bool] = None
    affinity = None
    confirmed = [iri for iri in precedents if iri in by_iri]
    if confirmed:
        precedent_types = {types[iri] for iri in confirmed}
        if len(precedent_types) == 1:
            hierarchy_type = next(iter(precedent_types))
            compatible_candidates = [
                iri
                for iri in candidates
                if iri in by_iri and types[iri] == hierarchy_type
            ]
            affinity_converged, affinity = _pagerank(
                graphs[hierarchy_type],
                compatible_candidates,
                sorted(confirmed),
                affinity_tolerance,
                affinity_max_iterations,
            )
        else:
            affinity_converged = False

    return TaxonomyGraphEvidence(
        node_count=len(index_iris),
        index_iris=index_iris,
        evidence_valid=True,
        diagnostics=diagnostics,
        component_count=component_count,
        orphan_iris=orphan_iris,
        candidates=candidate_evidence,
        candidate_pairs=candidate_pairs,
        affinity_converged=affinity_converged,
        affinity=affinity,
    )


__all__ = [
    "ABSOLUTE_MAX_NODES",
    "TaxonomyGraphBoundError",
    "TaxonomyGraphEvidence",
    "TaxonomyGraphInputError",
    "analyse_taxonomy",
]
