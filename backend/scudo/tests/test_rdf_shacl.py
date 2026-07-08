"""P2 — real RDF backend + pyshacl validation (WS5)."""

from __future__ import annotations

import pytest

from scudo.rdf import backend, fake, real

_SAMPLE_MAPPING = {
    "vendor_product_iri": "mds.lseg:00000000-0000-4000-8000-000000000001",
    "proposed_target_iri": "jpmorgan:data:cdao:equity-prices",
    "rationale": "Equity real-time prices mapped to CDAO concept.",
    "ontology_snapshot": "cdao-2026-01",
}


def test_rdf_backend_defaults_to_fake(monkeypatch):
    monkeypatch.delenv("SCUDO_RDF_BACKEND", raising=False)
    assert backend.rdf_backend() == "fake"


def test_fake_backend_validate_accepts_serialised_triples(monkeypatch):
    monkeypatch.delenv("SCUDO_RDF_BACKEND", raising=False)
    triples = fake.serialise_mapping(_SAMPLE_MAPPING)["triples"]
    result = backend.validate_shapes(triples)
    assert result["conforms"] is True
    assert result["report"]["engine"] == "scudo.rdf.fake.validate_shapes"


def test_fake_backend_rejects_empty_triples(monkeypatch):
    monkeypatch.delenv("SCUDO_RDF_BACKEND", raising=False)
    result = backend.validate_shapes([])
    assert result["conforms"] is False


@pytest.mark.parametrize("backend_name", ["rdflib"])
def test_rdflib_backend_validate_accepts_serialised_triples(monkeypatch, backend_name):
    pytest.importorskip("pyshacl")
    monkeypatch.setenv("SCUDO_RDF_BACKEND", backend_name)
    triples = fake.serialise_mapping(_SAMPLE_MAPPING)["triples"]
    result = backend.validate_shapes(triples)
    assert result["conforms"] is True
    assert result["report"]["engine"] == "scudo.rdf.real.validate_shapes"
    assert "shapes" in result["report"]


def test_real_serialise_round_trips_through_rdflib():
    out = real.serialise_mapping(_SAMPLE_MAPPING)
    assert out["triples"]
    assert out["conforms"] is True
    assert out["report"]["engine"] == "scudo.rdf.real.serialise_mapping"
    assert out["report"]["rdflib_triple_count"] >= len(out["triples"])


def test_real_pyshacl_rejects_dataset_without_theme():
    pytest.importorskip("pyshacl")
    mapping = {
        "vendor_product_iri": "mds.lseg:00000000-0000-4000-8000-000000000099",
        "rationale": "Dataset without a mapped theme.",
    }
    triples = fake.serialise_mapping(mapping)["triples"]
    result = real.validate_shapes(triples)
    assert result["conforms"] is False


def test_triples_to_graph_expands_curie_predicates():
    triples = [
        {
            "subject": "mds.lseg:abc",
            "predicate": "rdf:type",
            "object": "dcat:Dataset",
            "graph": "jpmorgan:data:cdao:graphs:enrichment:test",
        }
    ]
    graph = real.triples_to_graph(triples)
    assert len(graph) == 1
    _, p, o = next(iter(graph))
    assert str(p) == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    assert str(o) == "http://www.w3.org/ns/dcat#Dataset"


def test_tools_rdf_validate_uses_fake_by_default(monkeypatch):
    monkeypatch.delenv("SCUDO_RDF_BACKEND", raising=False)
    from scudo import tools

    triples = fake.serialise_mapping(_SAMPLE_MAPPING)["triples"]
    result = tools.rdf_validate_shapes(triples)
    assert result["conforms"] is True
    assert result["report"]["engine"] == "scudo.rdf.fake.validate_shapes"
