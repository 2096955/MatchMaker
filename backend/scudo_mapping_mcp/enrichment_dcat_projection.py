"""Enrichment → matcher DCAT projection (WS4 P1).

Projects ``ConceptualGraph`` nodes into ``TaxonomyNode`` hints the matcher
seam understands. This is a **read-only bridge** for bundle export and
future candidate widening — it does **not** feed ``matching.py`` (I-enrich).
"""

from __future__ import annotations

from .models import ConceptualGraph, ConceptualNode, ConceptualNodeKind, TaxonomyNode
from .models_dcat import project_dcat_entity


def project_conceptual_node(node: ConceptualNode) -> TaxonomyNode | None:
    """Map one enrichment-layer node to a TaxonomyNode hint, when DCAT-shaped."""
    parent = node.attaches_to_concept_iri or None
    themes = [parent] if parent else []

    if node.kind == ConceptualNodeKind.FIELD:
        desc_parts = [p for p in (node.data_type, node.vendor_field_name) if p]
        return project_dcat_entity(
            iri=node.iri,
            title=node.label,
            description=" | ".join(desc_parts),
            node_kind="property",
            themes=themes,
        )

    if node.kind == ConceptualNodeKind.FIELD_GROUP:
        desc_parts = [p for p in (node.database_notation, node.schema_notation) if p]
        return project_dcat_entity(
            iri=node.iri,
            title=node.label,
            description=" | ".join(desc_parts),
            node_kind="class",
            themes=themes,
        )

    dcat_kinds = {
        ConceptualNodeKind.DATA_SERVICE,
        ConceptualNodeKind.DISTRIBUTION,
        ConceptualNodeKind.DISTRIBUTED_DATASET,
        ConceptualNodeKind.MARKETING_DATASET,
        ConceptualNodeKind.DELIVERY_PRODUCT,
        ConceptualNodeKind.DELIVERY_CHANNEL,
        ConceptualNodeKind.PRODUCT_PACKAGE,
    }
    if node.kind in dcat_kinds:
        return project_dcat_entity(
            iri=node.iri,
            title=node.label,
            description="",
            node_kind="concept",
            themes=themes,
        )

    if node.kind in {
        ConceptualNodeKind.BUSINESS_CONCEPT_ELEMENT,
        ConceptualNodeKind.DATA_TAXONOMY,
        ConceptualNodeKind.DATA_DICTIONARY,
        ConceptualNodeKind.BUSINESS_DATA_ELEMENT,
    }:
        return project_dcat_entity(
            iri=node.iri,
            title=node.label,
            description="",
            node_kind="concept",
            themes=themes,
        )

    return None


def project_conceptual_graph(graph: ConceptualGraph) -> list[TaxonomyNode]:
    """Project all DCAT-projectable nodes from an enrichment graph."""
    out: list[TaxonomyNode] = []
    for node in graph.nodes:
        projected = project_conceptual_node(node)
        if projected is not None:
            out.append(projected)
    return out
