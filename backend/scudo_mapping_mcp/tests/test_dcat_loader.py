"""DCAT/SKOS RDF taxonomy loader tests (P0 / WS1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scudo_mapping_mcp.loaders.dcat_loader import (
    _rdflib_format,
    load_dcat_taxonomy,
)
from scudo_mapping_mcp.loaders.taxonomy_loader import (
    load_cdao_taxonomy,
    load_taxonomy_nodes,
)
from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.taxonomy_text import taxonomy_bm25_doc, taxonomy_candidate_desc

FIXTURE_TTL = Path(__file__).parent / "fixtures" / "dcat_taxonomy.ttl"
FIXTURE_JSONLD = Path(__file__).parent / "fixtures" / "dcat_taxonomy.jsonld"
EQ_PRICES = "https://example.com/taxonomy/EquityPrices"
EQUITIES = "https://example.com/taxonomy/Equities"
FINANCIAL = "https://example.com/taxonomy/FinancialData"
BONDS = "https://example.com/taxonomy/Bonds"
PRICE_FIELD = "https://example.com/taxonomy/priceField"
AMOUNT_FIELD = "https://example.com/taxonomy/amountField"


@pytest.fixture
def dcat_nodes_ttl():
    return load_dcat_taxonomy(FIXTURE_TTL)


def test_rdflib_format_suffix_map():
    assert _rdflib_format(Path("x.ttl")) == "turtle"
    assert _rdflib_format(Path("x.rdf")) == "xml"
    assert _rdflib_format(Path("x.jsonld")) == "json-ld"
    assert _rdflib_format(Path("x.unknown")) is None


def test_loader_extracts_definitions_and_hierarchy_ttl(dcat_nodes_ttl):
    by_iri = {n.iri: n for n in dcat_nodes_ttl}
    assert EQ_PRICES in by_iri
    eq_prices = by_iri[EQ_PRICES]
    assert "Real-time" in eq_prices.definition
    assert "Includes trades" in eq_prices.definition  # skos:scopeNote
    assert "AAPL" in eq_prices.definition  # skos:example
    assert eq_prices.parent_iri == EQUITIES
    equities = by_iri[EQUITIES]
    assert FINANCIAL in equities.superclass_iris  # rdfs:subClassOf
    assert "Stocks" in equities.alt_labels  # skos:altLabel
    assert EQ_PRICES in by_iri[EQUITIES].children_iris


def test_loader_parses_subproperty_of(dcat_nodes_ttl):
    by_iri = {n.iri: n for n in dcat_nodes_ttl}
    price = by_iri[PRICE_FIELD]
    assert price.node_kind == "property"
    assert AMOUNT_FIELD in price.superproperty_iris


def test_loader_parses_jsonld():
    nodes = load_dcat_taxonomy(FIXTURE_JSONLD)
    by_iri = {n.iri: n for n in nodes}
    assert BONDS in by_iri
    assert by_iri[BONDS].label == "Bonds"
    assert by_iri[BONDS].parent_iri == FINANCIAL
    assert BONDS in by_iri[FINANCIAL].children_iris


def test_cdao_loader_default_fixture():
    nodes = load_cdao_taxonomy()
    assert nodes
    assert all(isinstance(n, TaxonomyNode) for n in nodes)


def test_taxonomy_loader_registry_matches_allowlist():
    from scudo_mapping_mcp.config import ALLOWED_TAXONOMY_LOADERS
    from scudo_mapping_mcp.loaders import taxonomy_loader as tl

    assert set(tl._TAXONOMY_LOADERS) == set(ALLOWED_TAXONOMY_LOADERS)


def test_taxonomy_loader_dispatch_dcat(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_LOADER", "dcat")
    monkeypatch.setenv("SCUDO_TAXONOMY_SOURCE", str(FIXTURE_TTL))
    from scudo_mapping_mcp import config as cfg

    settings = cfg.Settings.from_env()
    nodes = load_taxonomy_nodes(settings)
    assert any(n.iri == EQ_PRICES for n in nodes)


def test_taxonomy_text_gated_by_flag(monkeypatch):
    node = TaxonomyNode(iri="x", label="L", definition="def text")
    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "")
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())
    assert taxonomy_candidate_desc(node) == ""
    assert "def" not in taxonomy_bm25_doc(node)

    monkeypatch.setenv("SCUDO_TAXONOMY_TEXT", "on")
    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())
    assert taxonomy_candidate_desc(node) == "def text"
    assert "def text" in taxonomy_bm25_doc(node)
