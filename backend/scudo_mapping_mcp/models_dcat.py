"""DCAT entity models and projection into matchable TaxonomyNode shapes.

Phase C (CatalogueOntology-UML, 2026-07-13) widened these models with the
attribute lists the customer UML gives Dataset / Distribution / DataService,
and added the G19 shapes (ProductPackage / DeliveryChannel / DataDictionary).
Field-name provenance is the transcription in
``docs/architecture/catalogue-ontology-uml.mmd``.

G20 consistency note: the vendor INGEST path canonicalises
``longName`` → ``name`` and ``featuresAndBenefitsDescription`` →
``description`` (``csvw_aliases._PROPERTY_CANONICAL``). The models here are
the CATALOGUE-side shapes and carry the UML attrs under their snake_case
names (``long_name``); they do not introduce competing vendor-facing
aliases — anything crossing to the vendor ingest surface must keep routing
through ``csvw_aliases``.

Phase E (D3, landed 2026-07-13): ``DcatDataset.business_concept`` /
``asset_class`` / ``super_asset_class`` now project onto TaxonomyNode's
STRUCTURED signal fields (lockstep with the loader path). Composition into
matching text stays flag-gated in ``taxonomy_text`` (SCUDO_TAXONOMY_UML_TEXT,
BM25-only — dense text never sees them). ``update_period`` and the coverage
block remain model fields only.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import TaxonomyNode

_DEFINITION_MAX_LEN = 2000


class FieldDef(BaseModel):
    property_url: str = ""
    title: str = ""
    description: str = ""


class DcatDataset(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    themes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    # ── Phase C (UML Dataset attrs; Phase E matching signals — see module
    # docstring: model + loader extraction only, never matching text here).
    update_period: Optional[str] = None  # MDSRights-UML Dataset.updatePeriod
    business_concept: Optional[str] = None  # UML Dataset.businessConcept
    asset_class: Optional[str] = None  # UML Dataset.assetClass
    super_asset_class: Optional[str] = None  # UML Dataset.superAssetClass
    # Coverage block (UML Dataset: geographicCoverage / temporalCoverage /
    # industryCoverage / contentTypeCoverage — cat: textual-coverage
    # properties in the transcript). Matching-signal candidates: same
    # Phase E boundary as asset_class above.
    geographic_coverage: Optional[str] = None  # UML Dataset.geographicCoverage
    temporal_coverage: Optional[str] = None  # UML Dataset.temporalCoverage
    industry_coverage: Optional[str] = None  # UML Dataset.industryCoverage
    content_type_coverage: Optional[str] = None  # UML Dataset.contentTypeCoverage


class DcatDistribution(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    access_url: str = ""
    dataset_iri: Optional[str] = None
    # ── Phase C (UML Distribution attrs).
    media_type: Optional[str] = None  # dcat:mediaType
    conforms_to: Optional[str] = None  # dcterms:conformsTo (→ DataDictionary)
    distribution_type: Optional[str] = None  # UML Distribution.distribution_type


class DcatDataService(BaseModel):
    iri: str
    title: str = ""
    description: str = ""
    endpoint_url: str = ""
    # ── Phase C (UML DataService attrs). UML ``type`` → ``service_type``:
    # same collision-avoidance mapping as ConceptualNode.group_type.
    service_type: Optional[str] = None  # UML DataService.type (dcterms:type)
    deployment_type: Optional[str] = None  # UML DataService.deploymentType
    api_type: Optional[str] = None  # UML DataService.apiType
    dataset_iri: Optional[str] = None  # UML DataService.dataset_identifier


# ── G19 shapes (Phase C) — pydantic models only. The DCAT loader does NOT
# currently recognise subjects typed cat:ProductPackage / cat:DeliveryChannel
# / cat:DataDictionary (``dcat_loader._is_taxonomy_entity`` accepts only
# SKOS/OWL/RDFS classes+properties and dcat Dataset/Distribution/DataService
# — verified 2026-07-13); no customer export with such instance subjects
# exists yet. Wire loader recognition when one does.


class DcatProductPackage(BaseModel):
    """UML ProductPackage (CatalogueOntology-UML): title, longName,
    description, dataset_identifier (ProdDataSetMap — shape unknown, G18;
    kept as an IRI string until the customer supplies its structure),
    ProductID."""

    iri: str
    title: str = ""
    long_name: Optional[str] = None  # UML longName (see G20 note above)
    description: str = ""
    dataset_iri: Optional[str] = None  # UML dataset_identifier: ProdDataSetMap
    product_id: Optional[str] = None  # UML ProductID


class DcatDeliveryChannel(BaseModel):
    """UML DeliveryChannel: title, distribution_type."""

    iri: str
    title: str = ""
    distribution_type: Optional[str] = None


class DcatDataDictionary(BaseModel):
    """UML DataDictionary: dataset_identifier, title."""

    iri: str
    title: str = ""
    dataset_iri: Optional[str] = None  # UML dataset_identifier


def _join_definition(*parts: str) -> str:
    joined = " | ".join(p.strip() for p in parts if p and p.strip())
    if len(joined) > _DEFINITION_MAX_LEN:
        return joined[: _DEFINITION_MAX_LEN - 3] + "..."
    return joined


def project_dcat_dataset(ds: DcatDataset) -> TaxonomyNode:
    """Project a DCAT dataset to the extended TaxonomyNode the matcher consumes.

    Phase E: business_concept / asset_class / super_asset_class project onto
    TaxonomyNode's STRUCTURED signal fields (lockstep with
    ``dcat_loader._extract_node`` — test_dcat_phase_c_lockstep pins full
    model_dump equality). They are never composed into any text surface here;
    BM25 composition is flag time only (``taxonomy_text.taxonomy_bm25_doc``
    behind SCUDO_TAXONOMY_UML_TEXT). ``update_period`` and the coverage block
    remain model-only (not matching signals).
    """
    parent = ds.themes[0] if ds.themes else None
    return TaxonomyNode(
        iri=ds.iri,
        label=ds.title or ds.iri.rsplit("/", 1)[-1],
        parent_iri=parent,
        definition=ds.description,
        alt_labels=ds.keywords,
        node_kind="concept",
        superclass_iris=list(ds.themes),
        business_concept=ds.business_concept,
        asset_class=ds.asset_class,
        super_asset_class=ds.super_asset_class,
    )


def project_dcat_entity(
    *,
    iri: str,
    title: str,
    description: str = "",
    alt_labels: list[str] | None = None,
    themes: list[str] | None = None,
    node_kind: Literal["concept", "class", "property"] = "concept",
) -> TaxonomyNode:
    """Generic DCAT-shaped projection used by the RDF loader."""
    theme_list = themes or []
    return TaxonomyNode(
        iri=iri,
        label=title or iri.rsplit("/", 1)[-1],
        parent_iri=theme_list[0] if theme_list else None,
        definition=description,
        alt_labels=alt_labels or [],
        node_kind=node_kind,
        superclass_iris=list(theme_list),
    )
