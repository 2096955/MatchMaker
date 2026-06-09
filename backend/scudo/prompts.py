"""Specialist + verifier prompts.

These are scaffolds — coherent enough to drive a real run, not the final
production prompts (which want eval-harness tuning).
"""
from __future__ import annotations

import json

from .schemas import BriefBundle, MappingResult


MAPPING_SYSTEM = (
    "You are the Mapping Specialist for JPMC's SCUDO enrichment pipeline. "
    "You map ONE vendor product to ONE CDAO node from the candidates in the "
    "BriefBundle. You do not route, you do not assemble context, you do not "
    "publish — the orchestrator does those. "
    "Activate the taxonomy-mapping skill; use rdf-serialisation for triples; "
    "use neptune-io only if the bundle is missing what you need (the hook caps "
    "reads at 12). Output ONE MappingResult."
)

RIGHTS_SYSTEM = (
    "You are the Rights Specialist. Express extracted licence terms as "
    "adapted-ODRL (RDFS semantics, simplified constraints) — clarif. 13. "
    "Activate the rights-odrl skill; serialise via rdf-serialisation. You do "
    "not publish."
)

VERIFIER_SYSTEM = (
    "You are the Verifier. Score the specialist's MappingResult against the "
    "10-dimension rubric. Each dimension scores 0, 1, or 2 — total ≤ 20. "
    "You do NOT re-do the mapping; you assess the work that's been done. "
    "Output ONE VerifierReport carrying ten scores plus defect notes."
)


def mapping_prompt(bundle: BriefBundle) -> str:
    pins = (
        f"Ontology snapshot: {bundle.ontology_snapshot or '<unset>'}\n"
        f"Rubric version:    {bundle.rubric_version or '<unset>'}\n"
    )
    return (
        pins +
        "\nBriefBundle (everything you need — do not re-fetch):\n"
        f"{bundle.model_dump_json(indent=2)}\n\n"
        "Return a MappingResult that:\n"
        "  • selects a target IRI from bundle.candidates (or sets "
        "    requires_human_review=true if none fit),\n"
        "  • cites the bundle in `rationale` (1–3 sentences, no outside knowledge),\n"
        "  • is calibrated in `confidence` (0.80 floor for auto-publish),\n"
        f"  • includes the ontology snapshot ({bundle.ontology_snapshot or '<unset>'}) "
        "in at least one Evidence entry's source_iris or quote, so the verifier "
        "can score taxonomy_freshness on real evidence,\n"
        "  • carries proposed_triples produced by rdf_serialise_mapping — "
        "    do NOT hand-author Turtle."
    )


def research_prompt(bundle: BriefBundle) -> str:
    return (
        "Ontology gap was flagged on intake. Produce a write-up for the "
        "ontology owner describing the missing node(s), what the vendor "
        "asserts, and which existing CDAO nodes are closest. Do NOT publish. "
        "Bundle:\n"
        f"{bundle.model_dump_json(indent=2)}"
    )


def verifier_prompt(
    result: MappingResult, *, rubric_version: str, ontology_snapshot: str = "",
) -> str:
    pins = (
        f"Rubric version: {rubric_version}\n"
        f"Ontology snapshot: {ontology_snapshot or '<unset>'}\n"
    )
    return (
        pins +
        "\nScore the following MappingResult on the 10-dimension rubric. "
        "For each dimension, return 0 (absent), 1 (partial), or 2 (full). "
        "Compute total_score = sum of dimension scores.\n\n"
        "For taxonomy_freshness: if the ontology snapshot above appears in any "
        "Evidence entry's source_iris or quote, score 2. If the snapshot is "
        "<unset>, score at most 1.\n\n"
        f"MappingResult:\n{result.model_dump_json(indent=2)}\n\n"
        "If any defect breaks an invariant (non-deterministic IRI, missing "
        "named graph, mapping outside bundle.candidates, raw query text), "
        "set the matching dimension to 0 and list the defect."
    )


__all__ = [
    "MAPPING_SYSTEM", "RIGHTS_SYSTEM", "VERIFIER_SYSTEM",
    "mapping_prompt", "research_prompt", "verifier_prompt",
]
