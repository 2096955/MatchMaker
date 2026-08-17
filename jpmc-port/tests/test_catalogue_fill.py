"""CatalogueOntology v0.1 fill + lookup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SCUDO_LOCAL"] = "1"


def test_fixture_parses_and_indexes_key_terms():
    from scudo.catalogue_ontology import fixture_path, lookup_term, ontology_graph

    g = ontology_graph()
    assert len(g) > 50
    assert fixture_path().is_file()
    ds = lookup_term("dcat:Dataset")
    assert ds is not None and ds["kind"] == "class"
    biz = lookup_term("cat:businessConcept")
    assert biz is not None and biz["kind"] == "property"
    # csvw alias
    assert lookup_term("AssetClassPermId") is not None
    assert lookup_term("PermID") is not None
    dist = lookup_term("cat:DistributedDataset")
    assert dist is not None


def test_list_fillable_dataset_fields_covers_uml_dataset_attrs():
    from scudo.catalogue_ontology import list_fillable_dataset_fields

    fields = {f["attr"] for f in list_fillable_dataset_fields()}
    assert {
        "identifier",
        "title",
        "business_concept",
        "asset_class",
        "super_asset_class",
        "features_and_benefits_description",
        "geographic_coverage",
        "landing_page",
    } <= fields


def test_fill_endpoint_maps_csvw_aliases():
    from scudo.handler import handle

    resp = handle(
        {
            "path": "/fill",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "Title": "I/B/E/S Estimates",
                "PermID": "PID-99",
                "AssetClassPermId": "AC-10294",
                "ParentAssetClassPermId": "PAC-88371",
                "DomainName": "Financial Markets",
                "SearchKeywordText": "estimates",
                "LandingPageUrl": "https://example.org/ibes",
                "Uri": "https://example.org/feed",
                "MediaTypeCode": "application/json",
            },
        }
    )
    assert resp["statusCode"] == 200, resp
    body = resp["body"]
    assert body["dataset"]["title"] == "I/B/E/S Estimates"
    assert body["dataset"]["identifier"] == "PID-99"
    assert body["dataset"]["asset_class"] == "AC-10294"
    assert body["dataset"]["super_asset_class"] == "PAC-88371"
    assert body["dataset"]["business_concept"] == "Financial Markets"
    assert body["dataset"]["dataset_class"] == "cat:DistributedDataset"
    assert body["distributions"][0]["access_url"] == "https://example.org/feed"
    assert body["confidence"] >= 0.8


def test_mapping_tools_include_catalogue_lookup():
    from scudo.tools import MAPPING_SPECIALIST_TOOLS

    names = {getattr(t, "__name__", "") for t in MAPPING_SPECIALIST_TOOLS}
    assert "lookup_catalogue_term" in names
    assert "list_catalogue_dataset_fields" in names
