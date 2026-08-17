"""Mapping + Verifier run multi-turn agentic loops (tool use + reasoning)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_verifier_tools_are_investigative():
    from scudo.tools import VERIFIER_TOOLS

    names = {getattr(t, "__name__", str(t)) for t in VERIFIER_TOOLS}
    assert names >= {
        "describe_system_context",
        "lookup_catalogue_term",
        "neptune_node_by_iri",
        "neptune_existing_mapping",
        "neptune_conflicts",
        "rdf_validate_shapes",
    }
    # Verifier must not remap or publish
    assert "graphrag_retrieve" not in names
    assert "neptune_publish_triples" not in names


def test_prompts_require_agentic_loops():
    from scudo.prompts import MAPPING_SYSTEM, VERIFIER_SYSTEM

    assert "AGENTIC LOOP" in MAPPING_SYSTEM
    assert "AGENTIC LOOP" in VERIFIER_SYSTEM
    assert "Token budget" in MAPPING_SYSTEM
    assert "neptune_node_by_iri" in VERIFIER_SYSTEM


def test_deterministic_agents_expose_agentic_structured():
    from scudo.agent_loop import AgentLoopResult, run_agentic_structured
    from scudo.agents_local import DeterministicMappingAgent, DeterministicVerifierAgent
    from scudo.schemas import MappingResult, VerifierReport

    mapping = DeterministicMappingAgent()
    prompt = (
        "Ontology snapshot: cdao-test\nRubric version: r1\n"
        'BriefBundle:\n{"vendor_product_iri":"mds.lseg:00000000-0000-4000-8000-000000000001",'
        '"candidates":[{"iri":"jpmorgan:data:cdao:EquityResearch"}],'
        '"ontology_snapshot":"cdao-test","rubric_version":"r1","route":"NEW_MAPPING"}'
    )
    loop = run_agentic_structured(mapping, prompt, MappingResult)
    assert isinstance(loop, AgentLoopResult)
    assert isinstance(loop.output, MappingResult)
    assert loop.turns >= 3
    assert {c["name"] for c in loop.tool_calls} >= {
        "graphrag_retrieve",
        "neptune_node_by_iri",
    }
    assert loop.reasoning_trace

    vloop = run_agentic_structured(
        DeterministicVerifierAgent(), "score this", VerifierReport
    )
    assert isinstance(vloop.output, VerifierReport)
    assert vloop.turns >= 3
    assert any(c["name"] == "neptune_node_by_iri" for c in vloop.tool_calls)


def test_orchestrator_records_mapping_and_verifier_loops():
    import os

    os.environ["SCUDO_LOCAL"] = "1"
    os.environ.pop("SCUDO_AGENT_MODE", None)
    from scudo.agents import get_agents
    from scudo.orchestrator import Orchestrator
    from scudo.schemas import (
        Band,
        BriefBundle,
        CandidateNode,
        IntakeRequest,
        Outcome,
        Route,
    )
    from scudo.stubs import (
        InMemoryHitlQueue,
        InMemoryPublishSink,
        InMemoryResearchQueue,
    )

    mapping, verifier, rights, _ = get_agents(
        ontology_snapshot="cdao-test", rubric_version="rubric-v1"
    )

    def assemble(request: IntakeRequest, route: Route) -> BriefBundle:
        return BriefBundle(
            request=request,
            route=route,
            vendor_product_iri="mds.lseg:00000000-0000-4000-8000-000000000001",
            vendor_assertion={"title": "IBES Estimates"},
            candidates=[
                CandidateNode(
                    iri="jpmorgan:data:cdao:EquityResearch",
                    label="Equity Research",
                    score=0.9,
                )
            ],
            ontology_snapshot="cdao-test",
            rubric_version="rubric-v1",
            assembled_at=datetime.now(timezone.utc),
            bundle_ref="b1",
        )

    orch = Orchestrator(
        mapping_specialist=mapping,
        rights_specialist=rights,
        verifier=verifier,
        hitl_queue=InMemoryHitlQueue(),
        research_queue=InMemoryResearchQueue(),
        publish_sink=InMemoryPublishSink(),
        ontology_snapshot="cdao-test",
        rubric_version="rubric-v1",
        bundle_assembler=assemble,
    )
    obj = orch.run(
        {
            "vendor": "lseg",
            "vendor_product_ref": "LSEG-IBES-EST-001",
        }
    )
    assert orch.last_mapping_loop is not None
    assert orch.last_verifier_loop is not None
    assert orch.last_mapping_loop.turns >= 3
    assert orch.last_verifier_loop.turns >= 3
    assert orch.last_mapping_loop.tool_calls
    assert orch.last_verifier_loop.tool_calls
    assert obj.outcome in {Outcome.PUBLISHED, Outcome.HITL, Outcome.RETRY}
    assert obj.mapping_result is not None
    assert obj.mapping_result.band is Band.HIGH
