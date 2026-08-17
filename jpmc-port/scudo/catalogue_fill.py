"""Structured catalogue fill — agents populate CatalogueOntology v0.1 fields."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DatasetFill(BaseModel):
    """dcat:Dataset / cat:DistributedDataset property fill."""

    model_config = ConfigDict(extra="forbid")
    dataset_class: str = Field(
        default="cat:DistributedDataset",
        description="dcat:Dataset | cat:DistributedDataset | cat:MarketingDataset",
    )
    identifier: Optional[str] = Field(
        default=None, description="dcterms:identifier / PermID|Code"
    )
    title: Optional[str] = Field(default=None, description="dcterms:title")
    description: Optional[str] = Field(default=None, description="dcterms:description")
    features_and_benefits_description: Optional[str] = Field(
        default=None, description="cat:featuresAndBenefitsDescription"
    )
    keyword: list[str] = Field(default_factory=list, description="dcat:keyword")
    theme: list[str] = Field(default_factory=list, description="dcat:theme")
    super_theme: Optional[str] = Field(default=None, description="cat:superTheme")
    business_concept: Optional[str] = Field(
        default=None, description="cat:businessConcept"
    )
    extent: Optional[str] = Field(default=None, description="dcterms:extent")
    start_date: Optional[str] = Field(default=None, description="dcat:startDate")
    end_date: Optional[str] = Field(default=None, description="dcat:endDate")
    asset_class: Optional[str] = Field(default=None, description="cat:assetClass")
    super_asset_class: Optional[str] = Field(
        default=None, description="cat:superAssetClass"
    )
    accrual_periodicity: Optional[str] = Field(
        default=None, description="dcterms:accrualPeriodicity"
    )
    spatial: Optional[str] = Field(default=None, description="dcterms:spatial")
    geographic_coverage: Optional[str] = Field(
        default=None, description="cat:geographicCoverage"
    )
    temporal_coverage: Optional[str] = Field(
        default=None, description="cat:temporalCoverage"
    )
    industry_coverage: Optional[str] = Field(
        default=None, description="cat:industryCoverage"
    )
    content_type_coverage: Optional[str] = Field(
        default=None, description="cat:contentTypeCoverage"
    )
    landing_page: Optional[str] = Field(default=None, description="dcat:landingPage")


class DistributionFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_url: Optional[str] = Field(default=None, description="dcat:accessURL")
    media_type: Optional[str] = Field(default=None, description="dcat:mediaType")
    conforms_to: Optional[str] = Field(default=None, description="dcterms:conformsTo")
    distribution_type: Optional[str] = Field(default=None, description="dcterms:type")


class DataServiceFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_type: Optional[str] = Field(default=None, description="dcterms:type")
    access_url: Optional[str] = Field(default=None, description="dcat:accessURL")
    deployment_type: Optional[str] = None
    api_type: Optional[str] = None


class DeliveryChannelFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    distribution_type: Optional[str] = None


class ProductPackageFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, description="dcterms:title")
    long_name: Optional[str] = Field(default=None, description="cat:longName")
    abstract: Optional[str] = Field(default=None, description="dcterms:abstract")
    table_of_contents: Optional[str] = Field(
        default=None, description="dcterms:tableOfContents"
    )
    product_id: Optional[str] = None


class FieldFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notation: Optional[str] = Field(default=None, description="skos:notation")
    field_type: Optional[str] = Field(default=None, description="dcterms:type")
    primary_key_flag: Optional[bool] = Field(
        default=None, description="cat:primaryKeyFlag"
    )
    nullable_flag: Optional[bool] = Field(default=None, description="cat:nullableFlag")
    sequence_number: Optional[int] = Field(
        default=None, description="cat:sequenceNumber"
    )


class FieldGroupFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notation: Optional[str] = None
    database_notation: Optional[str] = Field(
        default=None, description="cat:databaseNotation"
    )
    schema_notation: Optional[str] = Field(
        default=None, description="cat:schemaNotation"
    )
    field_group_type: Optional[str] = None
    fields: list[FieldFill] = Field(default_factory=list)


class DataDictionaryFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    field_groups: list[FieldGroupFill] = Field(default_factory=list)


class DataTaxonomyFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    description: Optional[str] = None
    perm_id: Optional[str] = Field(default=None, description="cat:permId")
    code: Optional[str] = Field(default=None, description="cat:code")


class CatalogueFillResult(BaseModel):
    """Full catalogue-side fill against CatalogueOntology v0.1 Deontic."""

    model_config = ConfigDict(extra="forbid")
    vendor: str
    vendor_product_ref: str
    dataset: DatasetFill
    distributions: list[DistributionFill] = Field(default_factory=list)
    data_services: list[DataServiceFill] = Field(default_factory=list)
    delivery_channels: list[DeliveryChannelFill] = Field(default_factory=list)
    product_package: Optional[ProductPackageFill] = None
    data_dictionary: Optional[DataDictionaryFill] = None
    data_taxonomy: Optional[DataTaxonomyFill] = None
    business_concept_element: Optional[str] = Field(
        default=None,
        description="cat:BusinessConceptElement label / IRI if resolved",
    )
    unmapped_source_fields: list[str] = Field(
        default_factory=list,
        description="Vendor fields that could not be placed on the ontology",
    )
    requires_human_review: bool = False
    rationale: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
