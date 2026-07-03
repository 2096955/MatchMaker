"""Catalogue ontology POC — transcript-derived fixture load + RDF CSVW aliases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from rdflib import Graph

from scudo_mapping_mcp.csvw_aliases import (
    csvw_aliases_from_graph,
    csvw_metadata_from_rdf,
)
from scudo_mapping_mcp.loaders.dcat_loader import (
    load_dcat_taxonomy,
    load_dcat_taxonomy_from_graph,
)

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "catalogue_ontology_v0_1_deontic.ttl"
)
CAT = "https://example.org/catalogue/ontology/v0.1/deontic/"
DCAT = "http://www.w3.org/ns/dcat#"

KEY_ENTITIES: dict[str, tuple[str, str]] = {
    f"{DCAT}Dataset": ("class", "Dataset"),
    f"{DCAT}DatasetSeries": ("class", "Dataset series"),
    f"{CAT}DistributedDataset": ("class", "Distributed Dataset"),
    f"{CAT}ProductPackage": ("class", "Product Package"),
    f"{CAT}Field": ("class", "Field"),
    f"{CAT}BusinessConceptElement": ("class", "Business Concept Element"),
    f"{DCAT}accessURL": ("property", "access address"),
    f"{CAT}databaseNotation": ("property", "database notation"),
}


def test_fixture_ttl_parses_with_rdflib():
    graph = Graph()
    graph.parse(FIXTURE, format="turtle")
    assert len(graph) > 0


def test_load_dcat_taxonomy_returns_nonempty_nodes():
    nodes = load_dcat_taxonomy(FIXTURE)
    assert len(nodes) >= 50


def test_load_dcat_taxonomy_from_graph_key_entities_and_subclass():
    graph = Graph()
    graph.parse(FIXTURE, format="turtle")
    nodes = load_dcat_taxonomy_from_graph(graph)
    by_iri = {node.iri: node for node in nodes}

    for iri, (kind, label) in KEY_ENTITIES.items():
        node = by_iri.get(iri)
        assert node is not None, f"missing {iri}"
        assert node.node_kind == kind
        assert node.label == label
        assert node.definition

    distributed = by_iri[f"{CAT}DistributedDataset"]
    assert f"{DCAT}Dataset" in distributed.superclass_iris

    dataset_series = by_iri[f"{DCAT}DatasetSeries"]
    assert f"{DCAT}Dataset" in dataset_series.superclass_iris

    db_notation = by_iri[f"{CAT}databaseNotation"]
    assert (
        "http://www.w3.org/2004/02/skos/core#notation" in db_notation.superproperty_iris
    )


def test_csvw_aliases_from_graph_maps_catalogue_headers():
    aliases = csvw_aliases_from_graph(FIXTURE)
    assert "PermID" in aliases["product_id"]
    assert "Code" in aliases["product_id"]
    assert "Title" in aliases["name"]
    assert "SummaryText" in aliases["description"]


def test_csvw_metadata_from_rdf_round_trips_through_ingest(monkeypatch):
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp.ingest import ingest_bytes

    metadata = csvw_metadata_from_rdf(FIXTURE)
    csv = b"PermID,Title,SummaryText\nABC-1,Alpha Product,Long summary\n"
    frames = ingest_bytes(
        "LSEG",
        "catalogue.csv",
        csv,
        upsert=False,
        csvw_metadata=metadata,
    )
    assert len(frames) == 1
    assert frames[0].product_id == "ABC-1"
    assert frames[0].name == "Alpha Product"
    assert frames[0].description == "Long summary"


def test_csvw_metadata_from_rdf_fail_closed_on_bad_path():
    with pytest.raises(Exception):
        csvw_metadata_from_rdf("/no/such/catalogue.ttl")
