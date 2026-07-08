"""Focused P0 ontology loader tests — minimal Turtle fixture + fallback paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from scudo_mapping_mcp.loaders.dcat_loader import load_dcat_taxonomy
from scudo_mapping_mcp.loaders.taxonomy_loader import (
    _CATALOGUE_FIXTURE,
    load_cdao_taxonomy,
    load_taxonomy_nodes,
)
from scudo_mapping_mcp.models import TaxonomyNode

FIXTURE_MINIMAL = Path(__file__).parent / "fixtures" / "p0_ontology_minimal.ttl"

ROOT = "https://example.com/p0-minimal/RootClass"
CHILD = "https://example.com/p0-minimal/ChildClass"
PRICE = "https://example.com/p0-minimal/PriceConcept"
BASE_AMOUNT = "https://example.com/p0-minimal/baseAmount"
PRICE_AMOUNT = "https://example.com/p0-minimal/priceAmount"
DATASET = "https://example.com/p0-minimal/vendorDataset"
IGNORED = "https://example.com/p0-minimal/ignoredPolicy"


@pytest.fixture
def minimal_nodes():
    return load_dcat_taxonomy(FIXTURE_MINIMAL)


@pytest.fixture
def minimal_by_iri(minimal_nodes):
    return {n.iri: n for n in minimal_nodes}


def test_minimal_fixture_extracts_class_concept_property_kinds(minimal_by_iri):
    assert minimal_by_iri[ROOT].node_kind == "class"
    assert minimal_by_iri[CHILD].node_kind == "class"
    assert minimal_by_iri[PRICE].node_kind == "concept"
    assert minimal_by_iri[BASE_AMOUNT].node_kind == "property"
    assert minimal_by_iri[PRICE_AMOUNT].node_kind == "property"
    # DCAT datasets project as taxonomy entities with default concept kind.
    assert minimal_by_iri[DATASET].node_kind == "concept"


def test_minimal_fixture_definitions_and_alt_labels(minimal_by_iri):
    root = minimal_by_iri[ROOT]
    assert root.definition == "Root class definition."
    assert "RootAlt" in root.alt_labels

    price = minimal_by_iri[PRICE]
    assert "Concept definition body." in price.definition
    assert "Concept scope note." in price.definition
    assert "PX" in price.alt_labels

    dataset = minimal_by_iri[DATASET]
    assert "Packaged vendor prices." in dataset.definition
    assert "equities" in dataset.alt_labels
    assert "prices" in dataset.alt_labels


def test_minimal_fixture_subclass_and_subproperty_links(minimal_by_iri):
    child = minimal_by_iri[CHILD]
    assert ROOT in child.superclass_iris

    price_amount = minimal_by_iri[PRICE_AMOUNT]
    assert BASE_AMOUNT in price_amount.superproperty_iris

    price = minimal_by_iri[PRICE]
    assert price.parent_iri == CHILD

    dataset = minimal_by_iri[DATASET]
    assert CHILD in dataset.superclass_iris


def test_minimal_fixture_wires_children_iris(minimal_by_iri):
    root = minimal_by_iri[ROOT]
    child = minimal_by_iri[CHILD]
    assert CHILD in root.children_iris
    assert PRICE in child.children_iris
    assert DATASET in child.children_iris
    assert PRICE_AMOUNT in minimal_by_iri[BASE_AMOUNT].children_iris


def test_minimal_fixture_ignores_non_taxonomy_entities(minimal_by_iri):
    assert IGNORED not in minimal_by_iri
    assert len(minimal_by_iri) == 6


def test_load_dcat_empty_file_returns_empty_list(tmp_path: Path):
    empty = tmp_path / "empty.ttl"
    empty.write_text(
        "@prefix ex: <https://example.com/empty/> .\n",
        encoding="utf-8",
    )
    assert load_dcat_taxonomy(empty) == []


def test_load_dcat_missing_file_raises():
    with pytest.raises(FileNotFoundError, match="not found"):
        load_dcat_taxonomy("/no/such/ontology.ttl")


def test_load_dcat_unknown_suffix_sniffs_turtle(tmp_path: Path):
    """rdflib format sniff fallback when suffix is not in the map."""
    path = tmp_path / "ontology.dat"
    path.write_text(FIXTURE_MINIMAL.read_text(encoding="utf-8"), encoding="utf-8")
    nodes = load_dcat_taxonomy(path)
    assert any(n.iri == PRICE for n in nodes)


def test_load_taxonomy_nodes_defaults_to_cdao_fixture(monkeypatch):
    monkeypatch.delenv("SCUDO_TAXONOMY_LOADER", raising=False)
    from scudo_mapping_mcp import config as cfg

    settings = cfg.Settings.from_env()
    assert settings.taxonomy_loader == "cdao"
    nodes = load_taxonomy_nodes(settings)
    assert nodes
    assert all(isinstance(n, TaxonomyNode) for n in nodes)
    assert any("jpmorgan:data:cdao:" in n.iri for n in nodes)


def test_load_cdao_taxonomy_custom_path_fallback(tmp_path: Path):
    """Explicit path overrides the bundled catalogue fixture."""
    tiny = tmp_path / "tiny.json"
    tiny.write_text(
        '[{"iri": "cdao:test", "label": "Test Node", "parent_iri": null}]',
        encoding="utf-8",
    )
    nodes = load_cdao_taxonomy(tiny)
    assert len(nodes) == 1
    assert nodes[0].iri == "cdao:test"
    assert nodes[0].label == "Test Node"


def test_load_cdao_taxonomy_bundled_fixture_exists():
    assert _CATALOGUE_FIXTURE.exists()
    nodes = load_cdao_taxonomy()
    assert len(nodes) >= 3


def test_load_taxonomy_nodes_dcat_requires_source(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_LOADER", "dcat")
    monkeypatch.delenv("SCUDO_TAXONOMY_SOURCE", raising=False)
    from scudo_mapping_mcp import config as cfg

    settings = cfg.Settings.from_env()
    with pytest.raises(ValueError, match="SCUDO_TAXONOMY_SOURCE"):
        load_taxonomy_nodes(settings)


def test_load_taxonomy_nodes_dcat_dispatch(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_LOADER", "dcat")
    monkeypatch.setenv("SCUDO_TAXONOMY_SOURCE", str(FIXTURE_MINIMAL))
    from scudo_mapping_mcp import config as cfg

    nodes = load_taxonomy_nodes(cfg.Settings.from_env())
    assert any(n.iri == PRICE for n in nodes)


def test_settings_rejects_unknown_taxonomy_loader(monkeypatch):
    monkeypatch.setenv("SCUDO_TAXONOMY_LOADER", "not_registered")
    from scudo_mapping_mcp import config as cfg

    with pytest.raises(ValueError, match="SCUDO_TAXONOMY_LOADER"):
        cfg.Settings.from_env()
