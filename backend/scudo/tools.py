"""Deterministic tools the specialists call. None of these ever reason — the
specialist asks them, they answer. The @tool signatures + docstrings are what
Strands turns into tool schemas.

The "two graphs" distinction matters here (per the neptune-io and
graphrag-retrieval SKILL.md files):

  graphrag_retrieve(query, top_k)            ← LEXICAL SIDECAR (non-authoritative)
  neptune_node_by_iri(iri)                   ← AUTHORITATIVE RDF graph (confirm)
  neptune_existing_mapping(...)              ← AUTHORITATIVE
  neptune_conflicts(...)                     ← AUTHORITATIVE
  neptune_publish_triples(...)               ← AUTHORITATIVE (orchestrator-only)

`graphrag_retrieve` takes a natural-language query, so it is exempt from the
raw-query rejection hook by virtue of its `graphrag_` prefix (the hook only
inspects neptune_*/rdf_* tools). Read budget caps both prefixes.

The rdf-serialisation skill calls three tools:
  rdf_serialise_mapping(mapping_result)      ← MappingResult  → DCAT Turtle
  rdf_serialise_rights(rights_result)        ← rights result  → adapted-ODRL
  rdf_validate_shapes(triples)               ← SHACL gate

All three are stubs pointing at the JPMC-side /modules/rdf/ until that
module lands.
"""
from __future__ import annotations

from typing import Optional

from strands import tool

from . import authoritative as _authoritative
from . import sidecar as _sidecar
from .rdf import fake as _rdf_fake


# ────────────────────────────────────────────────────────────────────────────
# Candidate discovery — reads the LEXICAL SIDECAR (non-authoritative)
# ────────────────────────────────────────────────────────────────────────────
@tool
def graphrag_retrieve(query: str, top_k: int = 10) -> list[dict]:
    """Retrieve candidate CDAO taxonomy nodes related to a vendor product.

    Reads the non-authoritative lexical sidecar and returns candidates by
    RELATEDNESS (taxonomic / structural), not similarity alone — surfacing
    nodes that pure vector search misses [clarif. 83]. Candidates only:
    confirm a chosen node's authoritative position with `neptune_node_by_iri`
    before relying on it. Never publish anything sourced from here.

    Returns a list of {iri, label, score}. Natural-language query — do NOT
    compose SPARQL or openCypher.
    """
    return _sidecar.candidate_nodes(term=query, limit=top_k)


# ────────────────────────────────────────────────────────────────────────────
# Authoritative reads — bounded, parameterised, no raw SPARQL/Cypher
# ────────────────────────────────────────────────────────────────────────────
@tool
def neptune_node_by_iri(iri: str) -> Optional[dict]:
    """Authoritative single-node lookup: one CDAO node + its definition and
    immediate edges, from the RDF graph (the system of record).

    Use to CONFIRM a candidate returned by `graphrag_retrieve` before relying
    on it. Returns {iri, label, definition, synonyms, edges} or None.
    """
    return _authoritative.node_by_iri(iri=iri)


@tool
def neptune_existing_mapping(vendor: str, vendor_product_ref: str) -> Optional[dict]:
    """Return the prior mapping for a vendor product if one exists, else None.

    Drives the deterministic NEW_MAPPING vs EXTEND_MAPPING route — but the
    routing itself happens in the orchestrator, not here.
    """
    return _authoritative.existing_mapping(vendor=vendor, vendor_product_ref=vendor_product_ref)


@tool
def neptune_conflicts(vendor_product_ref: str) -> list[dict]:
    """Return equivalent products from other vendors with differing target IRIs.

    Drives RECONCILE_CONFLICT routing. Read-only.
    """
    return _authoritative.conflicts(vendor_product_ref=vendor_product_ref)


