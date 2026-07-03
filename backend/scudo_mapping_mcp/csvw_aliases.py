"""CSVW Table Schema column alias parsing and normalization for vendor ingest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDFS

CANONICAL_KEYS = ("product_id", "name", "description")

CSVW_NS = "http://www.w3.org/ns/csvw#"
CSVW_NAME = URIRef(f"{CSVW_NS}name")
CAT_ONTOLOGY_NS = "https://example.org/catalogue/ontology/v0.1/deontic/"

_PROPERTY_CANONICAL: dict[str, str] = {
    str(DCTERMS.identifier): "product_id",
    str(DCTERMS.title): "name",
    f"{CAT_ONTOLOGY_NS}longName": "name",
    str(DCTERMS.abstract): "description",
    str(DCTERMS.description): "description",
    f"{CAT_ONTOLOGY_NS}featuresAndBenefitsDescription": "description",
}

_CANONICAL_HINTS: dict[str, tuple[str, ...]] = {
    "product_id": (
        "product_id",
        "productid",
        "product id",
        "ticker",
        "symbol",
        "sku",
        "id",
    ),
    "name": ("name", "title", "product_name", "product name", "label"),
    "description": ("description", "desc", "summary", "details"),
}


def _normalize_label(value: str) -> str:
    return value.strip().lower().replace("_", " ")


def _column_labels(column: dict[str, Any]) -> list[str]:
    """Collect CSVW column ``name`` and ``titles`` as normalized labels."""
    labels: list[str] = []
    name = column.get("name") or column.get("csvw:name")
    if isinstance(name, str) and name.strip():
        labels.append(name.strip())
    titles = column.get("titles") or column.get("csvw:titles") or []
    if isinstance(titles, str):
        titles = [titles]
    if isinstance(titles, list):
        for title in titles:
            if isinstance(title, str) and title.strip():
                labels.append(title.strip())
    return labels


def _match_canonical(labels: list[str]) -> str | None:
    normalized = [_normalize_label(label) for label in labels]
    for key, hints in _CANONICAL_HINTS.items():
        hint_norm = {_normalize_label(h) for h in hints}
        for label in normalized:
            if label in hint_norm:
                return key
            for hint in hint_norm:
                if hint in label or label in hint:
                    return key
    return None


def normalize_csvw_aliases(document: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Extract canonical-key → column alias tuples from a CSVW document."""
    schema = document.get("tableSchema") or document.get("csvw:tableSchema") or {}
    if not isinstance(schema, dict):
        return {}
    columns = schema.get("columns") or schema.get("csvw:columns") or []
    columns = _as_column_list(columns)

    result: dict[str, list[str]] = {key: [] for key in CANONICAL_KEYS}
    for column in columns:
        labels = _column_labels(column)
        forced = column.get("canonical")
        if forced in CANONICAL_KEYS:
            canonical = forced
        else:
            canonical = _match_canonical(labels)
        if canonical is None:
            continue
        for label in labels:
            if label not in result[canonical]:
                result[canonical].append(label)

    return {key: tuple(values) for key, values in result.items() if values}


def merge_col_aliases(
    base: dict[str, tuple[str, ...]],
    csvw_document: dict[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Merge CSVW-derived aliases into the ingest column alias map."""
    extra = normalize_csvw_aliases(csvw_document)
    merged = dict(base)
    for key, aliases in extra.items():
        existing = merged.get(key, ())
        merged[key] = tuple(dict.fromkeys([*existing, *aliases]))
    return merged


def is_csvw_document(document: dict[str, Any]) -> bool:
    """Return True when a JSON object looks like CSVW metadata."""
    return isinstance(document.get("tableSchema"), dict) or isinstance(
        document.get("csvw:tableSchema"), dict
    )


def _as_column_list(columns: Any) -> list[dict[str, Any]]:
    if isinstance(columns, dict):
        return [columns]
    if isinstance(columns, list):
        return [col for col in columns if isinstance(col, dict)]
    return []


def _graph_from_source(source: str | Path | Graph) -> Graph:
    if isinstance(source, Graph):
        return source
    path = Path(source)
    g = Graph()
    g.parse(path, format="turtle")
    return g


def _property_csvw_names(g: Graph, subject: URIRef) -> list[str]:
    names: list[str] = []
    for lit in g.objects(subject, CSVW_NAME):
        if isinstance(lit, Literal):
            value = str(lit).strip()
            if value and value not in names:
                names.append(value)
    if str(subject) == str(DCTERMS.title):
        for lit in g.objects(subject, RDFS.label):
            if isinstance(lit, Literal):
                value = str(lit).strip()
                if value and value not in names:
                    names.append(value)
    return names


def csvw_aliases_from_graph(source: str | Path | Graph) -> dict[str, tuple[str, ...]]:
    """Read ``csvw:name`` triples from an RDF graph and group by ingest key."""
    g = _graph_from_source(source)
    result: dict[str, list[str]] = {key: [] for key in CANONICAL_KEYS}
    subjects = set(g.subjects(predicate=CSVW_NAME, unique=True))
    subjects.update(URIRef(iri) for iri in _PROPERTY_CANONICAL)
    for subject in sorted(subjects, key=str):
        canonical = _PROPERTY_CANONICAL.get(str(subject))
        if canonical is None:
            continue
        names = _property_csvw_names(g, subject)
        if not names:
            continue
        for name in names:
            if name not in result[canonical]:
                result[canonical].append(name)
    return {key: tuple(values) for key, values in result.items() if values}


def csvw_metadata_from_rdf(source: str | Path | Graph) -> dict[str, Any]:
    """Build a CSVW tableSchema document from RDF ``csvw:name`` property aliases."""
    aliases = csvw_aliases_from_graph(source)
    columns: list[dict[str, str]] = []
    for canonical, names in aliases.items():
        for name in names:
            columns.append({"name": name, "canonical": canonical})
    return {"tableSchema": {"columns": columns}}
