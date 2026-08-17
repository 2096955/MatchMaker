"""Intelligence surface is wired for Bedrock (tools, hooks, skills, prompts)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_mapping_system_prompt_carries_procedure_and_floor():
    from scudo.prompts import MAPPING_SYSTEM, VERIFIER_SYSTEM

    assert "0.80" in MAPPING_SYSTEM
    assert "neptune_node_by_iri" in MAPPING_SYSTEM
    assert "graphrag_retrieve" in MAPPING_SYSTEM
    assert "Evidence" in MAPPING_SYSTEM
    assert "taxonomy_freshness" in VERIFIER_SYSTEM
    assert "adversarial" in VERIFIER_SYSTEM.lower() or "Inflated" in VERIFIER_SYSTEM


def test_mapping_tools_cover_discovery_confirm_serialise():
    from scudo.tools import MAPPING_SPECIALIST_TOOLS, VERIFIER_TOOLS

    names = {getattr(t, "__name__", str(t)) for t in MAPPING_SPECIALIST_TOOLS}
    assert names >= {
        "describe_system_context",
        "lookup_catalogue_term",
        "list_catalogue_dataset_fields",
        "graphrag_retrieve",
        "neptune_node_by_iri",
        "neptune_existing_mapping",
        "neptune_conflicts",
        "rdf_serialise_mapping",
        "rdf_validate_shapes",
    }
    vnames = {getattr(t, "__name__", str(t)) for t in VERIFIER_TOOLS}
    assert "neptune_node_by_iri" in vnames
    assert "graphrag_retrieve" not in vnames


def test_skills_pack_present():
    skills = ROOT / "scudo" / "skills"
    for name in (
        "taxonomy-mapping",
        "graphrag-retrieval",
        "neptune-io",
        "rdf-serialisation",
        "rights-odrl",
        "catalogue-ontology-fill",
    ):
        assert (skills / name / "SKILL.md").is_file()


def test_specialist_hooks_include_security_guards():
    import pytest

    pytest.importorskip("strands.hooks")
    from scudo.hooks import specialist_hooks

    hooks = specialist_hooks(
        ontology_snapshot="cdao-x", rubric_version="r1", schema_version="0.1.0"
    )
    types = {type(h).__name__ for h in hooks}
    assert types >= {
        "VersionPinHook",
        "RejectRawQueryHook",
        "PublishGateHook",
        "NeptuneReadCapHook",
        "TelemetryHook",
    }


def test_default_bedrock_model_is_opus_4_8():
    import os

    os.environ.pop("SCUDO_BEDROCK_LLM_ID", None)
    os.environ.pop("BEDROCK_LLM_MODEL_ID", None)
    from scudo.shared.bedrock import bedrock_llm_id

    assert bedrock_llm_id() == "us.anthropic.claude-opus-4-8"


def test_local_defaults_to_deterministic_agents():
    os.environ["SCUDO_LOCAL"] = "1"
    os.environ.pop("SCUDO_AGENT_MODE", None)
    from scudo.agents import get_agents
    from scudo.agents_local import DeterministicMappingAgent

    mapping, verifier, rights, catalogue = get_agents(
        ontology_snapshot="cdao-x", rubric_version="r1"
    )
    assert isinstance(mapping, DeterministicMappingAgent)
    assert rights is None
    assert catalogue is not None


def test_authoritative_confirm_path_works_in_mock():
    from scudo import authoritative

    node = authoritative.node_by_iri("jpmorgan:data:cdao:EquityResearch")
    assert node is not None
    assert "definition" in node
    prior = authoritative.existing_mapping("lseg", "LSEG-IBES-EST-001")
    assert prior is not None
