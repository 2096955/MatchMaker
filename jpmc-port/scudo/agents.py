"""Strands agent factory — tools + hooks + skills + rich system prompts.

Mapping Specialist and Verifier are the load-bearing agents: full tool surfaces,
high token budgets, and multi-turn agentic loops via ``agent_loop``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from .prompts import (
    CATALOGUE_FILL_SYSTEM,
    MAPPING_SYSTEM,
    RIGHTS_SYSTEM,
    VERIFIER_SYSTEM,
)
from .schemas import SCHEMA_VERSION
from .shared.bedrock import (
    anthropic_client_args,
    anthropic_llm_id,
    aws_region,
    bedrock_llm_id,
)
from .tools import (
    CATALOGUE_FILL_TOOLS,
    MAPPING_SPECIALIST_TOOLS,
    RIGHTS_SPECIALIST_TOOLS,
    VERIFIER_TOOLS,
)

log = logging.getLogger("scudo.agents")
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_AGENTS_CACHE: dict[str, tuple[Any, Any, Optional[Any], Any]] = {}

# Opus 4.8 max output is 128K — use the full ceiling for the load-bearing agents.
_AGENT_MAX_TOKENS = 128_000


def _skills_plugins() -> list:
    if not _SKILLS_DIR.is_dir():
        return []
    try:
        from strands.vended_plugins.skills import AgentSkills

        return [AgentSkills(skills=[str(_SKILLS_DIR)])]
    except Exception as exc:
        log.warning("AgentSkills unavailable: %s", exc)
        return []


def _bedrock_model(*, model_id: str, region: str) -> Any:
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=model_id,
        region_name=region,
        max_tokens=_AGENT_MAX_TOKENS,
        temperature=0.2,
    )


def _anthropic_model(*, model_id: str | None = None) -> Any:
    """Real Opus via Anthropic Messages API (local shim or cloud)."""
    from strands.models.anthropic import AnthropicModel

    mid = model_id or anthropic_llm_id()
    log.info(
        "Anthropic agents model=%s base=%s max_tokens=%s",
        mid,
        anthropic_client_args().get("base_url") or "<default>",
        _AGENT_MAX_TOKENS,
    )
    # Opus 4.8 rejects `temperature` on the Messages API (deprecated for this model).
    return AnthropicModel(
        client_args=anthropic_client_args() or None,
        model_id=mid,
        max_tokens=_AGENT_MAX_TOKENS,
    )


def build_anthropic_agents(
    *,
    ontology_snapshot: str,
    rubric_version: str,
    with_rights: bool = True,
) -> tuple[Any, Any, Optional[Any], Any]:
    """Mapping + verifier (+ optional rights) + catalogue-fill on Anthropic Opus."""
    model = _anthropic_model()
    mapping = build_mapping_specialist(
        model=model,
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    verifier = build_verifier(
        model=_anthropic_model(),
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    rights = None
    if with_rights:
        rights = build_rights_specialist(
            model=_anthropic_model(),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
        )
    catalogue = build_catalogue_fill_specialist(
        model=_anthropic_model(),
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    return mapping, verifier, rights, catalogue


def build_mapping_specialist(
    *,
    model: Any,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str = SCHEMA_VERSION,
    catalogue_tools: list | None = None,
    telemetry_sink=None,
) -> Any:
    from strands import Agent

    from .hooks import specialist_hooks

    return Agent(
        model=model,
        system_prompt=MAPPING_SYSTEM,
        tools=(catalogue_tools or []) + MAPPING_SPECIALIST_TOOLS,
        plugins=_skills_plugins(),
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
    schema_version: str = SCHEMA_VERSION,
    telemetry_sink=None,
) -> Any:
    from strands import Agent

    from .hooks import specialist_hooks

    return Agent(
        model=model,
        system_prompt=RIGHTS_SYSTEM,
        tools=RIGHTS_SPECIALIST_TOOLS,
        plugins=_skills_plugins(),
        hooks=specialist_hooks(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=schema_version,
            telemetry_sink=telemetry_sink,
        ),
    )


def build_verifier(
    *,
    model: Any,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str = SCHEMA_VERSION,
    telemetry_sink=None,
) -> Any:
    """Verifier is investigative — tools + hooks, not a tool-less scorer."""
    from strands import Agent

    from .hooks import specialist_hooks

    return Agent(
        model=model,
        system_prompt=VERIFIER_SYSTEM,
        tools=VERIFIER_TOOLS,
        plugins=_skills_plugins(),
        hooks=specialist_hooks(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=schema_version,
            telemetry_sink=telemetry_sink,
        ),
    )


def build_catalogue_fill_specialist(
    *,
    model: Any,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str = SCHEMA_VERSION,
    telemetry_sink=None,
) -> Any:
    from strands import Agent

    from .hooks import specialist_hooks

    return Agent(
        model=model,
        system_prompt=CATALOGUE_FILL_SYSTEM,
        tools=CATALOGUE_FILL_TOOLS,
        plugins=_skills_plugins(),
        hooks=specialist_hooks(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
            schema_version=schema_version,
            telemetry_sink=telemetry_sink,
        ),
    )


def build_bedrock_agents(
    *,
    ontology_snapshot: str,
    rubric_version: str,
    with_rights: bool = True,
) -> tuple[Any, Any, Optional[Any], Any]:
    """Mapping + verifier (+ optional rights) + catalogue-fill on Bedrock."""
    model_id = bedrock_llm_id()
    region = aws_region()
    log.info(
        "Bedrock agents model=%s region=%s max_tokens=%s",
        model_id,
        region,
        _AGENT_MAX_TOKENS,
    )
    mapping = build_mapping_specialist(
        model=_bedrock_model(model_id=model_id, region=region),
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    verifier = build_verifier(
        model=_bedrock_model(model_id=model_id, region=region),
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    rights = None
    if with_rights:
        rights = build_rights_specialist(
            model=_bedrock_model(model_id=model_id, region=region),
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
        )
    catalogue = build_catalogue_fill_specialist(
        model=_bedrock_model(model_id=model_id, region=region),
        ontology_snapshot=ontology_snapshot,
        rubric_version=rubric_version,
    )
    return mapping, verifier, rights, catalogue


def get_agents(
    *,
    ontology_snapshot: str,
    rubric_version: str,
    mode: str | None = None,
) -> tuple[Any, Any, Optional[Any], Any]:
    """Return (mapping, verifier, rights, catalogue_fill).

    Modes:
      bedrock       — Strands/Bedrock + tools + hooks + skills (needs AWS IAM)
      anthropic     — Strands/Anthropic Messages API (Opus 4.8 via shim or cloud)
      deterministic — local fakes for credential-free e2e (NOT Opus evidence)
    """
    resolved = (mode or os.environ.get("SCUDO_AGENT_MODE") or "").strip().lower()
    if not resolved:
        from . import local_state

        resolved = "deterministic" if local_state.is_local() else "bedrock"

    if resolved in {"deterministic", "local", "fake"}:
        from .agents_local import (
            DeterministicCatalogueFillAgent,
            DeterministicMappingAgent,
            DeterministicVerifierAgent,
        )

        return (
            DeterministicMappingAgent(),
            DeterministicVerifierAgent(),
            None,
            DeterministicCatalogueFillAgent(),
        )

    if resolved in {"anthropic", "opus", "live"}:
        cache_key = f"anthropic:{ontology_snapshot}:{rubric_version}"
        if cache_key not in _AGENTS_CACHE:
            _AGENTS_CACHE[cache_key] = build_anthropic_agents(
                ontology_snapshot=ontology_snapshot,
                rubric_version=rubric_version,
            )
        return _AGENTS_CACHE[cache_key]

    if resolved != "bedrock":
        raise ValueError(f"unknown SCUDO_AGENT_MODE={resolved!r}")

    cache_key = f"bedrock:{ontology_snapshot}:{rubric_version}"
    if cache_key not in _AGENTS_CACHE:
        _AGENTS_CACHE[cache_key] = build_bedrock_agents(
            ontology_snapshot=ontology_snapshot,
            rubric_version=rubric_version,
        )
    return _AGENTS_CACHE[cache_key]
