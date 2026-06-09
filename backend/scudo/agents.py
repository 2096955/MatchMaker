"""Build the three Strands agents the orchestrator drives.

The orchestrator owns deterministic state and the publish gate. The agents
own *judgement*. Their only dependencies are:

  • a Model object (BedrockModel / AnthropicModel / a stub model for tests)
  • the catalogue MCP tool list (handed in — only valid inside the MCP context)
  • the AgentSkills plugin and the hook bundle

Agents are tools-+-prompt-+-skills factories — no business logic lives here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from .hooks import specialist_hooks
from .prompts import MAPPING_SYSTEM, RIGHTS_SYSTEM, VERIFIER_SYSTEM
from .tools import MAPPING_SPECIALIST_TOOLS, RIGHTS_SPECIALIST_TOOLS


_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _skills_plugin() -> AgentSkills:
    # Hand the AgentSkills plugin the parent skills directory; it auto-discovers
    # taxonomy-mapping, rights-odrl, rdf-serialisation, neptune-io.
    return AgentSkills(skills=[str(_SKILLS_DIR)])


def build_mapping_specialist(
    *,
    model: Any,
    catalogue_tools: list,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str,
    telemetry_sink=None,
) -> Agent:
    return Agent(
        model=model,
        system_prompt=MAPPING_SYSTEM,
        tools=catalogue_tools + MAPPING_SPECIALIST_TOOLS,
        plugins=[_skills_plugin()],
        hooks=specialist_hooks(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=schema_version,
            telemetry_sink=telemetry_sink,
        ),
    )


def build_rights_specialist(
    *,
    model: Any,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str,
    telemetry_sink=None,
) -> Agent:
    return Agent(
        model=model,
        system_prompt=RIGHTS_SYSTEM,
        tools=RIGHTS_SPECIALIST_TOOLS,
        plugins=[_skills_plugin()],
        hooks=specialist_hooks(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=schema_version,
            telemetry_sink=telemetry_sink,
        ),
    )


def build_verifier(*, model: Any) -> Agent:
    # No tools, no skills — the verifier is a structured-output judgement
    # against a fixed rubric. Keeping its surface minimal is the point.
    return Agent(model=model, system_prompt=VERIFIER_SYSTEM)


__all__ = [
    "build_mapping_specialist", "build_rights_specialist", "build_verifier",
]
