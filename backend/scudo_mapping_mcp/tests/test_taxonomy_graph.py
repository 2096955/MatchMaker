"""Tests for deterministic, read-only SciPy taxonomy evidence.

All taxonomy records in this module are ILLUSTRATIVE fixtures.
"""

from __future__ import annotations

import pytest

from scudo_mapping_mcp.models import Candidate, TaxonomyNode
from scudo_mapping_mcp.taxonomy_graph import (
    TaxonomyGraphBoundError,
    TaxonomyGraphInputError,
    analyse_taxonomy,
)


def _illustrative_nodes() -> list[TaxonomyNode]:
    return [
        TaxonomyNode(
            iri="illustrative:root",
            label="Illustrative Root",
            children_iris=["illustrative:a", "illustrative:b"],
        ),
        TaxonomyNode(
            iri="illustrative:a",
            label="Illustrative A",
            parent_iri="illustrative:root",
            children_iris=["illustrative:a1", "illustrative:a2"],
        ),
        TaxonomyNode(
            iri="illustrative:a1",
            label="Illustrative A1",
            parent_iri="illustrative:a",
        ),
        TaxonomyNode(
            iri="illustrative:a2",
            label="Illustrative A2",
            parent_iri="illustrative:a",
        ),
        TaxonomyNode(
            iri="illustrative:b",
            label="Illustrative B",
            parent_iri="illustrative:root",
        ),
        TaxonomyNode(iri="illustrative:orphan", label="Illustrative Orphan"),
    ]


def test_index_is_sorted_and_independent_of_input_order():
    nodes = _illustrative_nodes()

    forward = analyse_taxonomy(nodes, candidate_iris=["illustrative:a1"])
    reverse = analyse_taxonomy(
        list(reversed(nodes)), candidate_iris=["illustrative:a1"]
    )

    expected = sorted(node.iri for node in nodes)
    assert forward.index_iris == expected
    assert reverse.model_dump(mode="json") == forward.model_dump(mode="json")
    assert forward.evidence_valid is True
    assert forward.diagnostics.cyclic_sccs == []


def test_paths_lca_distance_and_branch_ambiguity():
    result = analyse_taxonomy(
        _illustrative_nodes(),
        candidate_iris=["illustrative:a1", "illustrative:a2", "illustrative:a"],
        anchor_iris=["illustrative:root"],
        max_depth=5,
    )

    by_candidate = {item.candidate_iri: item for item in result.candidates}
    assert by_candidate["illustrative:a1"].anchor_paths[0].path_iris == [
        "illustrative:a1",
        "illustrative:a",
        "illustrative:root",
    ]
    assert by_candidate["illustrative:a1"].degree == 1
    assert by_candidate["illustrative:a"].degree == 3
    assert by_candidate["illustrative:a"].branch_ambiguity is True

    pair = next(
        item
        for item in result.candidate_pairs
        if {item.first_candidate_iri, item.second_candidate_iri}
        == {"illustrative:a1", "illustrative:a2"}
    )
    assert pair.lowest_common_ancestor_iri == "illustrative:a"
    assert pair.first_to_lca_distance == 1
    assert pair.second_to_lca_distance == 1
    assert pair.separation == 2


def test_parent_only_production_store_shape_is_valid_and_builds_children():
    nodes = [
        TaxonomyNode(iri="illustrative:root", label="Illustrative Root"),
        TaxonomyNode(
            iri="illustrative:child",
            label="Illustrative Child",
            parent_iri="illustrative:root",
        ),
    ]

    result = analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:root", "illustrative:child"],
    )

    assert result.evidence_valid is True
    by_candidate = {item.candidate_iri: item for item in result.candidates}
    assert by_candidate["illustrative:root"].child_count == 1
    assert by_candidate["illustrative:root"].degree == 1


def test_reverse_only_property_child_is_invalid():
    nodes = [
        TaxonomyNode(
            iri="illustrative:property-root",
            label="Illustrative Property Root",
            node_kind="property",
            children_iris=["illustrative:property-child"],
        ),
        TaxonomyNode(
            iri="illustrative:property-child",
            label="Illustrative Property Child",
            node_kind="property",
        ),
    ]

    result = analyse_taxonomy(nodes, candidate_iris=["illustrative:property-child"])

    assert result.evidence_valid is False
    assert result.diagnostics.asymmetric_declarations[0].relation == "child"


