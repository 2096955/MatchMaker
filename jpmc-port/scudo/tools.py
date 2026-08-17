"""Specialist tools — discovery (sidecar) + confirm (authoritative) + RDF."""

from __future__ import annotations

from typing import Optional

try:
    from strands import tool
except ImportError:  # local/tests without strands

    def tool(fn):  # type: ignore[misc]
        return fn


from . import authoritative as _authoritative
from . import local_state
from . import sidecar as _sidecar
from .catalogue_ontology import list_fillable_dataset_fields, lookup_term
from .rdf import backend as _rdf_backend
from .taxonomy_graph import analyse_taxonomy
from .taxonomy_graph_models import TaxonomyNode
from .zone_context import describe_system_context


@tool
def graphrag_retrieve(query: str, top_k: int = 10) -> list[dict]:
    """Retrieve candidate CDAO nodes by relatedness (lexical sidecar).

    Candidates only — confirm with neptune_node_by_iri before committing.
    Natural-language query; never compose SPARQL or openCypher.
    """
    return _sidecar.candidate_nodes(term=query, limit=top_k)


@tool
def neptune_node_by_iri(iri: str) -> Optional[dict]:
    """Authoritative CDAO node lookup (definition + edges). Confirm candidates here."""
    return _authoritative.node_by_iri(iri=iri)


@tool
def neptune_existing_mapping(vendor: str, vendor_product_ref: str) -> Optional[dict]:
    """Prior authoritative mapping for vendor product, if any."""
    return _authoritative.existing_mapping(
        vendor=vendor, vendor_product_ref=vendor_product_ref
    )


@tool
def neptune_conflicts(vendor_product_ref: str) -> list[dict]:
    """Cross-vendor products with differing target IRIs."""
    return _authoritative.conflicts(vendor_product_ref=vendor_product_ref)


@tool
def analyse_taxonomy_candidates(
    candidate_iris: list[str],
    anchor_iris: Optional[list[str]] = None,
    max_nodes: int = 100,
    max_depth: int = 8,
) -> dict:
    """Return bounded local graph evidence; never modifies scores or mappings."""
    illustrative = local_state.is_local()
    snapshot = (
        _authoritative.illustrative_taxonomy_snapshot()
        if illustrative
        else _authoritative.taxonomy_snapshot()
    )
    if snapshot is None:
        return {
            "evidence_valid": False,
            "error": "topology_unavailable",
            "illustrative": False,
        }
    nodes = [TaxonomyNode(**node) for node in snapshot]
    evidence = analyse_taxonomy(
        nodes,
        candidate_iris=candidate_iris,
        anchor_iris=anchor_iris,
        max_nodes=max_nodes,
        max_depth=max_depth,
    ).model_dump(mode="json")
    return {**evidence, "illustrative": illustrative}


@tool
def rdf_serialise_mapping(mapping_result: dict) -> dict:
    """Serialise MappingResult → DCAT triples. Never hand-author Turtle."""
    return _rdf_backend.serialise_mapping(mapping_result)


@tool
def rdf_serialise_rights(rights_result: dict) -> dict:
    """Serialise rights → adapted-ODRL triples."""
    return _rdf_backend.serialise_rights(rights_result)


@tool
def rdf_validate_shapes(triples: list[dict]) -> dict:
    """SHACL/structural gate after serialisation."""
    return _rdf_backend.validate_shapes(triples)


@tool
def neptune_publish_triples(named_graph: str, triples: list[dict]) -> dict:
    """Orchestrator-only. Specialists must never call this."""
    raise NotImplementedError("orchestrator-only publish")


@tool
def lookup_catalogue_term(query: str) -> dict:
    """Lookup CatalogueOntology v0.1 term by curie, IRI, local name, or csvw:name.

    Examples: 'cat:businessConcept', 'AssetClassPermId', 'dcat:Dataset'.
    Returns {iri, curie, kind, label, definition, csvw_names} or {error}.
    """
    hit = lookup_term(query)
    if hit is None:
        return {"error": f"unknown term: {query!r}", "hint": "try csvw name or curie"}
    return hit


@tool
def list_catalogue_dataset_fields() -> list[dict]:
    """List fillable dcat:Dataset / cat:* dataset properties with csvw column aliases."""
    return list_fillable_dataset_fields()


MAPPING_SPECIALIST_TOOLS = [
    describe_system_context,
    lookup_catalogue_term,
    list_catalogue_dataset_fields,
    graphrag_retrieve,
    neptune_node_by_iri,
    neptune_existing_mapping,
    neptune_conflicts,
    analyse_taxonomy_candidates,
    rdf_serialise_mapping,
    rdf_validate_shapes,
]

# Verifier investigates — never remaps. Confirm IRIs, check priors/conflicts,
# consult catalogue terms when scoring semantic_fit / provenance.
VERIFIER_TOOLS = [
    describe_system_context,
    lookup_catalogue_term,
    neptune_node_by_iri,
    neptune_existing_mapping,
    neptune_conflicts,
    analyse_taxonomy_candidates,
    rdf_validate_shapes,
]

CATALOGUE_FILL_TOOLS = [
    describe_system_context,
    lookup_catalogue_term,
    list_catalogue_dataset_fields,
]
RIGHTS_SPECIALIST_TOOLS = [rdf_serialise_rights, rdf_validate_shapes]
ORCHESTRATOR_ONLY_TOOLS = [neptune_publish_triples]
