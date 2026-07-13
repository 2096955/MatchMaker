"""Phase E step 4 — deterministic assetClass seam.

Behind its OWN opt-in flag ``SCUDO_ASSET_CLASS_VALIDATION`` (default off,
call-time read — measured-rollout discipline: loading a UML-enriched
catalogue must not change any matching outcome until the operator flips
this lever; it is deliberately SEPARATE from SCUDO_TAXONOMY_UML_TEXT so
deterministic validation can be enabled without touching BM25 text).

Flag on: the loaded ``TaxonomyNode.asset_class`` is plumbed into
``run_validations``' existing ``node_data_class`` parameter, so
vendor-vs-node asset-class disagreement becomes an explainable
deterministic validation failure (required fail → NEEDS_REVIEW regardless
of similarity) instead of probabilistic soup. Zero floor risk: no band,
threshold or similarity change — the seam is
``matching.map_vendor_product`` → ``validations.py``.
"""

from __future__ import annotations

from scudo_mapping_mcp.matching import map_vendor_product
from scudo_mapping_mcp.models import MappingStatus, TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.tests.fake_store import FakeStore

CONCEPT_IRI = "jpmorgan:data:cdao:EquityPrices"
FLAG = "SCUDO_ASSET_CLASS_VALIDATION"


def _store(node_asset_class):
    node = TaxonomyNode(
        iri=CONCEPT_IRI,
        label="Equity Prices",
        asset_class=node_asset_class,
    )
    store = FakeStore(nodes=[node])
    store.set_score(CONCEPT_IRI, 0.9)  # clearly above the PASS edge
    return store


def _ref(vendor_asset_class):
    raw = {"assetClass": vendor_asset_class} if vendor_asset_class else {}
    return VendorProductRef(
        vendor="LSEG",
        product_id="EQ-1",
        name="Equity Prices",
        raw=raw,
    )


def _data_class_validation(result):
    return next(v for v in result.validations if v.name == "data_class_match")


# ── flag OFF (default): loading an enriched catalogue changes NOTHING ──────


def test_flag_off_mismatched_asset_class_is_inert(monkeypatch):
    """Measured-rollout pin: with SCUDO_ASSET_CLASS_VALIDATION unset, a
    class-mismatched node maps EXACTLY as pre-Phase-E — node_data_class is
    not plumbed, data_class_match passes by default, and the mapping
    auto-maps at 0.9 despite the disagreement."""
    monkeypatch.delenv(FLAG, raising=False)
    result = map_vendor_product(_ref("FX"), store=_store("Equity"))
    v = _data_class_validation(result)
    assert v.status == "pass"
    assert "pass-by-default" in v.detail
    assert result.status == MappingStatus.AUTO_MAPPED
    assert result.band == "pass"


def test_flag_defaults_off_in_settings(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    from scudo_mapping_mcp import config as cfg

    assert cfg.env_asset_class_validation_enabled() is False
    assert cfg.Settings.from_env().asset_class_validation is False
    monkeypatch.setenv(FLAG, "on")
    assert cfg.env_asset_class_validation_enabled() is True


# ── flag ON: deterministic, explainable validation ─────────────────────────


def test_mismatched_asset_class_forces_needs_review_with_explainable_detail(
    monkeypatch,
):
    monkeypatch.setenv(FLAG, "on")
    result = map_vendor_product(_ref("FX"), store=_store("Equity"))
    v = _data_class_validation(result)
    assert v.status == "fail"
    assert v.required is True
    # Explainable: the message names both sides of the disagreement.
    assert "fx" in v.detail.lower()
    assert "equity" in v.detail.lower()
    # Required fail forces review even at 0.9 similarity, without consulting
    # the specialist (band = hard fail).
    assert result.status == MappingStatus.NEEDS_REVIEW
    assert result.band == "fail"
    assert "data_class_match" in result.rationale


def test_matching_asset_class_passes_case_insensitively(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    result = map_vendor_product(_ref("equity"), store=_store("Equity"))
    v = _data_class_validation(result)
    assert v.status == "pass"
    assert result.status == MappingStatus.AUTO_MAPPED


def test_absent_node_asset_class_is_a_noop(monkeypatch):
    """Catalogue carries no assetClass → pass-by-default, no false failures
    even with the flag on."""
    monkeypatch.setenv(FLAG, "on")
    result = map_vendor_product(_ref("FX"), store=_store(None))
    v = _data_class_validation(result)
    assert v.status == "pass"
    assert "pass-by-default" in v.detail
    assert result.status == MappingStatus.AUTO_MAPPED


def test_absent_vendor_asset_class_is_a_noop(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    result = map_vendor_product(_ref(None), store=_store("Equity"))
    v = _data_class_validation(result)
    assert v.status == "pass"
    assert "pass-by-default" in v.detail
    assert result.status == MappingStatus.AUTO_MAPPED