def test_components_and_orphans_are_reported():
    result = analyse_taxonomy(
        _illustrative_nodes(),
        candidate_iris=["illustrative:a1", "illustrative:orphan"],
    )

    assert result.component_count == 2
    assert result.orphan_iris == ["illustrative:orphan"]
    by_candidate = {item.candidate_iri: item for item in result.candidates}
    assert (
        by_candidate["illustrative:a1"].component_id
        != by_candidate["illustrative:orphan"].component_id
    )
    assert by_candidate["illustrative:orphan"].orphan is True


def test_cycle_asymmetry_missing_and_superproperty_separation_are_reported():
    nodes = [
        TaxonomyNode(
            iri="illustrative:a",
            label="Illustrative A",
            parent_iri="illustrative:b",
            node_kind="class",
            superclass_iris=["illustrative:missing-class"],
        ),
        TaxonomyNode(
            iri="illustrative:b",
            label="Illustrative B",
            parent_iri="illustrative:a",
        ),
        TaxonomyNode(
            iri="illustrative:p",
            label="Illustrative Property",
            node_kind="property",
            superproperty_iris=["illustrative:missing-property"],
        ),
    ]

    result = analyse_taxonomy(nodes, candidate_iris=["illustrative:a"])

    assert result.evidence_valid is False
    assert [
        item.model_dump(mode="json") for item in result.diagnostics.cyclic_sccs
    ] == [
        {
            "hierarchy_type": "class_concept",
            "node_iris": ["illustrative:a", "illustrative:b"],
        }
    ]
    assert {
        (item.source_iri, item.relation, item.target_iri)
        for item in result.diagnostics.missing_references
    } == {
        ("illustrative:a", "superclass", "illustrative:missing-class"),
        ("illustrative:p", "superproperty", "illustrative:missing-property"),
    }
    assert result.diagnostics.asymmetric_declarations == []
    assert result.component_count is None
    assert result.orphan_iris == []
    assert result.candidates == []
    assert result.candidate_pairs == []
    assert result.affinity is None


def test_hard_bound_rejects_oversized_taxonomy():
    nodes = [
        TaxonomyNode(iri=f"illustrative:{index:03d}", label=f"Illustrative {index}")
        for index in range(4)
    ]

    with pytest.raises(TaxonomyGraphBoundError, match="4 nodes exceeds"):
        analyse_taxonomy(nodes, candidate_iris=[], max_nodes=3)


def test_relationship_list_and_total_edge_amplification_are_rejected():
    oversized_list = [
        TaxonomyNode(
            iri="illustrative:source",
            label="Illustrative Source",
            superclass_iris=[f"illustrative:target-{index}" for index in range(26)],
        )
    ]
    with pytest.raises(TaxonomyGraphBoundError, match="relationship list"):
        analyse_taxonomy(oversized_list, candidate_iris=[])

    nodes = [
        TaxonomyNode(
            iri=f"illustrative:{index:02d}",
            label=f"Illustrative {index}",
            node_kind="class",
            superclass_iris=[
                f"illustrative:{target:02d}"
                for target in range(max(0, index - 25), index)
            ],
        )
        for index in range(100)
    ]
    with pytest.raises(TaxonomyGraphBoundError, match="500"):
        analyse_taxonomy(nodes, candidate_iris=[])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "candidate_iris",
            [f"illustrative:{index}" for index in range(26)],
            "at most 25",
        ),
        ("anchor_iris", [f"illustrative:{index}" for index in range(26)], "at most 25"),
        (
            "confirmed_precedent_iris",
            [f"illustrative:{index}" for index in range(26)],
            "at most 25",
        ),
        ("candidate_iris", ["illustrative:a", " illustrative:a "], "unique"),
        ("anchor_iris", [""], "nonempty"),
        ("confirmed_precedent_iris", ["x" * 513], "512"),
    ],
)
def test_boundary_rejects_invalid_iri_lists_before_analysis(field, value, message):
    kwargs = {
        "candidate_iris": ["illustrative:a"],
        "anchor_iris": [],
        "confirmed_precedent_iris": [],
    }
    kwargs[field] = value

    with pytest.raises(TaxonomyGraphInputError, match=message):
        analyse_taxonomy(_illustrative_nodes(), **kwargs)


