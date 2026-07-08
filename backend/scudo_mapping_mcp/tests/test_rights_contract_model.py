"""Rights/contract conceptual model — the "bottom half" of the CatalogueOntology.

The "top half" (Field->FieldGroup->DataDictionary->Distribution/DataService/
DeliveryChannel->Dataset/ProductPackage/DataTaxonomy) is already modelled 1:1
onto the CatalogueOntology transcript in ConceptualNodeKind/ConceptualEdgeKind.
The "bottom half" (Party, Contract, Policy, Duty, Permission, and a
ContentDeliveryModel enum) has no typed representation anywhere — confirmed
this session (grep for Party/Contract/Duty/Permission across models.py
returns nothing before this change).

PROVISIONAL, not a bug: only 3 of the reported ~11 ContentDeliveryModel
values are confirmed from source (distributionService, redistributionService,
displayService). This is deliberately incomplete rather than guessed — see
docs/superpowers/specs/2026-07-07-aurora-memory-rights-model-zone-tool-design.md.
The 5 new entity kinds and their edges ARE grounded in the public, stable
ODRL 2.2 spec structure (Policy contains Permission contains Duty; Party is
assigner/assignee of a Policy), which rights_odrl.py already partially
implements — not guessed.
"""

from __future__ import annotations


def test_rights_contract_node_kinds_exist():
    from scudo_mapping_mcp.models import ConceptualNodeKind

    assert ConceptualNodeKind.PARTY.value == "party"
    assert ConceptualNodeKind.CONTRACT.value == "contract"
    assert ConceptualNodeKind.POLICY.value == "policy"
    assert ConceptualNodeKind.DUTY.value == "duty"
    assert ConceptualNodeKind.PERMISSION.value == "permission"
    # the top half must be untouched — same 13 entries as before this change
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

    assert ConceptualEdgeKind.PARTY_ROLE.value == "party_role"
    assert ConceptualEdgeKind.GRANTS.value == "grants"
    assert ConceptualEdgeKind.HAS_PERMISSION.value == "has_permission"
    assert ConceptualEdgeKind.HAS_DUTY.value == "has_duty"


def test_content_delivery_model_has_only_confirmed_values():
    """Exactly the 3 values confirmed from source — not the full reported
    ~11. Deliberately incomplete; see module docstring."""
    from scudo_mapping_mcp.models import ContentDeliveryModel

    values = {m.value for m in ContentDeliveryModel}
    assert values == {
        "distributionService",
        "redistributionService",
        "displayService",
    }


_CDM_TRANSCRIPT_PROVENANCE = (
    "CatalogueOntology transcript reviewed conversationally during the "
    "2026-07-07 brainstorming session (the original task's pasted reference "
    "material) — not a file persisted in this repo, so cite the review date/"
    "session rather than a repo path."
)

_CONTENT_DELIVERY_MODEL_SOURCES = {
    "distributionService": _CDM_TRANSCRIPT_PROVENANCE,
    "redistributionService": _CDM_TRANSCRIPT_PROVENANCE,
    "displayService": _CDM_TRANSCRIPT_PROVENANCE,
}


def test_every_content_delivery_model_member_has_a_documented_source():
    """Structural guard against silently guessing future ContentDeliveryModel
    values (E4, gap closure): every enum member must have a non-empty source
    citation in _CONTENT_DELIVERY_MODEL_SOURCES above. This does not complete
    the enum — see test_content_delivery_model_has_only_confirmed_values for
    why it's deliberately incomplete (no source was found this session for
    the remaining ~8 reported values). It makes it structurally impossible
    to add a 4th member later without also adding — and thus surfacing for
    review — a citation for it."""
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


def test_conceptual_iri_works_for_new_kinds():
    """The existing deterministic IRI helper must accept the new kinds
    unchanged — no special-casing needed."""
    from scudo_mapping_mcp.models import ConceptualNodeKind, conceptual_iri

    iri = conceptual_iri(
        "jpmorgan:data:cdao:EquityResearch", ConceptualNodeKind.PARTY, "acme-vendor"
    )
    assert iri.startswith("mds.enrich:")
    # replay-safe: same inputs -> same IRI
    assert iri == conceptual_iri(
        "jpmorgan:data:cdao:EquityResearch", ConceptualNodeKind.PARTY, "acme-vendor"
    )
