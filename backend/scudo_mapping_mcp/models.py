"""
Typed contracts shared across the store, the matcher and the MCP tools.

Everything that crosses a boundary is a Pydantic model. Drift gets caught here,
at the edge, not mid-pipeline — the same discipline as a verified one-shot call.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .config import IRI_NAMESPACE

# Deterministic, replay-safe IRIs. Same (vendor, product_id) -> same IRI, always.
# This is what lets a snapshot, a re-run and a sibling product agree on identity.
_IRI_SEED = uuid.UUID("6f2a9c4e-1d3b-4f8a-9c7e-2b5d8a1f4c63")  # fixed namespace seed


def mds_iri(vendor: str, product_id: str) -> str:
    """Return the canonical mds.<vendor>:<uuid5> IRI for a vendor product."""
    key = f"{vendor.strip().lower()}::{product_id.strip()}"
    u = uuid.uuid5(_IRI_SEED, key)
    slug = vendor.strip().lower().replace(" ", "").replace("&", "and")
    return f"{IRI_NAMESPACE}.{slug}:{u}"


class MappingStatus(str, Enum):
    AUTO_MAPPED = "auto_mapped"       # confidence >= floor
    NEEDS_REVIEW = "needs_review"     # confidence < floor -> HITL
    OUT_OF_SCOPE = "out_of_scope"     # blocked by the deterministic scope gate
    APPROVED = "approved"             # human approved
    OVERRIDDEN = "overridden"         # human chose a different node
    REJECTED = "rejected"             # human rejected


class VendorProductRef(BaseModel):
    vendor: str = Field(..., description="One of the priority vendors")
    product_id: str = Field(..., description="Vendor-native product identifier")
    name: str = Field(default="", description="Vendor product display name")
    description: str = Field(default="", description="Vendor product description")
    raw: dict = Field(default_factory=dict, description="Original row, untouched")
    # Federated-audit fields (M8 — populated by the S3 reader). Optional so the
    # mock and inline-construction paths stay backward-compatible. They flow
    # through matching.map_vendor_product onto every MappingResult so any HITL
    # decision is traceable to the exact landed file.
    source_content_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 hex of the S3 object body the M8 reader saw; None on mock/inline paths.",
    )
    source_file_audit_id: Optional[str] = Field(
        default=None,
        description="x-amz-meta-file-audit-id from the S3 object; None when absent or on non-s3 paths.",
    )

    @property
    def iri(self) -> str:
        return mds_iri(self.vendor, self.product_id)


class TaxonomyNode(BaseModel):
    iri: str
    label: str
    parent_iri: Optional[str] = None
    children_iris: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    node: TaxonomyNode
    similarity: float = Field(..., ge=0.0, le=1.0)


class Subgraph(BaseModel):
    root_iri: str
    nodes: list[TaxonomyNode] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)  # (parent_iri, child_iri)


class ScopeResult(BaseModel):
    allowed: bool
    reason: str = ""


# M5 — richer, self-describing mapping artifact (Section 10c).
# The matcher carries these on every MappingResult so each mapping is portable
# and the same shape can be exported into the M6 bundle without re-derivation.

class FieldRule(BaseModel):
    """One vendor field -> CDAO field normalisation rule.

    ``transform`` is closed to a known set so the artifact stays deterministic
    and replay-safe (I1 / I6). When new transforms are needed they are added
    to this Literal AND the bundle import path; never accepted as free-form
    strings at the boundary.
    """
    vendor_field: str = Field(..., min_length=1)
    cdao_field: str = Field(..., min_length=1)
    transform: Literal["identity", "trim", "lower", "upper"] = "identity"


class Validation(BaseModel):
    """One deterministic check result run against the candidate mapping."""
    name: str = Field(..., min_length=1)
    status: Literal["pass", "fail", "warn"]
    detail: str = ""
    required: bool = True


class MappingResult(BaseModel):
    vendor_product_iri: str
    vendor: str
    product_id: str
    product_name: str
    mapped_node_iri: Optional[str] = None
    mapped_node_label: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: MappingStatus
    candidates: list[Candidate] = Field(default_factory=list)
    rationale: str = ""
    # Cost-ladder band — what the matcher actually did to reach this status.
    # "pass"       — clearly above floor; auto-mapped without specialist
    # "borderline" — sat in the borderline window; specialist was consulted
    # "fail"       — clearly below floor or required validation failed;
    #                specialist NOT consulted (cost-ladder discipline:
    #                the LLM only runs on cases it can plausibly resolve)
    # "n/a"        — out-of-scope / precedent reuse / no candidates;
    #                the band concept doesn't apply
    band: Literal["pass", "borderline", "fail", "n/a"] = "n/a"
    # Disagreement surface — populated only when the borderline specialist
    # picked a DIFFERENT node from the sparse ranker. The primary
    # mapped_node_iri stays anchored to candidates[0] (so a reviewer can
    # always trace it to the deterministic retrieval); the specialist's
    # alternative is preserved here so the reviewer sees BOTH picks, not
    # just one. None on every non-disagreement path.
    alternative_mapped_node_iri: Optional[str] = None
    alternative_mapped_node_label: Optional[str] = None
    # M5 fields — always populated (default empty rather than absent) so the
    # JSON shape is stable for both the MCP tool and the HTTP facade.
    field_normalisation: list[FieldRule] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    # M8 federated-audit fields — copied from the originating VendorProductRef
    # by the matcher on every code path. Lets an auditor answer "for mapping
    # decision X, which exact file did the matcher see?" by joining
    # source_content_hash / source_file_audit_id to the upstream audit table.
    # None on mock / inline construction paths (no upstream provenance).
    # PERSISTENCE NOTE: these fields are NOT yet written onto the precedent
    # edge by upsert_precedent — that requires a seam extension and is a
    # follow-up. Today they live on the in-memory result + bundle export only.
    source_content_hash: Optional[str] = None
    source_file_audit_id: Optional[str] = None


# M6 — the portable cutover artifact. CONFIRMED precedents serialise into
# MappingPatterns; a versioned bundle of them seeds a fresh environment.
# Kept flat + IRI-keyed + ISO-timestamped so the same shape can later be
# emitted as RDF triples (DCAT + adapted-ODRL) on the Neptune side.


class BundleProvenance(BaseModel):
    """Audit provenance carried on every exported pattern.

    ``decided_at`` is ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SS.sssZ``) so the
    bundle is human-readable, diffable, and decoupled from any single
    backend's native time representation.

    ``source_content_hash`` and ``source_file_audit_id`` are the M8 federated-
    audit fields the matcher saw at decision time, now PERSISTED on the
    precedent edge and replayed through the bundle round-trip. Together
    with the upstream ingestion's audit table they form a closed audit
    chain: any HITL decision can be traced back to the exact S3 object
    the matcher consulted.
    """
    decided_by: str = Field(..., min_length=1)
    decided_at: str = Field(..., min_length=1)
    decision: Literal["approve", "override"]
    source_content_hash: Optional[str] = None
    source_file_audit_id: Optional[str] = None


class MappingPattern(BaseModel):
    """One confirmed (vendor, product) -> CDAO mapping, packaged for export.

    Carries everything an importer needs to reproduce the PRECEDENT EDGE:
    the vendor product identity, the canonical IRI inputs, the human's
    recorded decision + preserved confidence + ISO-8601 timestamp.

    EXPORTER-SNAPSHOT FIELDS (diagnostic; NOT replayed by the importer):

      ``rank`` — the approval count the EXPORTER observed. The IMPORTER
          re-derives rank at query time from its own MAPPED_TO edges, so
          importing the same bundle leaves the derived count unchanged.
          The field is preserved on the artifact for audit / diff.

      ``field_normalisation``, ``validations`` — the M5 self-describing
          payload AS THE EXPORTER SAW IT. The current store seam does
          NOT persist per-pattern field rules or validation results on
          the precedent edge; the importer's runtime re-derives both
          from current code (``default_field_rules`` +
          ``run_validations``). The fields are preserved on the bundle
          for human review and forward compatibility — when per-pattern
          overrides become meaningful, ``upsert_precedent`` can be
          extended to persist them without a bundle format break.
    """
    vendor: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    product_name: str = ""
    description: str = ""
    signature: str = Field(..., min_length=1,
                           description="Snapshot of vendor_signature at export time")
    mapped_node_iri: str = Field(..., min_length=1)
    mapped_node_label: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)
    rank: int = Field(default=0, ge=0,
                      description="Rank-signal approval count for (signature, mapped_node_iri)")
    field_normalisation: list[FieldRule] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    provenance: BundleProvenance


class MappingBundle(BaseModel):
    """A versioned, portable bundle of confirmed mappings.

    The cutover artifact: built on the mock side, signed off, imported into
    UAT/Atlas. ``version`` is the bundle FORMAT semver — bump on schema
    change. ``taxonomy_version`` is a deterministic hash of the taxonomy
    seed at export time so an importer can detect divergence before
    re-seeding (an unknown ``mapped_node_iri`` is skipped, not faked).
    """
    version: str = Field(..., min_length=1,
                         description="Bundle format semver, e.g. '1.0.0'")
    created_at: str = Field(..., min_length=1,
                            description="ISO-8601 UTC")
    source_env: str = Field(..., min_length=1)
    taxonomy_version: str = Field(..., min_length=1,
                                  description="Deterministic hash of the taxonomy at export")
    patterns: list[MappingPattern] = Field(default_factory=list)


class BundleImportSummary(BaseModel):
    """Result of an import_bundle() call.

    Idempotent re-import returns the same numbers; ``skipped`` includes any
    pattern whose ``mapped_node_iri`` is not present in the importer's
    taxonomy or whose vendor is out of scope locally.
    """
    total: int = Field(..., ge=0)
    applied: int = Field(..., ge=0)
    skipped_unknown_node: int = Field(default=0, ge=0)
    skipped_out_of_scope: int = Field(default=0, ge=0)
    taxonomy_version_source: str = ""
    taxonomy_version_local: str = ""
