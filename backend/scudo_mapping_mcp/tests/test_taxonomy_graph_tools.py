"""Parity tests for the read-only taxonomy evidence tool surfaces."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scudo_mapping_mcp.models import Candidate, TaxonomyNode
from scudo_mapping_mcp.taxonomy_graph import TaxonomyGraphInputError


class _IllustrativeStore:
    def __init__(self) -> None:
        self.nodes = [
            TaxonomyNode(
                iri="illustrative:root",
                label="Illustrative Root",
                children_iris=["illustrative:leaf"],
            ),
            TaxonomyNode(
                iri="illustrative:leaf",
                label="Illustrative Leaf",
                parent_iri="illustrative:root",
            ),
        ]

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        return list(self.nodes)


@pytest.mark.asyncio
async def test_mapping_and_matchverify_mcp_graph_tools_have_matching_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    from scudo_mapping_mcp import match_verify_mcp, mcp_server

    store = _IllustrativeStore()
    monkeypatch.setattr(match_verify_mcp, "get_store", lambda: store)
    monkeypatch.setattr(mcp_server, "get_store", lambda: store)
    payload = {
        "candidate_iris": ["illustrative:leaf"],
        "anchor_iris": ["illustrative:root"],
        "max_nodes": 100,
        "max_depth": 4,
    }

    matchverify_result = json.loads(
        await match_verify_mcp.analyse_taxonomy_candidates(
            match_verify_mcp.TaxonomyAnalysisInput(**payload)
        )
    )
    mapping_result = json.loads(
        await mcp_server.analyse_taxonomy_candidates(
            mcp_server.TaxonomyAnalysisInput(**payload)
        )
    )

    assert mapping_result == matchverify_result
    assert mapping_result["candidates"][0]["anchor_paths"][0]["distance"] == 1
    assert "confirmed_precedent_iris" not in json.dumps(
        match_verify_mcp.TaxonomyAnalysisInput.model_json_schema()
    )
    assert "precedent_anchor_iris" not in json.dumps(
        mcp_server.TaxonomyAnalysisInput.model_json_schema()
    )


@pytest.mark.asyncio
async def test_all_backend_surfaces_enforce_shared_boundary_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    from scudo_mapping_mcp import agent, match_verify_mcp, mcp_server

    duplicate = ["illustrative:leaf", " illustrative:leaf "]
    with pytest.raises(TaxonomyGraphInputError, match="unique"):
        await match_verify_mcp.analyse_taxonomy_candidates(
            match_verify_mcp.TaxonomyAnalysisInput(candidate_iris=duplicate)
        )
    with pytest.raises(TaxonomyGraphInputError, match="unique"):
        await mcp_server.analyse_taxonomy_candidates(
            mcp_server.TaxonomyAnalysisInput(candidate_iris=duplicate)
        )
    with pytest.raises(ValidationError):
        match_verify_mcp.TaxonomyAnalysisInput(
            candidate_iris=[f"illustrative:{index}" for index in range(26)]
        )
    monkeypatch.setitem(sys.modules, "strands", SimpleNamespace(tool=lambda fn: fn))
    monkeypatch.setattr(agent, "get_store", _IllustrativeStore)
    by_name = {tool.__name__: tool for tool in agent._strands_tools_for_mapping()}
    with pytest.raises(TaxonomyGraphInputError, match="unique"):
        by_name["analyse_taxonomy_candidates"](candidate_iris=duplicate)


def test_strands_mapping_tools_expose_graph_analysis(monkeypatch: pytest.MonkeyPatch):
    from scudo_mapping_mcp import agent

    monkeypatch.setitem(sys.modules, "strands", SimpleNamespace(tool=lambda fn: fn))
    monkeypatch.setattr(agent, "get_store", _IllustrativeStore)

    tools = agent._strands_tools_for_mapping()
    by_name = {tool.__name__: tool for tool in tools}

    assert "analyse_taxonomy_candidates" in by_name
    result = json.loads(
        by_name["analyse_taxonomy_candidates"](
            candidate_iris=["illustrative:leaf"],
            anchor_iris=["illustrative:root"],
            max_nodes=100,
            max_depth=4,
        )
    )
    assert result["node_count"] == 2


@pytest.mark.asyncio
async def test_graph_tool_cannot_mutate_similarity_or_080_decision(
    monkeypatch: pytest.MonkeyPatch,
):
    from scudo_mapping_mcp import match_verify_mcp

    candidate = Candidate(
        node=TaxonomyNode(iri="illustrative:leaf", label="Illustrative Leaf"),
        similarity=0.80,
    )
    before = candidate.model_dump(mode="json")
    status_before = "auto_mapped" if candidate.similarity >= 0.80 else "needs_review"
    monkeypatch.setattr(match_verify_mcp, "get_store", _IllustrativeStore)

    await match_verify_mcp.analyse_taxonomy_candidates(
        match_verify_mcp.TaxonomyAnalysisInput(
            candidate_iris=[candidate.node.iri],
            anchor_iris=["illustrative:root"],
        )
    )

    assert candidate.model_dump(mode="json") == before
    status_after = "auto_mapped" if candidate.similarity >= 0.80 else "needs_review"
    assert status_after == status_before == "auto_mapped"
