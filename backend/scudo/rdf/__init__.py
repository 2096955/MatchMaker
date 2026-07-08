"""SCUDO RDF subpackage — serialiser + SHACL gate.

Default backend (``SCUDO_RDF_BACKEND=fake``) uses ``fake.py`` — deterministic
DCAT triple dicts and a structural SHACL stub. Set ``SCUDO_RDF_BACKEND=rdflib``
for ``real.py`` (rdflib parse + pyshacl against ``shapes/*.ttl``).
"""

from .backend import (
    ALLOWED_RDF_BACKENDS,
    named_graph_for,
    rdf_backend,
    serialise_mapping,
    serialise_rights,
    validate_shapes,
)

__all__ = [
    "ALLOWED_RDF_BACKENDS",
    "named_graph_for",
    "rdf_backend",
    "serialise_mapping",
    "serialise_rights",
    "validate_shapes",
]
