from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields

import numpy as np
import pytest
from scipy.sparse import spmatrix
from pydantic import ValidationError

from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.store.snapshot_manager import SnapshotManager
from scudo_mapping_mcp.store.taxonomy_snapshot import build_taxonomy_snapshot


def _large_taxonomy(size: int = 151) -> list[TaxonomyNode]:
    return [
        TaxonomyNode(
            iri=f"cdao:{index:03d}",
            label=f"Node {index}",
            parent_iri=f"cdao:{index - 1:03d}" if index else None,
        )
        for index in range(size)
    ]


def test_snapshot_indexes_full_taxonomy_in_deterministic_iri_order():
    nodes = list(reversed(_large_taxonomy()))

    snapshot = build_taxonomy_snapshot(nodes, revision=7)

    assert snapshot.revision == 7
    assert len(snapshot.iris) == 151
    assert snapshot.iris == tuple(sorted(node.iri for node in nodes))
    assert snapshot.class_concept.iris == snapshot.iris
    assert snapshot.property.iris == ()


def test_snapshot_separates_typed_upward_downward_and_undirected_csr():
    snapshot = build_taxonomy_snapshot(
        [
            TaxonomyNode(iri="cdao:child", label="Child", parent_iri="cdao:root"),
            TaxonomyNode(iri="cdao:root", label="Root"),
            TaxonomyNode(
                iri="cdao:price",
                label="Price",
                node_kind="property",
                superproperty_iris=["cdao:property"],
            ),
            TaxonomyNode(
                iri="cdao:property",
                label="Property",
                node_kind="property",
            ),
        ],
        revision=1,
    )

    classes = snapshot.class_concept
    assert classes.neighbors("cdao:child", direction="upward") == ("cdao:root",)
    assert classes.neighbors("cdao:root", direction="downward") == ("cdao:child",)
    assert classes.neighbors("cdao:child", direction="undirected") == ("cdao:root",)
    assert classes.neighbors("cdao:root", direction="undirected") == ("cdao:child",)
    assert snapshot.property.neighbors("cdao:price", direction="upward") == (
        "cdao:property",
    )


def test_snapshot_arrays_maps_and_nodes_are_immutable():
    snapshot = build_taxonomy_snapshot(_large_taxonomy(3), revision=1)

    with pytest.raises(TypeError):
        snapshot.nodes["cdao:new"] = snapshot.nodes["cdao:000"]
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        snapshot.nodes["cdao:000"].label = "changed"
    with pytest.raises(AttributeError):
        snapshot.nodes["cdao:000"].children_iris.append("cdao:new")
    with pytest.raises(TypeError):
        snapshot.class_concept.iri_to_index["cdao:new"] = 3
    assert not hasattr(snapshot.class_concept, "upward")
    vector = snapshot.class_concept.matvec(
        [1, 0, 0],
        direction="downward",
    )
    vector.flags.writeable = True
    vector[:] = 0
    assert snapshot.class_concept.neighbors("cdao:000", direction="downward") == (
        "cdao:001",
    )


def test_public_snapshot_fields_do_not_expose_scipy_or_numpy_storage():
    snapshot = build_taxonomy_snapshot(_large_taxonomy(3), revision=1)

    for value in vars(snapshot).values():
        assert not isinstance(value, (np.ndarray, spmatrix))
    for index in (
        snapshot.class_concept,
        snapshot.property,
        snapshot.class_concept_parent,
        snapshot.property_parent,
    ):
        assert all(
            not isinstance(getattr(index, field.name), (np.ndarray, spmatrix))
            for field in fields(index)
        )
        assert not any(
            isinstance(value, (np.ndarray, spmatrix)) for value in vars(index).values()
        )

    before = index.neighbors("cdao:000", direction="downward")
    with pytest.raises((AttributeError, TypeError)):
        index.iris += ("cdao:forged",)
    assert index.neighbors("cdao:000", direction="downward") == before


def test_deep_taxonomy_chain_and_cycle_are_validated_iteratively():
    chain = _large_taxonomy(2_000)
    snapshot = build_taxonomy_snapshot(chain, revision=1)
    assert len(snapshot.iris) == 2_000

    cycle = _large_taxonomy(2_000)
    cycle[0] = cycle[0].model_copy(update={"parent_iri": cycle[-1].iri})
    with pytest.raises(ValueError, match="cycle"):
        build_taxonomy_snapshot(cycle, revision=2)


@pytest.mark.parametrize("child_count", [26, 64])
def test_derived_high_degree_children_do_not_count_as_source_relations(child_count):
    children = [
        TaxonomyNode(
            iri=f"cdao:child-{index:02d}",
            label=f"Child {index}",
            parent_iri="cdao:root",
        )
        for index in range(child_count)
    ]

    snapshot = build_taxonomy_snapshot(
        [TaxonomyNode(iri="cdao:root", label="Root"), *children],
        revision=1,
    )

    expected = tuple(sorted(node.iri for node in children))
    assert snapshot.nodes["cdao:root"].children_iris == expected
    assert (
        snapshot.class_concept_parent.neighbors(
            "cdao:root",
            direction="downward",
        )
        == expected
    )


def test_explicit_high_degree_children_are_valid_reverse_declarations():
    child_iris = [f"cdao:child-{index:02d}" for index in range(40)]
    nodes = [
        TaxonomyNode(
            iri="cdao:root",
            label="Root",
            children_iris=child_iris,
        ),
        *[
            TaxonomyNode(iri=iri, label=iri, parent_iri="cdao:root")
            for iri in child_iris
        ],
    ]

    snapshot = build_taxonomy_snapshot(nodes, revision=1)

    assert snapshot.nodes["cdao:root"].children_iris == tuple(child_iris)


