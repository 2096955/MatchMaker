"""Single-file SCUDO demo for CloudShell — no SCUDO package required.

Paste this into CloudShell (~/cloudshell_demo.py), then:
  pip install --quiet strands-agents pydantic
  export AWS_REGION=us-east-1
  python ~/cloudshell_demo.py

It exercises the load-bearing SCUDO pattern in one file:
  • A real BedrockModel pointing at Opus 4.8 (strongest Claude on Bedrock).
  • A real strands.Agent for both the Mapping Specialist and the Verifier.
  • Pydantic-typed MappingResult and VerifierReport (forced via structured_output).
  • A deterministic FAKE RDF serialiser (DCAT triples + named graph) so the
    publish path can actually run end-to-end without the JPMC serialiser.
  • The deterministic publish gate (verifier >= 16, confidence >= 0.80,
    evidence-when-confident, IRI determinism, named-graph provenance).

With the fake serialiser present, the gate now routes to PUBLISHED when
verifier and confidence pass — not HITL.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pydantic import BaseModel, ConfigDict, Field
from strands import Agent
from strands.models import BedrockModel


# ────────────────────────────────────────────────────────────────────────────
# Schemas — the typed surfaces SCUDO binds the agents to
# ────────────────────────────────────────────────────────────────────────────
class Band(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def for_confidence(cls, c: float) -> "Band":
        if c >= 0.8: return cls.HIGH
        if c >= 0.5: return cls.MEDIUM
        return cls.LOW


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(..., min_length=1)
    source_iris: list[str] = Field(default_factory=list)
    quote: Optional[str] = None


class ProposedTriple(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    predicate: str
    object: str
    graph: str


class MappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_product_iri: str
    proposed_target_iri: str
    rationale: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    band: Band
    requires_human_review: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    proposed_triples: list[ProposedTriple] = Field(default_factory=list)


class VerifierDimension(str, Enum):
    SEMANTIC_FIT = "semantic_fit"
    EVIDENCE_USE = "evidence_use"
    CANDIDATE_COVERAGE = "candidate_coverage"
    CONFLICT_HANDLING = "conflict_handling"
    CONFIDENCE_CALIBRATION = "confidence_calibration"
    PROVENANCE_COMPLETE = "provenance_complete"
    IRI_DETERMINISM = "iri_determinism"
    TAXONOMY_FRESHNESS = "taxonomy_freshness"
    RUBRIC_ADHERENCE = "rubric_adherence"
    RAW_QUERY_DISCIPLINE = "raw_query_discipline"


class VerifierScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: VerifierDimension
    score: int = Field(..., ge=0, le=2)
    note: Optional[str] = None


class VerifierReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scores: list[VerifierScore] = Field(..., min_length=10, max_length=10)
    total_score: int = Field(..., ge=0, le=20)
    defects: list[str] = Field(default_factory=list)
    rubric_version: str


# ────────────────────────────────────────────────────────────────────────────
# Fixture — one LSEG product, two CDAO candidates
# ────────────────────────────────────────────────────────────────────────────
_IRI_NAMESPACE = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/catalogue")


def product_iri(vendor: str, vendor_product_ref: str) -> str:
    return f"mds.{vendor}:{uuid5(_IRI_NAMESPACE, f'{vendor}:{vendor_product_ref}')}"


VENDOR_PRODUCT = {
    "iri": product_iri("lseg", "LSEG-IBES-EST-001"),
    "vendor": "lseg",
    "vendor_product_ref": "LSEG-IBES-EST-001",
    "title": "I/B/E/S Estimates - Global Equities",
    "description": "Consensus analyst estimates across global equities.",
    "theme": "Investment Data",
    "asset_class": "Equities",
}

CANDIDATES = [
    {"iri": "jpmorgan:data:cdao:EquityResearch", "label": "EquityResearch",
     "definition": "Sell-side and independent analyst research on listed equities.",
     "score": 0.92},
    {"iri": "jpmorgan:data:cdao:InvestmentData", "label": "InvestmentData",
     "definition": "Curated investment-decision-relevant data products.",
     "score": 0.71},
]

# Version pins — load-bearing for taxonomy_freshness scoring (see verifier
# prompt). Carrying them in the bundle text is what lets the model cite them
# in evidence, which is what the verifier looks for.
ONTOLOGY_SNAPSHOT = "cdao-2026-05-19"
RUBRIC_VERSION = "v1"


# ────────────────────────────────────────────────────────────────────────────
# Prompts — concise, structured-output friendly
# ────────────────────────────────────────────────────────────────────────────
_MAPPING_SYSTEM = (
    "You are the SCUDO Mapping Specialist. Map ONE vendor product to ONE CDAO "
    "node from bundle.candidates. Cite at least one Evidence entry whose "
    "source_iris point at the chosen candidate IRI plus the vendor_product_iri. "
    "Include the ontology_snapshot value from the bundle in at least one "
    "Evidence entry's source_iris or quote — this lets the verifier confirm "
    "the candidate is from the current taxonomy edition. Set confidence_band "
    "from the numeric confidence: high>=0.8, medium>=0.5, low<0.5. Set "
    "proposed_triples = [] (RDF serialiser is offline for this demo)."
)

_VERIFIER_SYSTEM = (
    "You are the SCUDO Verifier. Score the MappingResult against the 10-"
    "dimension rubric. Each dimension scores 0, 1, or 2; total <= 20. Do not "
    "redo the mapping; assess it. For taxonomy_freshness: if the ontology "
    "snapshot from the bundle (given at the top of the user prompt) appears "
    "in any Evidence entry's source_iris or quote, score 2; otherwise score "
    "at most 1. Return a VerifierReport with all 10 dimensions present."
)


def _bundle_text() -> str:
    import json
    return (
        f"ontology_snapshot = {ONTOLOGY_SNAPSHOT}\n"
        f"rubric_version    = {RUBRIC_VERSION}\n"
        "\nBriefBundle:\n"
        f"  vendor_product = {json.dumps(VENDOR_PRODUCT, indent=2)}\n"
        f"  candidates     = {json.dumps(CANDIDATES, indent=2)}\n"
    )


# ────────────────────────────────────────────────────────────────────────────
# Fake RDF serialiser — deterministic DCAT triples + named graph
# Mirrors scudo.rdf.fake; inlined so this file is self-contained.
# ────────────────────────────────────────────────────────────────────────────
_GRAPH_NAMESPACE = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/graphs")


def named_graph_for(vendor_product_iri: str, snapshot: str) -> str:
    suffix = uuid5(_GRAPH_NAMESPACE, f"{vendor_product_iri}:{snapshot}")
    return f"jpmorgan:data:cdao:graphs:enrichment:{suffix}"


def serialise_mapping(mr: MappingResult, snapshot: str) -> list[ProposedTriple]:
    """Deterministic DCAT triple emission. Same MappingResult + snapshot →
    identical triples, including identical named-graph IRI."""
    subject = mr.vendor_product_iri
    graph = named_graph_for(subject, snapshot)
    triples: list[ProposedTriple] = []

    def add(predicate: str, obj: str) -> None:
        triples.append(ProposedTriple(
            subject=subject, predicate=predicate, object=obj, graph=graph,
        ))

    add("rdf:type", "dcat:Dataset")
    if mr.rationale:
        add("dct:description", mr.rationale[:200])
    if mr.proposed_target_iri:
        add("dcat:theme", mr.proposed_target_iri)
    if snapshot:
        add("dcat:version", snapshot)
    if subject.startswith("mds.") and ":" in subject:
        vendor = subject[len("mds."):].split(":", 1)[0]
        add("prov:wasAttributedTo", f"jpmorgan:data:vendors:{vendor}")
    for ev in mr.evidence:
        for src in ev.source_iris:
            if src.startswith("jpmorgan:data:cdao:") or src.startswith("mds."):
                add("rdfs:seeAlso", src)
    return triples


def validate_shapes(triples: list[ProposedTriple]) -> tuple[bool, list[str]]:
    """SHACL stub — checks structural completeness only."""
    violations: list[str] = []
    if not triples:
        violations.append("empty triple list")
    for i, t in enumerate(triples):
        for k in ("subject", "predicate", "object", "graph"):
            if not getattr(t, k):
                violations.append(f"triple[{i}] missing {k}")
    return (not violations, violations)


# ────────────────────────────────────────────────────────────────────────────
# Deterministic publish gate — pure Python, no model in the loop
# ────────────────────────────────────────────────────────────────────────────
VERIFIER_AUTOPUBLISH = 16
VERIFIER_RETRY_LO, VERIFIER_RETRY_HI = 12, 15
CONFIDENCE_FLOOR = 0.80
EVIDENCE_REQUIRED_ABOVE = 0.5
_IRI_DETERMINISM = re.compile(r"^(mds\.[a-z0-9_]+:[0-9a-f-]{36}|jpmorgan:data:cdao:.+)$")


def pre_verify_defects(result: MappingResult) -> list[str]:
    """The orchestrator-side checks SCUDO runs before the verifier call."""
    candidate_iris = {c["iri"] for c in CANDIDATES}
    defects: list[str] = []
    if result.proposed_target_iri not in candidate_iris:
        defects.append(f"proposed_target_iri {result.proposed_target_iri!r} not in candidates")
    if result.confidence > EVIDENCE_REQUIRED_ABOVE and not result.evidence:
        defects.append(f"confidence={result.confidence:.2f} but no evidence cited")
    for ev in result.evidence:
        if not ev.source_iris:
            defects.append(f"evidence {ev.claim!r} has no source_iris")
    expected = Band.for_confidence(result.confidence)
    if result.band is not expected:
        defects.append(
            f"band {result.band.value!r} disagrees with confidence "
            f"{result.confidence:.2f} (expected {expected.value!r})"
        )
    return defects


def decide_outcome(result: MappingResult, report: VerifierReport) -> tuple[str, str]:
    """Returns (outcome, reason). Outcomes: 'published'/'hitl'/'retry'."""
    total = report.total_score
    conf = result.confidence

    if total < VERIFIER_RETRY_LO or conf < CONFIDENCE_FLOOR or result.requires_human_review:
        return "hitl", (
            f"floor breach (verifier={total}, confidence={conf:.2f}, "
            f"self_flag={result.requires_human_review})"
        )
    if VERIFIER_RETRY_LO <= total <= VERIFIER_RETRY_HI:
        return "retry", f"verifier {total} in 12-15 — retry once then HITL"

    # Would-be publish — verify triples + named graph + deterministic IRIs.
    if not result.proposed_triples:
        return "hitl", "verifier OK but proposed_triples empty (serialiser failed)"
    for t in result.proposed_triples:
        if not t.graph:
            return "hitl", "triple missing named graph"
        if not _IRI_DETERMINISM.match(t.subject):
            return "hitl", f"non-deterministic IRI {t.subject!r}"
    return "published", "verifier >= 16, confidence >= floor, triples valid"


# ────────────────────────────────────────────────────────────────────────────
# Main — wire the real agents, run one mapping, print everything
# ────────────────────────────────────────────────────────────────────────────
def main() -> int:
    model_id = os.environ.get("BEDROCK_LLM_MODEL_ID", "us.anthropic.claude-opus-4-8")
    region = os.environ.get("AWS_REGION", "us-east-1")
    print(f"Bedrock model:   {model_id}")
    print(f"Region:          {region}")
    print(f"\nVendor product:  {VENDOR_PRODUCT['vendor_product_ref']} - "
          f"{VENDOR_PRODUCT['title']}")
    print(f"Candidates:")
    for c in CANDIDATES:
        print(f"  - {c['iri']:55s} score={c['score']:.3f}  {c['label']}")

    # NOTE: Opus 4.8 (and several other Claude 4.x profiles) deprecated the
    # `temperature` kwarg on Bedrock. Omitting it uses model defaults, which
    # are deterministic enough for structured output. Older models (Sonnet 3.x,
    # Haiku 3.x) still accept it.
    mapping_model = BedrockModel(model_id=model_id, region_name=region)
    verifier_model = BedrockModel(model_id=model_id, region_name=region)
    mapping_agent = Agent(model=mapping_model, system_prompt=_MAPPING_SYSTEM)
    verifier_agent = Agent(model=verifier_model, system_prompt=_VERIFIER_SYSTEM)

    print(f"\n-> mapping_agent(prompt, structured_output_model=MappingResult)")
    print(f"   (calling Bedrock — this can take ~5-30s on Opus)")
    mapping_prompt = (
        "Map the vendor product to ONE CDAO candidate.\n\n" + _bundle_text() +
        "\nReturn a MappingResult. The vendor_product_iri MUST equal "
        f"{VENDOR_PRODUCT['iri']!r}. Cite ontology_snapshot "
        f"{ONTOLOGY_SNAPSHOT!r} in at least one Evidence entry."
    )
    result = mapping_agent(mapping_prompt, structured_output_model=MappingResult).structured_output

    print(f"\nMappingResult:")
    print(f"  proposed_target_iri:    {result.proposed_target_iri}")
    print(f"  confidence:             {result.confidence:.2f}  (band: {result.band.value})")
    print(f"  requires_human_review:  {result.requires_human_review}")
    print(f"  rationale:              {result.rationale[:240]}")
    print(f"  evidence ({len(result.evidence)}):")
    for ev in result.evidence:
        print(f"    - claim:    {ev.claim[:120]}")
        print(f"      sources:  {ev.source_iris}")
        if ev.quote:
            print(f"      quote:    {ev.quote[:120]}")

    # Run the fake RDF serialiser — populates proposed_triples so the gate
    # can route to PUBLISHED. Mutates the MappingResult in place.
    triples = serialise_mapping(result, ONTOLOGY_SNAPSHOT)
    conforms, violations = validate_shapes(triples)
    result = result.model_copy(update={"proposed_triples": triples})
    print(f"\n-> rdf_serialise_mapping + rdf_validate_shapes")
    print(f"   triples emitted:  {len(triples)}")
    print(f"   named graph:      {triples[0].graph if triples else '(none)'}")
    print(f"   SHACL conforms:   {conforms}")
    if violations:
        print(f"   violations:       {violations}")

    # Pre-verify defects pass into the verifier prompt as additional context.
    defects = pre_verify_defects(result)
    if defects:
        print(f"\nPre-verify defects (will be fed to the verifier):")
        for d in defects:
            print(f"  - {d}")

    print(f"\n-> verifier_agent(prompt, structured_output_model=VerifierReport)")
    verifier_prompt = (
        f"Rubric version: {RUBRIC_VERSION}\n"
        f"Ontology snapshot: {ONTOLOGY_SNAPSHOT}\n\n"
        f"Score the following MappingResult on each of the 10 dimensions "
        f"(0/1/2). Compute total_score = sum.\n\n"
        f"For taxonomy_freshness: if {ONTOLOGY_SNAPSHOT!r} appears in any "
        f"Evidence entry's source_iris or quote, score 2; otherwise score "
        f"at most 1.\n\n"
        f"MappingResult:\n{result.model_dump_json(indent=2)}\n\n"
        f"Pre-verifier defects already detected:\n  - "
        + "\n  - ".join(defects or ["(none)"])
    )
    report = verifier_agent(verifier_prompt, structured_output_model=VerifierReport).structured_output

    print(f"\nVerifierReport:")
    print(f"  total_score:  {report.total_score}/20")
    for s in report.scores:
        note = f"  ({s.note})" if s.note else ""
        print(f"    - {s.dimension.value:24s} {s.score}{note}")
    if report.defects:
        print(f"  defects:")
        for d in report.defects:
            print(f"    - {d}")

    outcome, reason = decide_outcome(result, report)
    print(f"\nPublish-gate outcome:  {outcome.upper()}")
    print(f"  reason:              {reason}")

    if outcome == "published":
        print(f"\nPublished triples (named graph: {result.proposed_triples[0].graph}):")
        for t in result.proposed_triples:
            obj = t.object if len(t.object) <= 80 else t.object[:77] + "..."
            print(f"  <{t.subject[:40]:40s}> {t.predicate:24s} <{obj}>")

    print(f"\nDEMO OK — model produced typed output through every SCUDO surface.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
