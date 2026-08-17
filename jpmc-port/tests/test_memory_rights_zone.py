"""Aurora memory + rights/CDM model + zone context surfaces."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SCUDO_LOCAL"] = "1"


def test_content_delivery_model_has_exactly_11_uml_values():
    from scudo.ontology_model import ContentDeliveryModel

    assert len(ContentDeliveryModel) == 11
    assert ContentDeliveryModel.DISTRIBUTION_SERVICE.value == "distributionService"
    assert ContentDeliveryModel.DIRECT_ACCESS_SERVICE.value == "directAccessService"


def test_rights_half_kinds_derived_not_positional():
    from scudo.ontology_model import RIGHTS_HALF_NODE_KINDS, ConceptualNodeKind

    assert ConceptualNodeKind.CONTRACT in RIGHTS_HALF_NODE_KINDS
    assert ConceptualNodeKind.FIELD not in RIGHTS_HALF_NODE_KINDS
    assert ConceptualNodeKind.PRODUCT_PACKAGE not in RIGHTS_HALF_NODE_KINDS


def test_system_context_lists_both_halves_and_cdms():
    from scudo.zone_context import system_context_text

    text = system_context_text()
    assert "Zone 3 Matching Engine" in text
    assert "Contract" in text and "Policy" in text
    assert "ProductPackage" in text or "Product Package" in text.replace(" ", "")
    assert "distributionService" in text
    assert "requires_human_review" in text


def test_mapping_tools_include_describe_system_context():
    from scudo.tools import MAPPING_SPECIALIST_TOOLS

    names = {getattr(t, "__name__", "") for t in MAPPING_SPECIALIST_TOOLS}
    assert "describe_system_context" in names


def test_consult_priors_returns_rules_and_precedent():
    from scudo import aurora_memory, local_state

    local_state.reset()
    aurora_memory.record_verified_precedent(
        vendor="lseg",
        vendor_product_ref="P1",
        source_iri="mds.lseg:11111111-1111-4111-8111-111111111111",
        target_iri="jpmorgan:data:cdao:EquityResearch",
        confidence=0.9,
        rationale="prior",
    )
    local_state.MEMORY["rule:lseg:r1"] = {
        "memory_type": "rule",
        "payload": {"text": "prefer EquityResearch for IBES estimates"},
        "updated_at_ms": 1,
    }
    priors = aurora_memory.consult_priors(vendor="lseg", vendor_product_ref="P1")
    assert priors.precedent is not None
    assert priors.precedent["target_iri"].endswith("EquityResearch")
    assert len(priors.rules) == 1


def test_run_bundle_carries_system_context_and_rules():
    from scudo import aurora_memory, local_state
    from scudo.handler import handle

    local_state.reset()
    local_state.MEMORY["rule:lseg:r1"] = {
        "memory_type": "rule",
        "payload": {"text": "IBES → EquityResearch"},
        "updated_at_ms": 1,
    }
    resp = handle(
        {
            "path": "/run",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "name": "equity research estimates",
            },
        }
    )
    assert resp["statusCode"] == 200
    # published path still works with richer bundle
    assert resp["body"]["outcome"] == "published"
    # memory distilled
    priors = aurora_memory.consult_priors(
        vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001"
    )
    assert priors.precedent is not None


def test_mapping_prompt_injects_zone_context():
    from datetime import datetime, timezone

    from scudo.prompts import mapping_prompt
    from scudo.schemas import BriefBundle, IntakeRequest, Route
    from scudo.zone_context import system_context_text

    bundle = BriefBundle(
        request=IntakeRequest(vendor="lseg", vendor_product_ref="X"),
        route=Route.NEW_MAPPING,
        vendor_product_iri="mds.lseg:00000000-0000-4000-8000-000000000001",
        vendor_assertion={"vendor": "lseg"},
        assembled_at=datetime.now(timezone.utc),
        bundle_ref="b1",
        system_context=system_context_text(),
        promoted_rules=[{"text": "rule-a"}],
    )
    prompt = mapping_prompt(bundle)
    assert "SYSTEM CONTEXT" in prompt
    assert "PROMOTED RULES" in prompt
    assert "rule-a" in prompt
