"""Catalogue (DCAT) + rights/contract conceptual vocabulary for agents.

Mirrors Capone ``scudo_mapping_mcp.models`` closed enums (11 CDMs confirmed
from CatalogueOntology-UML + MDSRights-UML). Agents use these for domain
recognition; the deterministic matcher still gates on CDAO taxonomy placement.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConceptualNodeKind(str, Enum):
    # Catalogue / DCAT half
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
    # Rights / contract half (MDSRights-UML)
    PARTY = "party"
    CONTRACT = "contract"
    POLICY = "policy"
    DUTY = "duty"
    PERMISSION = "permission"
    OBLIGATION = "obligation"
    DOCUMENT = "document"


class ContentDeliveryModel(str, Enum):
    """Closed 11-literal CDM vocabulary (UML-confirmed; do not invent members)."""

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
    MADE_AVAILABLE_THROUGH = "made_available_through"
    DELIVERED_BY = "delivered_by"
    ACCESSED_THROUGH = "accessed_through"
    FORMATTED_AS = "formatted_as"
    IN_SERIES = "in_series"
    CONTAINS = "contains"
    CLASSIFIED_AS = "classified_as"
    GRANTS = "grants"
    POLICY_HAS_PERMISSION = "policy_has_permission"
    POLICY_HAS_DUTY = "policy_has_duty"
    RULE_OBJECT = "rule_object"
    RULE_SUBJECT = "rule_subject"
    CONTRACT_DOCUMENTS = "contract_documents"
    DATASET_PARTY = "dataset_party"
    HAS_DUTY = "has_duty"  # PROVISIONAL Permission→Duty


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

DocumentSubtype = Literal["order_form", "schedule", "pricelist", "master_agreement"]


class DatasetSignals(BaseModel):
    """DCAT/catalogue Dataset fields agents should weigh for taxonomy match."""

    model_config = ConfigDict(extra="ignore")
    identifier: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    keyword: list[str] = Field(default_factory=list)
    theme: list[str] = Field(default_factory=list)
    business_concept: Optional[str] = None
    asset_class: Optional[str] = None
    super_asset_class: Optional[str] = None
    geographic_coverage: Optional[str] = None
    temporal_coverage: Optional[str] = None
    industry_coverage: Optional[str] = None
    content_type_coverage: Optional[str] = None


class ContractTerms(BaseModel):
    """Contract attributes agents may see on rights-half nodes."""

    model_config = ConfigDict(extra="ignore")
    status: Optional[str] = None
    legal_basis: Optional[str] = None
    licensing_model: Optional[str] = None
    renewal_type: Optional[str] = None
    store_purpose: Optional[str] = None
    post_term_store_purpose: Optional[str] = None
    internal_controls: Optional[str] = None
    cdm: Optional[ContentDeliveryModel] = None