def test_parent_cardinality_is_checked_after_all_child_declarations():
    nodes = [
        TaxonomyNode(iri="cdao:a", label="A", children_iris=["cdao:child"]),
        TaxonomyNode(iri="cdao:b", label="B", children_iris=["cdao:child"]),
        TaxonomyNode(iri="cdao:child", label="Child"),
    ]

    for ordered in (nodes, list(reversed(nodes))):
        with pytest.raises(
            ValueError,
            match="parent relation cardinality exceeds one for 'cdao:child'",
        ):
            build_taxonomy_snapshot(ordered, revision=1)


def test_parent_and_children_conflict_is_rejected_deterministically():
    nodes = [
        TaxonomyNode(iri="cdao:a", label="A", children_iris=["cdao:child"]),
        TaxonomyNode(iri="cdao:b", label="B"),
        TaxonomyNode(
            iri="cdao:child",
            label="Child",
            parent_iri="cdao:b",
        ),
    ]

    for ordered in (nodes, [nodes[2], nodes[0], nodes[1]]):
        with pytest.raises(
            ValueError,
            match="parent relation cardinality exceeds one for 'cdao:child'",
        ):
            build_taxonomy_snapshot(ordered, revision=1)


@pytest.mark.parametrize(
    "nodes, message",
    [
        (
            [
                TaxonomyNode(iri="cdao:a", label="A"),
                TaxonomyNode(iri="cdao:a", label="Again"),
            ],
            "duplicate",
        ),
        (
            [TaxonomyNode(iri="cdao:a", label="A", parent_iri="cdao:missing")],
            "missing",
        ),
        (
            [TaxonomyNode(iri="cdao:a", label="A", parent_iri="cdao:a")],
            "self-loop",
        ),
        (
            [
                TaxonomyNode(iri="cdao:a", label="A", parent_iri="cdao:b"),
                TaxonomyNode(iri="cdao:b", label="B", parent_iri="cdao:a"),
            ],
            "cycle",
        ),
        (
            [
                TaxonomyNode(
                    iri="cdao:a",
                    label="A",
                    parent_iri="cdao:p",
                ),
                TaxonomyNode(
                    iri="cdao:p",
                    label="P",
                    node_kind="property",
                ),
            ],
            "typed",
        ),
    ],
)
def test_snapshot_rejects_invalid_topology(nodes, message):
    with pytest.raises(ValueError, match=message):
        build_taxonomy_snapshot(nodes, revision=1)


class _RevisionSource:
    def __init__(self, nodes: list[TaxonomyNode]) -> None:
        self.revision = 1
        self.nodes = nodes
        self.loads = 0
        self.lock = threading.Lock()
        self.build_started = threading.Event()
        self.allow_build = threading.Event()
        self.block_load = False

    def read_revision(self) -> int:
        with self.lock:
            return self.revision

    def load(self) -> tuple[int, list[TaxonomyNode]]:
        with self.lock:
            revision = self.revision
            nodes = list(self.nodes)
            self.loads += 1
        if self.block_load:
            self.build_started.set()
            assert self.allow_build.wait(timeout=5)
        return revision, nodes


def test_manager_rebuild_is_single_flight_and_publication_is_atomic():
    source = _RevisionSource(_large_taxonomy(3))
    manager = SnapshotManager(source.read_revision, source.load)
    source.block_load = True

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(manager.capture) for _ in range(8)]
        assert source.build_started.wait(timeout=5)
        assert manager.status().rebuilding
        source.allow_build.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert source.loads == 1
    assert len({id(snapshot) for snapshot in snapshots}) == 1
    status = manager.status()
    assert status.ready
    assert not status.stale
    assert status.revision == 1


def test_two_managers_detect_external_revision_and_refresh():
    source = _RevisionSource(_large_taxonomy(2))
    first = SnapshotManager(source.read_revision, source.load)
    second = SnapshotManager(source.read_revision, source.load)
    assert first.capture().revision == second.capture().revision == 1

    with source.lock:
        source.nodes = _large_taxonomy(4)
        source.revision = 2

    refreshed = second.capture()
    assert refreshed.revision == 2
    assert len(refreshed.iris) == 4
    assert first.status().stale
    assert first.capture().revision == 2


def test_manager_retries_when_revision_advances_during_rebuild():
    source = _RevisionSource(_large_taxonomy(2))
    manager = SnapshotManager(source.read_revision, source.load)
    source.block_load = True

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.capture)
        assert source.build_started.wait(timeout=5)
        with source.lock:
            source.nodes = _large_taxonomy(5)
            source.revision = 2
        source.block_load = False
        source.allow_build.set()
        snapshot = future.result(timeout=5)

    assert snapshot.revision == 2
    assert len(snapshot.iris) == 5
    assert source.loads == 2


def test_publish_rejects_stale_snapshot_and_never_mutates_old_snapshot():
    source = _RevisionSource(_large_taxonomy(2))
    manager = SnapshotManager(source.read_revision, source.load)
    old = manager.capture()
    replacement = build_taxonomy_snapshot(_large_taxonomy(3), revision=2)

    manager.publish(replacement)

    assert manager.current is replacement
    assert old.revision == 1
    assert old.class_concept.neighbors("cdao:000", direction="downward") == (
        "cdao:001",
    )
    with pytest.raises(ValueError, match="stale"):
        manager.publish(old)
