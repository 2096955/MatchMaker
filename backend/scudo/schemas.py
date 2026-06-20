"""SCUDO Pydantic contracts.

These are the typed surfaces between the orchestrator, the specialists, the
verifier, and downstream Neptune/RDF backends. Keeping them in one module makes
the data shapes the load-bearing thing — agents and tools are wired around
them, not the other way around.

Schema versioning: bump `SCHEMA_VERSION` on any breaking change; the orchestrator
stamps it into `invocation_state` so a replayed run with a different schema is
detectable (clarif. G39).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1.0"


# ────────────────────────────────────────────────────────────────────────────
# Routing — deterministic, set by the orchestrator's intake step
# ────────────────────────────────────────────────────────────────────────────
class Route(str, Enum):
    NEW_MAPPING = "NEW_MAPPING"
    EXTEND_MAPPING = "EXTEND_MAPPING"
    RECONCILE_CONFLICT = "RECONCILE_CONFLICT"
    RESEARCH = "RESEARCH"


# ────────────────────────────────────────────────────────────────────────────
# Intake — what arrives at the orchestrator
# ────────────────────────────────────────────────────────────────────────────
class IntakeRequest(BaseModel):
    """The deterministic intake stamps these flags before any agent reasons."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(..., examples=["lseg"])
    vendor_product_ref: str = Field(..., min_length=1, examples=["LSEG-IBES-EST-001"])
    has_precedent: bool = False
    has_conflict: bool = False
    ontology_gap: bool = False


# ────────────────────────────────────────────────────────────────────────────
# BriefBundle — pre-assembled context handed to the Mapping Specialist
# ────────────────────────────────────────────────────────────────────────────
class CandidateNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iri: str = Field(..., examples=["jpmorgan:data:cdao:EquityResearch"])
    label: str
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Retrieval score from Neptune."
    )


class PrecedentMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_iri: str
    target_iri: str
    rationale: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    other_vendor: str
    other_vendor_product_ref: str
    other_target_iri: str
    note: Optional[str] = None


class BriefBundle(BaseModel):
    """Everything a specialist needs in one envelope — no rediscovery loops.

    Carrying the bundle is the structural guard against the rediscovery
    failure mode (the hook caps Neptune reads as defence-in-depth).
    """

    model_config = ConfigDict(extra="forbid")

    request: IntakeRequest
    route: Route
    vendor_product_iri: str = Field(
        ..., description="mds.<vendor>:<uuid> from catalogue MCP."
    )
    vendor_assertion: dict = Field(
        ..., description="NormalisedProduct payload from the catalogue MCP, as dict."
    )
    candidates: list[CandidateNode] = Field(default_factory=list, max_length=25)
    precedent: Optional[PrecedentMapping] = None
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    assembled_at: datetime
    bundle_ref: str = Field(
        ..., description="Replay-safe handle for the assembled bundle."
    )
    # Version pins — carried explicitly so the specialist can cite them in
    # evidence and the verifier can score taxonomy_freshness on real evidence
    # rather than an absence. Default to "" so existing assemblers don't break;
    # the orchestrator backfills from its config if missing.
    ontology_snapshot: str = Field(
        default="",
        description="CDAO snapshot the candidates were retrieved from (e.g. cdao-2026-05-19).",
    )
    rubric_version: str = Field(
        default="",
        description="Verifier rubric version the specialist is being scored against.",
    )


# ────────────────────────────────────────────────────────────────────────────
# MappingResult — specialist's output
# ────────────────────────────────────────────────────────────────────────────
class ProposedTriple(BaseModel):
    """Triple plus its named graph (no graph → no provenance → blocked at publish)."""

    model_config = ConfigDict(extra="forbid")
    subject: str
    predicate: str
    object: str
    graph: str = Field(..., description="Named graph carrying the provenance envelope.")