def test_multiple_inheritance_lca_removes_nonlowest_common_ancestors():
    nodes = [
        TaxonomyNode(
            iri="illustrative:root",
            label="Illustrative Root",
            children_iris=["illustrative:left", "illustrative:right"],
        ),
        TaxonomyNode(
            iri="illustrative:left",
            label="Illustrative Left",
            node_kind="class",
            superclass_iris=["illustrative:root"],
            children_iris=["illustrative:x", "illustrative:y"],
        ),
        TaxonomyNode(
            iri="illustrative:right",
            label="Illustrative Right",
            node_kind="class",
            superclass_iris=["illustrative:root"],
            children_iris=["illustrative:x", "illustrative:y"],
        ),
        TaxonomyNode(
            iri="illustrative:x",
            label="Illustrative X",
            node_kind="class",
            superclass_iris=["illustrative:left", "illustrative:right"],
        ),
        TaxonomyNode(
            iri="illustrative:y",
            label="Illustrative Y",
            node_kind="class",
            superclass_iris=["illustrative:left", "illustrative:right"],
        ),
    ]

    result = analyse_taxonomy(
        nodes, candidate_iris=["illustrative:x", "illustrative:y"]
    )

    pair = result.candidate_pairs[0]
    assert pair.lowest_common_ancestor_iris == [
        "illustrative:left",
        "illustrative:right",
    ]
    assert pair.selected_lowest_common_ancestor_iri == "illustrative:left"
    assert "illustrative:root" not in pair.lowest_common_ancestor_iris


def test_lca_uses_full_bounded_graph_beyond_display_depth():
    nodes = [TaxonomyNode(iri="illustrative:root", label="Illustrative Root")]
    previous = "illustrative:root"
    for index in range(1, 10):
        iri = f"illustrative:level-{index}"
        nodes.append(
            TaxonomyNode(
                iri=iri,
                label=f"Illustrative Level {index}",
                parent_iri=previous,
            )
        )
        previous = iri
    nodes.extend(
        [
            TaxonomyNode(
                iri="illustrative:left",
                label="Illustrative Left",
                parent_iri=previous,
            ),
            TaxonomyNode(
                iri="illustrative:right",
                label="Illustrative Right",
                parent_iri=previous,
            ),
        ]
    )

    result = analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:left", "illustrative:right"],
        anchor_iris=["illustrative:root"],
        max_depth=8,
    )

    pair = result.candidate_pairs[0]
    assert pair.lowest_common_ancestor_iris == ["illustrative:level-9"]
    assert pair.ancestry_truncated is False
    assert result.candidates[0].anchor_paths[0].path_iris == []
    assert result.candidates[0].anchor_paths[0].ancestry_truncated is True


def test_cycle_diagnostic_reports_strong_components_and_self_loops():
    nodes = [
        TaxonomyNode(
            iri="illustrative:a",
            label="Illustrative A",
            node_kind="class",
            superclass_iris=["illustrative:b"],
            children_iris=["illustrative:b"],
        ),
        TaxonomyNode(
            iri="illustrative:b",
            label="Illustrative B",
            node_kind="class",
            superclass_iris=["illustrative:a"],
            children_iris=["illustrative:a"],
        ),
        TaxonomyNode(
            iri="illustrative:self",
            label="Illustrative Self",
            node_kind="property",
            superproperty_iris=["illustrative:self"],
        ),
    ]

    result = analyse_taxonomy(nodes, candidate_iris=["illustrative:a"])

    assert [
        item.model_dump(mode="json") for item in result.diagnostics.cyclic_sccs
    ] == [
        {
            "hierarchy_type": "class_concept",
            "node_iris": ["illustrative:a", "illustrative:b"],
        },
        {"hierarchy_type": "property", "node_iris": ["illustrative:self"]},
    ]


