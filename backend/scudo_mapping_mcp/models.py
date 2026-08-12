"""
Typed contracts shared across the store, the matcher and the MCP tools.

Everything that crosses a boundary is a Pydantic model. Drift gets caught here,
at the edge, not mid-pipeline — the same discipline as a verified one-shot call.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # Temporal coverage — the period/vintage/as-of the vendor product covers.
    # Optional with a None default: every pre-existing construction site
    # (137 of them) keeps working untouched, and None means "vendor declared
    # nothing", which the temporal validation treats as PASS-BY-DEFAULT.
    # Free text by design (vendor rows carry "2019-2021", "2020-06",
    # "historical", ...); the comparator parses what it can and passes on
    # anything it cannot. Consumed ONLY behind SCUDO_TEMPORAL_VALIDATION
    # (validations.temporal comparator) — never composed into any matching
    # text surface, so it can never move ``Candidate.similarity``.
    temporal_coverage: Optional[str] = Field(
        default=None,
        description=(
            "Period/vintage/as-of the vendor product covers, as declared "
            "upstream (e.g. '2019-01-01/2020-12-31', '2019-2021', '2020-06'). "
            "None when the vendor declares nothing."
        ),
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
    # ── Phase E structured matching signals (spec 2026-07-13, Phase E) ──────
    # UML Dataset.businessConcept / assetClass / superAssetClass, read
    # UNCONDITIONALLY at load into these structured fields — never
    # pre-concatenated into ``definition``/``alt_labels`` (that would be
    # irreversible per loaded graph and destroy flag separation / shadow
    # attribution). Composition into BM25 text happens only at flag time
    # behind SCUDO_TAXONOMY_UML_TEXT (see taxonomy_text.py) and is
    # BM25-ONLY: ``taxonomy_dense_text`` stays label+definition so these
    # fields can never move ``Candidate.similarity``. Provenance (I5):
    # values here come from the customer-curated catalogue export ONLY —
    # never from SCUDO-inferred enrichment (see the I5 boundary note on
    # the M10 block below).
    business_concept: Optional[str] = None
    asset_class: Optional[str] = None
    super_asset_class: Optional[str] = None
    # ── Temporal coverage (catalogue side) ─────────────────────────────────
    # The period the CDAO node's data covers, mirroring
    # ``DcatDataset.temporal_coverage`` (UML Dataset.temporalCoverage,
    # models_dcat.py). Optional/None default so every existing construction
    # site is unaffected. Same boundary discipline as the Phase E signals
    # above: STRUCTURED only, never concatenated into ``definition`` /
    # ``alt_labels`` and never composed into any text surface, so it cannot
    # move ``Candidate.similarity``. Read ONLY by the deterministic temporal
    # comparator behind SCUDO_TEMPORAL_VALIDATION (validations.py).
    #
    # NOT YET POPULATED by the loader/projector paths: ``project_dcat_dataset``
    # and ``dcat_loader._extract_node`` still drop DcatDataset.temporal_coverage
    # on the floor (they must be wired in lockstep — test_dcat_phase_c_lockstep
    # pins full model_dump equality between the two). Until then callers pass
    # the value explicitly via ``run_validations(node_temporal_coverage=...)``,
    # exactly as ``node_data_class`` is plumbed for asset_class. Absent on both
    # sides is a PASS, so an unpopulated field is inert, never a false failure.
    temporal_coverage: Optional[str] = None


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
#
# I5 PROVENANCE BOUNDARY (Phase E, 2026-07-13): only customer-curated
# catalogue text may feed matching text. SCUDO-inferred conceptual metadata
# (``classify_business_concept`` output, and any other enrichment-layer
# inference) must NEVER be written back into TaxonomyNode text fields
# (label / definition / alt_labels / business_concept / asset_class /
# super_asset_class) — that would close an LLM feedback loop into the
# confidence gate. Enrichment writes go through ``upsert_conceptual_node``
# onto ``ConceptualNode`` ONLY; the guard test is
# ``tests/test_phase_e_provenance_guard.py``.


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

    # ── Rights/contract "bottom half" (MDSRights-UML, 2026-07-13) ───────────
    # Grounded in the MDSRights-UML customer image (received 2026-07-13;
    # transcribed in docs/architecture/mds-rights-uml.mmd), with the
    # CatalogueOntology-UML image as the coarse catalogue-side view of the
    # same structure. See
    # docs/superpowers/specs/2026-07-13-catalogue-rights-uml-gap-analysis.md.
    # Obligation ⊂ Duty; Document carries OrderForm/Schedule/Pricelist/
    # MasterAgreement via ``subtype`` (D1). Rule stays abstract — its attrs
    # live on duty/permission/obligation nodes.
    PARTY = "party"
    CONTRACT = "contract"
    POLICY = "policy"
    DUTY = "duty"
    PERMISSION = "permission"
    OBLIGATION = "obligation"
    DOCUMENT = "document"


class ContentDeliveryModel(str, Enum):
    """Closed ContentDeliveryModel vocabulary — all 11 literals confirmed
    identically by CatalogueOntology-UML and MDSRights-UML customer images
    (received 2026-07-13; transcribed in docs/architecture/*.mmd).

    Every member MUST also have an entry in
    `test_rights_contract_model.py`'s `_CONTENT_DELIVERY_MODEL_SOURCES` map,
    which fails the test suite if a member is missing a citation.
    """

    DISTRIBUTION_SERVICE = "distributionService"
    REDISTRIBUTION_SERVICE = "redistributionService"
    USE_SERVICE = "useService"
    DISPLAY_SERVICE = "displayService"
    DIRECT_DISPLAY_SERVICE = "directDisplayService"
    NON_DISPLAY_SERVICE = "nonDisplayService"
    FULL_NON_DISPLAY_SERVICE = "fullNonDisplayService"
    AUTOMATED_TRADING_SERVICE = "automatedTradingService"
    DERIVED_DATA_SERVICE = "derivedDataService"
    INTERNAL_DISTRIBUTION_SERVICE = "internalDistributionService"
    DIRECT_ACCESS_SERVICE = "directAccessService"


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
    CONTAINS = "contains"  # generic parent -> child (FieldGroup -> Field;
    # also ProductPackage→Dataset, Distribution→DataDictionary per G15/G16)
    CLASSIFIED_AS = "classified_as"  # ... -> BusinessConceptElement / DataTaxonomy

    # ── Rights/contract "bottom half" (MDSRights-UML, 2026-07-13) ───────────
    GRANTS = "grants"  # Contract -> Policy
    POLICY_HAS_PERMISSION = "policy_has_permission"  # Policy -> Permission
    POLICY_HAS_DUTY = "policy_has_duty"  # Policy -> Duty (policyDuties)
    RULE_OBJECT = "rule_object"  # Duty/Permission/Obligation -> Party
    RULE_SUBJECT = "rule_subject"  # Duty/Permission/Obligation -> Party
    CONTRACT_DOCUMENTS = "contract_documents"  # Contract -> Document
    DATASET_PARTY = "dataset_party"  # Dataset -> Party (G14 bridge)
    # PROVISIONAL (D2 / G13): Permission→Duty contested link. CatalogueOntology-
    # UML draws Duty—Permission (arrowhead may be navigability-only);
    # MDSRights-UML omits the association. ODRL 2.2 odrl:duty is
    # Permission→Duty — retained as optional pending ontology-owner
    # confirmation of survival/direction/role name after the Rule refactor.
    HAS_DUTY = "has_duty"  # Permission -> Duty (PROVISIONAL)


# Document / Obligation subtypes (D1) — closed per-kind, not extra enum kinds.
DocumentSubtype = Literal["order_form", "schedule", "pricelist", "master_agreement"]
ObligationSubtype = Literal["direct_access", "internal_distribution"]
ConceptualSubtype = Literal[
    "order_form",
    "schedule",
    "pricelist",
    "master_agreement",
    "direct_access",
    "internal_distribution",
]

_DOCUMENT_SUBTYPES: frozenset[str] = frozenset(
    {"order_form", "schedule", "pricelist", "master_agreement"}
)
_OBLIGATION_SUBTYPES: frozenset[str] = frozenset(
    {"direct_access", "internal_distribution"}
)

# Rights-half kinds for agent context / enrich classification filtering.
RIGHTS_HALF_NODE_KINDS: frozenset[ConceptualNodeKind] = frozenset(
    {
        ConceptualNodeKind.PARTY,
        ConceptualNodeKind.CONTRACT,
        ConceptualNodeKind.POLICY,
        ConceptualNodeKind.DUTY,
        ConceptualNodeKind.PERMISSION,
        ConceptualNodeKind.OBLIGATION,
        ConceptualNodeKind.DOCUMENT,
    }
)


class PartyProfile(BaseModel):
    """Party attributes from MDSRights-UML (6 fields).

    TODO(g18-enums): ``supply_chain_status`` / ``organization_type`` are
    typed as plain strings until the customer supplies closed literal lists
    for SupplyChainStatus / OrganizationType — then promote to guard-tested
    enums (same citation discipline as ContentDeliveryModel).
    """

    perm_id: Optional[str] = None
    supply_chain_status: Optional[str] = None
    organization_type: Optional[str] = None
    issuer_on: Optional[str] = None
    member_of: Optional[str] = None
    exchange: Optional[str] = None


class ContractTerms(BaseModel):
    """Contract attributes from MDSRights-UML (11 fields).

    TODO(g18-enums): ``status`` / ``legal_basis`` / ``licensing_model`` /
    ``renewal_type`` are plain strings until ContractStatus / LegalBasis /
    LicensingModel / RenewalType literal lists are sourced.
    """

    status: Optional[str] = None
    legal_basis: Optional[str] = None
    licensing_model: Optional[str] = None
    renewal_type: Optional[str] = None
    term: Optional[str] = None
    initial_term: Optional[str] = None
    initial_term_end: Optional[str] = None
    renewal_term: Optional[str] = None
    store_purpose: Optional[str] = None
    post_term_store_purpose: Optional[str] = None
    internal_controls: Optional[str] = None


def conceptual_iri(concept_iri: str, kind: "ConceptualNodeKind", local_id: str) -> str:
    """Deterministic, replay-safe IRI for a conceptual-layer node.

    Namespaced under the CDAO Concept it enriches (``concept_iri``) so the
    IRI is visually distinguishable from both vendor-product IRIs
    (``mds.<vendor>:<uuid5>``) and taxonomy IRIs (``jpmorgan:data:cdao:*``).
    """
    key = f"{concept_iri.strip()}::{kind.value}::{local_id.strip().lower()}"
    u = uuid.uuid5(_IRI_SEED, key)
    return f"mds.enrich:{u}"


def conceptual_node_from_fixture_raw(
    raw: dict,
    *,
    iri: str,
    kind: ConceptualNodeKind,
    concept_iri: str,
) -> ConceptualNode:
    """Build a ConceptualNode from conceptual_layer.json-style raw dicts.

    Shared by ingest seeding and build_matching_graph fixture seeding so new
    rights fields cannot silently drop on one path.
    """
    cdm_raw = raw.get("cdm")
    terms_raw = raw.get("contract_terms")
    profile_raw = raw.get("party_profile")
    return ConceptualNode(
        iri=iri,
        kind=kind,
        label=str(raw.get("label") or ""),
        attaches_to_concept_iri=concept_iri,
        vendor_field_name=raw.get("vendor_field_name"),
        data_type=raw.get("data_type"),
        primary_key=raw.get("primary_key"),
        nullable=raw.get("nullable"),
        database_notation=raw.get("database_notation"),
        schema_notation=raw.get("schema_notation"),
        sequence_number=raw.get("sequence_number"),
        notation=raw.get("notation"),
        group_type=raw.get("group_type"),
        subtype=raw.get("subtype"),
        cdm=ContentDeliveryModel(cdm_raw) if cdm_raw else None,
        time_interval=raw.get("time_interval"),
        deadline=raw.get("deadline"),
        party_scope=raw.get("party_scope"),
        description=raw.get("description"),
        contract_terms=(ContractTerms.model_validate(terms_raw) if terms_raw else None),
        party_profile=(
            PartyProfile.model_validate(profile_raw) if profile_raw else None
        ),
    )


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
    #
    # G9 (vendor_field_name vs UML Field.notation — UNRESOLVED, TODO(g9)):
    # docs/architecture/catalogue-ontology-uml.mmd gives Field a ``notation``
    # attribute, grounded by the transcript fixture's skos:notation as "the
    # exact database table title, CSV file header, or column key". Two
    # readings, undecidable from the images alone:
    #   (a) ``vendor_field_name`` IS the Field-side notation equivalent —
    #       enrichment.py defines it as "an actual key from the vendor's raw
    #       row", i.e. the same physical-column-key semantics, sourced from
    #       the vendor side; or
    #   (b) they are DISTINCT — Field.notation is the customer catalogue's
    #       own column key, while ``vendor_field_name`` is a vendor-side
    #       alias mapped onto it during enrichment extraction.
    # Until the ontology owner adjudicates, a FIELD node may carry both:
    # ``notation`` (from a customer export) and ``vendor_field_name`` (from
    # vendor-row extraction). Do not merge or rename either.
    vendor_field_name: Optional[str] = None
    data_type: Optional[str] = None
    primary_key: Optional[bool] = None
    nullable: Optional[bool] = None
    # FieldGroup-only metadata (cat:databaseNotation / cat:schemaNotation).
    database_notation: Optional[str] = None
    schema_notation: Optional[str] = None
    # ── Phase C catalogue-half attrs (CatalogueOntology-UML, 2026-07-13) ────
    # ``sequence_number`` — UML Field.sequenceNumber AND
    # FieldGroup.sequenceNumber (cat:sequenceNumber: "an ordering integer
    # value ... deterministic sorting layer"). Meaningful for FIELD /
    # FIELD_GROUP kinds; None elsewhere (documented, not validator-enforced —
    # same discipline as vendor_field_name above). STRICT int: see
    # ``_sequence_number_strict_int`` below.
    sequence_number: Optional[int] = None
    # ``notation`` — UML FieldGroup.notation (and, pending G9 above, possibly
    # Field.notation): the structural identifier / physical name
    # (skos:notation, csvw "PhysicalName"/"LexicalValue" in the transcript).
    notation: Optional[str] = None
    # ``group_type`` — maps UML FieldGroup.``type`` (String) from
    # docs/architecture/catalogue-ontology-uml.mmd. Renamed at the model
    # boundary because bare ``type`` collides with common vocabulary
    # (dcterms:type, Field.data_type, the Python builtin); the UML attr name
    # is preserved via this documented explicit mapping: UML FieldGroup.type
    # → ConceptualNode.group_type.
    group_type: Optional[str] = None
    # Rights/structure attrs (MDSRights-UML Phase B).
    subtype: Optional[ConceptualSubtype] = None
    cdm: Optional[ContentDeliveryModel] = None
    time_interval: Optional[str] = None
    deadline: Optional[str] = None
    party_scope: Optional[Literal["internal", "external"]] = None
    description: Optional[str] = None
    contract_terms: Optional[ContractTerms] = None
    party_profile: Optional[PartyProfile] = None

    @field_validator("sequence_number", mode="before")
    @classmethod
    def _sequence_number_strict_int(cls, v):
        """STRICT ordering int — fail fast on corrupt data.

        Pydantic's lax Optional[int] would coerce ``True``→1 and ``"3"``→3,
        silently drifting from enrichment's strict ``_validated_fields``
        contract (which coerces model-proposed junk to None — a DIFFERENT
        trust boundary: the LLM's output is sanitised before node
        construction, whereas a fixture / store property carrying ``true`` or
        ``"3"`` is corrupt data and must fail loudly at ingest, not mutate).
        Accepts None and true ints only (bool is an int subclass — excluded
        explicitly).
        """
        if v is None:
            return v
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"sequence_number must be an int or None; got {v!r} "
                f"({type(v).__name__})"
            )
        return v

    @model_validator(mode="after")
    def _validate_subtype_per_kind(self) -> "ConceptualNode":
        """D1: subtype is legal only for DOCUMENT / OBLIGATION, and only
        with the closed set for that kind. All other kinds require None."""
        if self.kind == ConceptualNodeKind.DOCUMENT:
            if self.subtype is None or self.subtype not in _DOCUMENT_SUBTYPES:
                raise ValueError(
                    f"DOCUMENT subtype must be one of {sorted(_DOCUMENT_SUBTYPES)}; "
                    f"got {self.subtype!r}"
                )
        elif self.kind == ConceptualNodeKind.OBLIGATION:
            if self.subtype is None or self.subtype not in _OBLIGATION_SUBTYPES:
                raise ValueError(
                    f"OBLIGATION subtype must be one of "
                    f"{sorted(_OBLIGATION_SUBTYPES)}; got {self.subtype!r}"
                )
        elif self.subtype is not None:
            raise ValueError(
                f"subtype is not allowed for kind {self.kind.value!r}; "
                f"got {self.subtype!r}"
            )
        return self


class ConceptualEdge(BaseModel):
    from_iri: str
    to_iri: str
    kind: ConceptualEdgeKind
    label: str = ""


class ConceptualGraph(BaseModel):
    root_concept_iri: str
    nodes: list[ConceptualNode] = Field(default_factory=list)
    edges: list[ConceptualEdge] = Field(default_factory=list)
