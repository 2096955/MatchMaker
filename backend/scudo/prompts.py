"""Specialist + verifier prompts.

These are scaffolds — coherent enough to drive a real run, not the final
production prompts (which want eval-harness tuning).
"""

from __future__ import annotations


from .schemas import BriefBundle, MappingResult, VerifierDimension


# ────────────────────────────────────────────────────────────────────────────
# The rubric, defined ONCE, in words, for BOTH models.
#
# Before this existed, `VerifierDimension` was ten bare enum names with no
# definition anywhere in the repo. Two consequences, both real:
#
#   1. The VERIFIER invented what each name meant on every call, so scores
#      were not comparable between runs — and `total_score` drives a hard
#      publish/retry/HITL gate.
#   2. The SPECIALIST was never shown the dimensions at all. It was graded on
#      a rubric it could not see. The symptom is visible in verifier_prompt's
#      hand-coded "include the ontology snapshot so taxonomy_freshness can be
#      scored" line: one dimension patched by hand because the list was not
#      shared.
#
# Deriving the text from the enum (rather than restating the names) means a
# new dimension cannot be added without a definition — `rubric_text()` raises.
#
# ON GAMING. Telling a model its grading rubric invites writing-to-the-scorer.
# Accepted deliberately, because the alternative is worse: a specialist
# optimising blind produces work the verifier then rejects for reasons nobody
# stated, which costs a Bedrock call and a HITL ticket per miss. Three things
# bound the risk — the verifier is a SEPARATE model, the deterministic
# `_gate_and_decide` checks (IRI echo, candidate membership, triple subjects)
# cannot be talked out of, and every dimension below rewards a VERIFIABLE
# property of the output, not a rhetorical one. "Cite the evidence you used"
# is not gameable in a way that hurts; it is the actual goal.
# ────────────────────────────────────────────────────────────────────────────
_RUBRIC: dict[VerifierDimension, str] = {
    VerifierDimension.SEMANTIC_FIT: (
        "does the chosen CDAO node actually mean what the vendor product is? "
        "Label overlap alone is not fit — a shared word across different asset "
        "classes scores 0."
    ),
    VerifierDimension.EVIDENCE_USE: (
        "is every claim in `rationale` backed by an Evidence entry drawn from "
        "the bundle? Outside knowledge, however correct, is not evidence here."
    ),
    VerifierDimension.CANDIDATE_COVERAGE: (
        "were the offered candidates genuinely considered, and is the runner-up "
        "addressed? Silently picking the top-scored one without saying why the "
        "others lose scores low."
    ),
    VerifierDimension.CONFLICT_HANDLING: (
        "if bundle.conflicts is non-empty, is each conflict named and resolved "
        "or escalated? Ignoring a stated conflict scores 0."
    ),
    VerifierDimension.CONFIDENCE_CALIBRATION: (
        "does `confidence` match the strength of the evidence? Both directions "
        "are penalised: a thin match at 0.95, and a well-evidenced one at 0.5. "
        "0.80 is the auto-publish floor, so this number has consequences."
    ),
    VerifierDimension.PROVENANCE_COMPLETE: (
        "do Evidence entries carry source_iris, and does the result carry the "
        "pins (ontology snapshot, rubric version) it was given?"
    ),
    VerifierDimension.IRI_DETERMINISM: (
        "are all IRIs echoed verbatim from the bundle rather than composed? "
        "This is ALSO enforced deterministically after you answer — a mismatch "
        "is rejected outright, so there is nothing to gain by guessing."
    ),
    VerifierDimension.TAXONOMY_FRESHNESS: (
        "does the result show it was scored against the stated ontology "
        "snapshot — i.e. does the snapshot appear in an Evidence source_iri or "
        "quote? An <unset> snapshot caps this at 1."
    ),
    VerifierDimension.RUBRIC_ADHERENCE: (
        "is the output the requested shape — one MappingResult, required fields "
        "populated, no commentary outside the schema?"
    ),
    VerifierDimension.RAW_QUERY_DISCIPLINE: (
        "no hand-authored SPARQL/Cypher/Turtle in any field. Triples come from "
        "the serialisation tool, never from the model."
    ),
}


