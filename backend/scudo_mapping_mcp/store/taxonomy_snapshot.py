"""Immutable, revision-stamped sparse indexes for a complete taxonomy."""

from __future__ import annotations

import os
import threading
import weakref
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, Sequence

import numpy as np
from pydantic import ConfigDict, Field
from scipy.sparse import csr_matrix

from ..models import TaxonomyNode

_DEFAULT_MAX_RELATIONS_PER_NODE = 25


class ImmutableTaxonomyNode(TaxonomyNode):
    """A TaxonomyNode value that cannot be mutated after publication."""

    model_config = ConfigDict(frozen=True)
    children_iris: tuple[str, ...] = Field(default_factory=tuple)
    alt_labels: tuple[str, ...] = Field(default_factory=tuple)
    superclass_iris: tuple[str, ...] = Field(default_factory=tuple)
    superproperty_iris: tuple[str, ...] = Field(default_factory=tuple)


def public_taxonomy_node(node: ImmutableTaxonomyNode) -> TaxonomyNode:
    """Materialize a mutable boundary value without serializing tuple storage."""

    payload = {
        field_name: getattr(node, field_name)
        for field_name in TaxonomyNode.model_fields
    }
    for field_name in (
        "children_iris",
        "alt_labels",
        "superclass_iris",
        "superproperty_iris",
    ):
        payload[field_name] = list(payload[field_name])
    return TaxonomyNode.model_validate(payload)


Direction = Literal["upward", "downward", "undirected"]
_SPARSE_INDEXES: weakref.WeakKeyDictionary[
    TypedHierarchyIndex,
    Mapping[Direction, csr_matrix],
] = weakref.WeakKeyDictionary()
_SPARSE_INDEXES_LOCK = threading.Lock()


@dataclass(frozen=True, eq=False)
class TypedHierarchyIndex:
    """Immutable query surface over private sparse adjacency matrices."""

    iris: tuple[str, ...]
    iri_to_index: Mapping[str, int]
    upward_neighbors: Mapping[str, tuple[str, ...]]
    downward_neighbors: Mapping[str, tuple[str, ...]]
    undirected_neighbors: Mapping[str, tuple[str, ...]]

    def neighbors(self, iri: str, *, direction: Direction) -> tuple[str, ...]:
        """Return deterministic adjacent IRIs without exposing sparse storage."""

        adjacency = {
            "upward": self.upward_neighbors,
            "downward": self.downward_neighbors,
            "undirected": self.undirected_neighbors,
        }.get(direction)
        if adjacency is None:
            raise ValueError(f"unknown hierarchy direction {direction!r}")
        return adjacency.get(iri, ())

    def matvec(
        self,
        values: Sequence[int | float],
        *,
        direction: Direction,
    ) -> np.ndarray:
        """Return a defensive dense result for advisory graph calculations."""

        if len(values) != len(self.iris):
            raise ValueError("hierarchy vector length does not match index")
        with _SPARSE_INDEXES_LOCK:
            matrix = _SPARSE_INDEXES[self].get(direction)
        if matrix is None:
            raise ValueError(f"unknown hierarchy direction {direction!r}")
        return np.asarray(matrix @ np.asarray(values)).copy()


@dataclass(frozen=True)
class TaxonomySnapshot:
    """One complete immutable taxonomy revision."""

    revision: int
    iris: tuple[str, ...]
    nodes: Mapping[str, ImmutableTaxonomyNode]
    class_concept: TypedHierarchyIndex
    property: TypedHierarchyIndex
    class_concept_parent: TypedHierarchyIndex
    property_parent: TypedHierarchyIndex


def _operational_relation_limit() -> int:
    raw = os.getenv(
        "SCUDO_SCIPY_MAX_RELATIONS_PER_NODE",
        str(_DEFAULT_MAX_RELATIONS_PER_NODE),
    )
    value = int(raw)
    if value < 1:
        raise ValueError("SCUDO_SCIPY_MAX_RELATIONS_PER_NODE must be positive")
    return value


def _freeze_csr(matrix: csr_matrix) -> csr_matrix:
    matrix.sort_indices()
    matrix.data.flags.writeable = False
    matrix.indices.flags.writeable = False
    matrix.indptr.flags.writeable = False
    return matrix


def _typed_index(
    iris: Sequence[str],
    parent_edges: set[tuple[str, str]],
) -> TypedHierarchyIndex:
    ordered = tuple(iris)
    index = {iri: position for position, iri in enumerate(ordered)}
    rows = [index[child] for child, parent in sorted(parent_edges)]
    columns = [index[parent] for child, parent in sorted(parent_edges)]
    data = np.ones(len(rows), dtype=np.uint8)
    upward = csr_matrix(
        (data, (rows, columns)),
        shape=(len(ordered), len(ordered)),
        dtype=np.uint8,
    )
    downward = upward.transpose().tocsr()
    undirected = (upward + downward).tocsr()
    matrices = {
        "upward": _freeze_csr(upward),
        "downward": _freeze_csr(downward),
        "undirected": _freeze_csr(undirected),
    }

    def adjacency(matrix: csr_matrix) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType(
            {
                iri: tuple(
                    ordered[int(position)] for position in matrix.getrow(row).indices
                )
                for row, iri in enumerate(ordered)
            }
        )

    typed_index = TypedHierarchyIndex(
        iris=ordered,
        iri_to_index=MappingProxyType(index),
        upward_neighbors=adjacency(matrices["upward"]),
        downward_neighbors=adjacency(matrices["downward"]),
        undirected_neighbors=adjacency(matrices["undirected"]),
    )
    with _SPARSE_INDEXES_LOCK:
        _SPARSE_INDEXES[typed_index] = MappingProxyType(matrices)
    return typed_index