class Band(str, Enum):
    """Confidence band — high / medium / low, per the taxonomy-mapping skill.
    Threshold: high ≥ 0.8 ; medium 0.5 ≤ c < 0.8 ; low < 0.5."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def for_confidence(cls, c: float) -> "Band":
        if c >= 0.8:
            return cls.HIGH
        if c >= 0.5:
            return cls.MEDIUM
        return cls.LOW


class Evidence(BaseModel):
    """A single cited piece of support for a mapping assertion.

    Per the taxonomy-mapping skill: every assertion above confidence > 0.5
    needs non-empty, cited evidence (including which retrieved candidate and
    which authoritative node supported the call). Evidence-free = hallucination;
    the orchestrator's pre-verifier check rejects it.
    """

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(..., min_length=1, description="What this evidence supports.")
    source_iris: list[str] = Field(
        default_factory=list,
        description=(
            "IRIs cited — typically a graphrag candidate IRI and the "
            "authoritative CDAO node IRI returned by neptune_node_by_iri."
        ),
    )
    quote: Optional[str] = Field(
        default=None,
        description="Optional verbatim span from a source (vendor product, "
        "CDAO definition, precedent rationale, etc.).",
    )


class MappingResult(BaseModel):
    """What the Mapping Specialist returns. Carries everything the publish gate
    needs to decide auto-publish vs. retry vs. HITL.
    """

    model_config = ConfigDict(extra="forbid")

    vendor_product_iri: str
    proposed_target_iri: str = Field(
        ...,
        examples=["jpmorgan:data:cdao:EquityResearch"],
        description="Selected CDAO node, or empty when route is RESEARCH.",
    )
    rationale: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    band: Band
    requires_human_review: bool = False
    evidence: list[Evidence] = Field(
        default_factory=list,
        description=(
            "Cited support for the mapping. The taxonomy-mapping skill requires "
            "non-empty, cited evidence for any assertion with confidence > 0.5."
        ),
    )
    proposed_triples: list[ProposedTriple] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# VerifierReport — the 10-dimension rubric, 0/1/2 each, total ≤ 20
# ────────────────────────────────────────────────────────────────────────────
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

    def recompute_total(self) -> int:
        return sum(s.score for s in self.scores)


# ────────────────────────────────────────────────────────────────────────────
# Final outcomes — what flows back out of orchestrator.run()
# ────────────────────────────────────────────────────────────────────────────
class Outcome(str, Enum):
    PUBLISHED = "published"
    HITL = "hitl"
    RETRY = "retry"
    RESEARCH_QUEUED = "research_queued"


class MappingObject(BaseModel):
    """The replay-safe envelope persisted at the end of every run (clarif. L69)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    route: Route
    bundle_ref: str
    mapping_result: Optional[MappingResult] = None
    verifier_report: Optional[VerifierReport] = None
    outcome: Outcome
    outcome_reason: Optional[str] = None
    published_graph: Optional[str] = None
    hitl_ticket: Optional[str] = None
    invocation_pins: dict = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────
# Batch ledger — the self-verifying-loop's explicit requeue ledger.
#
# A swarm with no ledger has exactly one quality setting: whatever the worst
# unit produced. The ledger makes every pass, every requeue reason, and every
# quarantined unit inspectable after the fact (the `loops` skill's run-history
# observability) instead of living in the model's head. Additive to the schema:
# nothing here is embedded in MappingObject, so SCHEMA_VERSION is unchanged.
# ────────────────────────────────────────────────────────────────────────────
class UnitStatus(str, Enum):
    """Terminal state of a single batch unit. PASSED = verified clean and
    published; RESEARCH = escalated to the ontology owner (terminal, never
    published, *not* verified-clean); QUARANTINED = needs a human (HITL self-flag
    / floor breach, or a retry budget exhausted); REQUEUED = transient, in flight."""

    PASSED = "passed"
    REQUEUED = "requeued"
    QUARANTINED = "quarantined"
    RESEARCH = "research"


class RejectedUnit(BaseModel):
    """A unit that did not pass, carrying the reason so it rides back into the
    rerun (self-verifying-loop) and becomes the quarantine note if it never passes."""

    model_config = ConfigDict(extra="forbid")

    unit_key: str = Field(
        ..., description="vendor + vendor_product_ref, collision-safe join."
    )
    reason: str = Field(
        ..., min_length=1, description="Why the unit was rejected this pass."
    )
    last_outcome: Outcome


class LedgerPass(BaseModel):
    """One pass of the verify→requeue cycle over the pending units."""

    model_config = ConfigDict(extra="forbid")

    pass_no: int = Field(..., ge=1)
    checked: int = Field(..., ge=0)
    passed: int = Field(..., ge=0)
    requeued: int = Field(..., ge=0)
    rejected: list[RejectedUnit] = Field(default_factory=list)


class BatchLedger(BaseModel):
    """The auditable record of a batch run. Source of truth for resume: a unit in
    `passed`/`research`/`quarantined` is terminal and is skipped on a resumed run
    (the `loops` checkpoint property — no re-call of the LLM for done units)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    schema_version: str = Field(default=SCHEMA_VERSION)
    retry_budget: int = Field(..., ge=1)
    passes: list[LedgerPass] = Field(default_factory=list)
    passed: list[str] = Field(default_factory=list, description="unit_keys published.")
    research: list[str] = Field(
        default_factory=list, description="unit_keys escalated to ontology owner."
    )
    quarantined: list[RejectedUnit] = Field(
        default_factory=list, description="unit_keys needing a human, with last reason."
    )

    def terminal_keys(self) -> set[str]:
        """All unit_keys that have reached a terminal state — used to skip work on resume."""
        return (
            set(self.passed)
            | set(self.research)
            | {r.unit_key for r in self.quarantined}
        )


__all__ = [
    "SCHEMA_VERSION",
    "Route",
    "IntakeRequest",
    "CandidateNode",
    "PrecedentMapping",
    "ConflictRecord",
    "BriefBundle",
    "ProposedTriple",
    "Band",
    "Evidence",
    "MappingResult",
    "VerifierDimension",
    "VerifierScore",
    "VerifierReport",
    "Outcome",
    "MappingObject",
    "UnitStatus",
    "RejectedUnit",
    "LedgerPass",
    "BatchLedger",
]