def rubric_text(*, audience: str) -> str:
    """The 10 dimensions with definitions, phrased for ``specialist`` or
    ``verifier``.

    Raises if a ``VerifierDimension`` has no definition — a new dimension
    cannot silently ship undefined, which is the state this replaced.
    """
    missing = [d.value for d in VerifierDimension if d not in _RUBRIC]
    if missing:
        raise ValueError(f"VerifierDimension(s) with no rubric definition: {missing}")

    lines = "\n".join(f"  • {d.value} — {_RUBRIC[d]}" for d in VerifierDimension)
    if audience == "specialist":
        head = (
            "HOW YOUR WORK IS SCORED — a separate Verifier model grades this "
            "result on ten dimensions, 0/1/2 each (max 20). The total drives a "
            "hard publish / retry / human-review gate, so these are the goals, "
            "not style notes. Do the work well; do not write to the scorer:\n"
        )
    elif audience == "verifier":
        head = (
            "THE TEN DIMENSIONS — score each 0 (absent), 1 (partial), "
            "2 (full). These definitions are shared with the specialist, so "
            "you are scoring against the same standard it was given:\n"
        )
    else:  # pragma: no cover - programmer error
        raise ValueError(f"unknown audience {audience!r}")
    return head + lines


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
    # Part D — current best matching skill (SkillOpt-style), when one has been
    # promoted. A clearly-labelled, standalone section — not left buried
    # inside the BriefBundle JSON dump below, since SkillOpt's own premise is
    # that the skill doc is a standalone instructional text, not incidental
    # data. Omitted entirely (not even a "None" line) when nothing is
    # promoted yet.
    skill_section = (
        f"CURRENT BEST MATCHING SKILL (from prior verified outcomes):\n"
        f"{bundle.skill_hint}\n\n"
        if bundle.skill_hint
        else ""
    )
    pins = (
        f"Ontology snapshot: {bundle.ontology_snapshot or '<unset>'}\n"
        f"Rubric version:    {bundle.rubric_version or '<unset>'}\n"
    )
    return (
        skill_section
        + pins
        + "\nBriefBundle (everything you need — do not re-fetch):\n"
        f"{bundle.model_dump_json(indent=2)}\n\n"
        "HARD REQUIREMENTS — these are enforced by deterministic code AFTER\n"
        "you answer, not by the verifier's judgement. Violating one means the\n"
        "result is REJECTED outright (PublishGateError), so there is nothing to\n"
        "be gained by guessing:\n"
        f"  1. `vendor_product_iri` MUST be exactly "
        f"{bundle.vendor_product_iri or '<unset>'} — copy it verbatim from the\n"
        "     bundle. It is minted deterministically upstream and is the primary\n"
        "     key of the published record. Do NOT invent, reformat or re-derive it.\n"
        "  2. `proposed_target_iri` MUST be one of the IRIs listed in\n"
        "     bundle.candidates. You may not introduce a node that was not\n"
        "     offered, however plausible it seems. If none of the candidates fit,\n"
        "     that is a legitimate answer — set requires_human_review=true and\n"
        "     say why in `rationale`. An empty candidate list means nothing can\n"
        "     be published; say so rather than proposing something.\n\n"
        + rubric_text(audience="specialist")
        + "\n\n"
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
    result: MappingResult,
    *,
    rubric_version: str,
    ontology_snapshot: str = "",
) -> str:
    pins = (
        f"Rubric version: {rubric_version}\n"
        f"Ontology snapshot: {ontology_snapshot or '<unset>'}\n"
    )
    return (
        pins
        + "\n"
        + rubric_text(audience="verifier")
        + "\n\nCompute total_score = sum of the ten dimension scores.\n\n"
        "For taxonomy_freshness: if the ontology snapshot above appears in any "
        "Evidence entry's source_iris or quote, score 2. If the snapshot is "
        "<unset>, score at most 1.\n\n"
        f"MappingResult:\n{result.model_dump_json(indent=2)}\n\n"
        "If any defect breaks an invariant (non-deterministic IRI, missing "
        "named graph, mapping outside bundle.candidates, raw query text), "
        "set the matching dimension to 0 and list the defect."
    )


__all__ = [
    "MAPPING_SYSTEM",
    "RIGHTS_SYSTEM",
    "VERIFIER_SYSTEM",
    "mapping_prompt",
    "research_prompt",
    "verifier_prompt",
]
