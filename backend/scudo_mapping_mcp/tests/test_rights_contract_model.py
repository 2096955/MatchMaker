"""Rights/contract conceptual model — the "bottom half" of the CatalogueOntology.

The "top half" (Field->FieldGroup->DataDictionary->Distribution/DataService/
DeliveryChannel->Dataset/ProductPackage/DataTaxonomy) is already modelled 1:1
onto the CatalogueOntology transcript in ConceptualNodeKind/ConceptualEdgeKind.
The "bottom half" (Party, Contract, Policy, Duty, Permission, Obligation,
Document, and ContentDeliveryModel) is grounded in the MDSRights-UML /
CatalogueOntology-UML customer images (received 2026-07-13; transcribed in
docs/architecture/*.mmd). See
docs/superpowers/specs/2026-07-13-catalogue-rights-uml-gap-analysis.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_rights_contract_node_kinds_exist():
    from scudo_mapping_mcp.models import ConceptualNodeKind

    assert ConceptualNodeKind.PARTY.value == "party"
    assert ConceptualNodeKind.CONTRACT.value == "contract"
    assert ConceptualNodeKind.POLICY.value == "policy"
    assert ConceptualNodeKind.DUTY.value == "duty"
    assert ConceptualNodeKind.PERMISSION.value == "permission"
    assert ConceptualNodeKind.OBLIGATION.value == "obligation"
    assert ConceptualNodeKind.DOCUMENT.value == "document"
    # the top half must be untouched — same 13 entries as before
    top_half = {
        "product_package",
        "delivery_product",
        "data_service",
        "delivery_channel",
        "distribution",
        "distributed_dataset",
        "marketing_dataset",
        "business_concept_element",
        "data_taxonomy",
        "data_dictionary",
        "field_group",
        "field",
        "business_data_element",
    }
    assert top_half <= {k.value for k in ConceptualNodeKind}


def test_rights_contract_edge_kinds_exist():
    from scudo_mapping_mcp.models import ConceptualEdgeKind

    assert not hasattr(ConceptualEdgeKind, "PARTY_ROLE")
    assert not hasattr(ConceptualEdgeKind, "HAS_PERMISSION")
    assert ConceptualEdgeKind.GRANTS.value == "grants"
    assert ConceptualEdgeKind.POLICY_HAS_PERMISSION.value == "policy_has_permission"
    assert ConceptualEdgeKind.POLICY_HAS_DUTY.value == "policy_has_duty"
    assert ConceptualEdgeKind.RULE_OBJECT.value == "rule_object"
    assert ConceptualEdgeKind.RULE_SUBJECT.value == "rule_subject"
    assert ConceptualEdgeKind.CONTRACT_DOCUMENTS.value == "contract_documents"
    assert ConceptualEdgeKind.DATASET_PARTY.value == "dataset_party"
    assert ConceptualEdgeKind.HAS_DUTY.value == "has_duty"


def test_content_delivery_model_has_only_confirmed_values():
    """Exactly the 11 ContentDeliveryModel literals confirmed by both
    CatalogueOntology-UML and MDSRights-UML customer images (2026-07-13)."""
    from scudo_mapping_mcp.models import ContentDeliveryModel

    values = {m.value for m in ContentDeliveryModel}
    assert values == {
        "distributionService",
        "redistributionService",
        "useService",
        "displayService",
        "directDisplayService",
        "nonDisplayService",
        "fullNonDisplayService",
        "automatedTradingService",
        "derivedDataService",
        "internalDistributionService",
        "directAccessService",
    }


_CDM_UML_PROVENANCE = (
    "CatalogueOntology-UML / MDSRights-UML customer images, received "
    "2026-07-13, transcribed in docs/architecture/*.mmd"
)

_CONTENT_DELIVERY_MODEL_SOURCES = {
    "distributionService": _CDM_UML_PROVENANCE,
    "redistributionService": _CDM_UML_PROVENANCE,
    "useService": _CDM_UML_PROVENANCE,
    "displayService": _CDM_UML_PROVENANCE,
    "directDisplayService": _CDM_UML_PROVENANCE,
    "nonDisplayService": _CDM_UML_PROVENANCE,
    "fullNonDisplayService": _CDM_UML_PROVENANCE,
    "automatedTradingService": _CDM_UML_PROVENANCE,
    "derivedDataService": _CDM_UML_PROVENANCE,
    "internalDistributionService": _CDM_UML_PROVENANCE,
    "directAccessService": _CDM_UML_PROVENANCE,
}


def test_every_content_delivery_model_member_has_a_documented_source():
    """Structural guard: every ContentDeliveryModel member must have a
    non-empty source citation in _CONTENT_DELIVERY_MODEL_SOURCES."""
    from scudo_mapping_mcp.models import ContentDeliveryModel

    for member in ContentDeliveryModel:
        assert member.value in _CONTENT_DELIVERY_MODEL_SOURCES, (
            f"ContentDeliveryModel.{member.name} ({member.value!r}) has no "
            "documented source in _CONTENT_DELIVERY_MODEL_SOURCES — do not "
            "add a member without citing where its value came from"
        )
        source = _CONTENT_DELIVERY_MODEL_SOURCES[member.value]
        assert source and source.strip(), f"{member.value} has an empty source citation"

    current_values = {m.value for m in ContentDeliveryModel}
    stale = set(_CONTENT_DELIVERY_MODEL_SOURCES) - current_values
    assert not stale, f"citation map has stale entries no longer in the enum: {stale}"


_HAS_DUTY_SOURCES = {
    "has_duty": (
        "PROVISIONAL (D2 / G13): Permission→Duty contested link. "
        "CatalogueOntology-UML draws Duty—Permission (arrowhead may be "
        "navigability-only); MDSRights-UML omits the association. ODRL 2.2 "
        "odrl:duty is Permission→Duty — retained optional pending ontology-"
        "owner confirmation. Transcribed in docs/architecture/*.mmd; see "
        "docs/superpowers/specs/2026-07-13-catalogue-rights-uml-gap-analysis.md."
    ),
}


def test_has_duty_retains_provisional_citation_guard():
    """D2: HAS_DUTY stays as optional contested link with a sources-map
    guard mirroring the CDM citation discipline."""
    from scudo_mapping_mcp.models import ConceptualEdgeKind

    assert ConceptualEdgeKind.HAS_DUTY.value in _HAS_DUTY_SOURCES
    source = _HAS_DUTY_SOURCES[ConceptualEdgeKind.HAS_DUTY.value]
    assert source and source.strip()
    assert "PROVISIONAL" in source
    assert "G13" in source or "D2" in source
    stale = set(_HAS_DUTY_SOURCES) - {ConceptualEdgeKind.HAS_DUTY.value}
    assert not stale


def test_document_and_obligation_subtype_legal_pairings():
    from scudo_mapping_mcp.models import ConceptualNode, ConceptualNodeKind

    ConceptualNode(
        iri="mds.enrich:doc1",
        kind=ConceptualNodeKind.DOCUMENT,
        label="Order Form",
        attaches_to_concept_iri="jpmorgan:data:cdao:x",
        subtype="order_form",
    )
    ConceptualNode(
        iri="mds.enrich:obl1",
        kind=ConceptualNodeKind.OBLIGATION,
        label="Direct Access",
        attaches_to_concept_iri="jpmorgan:data:cdao:x",
        subtype="direct_access",
    )


def test_invalid_subtype_cross_pairing_raises():
    from scudo_mapping_mcp.models import ConceptualNode, ConceptualNodeKind

    with pytest.raises(ValidationError):
        ConceptualNode(
            iri="mds.enrich:bad1",
            kind=ConceptualNodeKind.DOCUMENT,
            label="Bad",
            attaches_to_concept_iri="jpmorgan:data:cdao:x",
            subtype="direct_access",  # obligation subtype on document
        )
    with pytest.raises(ValidationError):
        ConceptualNode(
            iri="mds.enrich:bad2",
            kind=ConceptualNodeKind.OBLIGATION,
            label="Bad",
            attaches_to_concept_iri="jpmorgan:data:cdao:x",
            subtype="order_form",  # document subtype on obligation
        )
    with pytest.raises(ValidationError):
        ConceptualNode(
            iri="mds.enrich:bad3",
            kind=ConceptualNodeKind.PARTY,
            label="Bad",
            attaches_to_concept_iri="jpmorgan:data:cdao:x",
            subtype="order_form",  # any subtype on non-carrier kind
        )
    with pytest.raises(ValidationError):
        ConceptualNode(
            iri="mds.enrich:bad4",
            kind=ConceptualNodeKind.DOCUMENT,
            label="Missing subtype",
            attaches_to_concept_iri="jpmorgan:data:cdao:x",
            subtype=None,
        )


def test_rights_attributes_and_submodels_round_trip_on_model():
    from scudo_mapping_mcp.models import (
        ConceptualNode,
        ConceptualNodeKind,
        ContentDeliveryModel,
        ContractTerms,
        PartyProfile,
    )

    node = ConceptualNode(
        iri="mds.enrich:policy1",
        kind=ConceptualNodeKind.POLICY,
        label="Display Policy",
        attaches_to_concept_iri="jpmorgan:data:cdao:x",
        cdm=ContentDeliveryModel.DISPLAY_SERVICE,
        time_interval="P1Y",
        deadline="2027-01-01T00:00:00Z",
        description="illustrative policy",
        contract_terms=ContractTerms(
            status="active",
            legal_basis="licence",
            licensing_model="enterprise",
            renewal_type="auto",
            term="P1Y",
            initial_term="P1Y",
            initial_term_end="2026-12-31",
            renewal_term="P1Y",
            store_purpose="research",
            post_term_store_purpose="archive",
            internal_controls="segregation",
        ),
        party_profile=PartyProfile(
            perm_id="P-1",
            supply_chain_status="active",
            organization_type="vendor",
            issuer_on="LSEG",
            member_of="exchange-A",
            exchange="XLON",
        ),
        party_scope="external",
    )
    dumped = node.model_dump()
    restored = ConceptualNode.model_validate(dumped)
    assert restored.cdm == ContentDeliveryModel.DISPLAY_SERVICE
    assert restored.contract_terms is not None
    assert restored.contract_terms.legal_basis == "licence"
    assert restored.party_profile is not None
    assert restored.party_profile.perm_id == "P-1"
    assert restored.party_scope == "external"


def test_conceptual_iri_works_for_new_kinds():
    """The existing deterministic IRI helper must accept the new kinds
    unchanged — no special-casing needed."""
    from scudo_mapping_mcp.models import ConceptualNodeKind, conceptual_iri

    iri = conceptual_iri(
        "jpmorgan:data:cdao:EquityResearch", ConceptualNodeKind.PARTY, "acme-vendor"
    )
    assert iri.startswith("mds.enrich:")
    assert iri == conceptual_iri(
        "jpmorgan:data:cdao:EquityResearch", ConceptualNodeKind.PARTY, "acme-vendor"
    )
    obl = conceptual_iri(
        "jpmorgan:data:cdao:EquityResearch",
        ConceptualNodeKind.OBLIGATION,
        "direct-access",
    )
    assert obl.startswith("mds.enrich:")
