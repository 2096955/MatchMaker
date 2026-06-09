"""SCUDO RDF subpackage — serialiser + SHACL gate.

Real implementation lives in modules.rdf (JPMC-side). This subpackage holds a
faithful FAKE that produces DCAT-conformant triple dicts deterministically
from a MappingResult, plus a SHACL stub that always reports `conforms=True`.

The fake is good enough to drive the SCUDO publish path end-to-end (verifier
gate, IRI determinism check, named-graph provenance) — so demos and tests can
exercise the PUBLISHED outcome without the JPMC serialiser being wired up.
"""
from .fake import serialise_mapping, serialise_rights, validate_shapes

__all__ = ["serialise_mapping", "serialise_rights", "validate_shapes"]
