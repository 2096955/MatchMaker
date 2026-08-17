"""Typed contracts between orchestrator, specialists, verifier, and sinks."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1.0"


class Route(str, Enum):
    NEW_MAPPING = "NEW_MAPPING"
    EXTEND_MAPPING = "EXTEND_MAPPING"
    RECONCILE_CONFLICT = "RECONCILE_CONFLICT"
    RESEARCH = "RESEARCH"


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: str = Field(..., examples=["lseg"])
    vendor_product_ref: str = Field(..., min_length=1, examples=["LSEG-IBES-EST-001"])
    has_precedent: bool = False
    has_conflict: bool = False
    ontology_gap: bool = False
    agent_provider: Optional[str] = Field(
        default=None, description="Inference runtime provider (bedrock)."
    )


class CandidateNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iri: str = Field(..., examples=["jpmorgan:data:cdao:EquityResearch"])
    label: str
    score: float = Field(..., ge=0.0, le=1.0, description="Retrieval score.")


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
    model_config = ConfigDict(extra="forbid")
    request: IntakeRequest
    route: Route
    vendor_product_iri: str = Field(..., description="mds.<vendor>:<uuid5>.")
    vendor_assertion: dict = Field(..., description="Normalised product payload.")
    candidates: list[CandidateNode] = Field(default_factory=list, max_length=25)
    precedent: Optional[PrecedentMapping] = None
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    skill_hint: Optional[str] = Field(
        default=None, description="Promoted matching skill text, if any."
    )
    skill_version: Optional[int] = Field(default=None, ge=1)
    promoted_rules: list[dict] = Field(
        default_factory=list,
        description="Vendor-scoped rules from Aurora CONSULT (advisory).",
    )
    system_context: str = Field(
        default="",
        description="Zone + catalogue/rights ontology context for the specialist.",
    )
    assembled_at: datetime
    bundle_ref: str = Field(..., description="Replay-safe bundle handle.")
    ontology_snapshot: str = Field(default="", description="CDAO snapshot id.")
    rubric_version: str = Field(default="", description="Verifier rubric version.")


class ProposedTriple(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    predicate: str
    object: str
    graph: str = Field(..., description="Named graph for provenance.")


class Band(str, Enum):
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
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(..., min_length=1, description="What this evidence supports.")
    source_iris: list[str] = Field(default_factory=list, description="Cited IRIs.")
    quote: Optional[str] = Field(default=None, description="Optional source span.")


class MappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_product_iri: str
    proposed_target_iri: str = Field(
        ..., description="Selected CDAO node, or empty on RESEARCH."
    )
    rationale: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    band: Band
    requires_human_review: bool = False
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Cited support; required when confidence > 0.5.",
    )
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

    def recompute_total(self) -> int:
        return sum(s.score for s in self.scores)


class Outcome(str, Enum):
    PUBLISHED = "published"
    HITL = "hitl"
    RETRY = "retry"
    RESEARCH_QUEUED = "research_queued"


class MappingObject(BaseModel):
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
