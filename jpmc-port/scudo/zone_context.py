"""Zone-aware system context for mapping agents (Part C)."""

from __future__ import annotations

from .ontology_model import (
    RIGHTS_HALF_NODE_KINDS,
    ConceptualNodeKind,
    ContentDeliveryModel,
)

try:
    from strands import tool
except ImportError:

    def tool(fn):  # type: ignore[misc]
        return fn


def system_context_text() -> str:
    """5-zone + catalogue/DCAT vs rights/contract halves (derived from enums)."""

    def _label(kind: ConceptualNodeKind) -> str:
        return kind.value.replace("_", " ").title().replace(" ", "")

    catalogue_kinds = ", ".join(
        _label(k) for k in ConceptualNodeKind if k not in RIGHTS_HALF_NODE_KINDS
    )
    rights_kinds = ", ".join(
        _label(k) for k in ConceptualNodeKind if k in RIGHTS_HALF_NODE_KINDS
    )
    cdms = ", ".join(m.value for m in ContentDeliveryModel)

    return (
        "SYSTEM CONTEXT — SCUDO 5-zone architecture:\n"
        "  Zone 1 Ingress -> Zone 2 ETL -> Zone 3 Matching Engine (you are "
        "here) -> Zone 4 Orchestration (Bedrock specialist+verifier) "
        "-> Zone 5 Persistence + HITL (Aurora PostgreSQL).\n\n"
        "Conceptual enrichment has two halves. The CATALOGUE / DCAT half "
        f"(already modelled: {catalogue_kinds}) describes WHAT a data asset "
        "is and how it's delivered — weigh Dataset fields businessConcept, "
        "assetClass, superAssetClass, keyword/theme, coverages, plus "
        f"ProductPackage / Distribution / DataDictionary / Field. "
        f"ContentDeliveryModel closed set: {cdms}.\n\n"
        "The RIGHTS / CONTRACT half "
        f"({rights_kinds}) describes WHO may use it and under what terms — "
        "Contract grants Policy; Policy links Duties/Permissions; rules bind "
        "InternalParty/ExternalParty via ruleObject/ruleSubject; Document "
        "subtypes order_form/schedule/pricelist/master_agreement. "
        "HAS_DUTY (Permission→Duty) is PROVISIONAL.\n\n"
        "If a field/term is about licensing, legal basis, redistribution "
        "rights, or delivery-model terms rather than taxonomy placement, it "
        "belongs to the rights/contract half — set requires_human_review=true "
        "and note that in rationale rather than forcing a CDAO node."
    )


@tool
def describe_system_context() -> str:
    """Describe the 5-zone architecture and catalogue/DCAT vs rights/contract halves."""
    return system_context_text()
