"""
Neptune backend — the production cutover target.

Same interface as FalkorDBStore. The point of this file is to prove the seam:
the SPARQL lives here and never escapes. Named graphs carry provenance, in line
with the RDF-first canonical decision (L69) and the ODRL rights model.

The query bodies are illustrative and the transport is left as the integration
point — in JPMC's environment Neptune is reached over a SigV4-signed HTTPS
SPARQL endpoint from inside the VPC/VDI, not from here. Methods that depend on
that live connection raise NotImplementedError with a clear message rather than
pretending to work, so a smoke test fails loudly instead of silently.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candidate, MappingResult, Subgraph, TaxonomyNode, VendorProductRef
from .base import RetrievalStore

# IRIs are real URIs in RDF; bounded SPARQL templates, never raw passthrough.
_PREFIXES = """
PREFIX mds:  <https://jpmc.example/mds#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
"""

# get_taxonomy_node — one hop, parent + children.
SPARQL_TAXONOMY_NODE = _PREFIXES + """
SELECT ?label ?parent (GROUP_CONCAT(?child; SEPARATOR=",") AS ?children) WHERE {
  <%(iri)s> skos:prefLabel ?label .
  OPTIONAL { <%(iri)s> skos:broader ?parent }
  OPTIONAL { ?child skos:broader <%(iri)s> }
} GROUP BY ?label ?parent
"""

# get_ontology_neighbourhood — bounded property path (depth clamped at the edge).
SPARQL_NEIGHBOURHOOD = _PREFIXES + """
SELECT ?node ?label ?parent WHERE {
  <%(iri)s> skos:narrower{1,%(depth)d} ?node .
  ?node skos:prefLabel ?label .
  OPTIONAL { ?node skos:broader ?parent }
} LIMIT %(cap)d
"""


class NeptuneStore(RetrievalStore):
    def __init__(self, endpoint: str, graph_name: str):
        self._endpoint = endpoint
        self._graph = graph_name
        # In prod: build a SigV4-signed SPARQL client bound to the Neptune endpoint.
        self._client = None  # integration point

    def _require_client(self):
        if self._client is None:
            raise NotImplementedError(
                "NeptuneStore needs a SigV4-signed SPARQL client bound to "
                f"{self._endpoint or '<NEPTUNE_ENDPOINT unset>'}. Wire it in the "
                "Atlas environment; locally use STORE_BACKEND=falkordb."
            )
        return self._client

    def health(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        pass

    # Write side: in prod, approved mappings become RDF triples with provenance
    # in a named graph; out of scope for the local prototype.
    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
        self._require_client()

    def upsert_vendor_product(self, ref: VendorProductRef) -> None:
        self._require_client()

    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        self._require_client()
        # client.select(SPARQL_TAXONOMY_NODE % {"iri": node_iri}) -> map rows
        return None

    def find_similar_products(
        self, ref: VendorProductRef, max_results: int = 10, min_similarity: float = 0.0
    ) -> list[Candidate]:
        # Production candidate retrieval = Neptune Analytics vector search /
        # GraphRAG semantic search, then the bounded SPARQL above for structure.
        self._require_client()
        return []

    def get_ontology_neighbourhood(
        self, node_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> Subgraph:
        self._require_client()
        _ = SPARQL_NEIGHBOURHOOD % {
            "iri": node_iri,
            "depth": self.clamp_depth(max_depth),
            "cap": self.clamp_nodes(max_nodes),
        }
        return Subgraph(root_iri=node_iri)

    def get_precedent_mapping(self, vendor: str, product_id: str) -> Optional[MappingResult]:
        self._require_client()
        return None

    # --- HITL write side (M4 new) ----------------------------------------
    # The production target is RDF triples in a named graph carrying the
    # decision + decided_by + decided_at provenance, with ODRL rights stored
    # separately. Wire the SigV4-signed SPARQL UPDATE here in the Atlas
    # environment; until then these fail loudly via _require_client so a
    # smoke test doesn't pass silently against an unwired Neptune.
    def upsert_precedent(
        self,
        *,
        ref: VendorProductRef,
        node: TaxonomyNode,
        decision: str,
        decided_by: str,
        confidence: float,
        provisional: bool = False,
        decided_at_ms=None,
    ) -> None:
        self._require_client()

    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        self._require_client()
        return {}

    def get_negative_precedents(self, vendor: str, product_id: str) -> list[str]:
        self._require_client()
        return []

    # --- M6 — bundle export surface --------------------------------------
    def list_confirmed_precedents(self) -> list[dict]:
        self._require_client()
        return []

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        self._require_client()
        return []
