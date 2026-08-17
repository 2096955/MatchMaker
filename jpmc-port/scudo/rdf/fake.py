"""Deterministic DCAT triples + structural SHACL stub (deployed default)."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

_GRAPH_NAMESPACE = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/graphs")
_RDF_TYPE = "rdf:type"
_DCAT_DATASET = "dcat:Dataset"
_DCT_DESCRIPTION = "dct:description"
_DCAT_THEME = "dcat:theme"
_DCAT_VERSION = "dcat:version"
_PROV_ATTRIBUTED_TO = "prov:wasAttributedTo"


def named_graph_for(vendor_product_iri: str, ontology_snapshot: str = "") -> str:
    suffix = uuid5(_GRAPH_NAMESPACE, f"{vendor_product_iri}:{ontology_snapshot}")
    return f"jpmorgan:data:cdao:graphs:enrichment:{suffix}"


def serialise_mapping(mapping_result: dict) -> dict:
    subject = mapping_result["vendor_product_iri"]
    target = mapping_result.get("proposed_target_iri", "")
    snapshot = mapping_result.get("ontology_snapshot", "") or ""
    graph = named_graph_for(subject, snapshot)
    triples: list[dict[str, str]] = []

    def add(predicate: str, obj: str) -> None:
        triples.append(
            {"subject": subject, "predicate": predicate, "object": obj, "graph": graph}
        )

    add(_RDF_TYPE, _DCAT_DATASET)
    rationale = (mapping_result.get("rationale") or "").strip()
    if rationale:
        add(_DCT_DESCRIPTION, rationale[:200])
    if target:
        add(_DCAT_THEME, target)
    if snapshot:
        add(_DCAT_VERSION, snapshot)
    if subject.startswith("mds.") and ":" in subject:
        vendor = subject[len("mds.") :].split(":", 1)[0]
        add(_PROV_ATTRIBUTED_TO, f"jpmorgan:data:vendors:{vendor}")
    for ev in mapping_result.get("evidence", []) or []:
        for src in ev.get("source_iris", []) or []:
            if src.startswith("jpmorgan:data:cdao:") or src.startswith("mds."):
                add("rdfs:seeAlso", src)
    return {
        "triples": triples,
        "conforms": True,
        "report": {
            "engine": "scudo.rdf.fake.serialise_mapping",
            "graph": graph,
            "triple_count": len(triples),
        },
    }


def serialise_rights(rights_result: dict) -> dict:
    return {
        "triples": [],
        "conforms": True,
        "report": {"engine": "scudo.rdf.fake.serialise_rights", "stub": True},
    }


def validate_shapes(triples: list[dict]) -> dict:
    violations: list[str] = []
    if not triples:
        violations.append("empty triple list")
    for i, t in enumerate(triples):
        for k in ("subject", "predicate", "object", "graph"):
            if not t.get(k):
                violations.append(f"triple[{i}] missing {k}")
    return {
        "conforms": not violations,
        "report": {
            "engine": "scudo.rdf.fake.validate_shapes",
            "violations": violations,
            "triple_count": len(triples),
        },
    }
