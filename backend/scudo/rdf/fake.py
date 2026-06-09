"""Fake RDF serialiser — deterministic DCAT triples + a no-op SHACL gate.

What "fake" means:
  • Deterministic: same MappingResult dict in → identical triple list out
    (replay-safety per clarif. G39). No random IRIs anywhere.
  • DCAT-shaped: predicates use the DCAT/DCT/RDF vocabulary the real
    serialiser will use, so downstream consumers (the publish gate, the
    Neptune publish path) see a wire shape that already matches.
  • Named-graph-stamped: every triple carries a `graph` value derived
    deterministically from the vendor IRI and the contract IRI namespace.
  • SHACL stub: validate_shapes returns conforms=True with an empty report.
    Real SHACL needs the JPMC shapes file at /modules/rdf/shapes/.

When the real serialiser lands, swap the import in scudo.tools — the contract
(`{triples, conforms, report}` dicts on the way out) does not change.
"""
from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5


# Same namespace as the contract.py / catalogue IRI minter, so the fake's
# named-graph IRI is deterministic in the same way the vendor IRIs are.
_GRAPH_NAMESPACE = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/graphs")

# DCAT / DCT / RDF predicates the fake produces. Real serialiser will use
# the same vocabulary; we match it deliberately so the publish gate, hooks,
# and Neptune publish stubs see no surprises when the real one lands.
_RDF_TYPE = "rdf:type"
_DCAT_DATASET = "dcat:Dataset"
_DCT_TITLE = "dct:title"
_DCT_DESCRIPTION = "dct:description"
_DCT_IDENTIFIER = "dct:identifier"
_DCAT_THEME = "dcat:theme"
_DCT_TYPE = "dct:type"
_DCT_PUBLISHER = "dct:publisher"
_DCAT_KEYWORD = "dcat:keyword"
_DCAT_VERSION = "dcat:version"
_PROV_ATTRIBUTED_TO = "prov:wasAttributedTo"


def named_graph_for(vendor_product_iri: str, ontology_snapshot: str = "") -> str:
    """Deterministic named-graph IRI for the published triples.

    Mirrors the shape used by the publish gate's IRI determinism regex
    (`jpmorgan:data:cdao:…`). Re-running the same mapping produces the
    same graph; that's what makes the publish call idempotent.
    """
    suffix = uuid5(_GRAPH_NAMESPACE, f"{vendor_product_iri}:{ontology_snapshot}")
    return f"jpmorgan:data:cdao:graphs:enrichment:{suffix}"


def serialise_mapping(mapping_result: dict) -> dict:
    """MappingResult dict → DCAT triple set with a stamped named graph.

    The fake emits a minimal-but-real triple set:
      mds.<vendor>:<uuid>  rdf:type           dcat:Dataset
      mds.<vendor>:<uuid>  dct:title          "<vendor product title>"
      mds.<vendor>:<uuid>  dct:identifier     "<vendor_product_ref>"
      mds.<vendor>:<uuid>  dcat:theme         <target CDAO node IRI>
      mds.<vendor>:<uuid>  dcat:version       "<ontology snapshot>"   (if known)
      mds.<vendor>:<uuid>  prov:wasAttributedTo  <vendor>

    Returns {triples: [...], conforms: bool, report: ...} so the call signature
    matches the rdf-serialisation skill's contract.
    """
    subject = mapping_result["vendor_product_iri"]
    target = mapping_result.get("proposed_target_iri", "")
    # Allow the orchestrator to thread a snapshot through; fall back to "".
    snapshot = mapping_result.get("ontology_snapshot", "") or ""
    graph = named_graph_for(subject, snapshot)

    triples: list[dict[str, str]] = []

    def add(predicate: str, obj: str) -> None:
        triples.append({
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "graph": graph,
        })

    add(_RDF_TYPE, _DCAT_DATASET)

    # Title / identifier come from the request the mapping addressed. We don't
    # have the vendor product blob in the MappingResult — only its IRI — so
    # use the rationale as a short title for the demo, and the IRI's vendor
    # tag as identifier. Real serialiser pulls richer fields from the bundle.
    rationale = (mapping_result.get("rationale") or "").strip()
    if rationale:
        add(_DCT_DESCRIPTION, rationale[:200])

    if target:
        add(_DCAT_THEME, target)

    if snapshot:
        add(_DCAT_VERSION, snapshot)

    # prov:wasAttributedTo — pull vendor name out of the `mds.<vendor>:…` form.
    if subject.startswith("mds.") and ":" in subject:
        vendor = subject[len("mds."):].split(":", 1)[0]
        add(_PROV_ATTRIBUTED_TO, f"jpmorgan:data:vendors:{vendor}")

    # Identifiers from evidence — when the model cited deterministic IRIs,
    # surface them as evidence-of-evidence (a debug aid for human reviewers).
    for ev in mapping_result.get("evidence", []) or []:
        for src in ev.get("source_iris", []) or []:
            if src.startswith("jpmorgan:data:cdao:") or src.startswith("mds."):
                add("rdfs:seeAlso", src)

    report = {
        "engine": "scudo.rdf.fake.serialise_mapping",
        "graph": graph,
        "triple_count": len(triples),
        "predicates_used": sorted({t["predicate"] for t in triples}),
    }
    return {"triples": triples, "conforms": True, "report": report}


def serialise_rights(rights_result: dict) -> dict:
    """Rights result dict → adapted-ODRL triples. Fake stub for now —
    returns an empty triple set conformant for the smoke. Real serialiser
    lives in modules.rdf.serialiser.serialise_rights."""
    return {
        "triples": [],
        "conforms": True,
        "report": {"engine": "scudo.rdf.fake.serialise_rights", "stub": True},
    }


def validate_shapes(triples: list[dict]) -> dict:
    """SHACL stub. Always conforms when given a non-empty triple list with
    valid {subject, predicate, object, graph} structure; otherwise flags
    a violation. Real SHACL lives in modules.rdf.shapes."""
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


__all__ = ["serialise_mapping", "serialise_rights", "validate_shapes", "named_graph_for"]
