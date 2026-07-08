"""Real RDF serialisation + SHACL validation (WS5 P2).

Contract matches ``scudo.rdf.fake``: ``serialise_mapping`` and
``validate_shapes`` return ``{triples, conforms, report}`` dicts with the
same DCAT/DCT/PROV vocabulary in triple dicts.
"""

from __future__ import annotations

import os
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, PROV, RDF, RDFS

from .fake import named_graph_for
from .fake import serialise_mapping as _fake_serialise_mapping

_ODRL_NS = "http://www.w3.org/ns/odrl/2/"

_PREFIXES: dict[str, str] = {
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "dcat": str(DCAT),
    "dct": str(DCTERMS),
    "prov": str(PROV),
    "odrl": _ODRL_NS,
}


def _shapes_dir() -> Path:
    env = os.getenv("SCUDO_SHACL_SHAPES_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "shapes"


def _term(value: str) -> URIRef | Literal:
    if value.startswith(("http://", "https://", "urn:")):
        return URIRef(value)
    if ":" in value:
        prefix, local = value.split(":", 1)
        base = _PREFIXES.get(prefix)
        if base is not None:
            return URIRef(base + local)
        return URIRef(value)
    return Literal(value)


def triples_to_graph(triples: list[dict]) -> Graph:
    """Convert SCUDO triple dicts (CURIE predicates) to an rdflib Graph."""
    graph = Graph()
    for t in triples:
        subject = t.get("subject")
        predicate = t.get("predicate")
        obj = t.get("object")
        if not subject or not predicate or obj is None or obj == "":
            continue
        graph.add((_term(str(subject)), _term(str(predicate)), _term(str(obj))))
    return graph


def serialise_mapping(mapping_result: dict) -> dict:
    """MappingResult dict → deterministic DCAT triple set (fake-compatible)."""
    out = _fake_serialise_mapping(mapping_result)
    parsed = triples_to_graph(out.get("triples") or [])
    report = dict(out.get("report") or {})
    report["engine"] = "scudo.rdf.real.serialise_mapping"
    report["rdflib_triple_count"] = len(parsed)
    return {**out, "report": report}


def serialise_rights(rights_result: dict) -> dict:
    """Rights result → adapted-ODRL triple dicts, validated via pyshacl."""
    subject = (
        rights_result.get("policy_iri") or rights_result.get("vendor_product_iri") or ""
    ).strip()
    if not subject:
        return {
            "triples": [],
            "conforms": False,
            "report": {
                "engine": "scudo.rdf.real.serialise_rights",
                "error": "missing policy_iri / vendor_product_iri",
            },
        }

    snapshot = rights_result.get("ontology_snapshot", "") or ""
    graph = named_graph_for(subject, snapshot)
    triples: list[dict[str, str]] = []

    def add(predicate: str, obj: str) -> None:
        triples.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "graph": graph,
            }
        )

    add("rdf:type", "odrl:Policy")
    uid = str(rights_result.get("policy_uid") or subject)
    add("odrl:uid", uid)

    target = (rights_result.get("target_iri") or "").strip()
    if target:
        add("odrl:target", target)

    for perm in rights_result.get("permissions") or []:
        if isinstance(perm, dict):
            action = str(perm.get("action") or "use")
        else:
            action = str(perm)
        if not action.startswith("odrl:"):
            action = f"odrl:{action}"
        add("odrl:action", action)

    for prohib in rights_result.get("prohibitions") or []:
        if isinstance(prohib, dict):
            action = str(prohib.get("action") or "use")
        else:
            action = str(prohib)
        if not action.startswith("odrl:"):
            action = f"odrl:{action}"
        add("odrl:prohibition", action)

    validation = validate_shapes(triples)
    conforms = bool(validation.get("conforms")) and bool(triples)
    return {
        "triples": triples,
        "conforms": conforms,
        "report": {
            "engine": "scudo.rdf.real.serialise_rights",
            "graph": graph,
            "triple_count": len(triples),
            "validation": validation.get("report"),
        },
    }


def _structural_violations(triples: list[dict]) -> list[str]:
    violations: list[str] = []
    if not triples:
        violations.append("empty triple list")
    for i, t in enumerate(triples):
        for key in ("subject", "predicate", "object", "graph"):
            if not t.get(key):
                violations.append(f"triple[{i}] missing {key}")
    return violations


def validate_shapes(triples: list[dict]) -> dict:
    """Run pyshacl against repo-local shapes; fail-closed on non-conformance."""
    violations = _structural_violations(triples)
    if violations:
        return {
            "conforms": False,
            "report": {
                "engine": "scudo.rdf.real.validate_shapes",
                "violations": violations,
                "triple_count": len(triples),
            },
        }

    shapes_path = _shapes_dir()
    shape_files = sorted(shapes_path.glob("*.ttl")) if shapes_path.is_dir() else []
    if not shape_files:
        return {
            "conforms": True,
            "report": {
                "engine": "scudo.rdf.real.validate_shapes",
                "note": "no shapes dir",
                "triple_count": len(triples),
            },
        }

    try:
        from pyshacl import validate
    except ImportError as exc:
        return {
            "conforms": False,
            "report": {
                "engine": "scudo.rdf.real.validate_shapes",
                "error": f"ImportError: {exc}",
            },
        }

    data = triples_to_graph(triples)
    shapes = Graph()
    for shape_file in shape_files:
        shapes.parse(shape_file)

    try:
        conforms, _report_graph, report_text = validate(
            data_graph=data,
            shacl_graph=shapes,
            inference="none",
            abort_on_first=False,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed gate
        return {
            "conforms": False,
            "report": {
                "engine": "scudo.rdf.real.validate_shapes",
                "error": f"{type(exc).__name__}: {exc}",
            },
        }

    return {
        "conforms": bool(conforms),
        "report": {
            "engine": "scudo.rdf.real.validate_shapes",
            "text": report_text,
            "shapes": [str(path) for path in shape_files],
            "triple_count": len(triples),
        },
    }
