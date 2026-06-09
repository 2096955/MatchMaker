"""SCUDO Bedrock smoke — exercises a real Strands Agent against AWS Bedrock.

What it does:
  • Builds a real BedrockModel pointing at the model id in
    scudo.shared.bedrock (default us.anthropic.claude-sonnet-4-6).
  • Builds a real strands.Agent for both the Mapping Specialist and the
    Verifier — no FakeAgents this time.
  • Hands them a fixed BriefBundle (no catalogue MCP, no Neptune) so the
    smoke is reproducible: only the model output varies.
  • Drives the full deterministic orchestrator: pre-verify defects pass into
    the verifier prompt, the publish gate fires, the outcome (PUBLISHED /
    HITL / RETRY) lands in a MappingObject.

Requires AWS credentials reachable to boto3 (CloudShell session, SSO, IAM
user, instance role — any of them). Cost is ~$0.01 per run on Sonnet 4.6.

Run:
    # In CloudShell — creds inherited from the session
    export AWS_REGION=us-east-1
    python -m scudo.tests.bedrock_smoke

    # On a laptop with SSO configured
    $env:AWS_PROFILE = "your-sso-profile"
    $env:AWS_REGION = "us-east-1"
    python -m scudo.tests.bedrock_smoke
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timezone
from uuid import uuid4

from strands import Agent
from strands.models import BedrockModel

from scudo import sidecar as scudo_sidecar
from scudo.orchestrator import Orchestrator
from scudo.schemas import (
    BriefBundle, CandidateNode, IntakeRequest, MappingResult, Route, VerifierReport,
)
from scudo.shared.bedrock import aws_region, bedrock_llm_id
from scudo.stubs import InMemoryHitlQueue, InMemoryPublishSink, InMemoryResearchQueue


# A small Mapping Specialist prompt geared at the structured-output call.
# The full prompts live in scudo.prompts; we use a tighter one here so the
# smoke fits on one screen and is deterministic across model upgrades.
_MAPPING_SYSTEM = (
    "You are the Mapping Specialist for JPMC's SCUDO enrichment pipeline. "
    "Map ONE vendor product to ONE CDAO node chosen from bundle.candidates. "
    "Cite at least one Evidence entry whose source_iris point at the chosen "
    "candidate IRI. Set proposed_triples to an empty list (the RDF serialiser "
    "is offline for this smoke). Set confidence_band to match the numeric "
    "confidence: high >= 0.8, medium >= 0.5, low < 0.5."
)

_VERIFIER_SYSTEM = (
    "You are the Verifier. Score the MappingResult against the 10-dimension "
    "rubric. Each dimension gets 0, 1, or 2; total <= 20. Do NOT redo the "
    "mapping; assess it. Output a VerifierReport with exactly 10 scores."
)


def _make_bundle() -> BriefBundle:
    """Fixed bundle so the smoke is reproducible across runs."""
    candidates = scudo_sidecar.candidate_nodes(term="estimates", limit=5)
    return BriefBundle(
        request=IntakeRequest(vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001"),
        route=Route.NEW_MAPPING,
        vendor_product_iri="mds.lseg:9b911986-764b-529c-be8f-9744d86ec0b8",
        vendor_assertion={
            "iri": "mds.lseg:9b911986-764b-529c-be8f-9744d86ec0b8",
            "vendor": "lseg",
            "vendor_product_ref": "LSEG-IBES-EST-001",
            "title": "I/B/E/S Estimates - Global Equities",
            "description": "Consensus analyst estimates across global equities.",
            "theme": "Investment Data",
            "asset_class": "Equities",
        },
        candidates=[CandidateNode(**c) for c in candidates],
        precedent=None,
        conflicts=[],
        assembled_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc),
        bundle_ref=f"bedrock-smoke-{uuid4()}",
    )


def main() -> int:
    model_id = bedrock_llm_id()
    region = aws_region()
    print(f"Bedrock model:  {model_id}")
    print(f"Region:         {region}")

    bundle = _make_bundle()
    print(f"\nBundle candidates ({len(bundle.candidates)}):")
    for c in bundle.candidates:
        print(f"  - {c.iri:55s} score={c.score:.3f}  {c.label}")

    # Real Strands agents. No tools / plugins — this smoke proves the model +
    # structured_output + Pydantic coercion + orchestrator gate all work.
    mapping_model = BedrockModel(model_id=model_id, region_name=region, temperature=0.2)
    verifier_model = BedrockModel(model_id=model_id, region_name=region, temperature=0.0)
    mapping_agent = Agent(model=mapping_model, system_prompt=_MAPPING_SYSTEM)
    verifier_agent = Agent(model=verifier_model, system_prompt=_VERIFIER_SYSTEM)

    # Bind everything to the orchestrator; bundle_assembler returns our fixed
    # bundle so the smoke isn't tied to a live catalogue MCP.
    orch = Orchestrator(
        mapping_specialist=mapping_agent,
        rights_specialist=None,
        verifier=verifier_agent,
        hitl_queue=InMemoryHitlQueue(),
        research_queue=InMemoryResearchQueue(),
        publish_sink=InMemoryPublishSink(),
        ontology_snapshot="cdao-2026-05-19",
        rubric_version="v1",
        bundle_assembler=lambda req, route: bundle,
    )

    print("\n-> orchestrator.run() — calls Bedrock for mapping + verifier")
    obj = orch.run({
        "vendor": "lseg", "vendor_product_ref": "LSEG-IBES-EST-001",
        "has_precedent": False, "has_conflict": False, "ontology_gap": False,
    })

    # Print the MappingResult that came back from Sonnet
    if obj.mapping_result is not None:
        mr = obj.mapping_result
        print(f"\nMappingResult:")
        print(f"  proposed_target_iri:    {mr.proposed_target_iri}")
        print(f"  confidence:             {mr.confidence:.2f}  (band: {mr.band.value})")
        print(f"  requires_human_review:  {mr.requires_human_review}")
        print(f"  rationale:              {mr.rationale[:200]}")
        print(f"  evidence entries:       {len(mr.evidence)}")
        for ev in mr.evidence[:3]:
            print(f"    - claim:    {ev.claim[:100]}")
            print(f"      sources:  {ev.source_iris}")
            if ev.quote:
                print(f"      quote:    {ev.quote[:120]}")

    # Print the VerifierReport
    if obj.verifier_report is not None:
        vr = obj.verifier_report
        print(f"\nVerifierReport:")
        print(f"  total_score:  {vr.total_score}/20")
        for s in vr.scores:
            print(f"    - {s.dimension.value:24s} {s.score}")
        if vr.defects:
            print(f"  defects:")
            for d in vr.defects:
                print(f"    - {d}")

    # Final outcome
    print(f"\nOrchestrator outcome: {obj.outcome.value}")
    print(f"  reason:           {obj.outcome_reason}")
    if obj.published_graph:
        print(f"  published_graph:  {obj.published_graph}")
    if obj.hitl_ticket:
        print(f"  hitl_ticket:      {obj.hitl_ticket}")

    # Smoke invariants — model output varies, but these MUST hold.
    assert obj.invocation_pins.get("ontology_snapshot") == "cdao-2026-05-19"
    assert obj.invocation_pins.get("rubric_version") == "v1"
    assert obj.outcome.value in {"published", "hitl", "retry"}, (
        f"unexpected outcome {obj.outcome.value!r}"
    )

    print("\nBEDROCK SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
