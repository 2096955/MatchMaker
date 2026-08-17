"""Standalone JPMC taxonomy graph adapter and tool contract tests.

Fixtures and local snapshots exercised here are ILLUSTRATIVE.
"""

from __future__ import annotations

import importlib
import os


def test_taxonomy_graph_module_imports_standalone_and_is_deterministic():
    module = importlib.import_module("scudo.taxonomy_graph")
    models = importlib.import_module("scudo.taxonomy_graph_models")
    nodes = [
        models.TaxonomyNode(
            iri="illustrative:root",
            label="Illustrative Root",
            children_iris=["illustrative:leaf"],
        ),
        models.TaxonomyNode(
            iri="illustrative:leaf",
            label="Illustrative Leaf",
            parent_iri="illustrative:root",
        ),
    ]

    forward = module.analyse_taxonomy(
        nodes,
        candidate_iris=["illustrative:leaf"],
        anchor_iris=["illustrative:root"],
    )
    reverse = module.analyse_taxonomy(
        list(reversed(nodes)),
        candidate_iris=["illustrative:leaf"],
        anchor_iris=["illustrative:root"],
    )

    assert forward.index_iris == ["illustrative:leaf", "illustrative:root"]
    assert forward.model_dump(mode="json") == reverse.model_dump(mode="json")


def test_mapping_and_verifier_lists_include_taxonomy_analysis():
    from scudo.tools import MAPPING_SPECIALIST_TOOLS, VERIFIER_TOOLS

    mapping_names = {getattr(tool, "__name__", "") for tool in MAPPING_SPECIALIST_TOOLS}
    verifier_names = {getattr(tool, "__name__", "") for tool in VERIFIER_TOOLS}

    assert "analyse_taxonomy_candidates" in mapping_names
    assert "analyse_taxonomy_candidates" in verifier_names


def test_tool_uses_snapshot_without_changing_candidate_score_or_080_decision():
    from scudo.schemas import CandidateNode
    from scudo.tools import analyse_taxonomy_candidates

    candidate = CandidateNode(
        iri="jpmorgan:data:cdao:EquityResearch",
        label="EquityResearch",
        score=0.80,
    )
    before = candidate.model_dump(mode="json")
    decision_before = "high" if candidate.score >= 0.80 else "review"

    previous = os.environ.get("SCUDO_LOCAL")
    os.environ["SCUDO_LOCAL"] = "1"
    try:
        evidence = analyse_taxonomy_candidates(
            candidate_iris=[candidate.iri],
            anchor_iris=[],
            max_nodes=100,
            max_depth=8,
        )
    finally:
        if previous is None:
            os.environ.pop("SCUDO_LOCAL", None)
        else:
            os.environ["SCUDO_LOCAL"] = previous

    assert evidence["node_count"] >= 1
    assert evidence["illustrative"] is True
    assert candidate.model_dump(mode="json") == before
    decision_after = "high" if candidate.score >= 0.80 else "review"
    assert decision_after == decision_before == "high"


def test_sidecar_exports_candidate_nodes():
    from scudo import sidecar

    assert callable(sidecar.candidate_nodes)
    assert sidecar.candidate_nodes("equity", limit=1)


def test_jpmc_tool_relies_on_canonical_boundary_validation(monkeypatch):
    import pytest

    from scudo.taxonomy_graph import TaxonomyGraphInputError
    from scudo.tools import analyse_taxonomy_candidates

    monkeypatch.setenv("SCUDO_LOCAL", "1")
    with pytest.raises(TaxonomyGraphInputError, match="unique"):
        analyse_taxonomy_candidates(
            candidate_iris=[
                "jpmorgan:data:cdao:EquityResearch",
                " jpmorgan:data:cdao:EquityResearch ",
            ]
        )


def test_jpmc_nonlocal_without_authoritative_snapshot_fails_closed(monkeypatch):
    from scudo import authoritative
    from scudo.tools import analyse_taxonomy_candidates

    monkeypatch.delenv("SCUDO_LOCAL", raising=False)
    monkeypatch.setattr(authoritative, "taxonomy_snapshot", lambda: None)

    result = analyse_taxonomy_candidates(
        candidate_iris=["jpmorgan:data:cdao:EquityResearch"]
    )

    assert result == {
        "evidence_valid": False,
        "error": "topology_unavailable",
        "illustrative": False,
    }
