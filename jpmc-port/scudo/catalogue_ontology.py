"""CatalogueOntology v0.1 Deontic — load + lookup for agent fill.

Uses Capone's normalized TTL (canonical prefixes). Visual Citrix transcript
prefixes like ``<http://w3.org>`` are invalid and must not be used.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Optional

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "catalogue_ontology_v0_1_deontic.ttl"
)
CAT = "https://example.org/catalogue/ontology/v0.1/deontic/"
DCAT = "http://www.w3.org/ns/dcat#"
DCT = "http://purl.org/dc/terms/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CSVW = "http://www.w3.org/ns/csvw#"

DATASET_FILL_PROPERTIES: dict[str, str] = {
    "dcterms:identifier": "identifier",
    "dcterms:title": "title",
    "dcterms:description": "description",
    "cat:featuresAndBenefitsDescription": "features_and_benefits_description",
    "dcat:keyword": "keyword",
    "dcat:theme": "theme",
    "cat:superTheme": "super_theme",
    "cat:businessConcept": "business_concept",
    "dcterms:extent": "extent",
    "dcat:startDate": "start_date",
    "dcat:endDate": "end_date",
    "cat:assetClass": "asset_class",
    "cat:superAssetClass": "super_asset_class",
    "dcterms:accrualPeriodicity": "accrual_periodicity",
    "dcterms:spatial": "spatial",
    "cat:geographicCoverage": "geographic_coverage",
    "cat:temporalCoverage": "temporal_coverage",
    "cat:industryCoverage": "industry_coverage",
    "cat:contentTypeCoverage": "content_type_coverage",
    "dcat:landingPage": "landing_page",
}

DISTRIBUTION_FILL_PROPERTIES: dict[str, str] = {
    "dcat:accessURL": "access_url",
    "dcat:mediaType": "media_type",
    "dcterms:conformsTo": "conforms_to",
    "dcterms:type": "distribution_type",
}

FIELD_FILL_PROPERTIES: dict[str, str] = {
    "skos:notation": "notation",
    "cat:primaryKeyFlag": "primary_key_flag",
    "cat:nullableFlag": "nullable_flag",
    "cat:sequenceNumber": "sequence_number",
    "dcterms:type": "field_type",
}


@functools.lru_cache(maxsize=1)
def ontology_graph():
    from rdflib import Graph

    g = Graph()
    g.parse(_FIXTURE, format="turtle")
    return g


def _to_curie(iri: str) -> str:
    for ns, pref in (
        (DCAT, "dcat:"),
        (DCT, "dcterms:"),
        (SKOS, "skos:"),
        (RDFS, "rdfs:"),
        (CAT, "cat:"),
        (RDF, "rdf:"),
    ):
        if iri.startswith(ns):
            return pref + iri[len(ns) :]
    return iri


@functools.lru_cache(maxsize=1)
def _term_index() -> dict[str, dict[str, Any]]:
    from rdflib import Literal, URIRef
    from rdflib.namespace import RDF as RDF_NS
    from rdflib.namespace import RDFS as RDFS_NS
    from rdflib.namespace import SKOS as SKOS_NS

    g = ontology_graph()
    csvw_name = URIRef(CSVW + "name")
    idx: dict[str, dict[str, Any]] = {}

    def _put(key: str, entry: dict) -> None:
        if key:
            idx[key.lower()] = entry

    for s in set(g.subjects()):
        types = {str(t) for t in g.objects(s, RDF_NS.type)}
        if not types:
            continue
        iri = str(s)
        is_prop = str(RDF_NS.Property) in types
        is_class = str(RDFS_NS.Class) in types
        if not is_prop and not is_class:
            continue
        kind = "property" if is_prop else "class"
        label = next(
            (str(o) for o in g.objects(s, RDFS_NS.label) if isinstance(o, Literal)),
            "",
        )
        definition = next(
            (
                str(o)
                for o in g.objects(s, SKOS_NS.definition)
                if isinstance(o, Literal)
            ),
            "",
        )
        comment = next(
            (str(o) for o in g.objects(s, RDFS_NS.comment) if isinstance(o, Literal)),
            "",
        )
        csvw_names = [str(o) for o in g.objects(s, csvw_name)]
        curie = _to_curie(iri)
        entry = {
            "iri": iri,
            "curie": curie,
            "kind": kind,
            "label": label,
            "definition": definition or comment,
            "csvw_names": csvw_names,
        }
        _put(curie, entry)
        _put(iri, entry)
        _put(iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1], entry)
        for name in csvw_names:
            _put(name, entry)

    return idx


def lookup_term(query: str) -> Optional[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return None
    return _term_index().get(q.lower())


def list_fillable_dataset_fields() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for curie, attr in DATASET_FILL_PROPERTIES.items():
        term = lookup_term(curie) or {}
        out.append(
            {
                "curie": curie,
                "attr": attr,
                "label": str(term.get("label") or attr),
                "csvw_names": ",".join(term.get("csvw_names") or []),
            }
        )
    return out


def fixture_path() -> Path:
    return _FIXTURE
