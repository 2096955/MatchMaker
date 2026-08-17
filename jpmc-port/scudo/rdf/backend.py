"""RDF backend dispatch — day-one hardwires fake (rdflib not in JPMC scope)."""

from __future__ import annotations

import os

from . import fake

ALLOWED_RDF_BACKENDS: tuple[str, ...] = ("fake",)


def rdf_backend() -> str:
    backend = os.getenv("SCUDO_RDF_BACKEND", "fake").strip().lower()
    if backend not in ALLOWED_RDF_BACKENDS:
        raise ValueError(
            f"SCUDO_RDF_BACKEND={backend!r} not in {ALLOWED_RDF_BACKENDS!r}"
        )
    return backend


def serialise_mapping(mapping_result: dict) -> dict:
    rdf_backend()
    return fake.serialise_mapping(mapping_result)


def serialise_rights(rights_result: dict) -> dict:
    rdf_backend()
    return fake.serialise_rights(rights_result)


def validate_shapes(triples: list[dict]) -> dict:
    rdf_backend()
    return fake.validate_shapes(triples)


def named_graph_for(vendor_product_iri: str, ontology_snapshot: str = "") -> str:
    return fake.named_graph_for(vendor_product_iri, ontology_snapshot)