def test_property_hierarchy_is_typed_and_mixed_candidates_are_incompatible():
    nodes = [
        TaxonomyNode(
            iri="illustrative:concept-root",
            label="Illustrative Concept Root",
            children_iris=["illustrative:concept-leaf"],
        ),
        TaxonomyNode(
            iri="illustrative:concept-leaf",
            label="Illustrative Concept Leaf",
            parent_iri="illustrative:concept-root",
        ),
        TaxonomyNode(
            iri="illustrative:property-root",
            label="Illustrative Property Root",
            node_kind="property",
        ),
        TaxonomyNode(
            iri="illustrative:property-leaf",
            label="Illustrative Property Leaf",
            node_kind="property",
            superproperty_iris=["illustrative:property-root"],
        ),
    ]

    result = analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:concept-leaf", "illustrative:property-leaf"],
        anchor_iris=["illustrative:concept-root", "illustrative:property-root"],
    )

    by_candidate = {item.candidate_iri: item for item in result.candidates}
    assert by_candidate["illustrative:concept-leaf"].hierarchy_type == "class_concept"
    assert by_candidate["illustrative:property-leaf"].hierarchy_type == "property"
    concept_paths = {
        item.anchor_iri: item
        for item in by_candidate["illustrative:concept-leaf"].anchor_paths
    }
    assert concept_paths["illustrative:property-root"].compatible_hierarchy is False
    assert concept_paths["illustrative:property-root"].path_iris == []
    pair = result.candidate_pairs[0]
    assert pair.compatible_hierarchy is False
    assert pair.separation is None


def test_affinity_is_opt_in_deterministic_and_seeded_only_by_confirmed_anchors():
    nodes = _illustrative_nodes()

    unseeded = analyse_taxonomy(nodes, candidate_iris=["illustrative:a1"])
    first = analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:a1", "illustrative:b"],
        confirmed_precedent_iris=["illustrative:a2"],
    )
    second = analyse_taxonomy(
        list(reversed(nodes)),
        candidate_iris=["illustrative:a1", "illustrative:b"],
        confirmed_precedent_iris=["illustrative:a2"],
    )

    assert unseeded.affinity is None
    assert first.affinity is not None
    assert first.affinity_converged is True
    assert first.affinity.confirmed_anchor_iris == ["illustrative:a2"]
    assert first.affinity.model_dump(mode="json") == second.affinity.model_dump(
        mode="json"
    )
    scores = {item.candidate_iri: item.score for item in first.affinity.candidates}
    assert scores["illustrative:a1"] > scores["illustrative:b"]
    assert sum(item.score for item in first.affinity.all_nodes) == pytest.approx(1.0)


def test_affinity_redistributes_dangling_mass_and_converges_deterministically():
    nodes = [
        TaxonomyNode(
            iri="illustrative:root",
            label="Illustrative Root",
            children_iris=["illustrative:near"],
        ),
        TaxonomyNode(
            iri="illustrative:near",
            label="Illustrative Near",
            parent_iri="illustrative:root",
        ),
        TaxonomyNode(iri="illustrative:dangling", label="Illustrative Dangling"),
    ]

    result = analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:near", "illustrative:dangling"],
        confirmed_precedent_iris=["illustrative:dangling"],
    )

    assert result.affinity_converged is True
    assert result.affinity is not None
    scores = {item.candidate_iri: item.score for item in result.affinity.candidates}
    assert scores["illustrative:dangling"] > scores["illustrative:near"]
    assert sum(item.score for item in result.affinity.all_nodes) == pytest.approx(
        1.0, abs=1e-10
    )


def test_nonconverged_affinity_is_flagged_and_withheld():
    result = analyse_taxonomy(
        _illustrative_nodes(),
        candidate_iris=["illustrative:a1"],
        confirmed_precedent_iris=["illustrative:a2"],
        affinity_tolerance=1e-15,
        affinity_max_iterations=1,
    )

    assert result.evidence_valid is True
    assert result.affinity_converged is False
    assert result.affinity is None


def test_graph_evidence_does_not_mutate_candidate_similarity_or_decision():
    candidates = [
        Candidate(
            node=TaxonomyNode(
                iri="illustrative:a1",
                label="Illustrative A1",
                parent_iri="illustrative:a",
            ),
            similarity=0.80,
        ),
        Candidate(
            node=TaxonomyNode(
                iri="illustrative:a2",
                label="Illustrative A2",
                parent_iri="illustrative:a",
            ),
            similarity=0.79,
        ),
    ]
    before = [candidate.model_dump(mode="json") for candidate in candidates]
    status_before = (
        "auto_mapped" if candidates[0].similarity >= 0.80 else "needs_review"
    )

    analyse_taxonomy(
        _illustrative_nodes(),
        candidate_iris=[candidate.node.iri for candidate in candidates],
        anchor_iris=["illustrative:root"],
    )

    assert [candidate.model_dump(mode="json") for candidate in candidates] == before
    status_after = "auto_mapped" if candidates[0].similarity >= 0.80 else "needs_review"
    assert status_after == status_before == "auto_mapped"