# ────────────────────────────────────────────────────────────────────────────
# RDF serialisation + SHACL validation — deterministic transforms, never
# author Turtle by hand
# ────────────────────────────────────────────────────────────────────────────
@tool
def rdf_serialise_mapping(mapping_result: dict) -> dict:
    """Serialise a MappingResult to DCAT Turtle (NOT by hand). The serialiser
    owns prefixes, the DCAT shape, deterministic IRI minting
    (`mds.<vendor>:<uuid5>`, `jpmorgan:data:cdao:…`), and the named-graph
    wrapper. Returns {triples: [...], conforms: bool, report: ...}.

    The mapping_result MUST be the Pydantic dict — the serialiser refuses
    free-form input. Backed today by the deterministic fake in scudo.rdf.fake;
    swap this import to modules.rdf.serialiser when the JPMC one lands.
    """
    return _rdf_fake.serialise_mapping(mapping_result)


@tool
def rdf_serialise_rights(rights_result: dict) -> dict:
    """Serialise a rights result to adapted-ODRL triples (RDFS semantics,
    simplified constraints — see the rights-odrl skill + clarif. 13).

    Same contract as rdf_serialise_mapping: structured dict in, triples +
    SHACL conformance out. Never hand-write Turtle. Fake-backed today.
    """
    return _rdf_fake.serialise_rights(rights_result)


@tool
def rdf_validate_shapes(triples: list[dict]) -> dict:
    """SHACL gate. The rdf-serialisation skill calls this after either
    serialiser. Returns {conforms: bool, report: ...}.

      conforms=true  → orchestrator may publish (subject to verifier gate)
      conforms=false → return the violation report; never publish; fix the
                       upstream result and re-serialise (do NOT hand-edit Turtle).

    Fake-backed today (returns conforms=True for any non-empty, well-formed
    triple list). Real shapes live in /modules/rdf/shapes/.
    """
    return _rdf_fake.validate_shapes(triples)


# ────────────────────────────────────────────────────────────────────────────
# Orchestrator-only — declared so the publish-gate hook has a name to match,
# but NEVER added to a specialist's `tools=` list. The hook denies it anyway.
# ────────────────────────────────────────────────────────────────────────────
@tool
def neptune_publish_triples(named_graph: str, triples: list[dict]) -> dict:
    """Publish validated triples to a named graph in Neptune. Idempotent.

    NOT exposed to specialists. The orchestrator calls this directly after the
    deterministic publish gate passes. The PublishGateHook denies any attempt
    to reach this from inside an agent loop.
    """
    raise NotImplementedError("→ modules.neptune.publish.publish_triples (orchestrator only)")


# ────────────────────────────────────────────────────────────────────────────
# Tool groupings — what the agents factory consumes
# ────────────────────────────────────────────────────────────────────────────
# Mapping specialist: discovery on the sidecar, confirmation on the
# authoritative graph, then serialise + validate.
MAPPING_SPECIALIST_TOOLS = [
    graphrag_retrieve,
    neptune_node_by_iri,
    neptune_existing_mapping,
    neptune_conflicts,
    rdf_serialise_mapping,
    rdf_validate_shapes,
]

# Rights specialist: serialise rights + validate. No graph reads — its inputs
# come from the extracted-rights pipeline upstream.
RIGHTS_SPECIALIST_TOOLS = [
    rdf_serialise_rights,
    rdf_validate_shapes,
]

# Orchestrator-only (never appears in a specialist's `tools=`).
ORCHESTRATOR_ONLY_TOOLS = [neptune_publish_triples]


__all__ = [
    "graphrag_retrieve",
    "neptune_node_by_iri", "neptune_existing_mapping", "neptune_conflicts",
    "rdf_serialise_mapping", "rdf_serialise_rights", "rdf_validate_shapes",
    "neptune_publish_triples",
    "MAPPING_SPECIALIST_TOOLS", "RIGHTS_SPECIALIST_TOOLS", "ORCHESTRATOR_ONLY_TOOLS",
]
