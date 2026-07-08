"""RDF backend dispatch — ``SCUDO_RDF_BACKEND`` seam (WS5 P2).

Default ``fake`` preserves pre-P2 stub behaviour. ``rdflib`` selects
``scudo.rdf.real`` (rdflib parse + pyshacl validation).
"""

from __future__ import annotations

import os

_ALLOWED_RDF_BACKENDS: tuple[str, ...] = ("fake", "rdflib")
ALLOWED_RDF_BACKENDS = _ALLOWED_RDF_BACKENDS


def rdf_backend() -> str:
    """Live ``SCUDO_RDF_BACKEND`` — re-reads env for smoke/tests."""
    backend = os.getenv("SCUDO_RDF_BACKEND", "fake").strip().lower()
    if backend not in _ALLOWED_RDF_BACKENDS:
        raise ValueError(
            f"SCUDO_RDF_BACKEND={backend!r} not in {_ALLOWED_RDF_BACKENDS!r}"
        )
    return backend


def _impl():
    if rdf_backend() == "rdflib":
        from . import real

        return real
    from . import fake

    return fake


def serialise_mapping(mapping_result: dict) -> dict:
    return _impl().serialise_mapping(mapping_result)


def serialise_rights(rights_result: dict) -> dict:
    return _impl().serialise_rights(rights_result)


def validate_shapes(triples: list[dict]) -> dict:
    return _impl().validate_shapes(triples)


def named_graph_for(vendor_product_iri: str, ontology_snapshot: str = "") -> str:
    from .fake import named_graph_for as _named_graph_for

    return _named_graph_for(vendor_product_iri, ontology_snapshot)
