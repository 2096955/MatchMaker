"""Part D — the current best matching skill (SkillOpt-style) surfaces in the
Orchestrator's mapping prompt.

Ground truth verified this session: orchestrator.py's mapping_specialist
(Bedrock's bare strands.Agent or Azure's AzureOpenAIShim, built by
_build_bedrock_agents()/_build_azure_agents()) is called via
self._structured_call(self.mapping, MappingResult, prompt) — a single
structured_output(Model, prompt) call for BOTH backends, with NO tool-calling
loop on either side (unlike scudo_mapping_mcp/agent.py's BedrockMappingAgent/
AzureMappingAgent, a DIFFERENT pipeline). So both backends get the skill hint
the same way: as part of the one prompt string mapping_prompt(bundle) builds
from BriefBundle.skill_hint — no tool, no asymmetric delivery needed here.

Pure-function tests: no Aurora, no monkeypatching, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from scudo.schemas import BriefBundle, IntakeRequest, Route


def _bundle(skill_hint=None) -> BriefBundle:
    request = IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-EQ-1")
    return BriefBundle(
        request=request,
        route=Route.NEW_MAPPING,
        vendor_product_iri="mds.lseg:x",
        vendor_assertion={"iri": "mds.lseg:x", "vendor": "lseg"},
        candidates=[],
        assembled_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc),
        bundle_ref=f"bundle-{uuid4()}",
        skill_hint=skill_hint,
    )


def test_briefbundle_accepts_skill_hint_field():
    bundle = _bundle(skill_hint="Prefer exact vendor-code matches.")
    assert bundle.skill_hint == "Prefer exact vendor-code matches."


def test_briefbundle_skill_hint_defaults_to_none():
    bundle = _bundle()
    assert bundle.skill_hint is None


def test_mapping_prompt_surfaces_skill_hint_prominently_when_present():
    from scudo.prompts import mapping_prompt

    bundle = _bundle(
        skill_hint="Prefer exact vendor-code matches over fuzzy label matches."
    )
    prompt = mapping_prompt(bundle)

    assert "Prefer exact vendor-code matches over fuzzy label matches." in prompt
    # must be a clearly-labelled section, not just buried inside the JSON dump
    assert "SKILL" in prompt.upper()


def test_mapping_prompt_omits_skill_section_gracefully_when_absent():
    from scudo.prompts import mapping_prompt

    bundle = _bundle(skill_hint=None)
    prompt = mapping_prompt(bundle)

    # must not crash, must not print the literal word "None" as if it were a skill
    assert "\nNone\n" not in prompt
    assert prompt  # still produces a normal prompt
