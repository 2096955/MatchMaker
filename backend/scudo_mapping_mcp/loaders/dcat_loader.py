"""RDF/Turtle DCAT+SKOS taxonomy loader (WS1 / P0).

Parses ``.ttl`` / ``.rdf`` / ``.jsonld`` with rdflib and projects entities
into extended ``TaxonomyNode`` shapes for the matcher seam. Load-time RDFS
closure is optional (``SCUDO_SUBSUMPTION_DEPTH`` > 0); BM25 expansion is P1
in ``retrieval.multi_path_retrieve``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rdflib import Graph

from rdflib import Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, OWL, RDF, RDFS, SKOS

from ..csvw_aliases import CAT_ONTOLOGY_NS
from ..models import TaxonomyNode
from ..models_dcat import (
    DcatDataService,
    DcatDataset,
    DcatDistribution,
    _join_definition,
)
from .subsumption import materialize_subsumption_closure

SKOS_NS = Namespace("http://www.w3.org/2004/02/skos/core#")
RDFS_NS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
OWL_NS = Namespace("http://www.w3.org/2002/07/owl#")
DCAT_NS = Namespace("http://www.w3.org/ns/dcat#")
# cat: placeholder namespace — SAME constant the ingest-side alias map uses
# (G20 consistency: one canonical cat: IRI, no second hardcoding).
CAT_NS = Namespace(CAT_ONTOLOGY_NS)

_CONCEPT_TYPES = {SKOS.Concept, SKOS_NS.Concept}
_CLASS_TYPES = {OWL.Class, OWL_NS.Class, RDFS.Class, RDFS_NS.Class}
RDFS_PROPERTY = URIRef("http://www.w3.org/2000/01/rdf-schema#Property")

_PROPERTY_TYPES = {
    RDF.Property,
    RDFS_PROPERTY,
    OWL.DatatypeProperty,
    OWL.ObjectProperty,
}
_DCAT_DATASET = {DCAT.Dataset, DCAT_NS.Dataset}
_DCAT_DISTRIBUTION = {DCAT.Distribution, DCAT_NS.Distribution}
_DCAT_DATASERVICE = {DCAT.DataService, DCAT_NS.DataService}


def _as_str(term) -> str:
    if term is None:
        return ""
    if isinstance(term, Literal):
        return str(term)
    return str(term)


def _labels(g: "Graph", subject: URIRef) -> tuple[str, list[str]]:
    pref = ""
    for p in (SKOS.prefLabel, DCTERMS.title, RDFS.label, DCTERMS.identifier):
        for lit in g.objects(subject, p):
            if isinstance(lit, Literal):
                pref = str(lit)
                break
        if pref:
            break
    if not pref:
        s = str(subject)
        pref = s.rsplit("/", 1)[-1].replace("_", " ").replace("-", " ")
    alts = [str(l) for l in g.objects(subject, SKOS.altLabel) if isinstance(l, Literal)]
    return pref, alts


def _definition(g: "Graph", subject: URIRef) -> str:
    parts: list[str] = []
    for p in (
        SKOS.definition,
        SKOS.scopeNote,
        SKOS.example,
        DCTERMS.description,
        RDFS.comment,
    ):
        for lit in g.objects(subject, p):
            if isinstance(lit, Literal):
                parts.append(str(lit))
    return _join_definition(*parts)


def _object_iris(g: "Graph", subject: URIRef, predicate) -> list[str]:
    return sorted({_as_str(o) for o in g.objects(subject, predicate) if o})


def _node_kind(g: "Graph", subject: URIRef) -> Literal["concept", "class", "property"]:
    types = set(g.objects(subject, RDF.type))
    if types & _PROPERTY_TYPES:
        return "property"
    if types & _CLASS_TYPES:
        return "class"
    return "concept"


def _is_taxonomy_entity(g: "Graph", subject: URIRef) -> bool:
    types = set(g.objects(subject, RDF.type))
    return bool(
        types
        & (
            _CONCEPT_TYPES
            | _CLASS_TYPES
            | _PROPERTY_TYPES
            | _DCAT_DATASET
            | _DCAT_DISTRIBUTION
            | _DCAT_DATASERVICE
        )
    )


def _first_text(g: "Graph", subject: URIRef, *predicates) -> str | None:
    """First non-empty object as DISPLAY TEXT: literals pass through, IRI
    references resolve to their prefLabel via ``_labels`` (Phase E step 3 —
    never raw IRIs into text-bearing fields; raw IRIs are tokeniser junk).
    """
    for p in predicates:
        for o in g.objects(subject, p):
            if isinstance(o, Literal):
                s = str(o)
                if s:
                    return s
                continue
            if isinstance(o, URIRef):
                label, _ = _labels(g, o)
                if label:
                    return label
    return None


def _extract_node(g: "Graph", subject: URIRef) -> TaxonomyNode | None:
    if not _is_taxonomy_entity(g, subject):
        return None
    label, alt_labels = _labels(g, subject)
    definition = _definition(g, subject)
    broader = _object_iris(g, subject, SKOS.broader)
    parent = broader[0] if broader else None
    super_cls = _object_iris(g, subject, RDFS.subClassOf)
    super_prop = _object_iris(g, subject, RDFS.subPropertyOf)
    themes = _object_iris(g, subject, DCAT.theme)
    keywords = [
        str(l) for l in g.objects(subject, DCAT.keyword) if isinstance(l, Literal)
    ]
    if keywords:
        alt_labels = list(dict.fromkeys(alt_labels + keywords))
    merged_super = list(dict.fromkeys(super_cls + themes))
    return TaxonomyNode(
        iri=_as_str(subject),
        label=label,
        parent_iri=parent,
        definition=definition,
        alt_labels=alt_labels,
        node_kind=_node_kind(g, subject),
        superclass_iris=merged_super,
        superproperty_iris=super_prop,
        # Phase E matching signals — read UNCONDITIONALLY into STRUCTURED
        # fields (never pre-concatenated into definition/alt_labels; text
        # composition is flag time only, see taxonomy_text.py). IRI-valued
        # references resolve to prefLabels via _first_text.
        business_concept=_first_text(g, subject, CAT_NS.businessConcept),
        asset_class=_first_text(g, subject, CAT_NS.assetClass),
        super_asset_class=_first_text(g, subject, CAT_NS.superAssetClass),
    )


# ── Phase C — typed DCAT entity extraction (2026-07-13 UML attrs) ──────────
# LOCKSTEP RULE (spec Phase C): ``_extract_node`` above builds TaxonomyNode
# directly for the loader path; ``models_dcat`` projectors are the parallel
# path used by ``enrichment_dcat_projection``. These extractors are the
# single place graph → widened-Dcat-model reading happens, so both paths
# read the same predicates. ``test_dcat_phase_c_lockstep.py`` pins the two
# paths' TaxonomyNode agreement for the same input graph.


def _first_literal(g: "Graph", subject: URIRef, *predicates) -> str | None:
    for p in predicates:
        for o in g.objects(subject, p):
            s = _as_str(o)
            if s:
                return s
    return None


def _dataset_iri_for(g: "Graph", subject: URIRef, forward, backward) -> str | None:
    """Resolve the owning dataset via a forward predicate (subject→dataset)
    or a backward one (dataset→subject), whichever the export used."""
    for o in g.objects(subject, forward):
        return _as_str(o)
    for s in g.subjects(backward, subject):
        return _as_str(s)
    return None


def extract_dcat_dataset(g: "Graph", subject: URIRef) -> DcatDataset:
    """Read a dcat:Dataset subject into the widened DcatDataset model.

    Phase E boundary: business_concept / asset_class / super_asset_class /
    update_period and the coverage block (geographic / temporal / industry /
    contentType) are extracted into MODEL fields only — nothing here (or in
    ``project_dcat_dataset``) feeds them into matching text. That wiring is
    Phase E's flag-gated rollout.
    """
    title, alts = _labels(g, subject)
    keywords = [
        str(o) for o in g.objects(subject, DCAT.keyword) if isinstance(o, Literal)
    ]
    return DcatDataset(
        iri=_as_str(subject),
        title=title,
        description=_definition(g, subject),
        themes=_object_iris(g, subject, DCAT.theme),
        keywords=list(dict.fromkeys(alts + keywords)),
        update_period=_first_literal(g, subject, CAT_NS.updatePeriod),
        # Phase E signals: _first_text (not _first_literal) so IRI-valued
        # references resolve to prefLabels — keeps this projector path in
        # LOCKSTEP with _extract_node above (test_dcat_phase_c_lockstep).
        business_concept=_first_text(g, subject, CAT_NS.businessConcept),
        asset_class=_first_text(g, subject, CAT_NS.assetClass),
        super_asset_class=_first_text(g, subject, CAT_NS.superAssetClass),
        geographic_coverage=_first_literal(g, subject, CAT_NS.geographicCoverage),
        temporal_coverage=_first_literal(g, subject, CAT_NS.temporalCoverage),
        industry_coverage=_first_literal(g, subject, CAT_NS.industryCoverage),
        content_type_coverage=_first_literal(g, subject, CAT_NS.contentTypeCoverage),
    )


def extract_dcat_distribution(g: "Graph", subject: URIRef) -> DcatDistribution:
    """Read a dcat:Distribution subject into the widened model.

    UML ``distribution_type`` has no dcat: predicate; the transcript grounds
    typed-ness on dcterms:type, so that is what carries it here.
    """
    title, _ = _labels(g, subject)
    return DcatDistribution(
        iri=_as_str(subject),
        title=title,
        description=_definition(g, subject),
        access_url=_first_literal(g, subject, DCAT.accessURL) or "",
        dataset_iri=_dataset_iri_for(g, subject, DCTERMS.isPartOf, DCAT.distribution),
        media_type=_first_literal(g, subject, DCAT.mediaType),
        conforms_to=_first_literal(g, subject, DCTERMS.conformsTo),
        distribution_type=_first_literal(g, subject, DCTERMS.type),
    )


def extract_dcat_data_service(g: "Graph", subject: URIRef) -> DcatDataService:
    """Read a dcat:DataService subject into the widened model.

    UML ``type`` → ``service_type`` (dcterms:type, per the transcript's
    "Database Data Service, API Data Service" note); deploymentType/apiType
    have no standard dcat: home so they read from the cat: namespace.
    """
    title, _ = _labels(g, subject)
    return DcatDataService(
        iri=_as_str(subject),
        title=title,
        description=_definition(g, subject),
        endpoint_url=_first_literal(g, subject, DCAT.endpointURL) or "",
        service_type=_first_literal(g, subject, DCTERMS.type),
        deployment_type=_first_literal(g, subject, CAT_NS.deploymentType),
        api_type=_first_literal(g, subject, CAT_NS.apiType),
        dataset_iri=_dataset_iri_for(g, subject, DCAT.servesDataset, DCAT.service),
    )


def _wire_children(nodes: list[TaxonomyNode]) -> list[TaxonomyNode]:
    child_map: dict[str, list[str]] = {}
    for n in nodes:
        for parent in [n.parent_iri, *n.superclass_iris, *n.superproperty_iris]:
            if parent:
                child_map.setdefault(parent, []).append(n.iri)
    out: list[TaxonomyNode] = []
    for n in nodes:
        kids = sorted(set(n.children_iris) | set(child_map.get(n.iri, [])))
        out.append(n.model_copy(update={"children_iris": kids}))
    return out


def _rdflib_format(path: Path) -> str | None:
    """Map file suffix to an rdflib parse format name."""
    suffix = path.suffix.lower()
    return {
        ".ttl": "turtle",
        ".rdf": "xml",
        ".xml": "xml",
        ".jsonld": "json-ld",
        ".json": "json-ld",
        ".n3": "n3",
        ".nt": "nt",
    }.get(suffix)


def _parse_rdf_file(path: Path) -> "Graph":
    try:
        from rdflib import Graph
    except ImportError as e:
        raise ImportError(
            "SCUDO_TAXONOMY_LOADER=dcat requires rdflib; "
            "install backend/requirements.txt"
        ) from e

    g = Graph()
    fmt = _rdflib_format(path)
    if fmt:
        g.parse(path, format=fmt)
    else:
        # Let rdflib sniff when the suffix is unknown.
        g.parse(path)
    return g


def load_dcat_taxonomy(
    source: str | Path,
    *,
    subsumption_depth: int = 0,
) -> list[TaxonomyNode]:
    """Parse an RDF ontology file and return TaxonomyNode list."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"DCAT taxonomy source not found: {path}")
    g = _parse_rdf_file(path)
    return load_dcat_taxonomy_from_graph(g, subsumption_depth=subsumption_depth)


def load_dcat_taxonomy_from_graph(
    g,
    *,
    subsumption_depth: int = 0,
) -> list[TaxonomyNode]:
    """Test helper — load from an in-memory rdflib Graph."""
    from rdflib import URIRef

    subjects = {s for s in g.subjects() if isinstance(s, URIRef)}
    raw = []
    for subj in sorted(subjects, key=str):
        node = _extract_node(g, subj)
        if node is not None:
            raw.append(node)
    if not raw:
        return []
    wired = _wire_children(raw)
    if subsumption_depth > 0:
        return materialize_subsumption_closure(wired, max_depth=subsumption_depth)
    return wired
