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
    AUTO_MAPPED = "auto_mapped"  # confidence >= floor
    NEEDS_REVIEW = "needs_review"  # confidence < floor -> HITL
    OUT_OF_SCOPE = "out_of_scope"  # blocked by the deterministic scope gate
    APPROVED = "approved"  # human approved
    OVERRIDDEN = "overridden"  # human chose a different node
    REJECTED = "rejected"  # human rejected


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
    definition: str = ""
    alt_labels: list[str] = Field(default_factory=list)
    node_kind: Literal["concept", "class", "property"] = "concept"
    superclass_iris: list[str] = Field(default_factory=list)
    superproperty_iris: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    node: TaxonomyNode
    similarity: float = Field(..., ge=0.0, le=1.0)


class Subgraph(BaseModel):
    root_iri: str
    nodes: list[TaxonomyNode] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(
        default_factory=list
    )  # (parent_iri, child_iri)


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
    # Invariant violation surface (ARB 5.3) — populated when the matcher
    # fails closed because a downstream component broke its contract. The
    # canonical case today is "specialist_off_list": the specialist returned
    # a pick whose IRI is NOT among the candidates the sparse ranker
    # surfaced. The specialist scores within the top-N anchor window — it
    # does not bring its own picks in from the wider taxonomy. When that
    # invariant is broken (hallucinated node / stale taxonomy / prompt
    # injection) the matcher abstains entirely, routes to NEEDS_REVIEW, and
    # records the violation here so a reviewer can see WHY the case landed
    # in the queue (not just THAT it did). None on every healthy path.
    invariant_violation: Optional[Literal["specialist_off_list"]] = None
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
    signature: str = Field(
        ..., min_length=1, description="Snapshot of vendor_signature at export time"
    )
    mapped_node_iri: str = Field(..., min_length=1)
    mapped_node_label: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)
    rank: int = Field(
        default=0,
        ge=0,
        description="Rank-signal approval count for (signature, mapped_node_iri)",
    )
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

    version: str = Field(
        ..., min_length=1, description="Bundle format semver, e.g. '1.0.0'"
    )
    created_at: str = Field(..., min_length=1, description="ISO-8601 UTC")
    source_env: str = Field(..., min_length=1)
    taxonomy_version: str = Field(
        ..., min_length=1, description="Deterministic hash of the taxonomy at export"
    )
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


# M10 — conceptual enrichment layer (ADDITIVE, Section 10c follow-up).
#
# Governance/lineage metadata that hangs off an already-mapped CDAO Concept:
# how a matched concept is packaged (ProductPackage), delivered
# (DeliveryProduct / DeliveryChannel / DataService / Distribution), and
# structurally broken down (DistributedDataset / MarketingDataset ->
# BusinessConceptElement / DataTaxonomy -> DataDictionary -> FieldGroup ->
# Field / BusinessDataElement).
#
# Modelled directly on the DCAT/SKOS-based CatalogueOntology transcript
# (cat:/dcat:/skos: prefixes) rather than invented from scratch — class and
# property names below map 1:1 onto that ontology's cat:ProductPackage,
# cat:DeliveryChannel, dcat:DataService, dcat:Distribution,
# cat:DistributedDataset, cat:MarketingDataset, cat:BusinessConceptElement,
# cat:DataTaxonomy, cat:DataDictionary, cat:FieldGroup, cat:Field.
#
# One discriminated node shape, not thirteen classes — mirrors how
# cdao_catalogue.json already discriminates Domain/Subdomain/Concept via a
# single @type string. This is METADATA ONLY: it never feeds matching.py's
# cost ladder (I5 — the ladder's decision surface does not grow a new input)
# and is never queried above the store seam as raw Cypher/SPARQL (I2).


class ConceptualNodeKind(str, Enum):
    PRODUCT_PACKAGE = "product_package"
    DELIVERY_PRODUCT = "delivery_product"
    DATA_SERVICE = "data_service"
    DELIVERY_CHANNEL = "delivery_channel"
    DISTRIBUTION = "distribution"
    DISTRIBUTED_DATASET = "distributed_dataset"
    MARKETING_DATASET = "marketing_dataset"
    BUSINESS_CONCEPT_ELEMENT = "business_concept_element"
    DATA_TAXONOMY = "data_taxonomy"
    DATA_DICTIONARY = "data_dictionary"
    FIELD_GROUP = "field_group"
    FIELD = "field"
    BUSINESS_DATA_ELEMENT = "business_data_element"


class ConceptualEdgeKind(str, Enum):
    """Closed edge-vocabulary (I1 / I6) — mirrors named predicates in the
    CatalogueOntology transcript (cat:deliveryChannel, dcat:distribution,
    cat:businessConceptElement, dcat:inSeries, ...) rather than accepting
    free-form relationship labels at the boundary."""

    MADE_AVAILABLE_THROUGH = (
        "made_available_through"  # DeliveryChannel -> DeliveryProduct
    )
    DELIVERED_BY = "delivered_by"  # DataService -> DeliveryChannel
    ACCESSED_THROUGH = "accessed_through"  # Distribution -> DataService
    FORMATTED_AS = "formatted_as"  # DistributedDataset <-> Distribution
    IN_SERIES = "in_series"  # MarketingDataset -> DistributedDataset
    CONTAINS = "contains"  # generic parent -> child (FieldGroup -> Field)
    CLASSIFIED_AS = "classified_as"  # ... -> BusinessConceptElement / DataTaxonomy


def conceptual_iri(concept_iri: str, kind: "ConceptualNodeKind", local_id: str) -> str:
    """Deterministic, replay-safe IRI for a conceptual-layer node.

    Namespaced under the CDAO Concept it enriches (``concept_iri``) so the
    IRI is visually distinguishable from both vendor-product IRIs
    (``mds.<vendor>:<uuid5>``) and taxonomy IRIs (``jpmorgan:data:cdao:*``).
    """
    key = f"{concept_iri.strip()}::{kind.value}::{local_id.strip().lower()}"
    u = uuid.uuid5(_IRI_SEED, key)
    return f"mds.enrich:{u}"


class ConceptualNode(BaseModel):
    iri: str
    kind: ConceptualNodeKind
    label: str
    attaches_to_concept_iri: str = Field(
        ..., description="The CDAO Concept IRI this enrichment hangs off"
    )
    # Field-only metadata (cat:Field / cat:primaryKeyFlag / cat:nullableFlag
    # in the CatalogueOntology transcript) — None for every other kind.
    # Kept as optional attributes on the shared shape rather than a Field
    # subclass, since only these two properties are kind-specific.
    vendor_field_name: Optional[str] = None
    data_type: Optional[str] = None
    primary_key: Optional[bool] = None
    nullable: Optional[bool] = None
    # FieldGroup-only metadata (cat:databaseNotation / cat:schemaNotation).
    database_notation: Optional[str] = None
    schema_notation: Optional[str] = None


class ConceptualEdge(BaseModel):
    from_iri: str
    to_iri: str
    kind: ConceptualEdgeKind
    label: str = ""


class ConceptualGraph(BaseModel):
    root_concept_iri: str
    nodes: list[ConceptualNode] = Field(default_factory=list)
    edges: list[ConceptualEdge] = Field(default_factory=list)