def build_taxonomy_snapshot(
    nodes: Sequence[TaxonomyNode],
    *,
    revision: int,
    max_relations_per_node: int | None = None,
) -> TaxonomySnapshot:
    """Validate and build an uncapped complete-taxonomy snapshot."""

    relation_limit = (
        _operational_relation_limit()
        if max_relations_per_node is None
        else max_relations_per_node
    )
    by_iri: dict[str, TaxonomyNode] = {}
    for source in nodes:
        iri = source.iri.strip()
        if not iri:
            raise ValueError("taxonomy IRI must be nonempty")
        if iri in by_iri:
            raise ValueError(f"taxonomy contains duplicate IRI {iri!r}")
        source_relations = (
            source.superclass_iris,
            source.superproperty_iris,
        )
        if any(len(values) > relation_limit for values in source_relations):
            raise ValueError(f"taxonomy relation cardinality exceeds {relation_limit}")
        by_iri[iri] = source.model_copy(deep=True, update={"iri": iri})

    hierarchy = {
        iri: "property" if node.node_kind == "property" else "class_concept"
        for iri, node in by_iri.items()
    }
    parents: dict[str, set[str]] = {iri: set() for iri in by_iri}
    superclass: dict[str, set[str]] = {iri: set() for iri in by_iri}
    superproperty: dict[str, set[str]] = {iri: set() for iri in by_iri}

    def add(
        child: str,
        parent: str,
        *,
        expected: str,
        relation: str,
        target: dict[str, set[str]],
    ) -> None:
        parent = parent.strip()
        if parent not in by_iri:
            raise ValueError(f"taxonomy missing reference {parent!r} from {child!r}")
        if child == parent:
            raise ValueError(f"taxonomy self-loop at {child!r}")
        if hierarchy[child] != expected or hierarchy[parent] != expected:
            raise ValueError(
                f"taxonomy typed relation {relation!r} crosses hierarchy boundary"
            )
        target[child].add(parent)

    for iri, node in by_iri.items():
        expected = hierarchy[iri]
        if node.parent_iri:
            add(
                iri,
                node.parent_iri,
                expected=expected,
                relation="parent",
                target=parents,
            )
        for child in node.children_iris:
            child = child.strip()
            if child not in by_iri:
                raise ValueError(
                    f"taxonomy missing child reference {child!r} from {iri!r}"
                )
            add(
                child,
                iri,
                expected=expected,
                relation="child",
                target=parents,
            )
        for parent in node.superclass_iris:
            add(
                iri,
                parent,
                expected="class_concept",
                relation="superclass",
                target=superclass,
            )
        for parent in node.superproperty_iris:
            add(
                iri,
                parent,
                expected="property",
                relation="superproperty",
                target=superproperty,
            )

    for iri in sorted(parents):
        if len(parents[iri]) > 1:
            raise ValueError(
                f"taxonomy parent relation cardinality exceeds one for {iri!r}"
            )

    directed = {
        iri: parents[iri] | superclass[iri] | superproperty[iri] for iri in by_iri
    }
    dependants: dict[str, set[str]] = {iri: set() for iri in by_iri}
    remaining_parents = {
        iri: len(node_parents) for iri, node_parents in directed.items()
    }
    for child, node_parents in directed.items():
        for parent in node_parents:
            dependants[parent].add(child)
    ready = sorted(iri for iri, count in remaining_parents.items() if count == 0)
    processed = 0
    while ready:
        iri = ready.pop()
        processed += 1
        for child in sorted(dependants[iri], reverse=True):
            remaining_parents[child] -= 1
            if remaining_parents[child] == 0:
                ready.append(child)
    if processed != len(by_iri):
        cycle_iri = min(iri for iri, count in remaining_parents.items() if count)
        raise ValueError(f"taxonomy cycle includes {cycle_iri!r}")

    children: dict[str, set[str]] = {iri: set() for iri in by_iri}
    for child, node_parents in parents.items():
        for parent in node_parents:
            children[parent].add(child)

    immutable: dict[str, ImmutableTaxonomyNode] = {}
    for iri in sorted(by_iri):
        payload = by_iri[iri].model_dump()
        payload.update(
            {
                "parent_iri": next(iter(parents[iri])) if parents[iri] else None,
                "children_iris": tuple(sorted(children[iri])),
                "alt_labels": tuple(by_iri[iri].alt_labels),
                "superclass_iris": tuple(sorted(superclass[iri])),
                "superproperty_iris": tuple(sorted(superproperty[iri])),
            }
        )
        immutable[iri] = ImmutableTaxonomyNode.model_validate(payload)

    class_iris = tuple(
        iri for iri in sorted(immutable) if hierarchy[iri] == "class_concept"
    )
    property_iris = tuple(
        iri for iri in sorted(immutable) if hierarchy[iri] == "property"
    )
    class_edges = {
        (child, parent)
        for child in class_iris
        for parent in parents[child] | superclass[child]
    }
    property_edges = {
        (child, parent)
        for child in property_iris
        for parent in parents[child] | superproperty[child]
    }
    class_parent_edges = {
        (child, parent) for child in class_iris for parent in parents[child]
    }
    property_parent_edges = {
        (child, parent) for child in property_iris for parent in parents[child]
    }
    return TaxonomySnapshot(
        revision=int(revision),
        iris=tuple(sorted(immutable)),
        nodes=MappingProxyType(immutable),
        class_concept=_typed_index(class_iris, class_edges),
        property=_typed_index(property_iris, property_edges),
        class_concept_parent=_typed_index(class_iris, class_parent_edges),
        property_parent=_typed_index(property_iris, property_parent_edges),
    )
