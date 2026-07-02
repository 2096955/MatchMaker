"""
Neptune backend — the production cutover target.

Same interface as FalkorDBStore. The point of this file is to prove the seam:
the SPARQL lives here and never escapes. Named graphs carry provenance, in line
with the RDF-first canonical decision (L69) and the ODRL rights model.

Transport: a thin urllib3 + botocore SigV4Auth client that signs each SPARQL
HTTP POST against ``neptune-db``. This keeps the dependency surface to what
boto3 already ships (urllib3 is a boto3 transitive dependency), and keeps the
client cluster-agnostic — the endpoint is the only configuration.

INVARIANTS PRESERVED HERE
  I2  No raw-query tool: this module exposes typed methods only; the SPARQL
      strings are bounded templates filled by parameter substitution.
  I7  SPARQL stays inside this file. The matcher / feedback / routes / MCP
      transports never see a SPARQL string.
  I9  ODRL rights + named-graph provenance stay on Neptune. The FalkorDB store
      is non-authoritative for both.
  I5  ``find_similar_products`` returns ``Candidate.similarity`` as the raw
      oracle score; the structural tilt is a sort-key concern of the matcher.

PRAGMATIC LIMITS
  ``find_similar_products`` here is a placeholder. The production semantic
  retrieval is Neptune Analytics vector search / GraphRAG Toolkit (M9
  specialist's path). The seam contract is honoured by returning every
  taxonomy node with similarity=0.0 — clearly stubbed but non-empty so the
  matcher's structural pass still has nodes to score, filter and tilt.
"""

from __future__ import annotations

import json
from typing import Optional

from ..models import (
    Candidate,
    ConceptualEdge,
    ConceptualGraph,
    ConceptualNode,
    MappingResult,
    MappingStatus,
    Subgraph,
    TaxonomyNode,
    VendorProductRef,
)
from .base import CandidateFilter, RetrievalStore

# IRIs are real URIs in RDF; bounded SPARQL templates, never raw passthrough.
_PREFIXES = """
PREFIX mds:  <https://jpmc.example/mds#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX prov: <http://www.w3.org/ns/prov#>
"""

# get_taxonomy_node — one hop, parent + children.
SPARQL_TAXONOMY_NODE = (
    _PREFIXES
    + """
SELECT ?label ?parent (GROUP_CONCAT(?child; SEPARATOR=",") AS ?children) WHERE {
  <%(iri)s> skos:prefLabel ?label .
  OPTIONAL { <%(iri)s> skos:broader ?parent }
  OPTIONAL { ?child skos:broader <%(iri)s> }
} GROUP BY ?label ?parent
"""
)

# get_ontology_neighbourhood — bounded property path (depth clamped at the edge).
SPARQL_NEIGHBOURHOOD = (
    _PREFIXES
    + """
SELECT ?node ?label ?parent WHERE {
  <%(iri)s> skos:narrower{1,%(depth)d} ?node .
  ?node skos:prefLabel ?label .
  OPTIONAL { ?node skos:broader ?parent }
} LIMIT %(cap)d
"""
)

# upsert_taxonomy_node — idempotent: drop the prior triples for this IRI, then
# re-insert. Named graph carries provenance.
SPARQL_UPSERT_TAXONOMY_NODE = (
    _PREFIXES
    + """
WITH <%(graph)s>
DELETE { <%(iri)s> skos:prefLabel ?l . <%(iri)s> skos:broader ?p }
WHERE  { OPTIONAL { <%(iri)s> skos:prefLabel ?l }
         OPTIONAL { <%(iri)s> skos:broader   ?p } } ;
INSERT DATA { GRAPH <%(graph)s> {
  <%(iri)s> skos:prefLabel %(label)s .
  %(parent_triple)s
} }
"""
)

# upsert_vendor_product — same idempotent shape.
SPARQL_UPSERT_VENDOR_PRODUCT = (
    _PREFIXES
    + """
WITH <%(graph)s>
DELETE { <%(iri)s> mds:vendor ?v . <%(iri)s> mds:productId ?p .
         <%(iri)s> mds:name ?n . <%(iri)s> mds:description ?d .
         <%(iri)s> mds:signature ?s }
WHERE  { OPTIONAL { <%(iri)s> mds:vendor      ?v }
         OPTIONAL { <%(iri)s> mds:productId   ?p }
         OPTIONAL { <%(iri)s> mds:name        ?n }
         OPTIONAL { <%(iri)s> mds:description ?d }
         OPTIONAL { <%(iri)s> mds:signature   ?s } } ;
INSERT DATA { GRAPH <%(graph)s> {
  <%(iri)s> a mds:VendorProduct ;
            mds:vendor      %(vendor)s ;
            mds:productId   %(pid)s ;
            mds:name        %(name)s ;
            mds:description %(desc)s ;
            mds:signature   %(sig)s .
} }
"""
)

# get_precedent_mapping — CONFIRMED edge only (exclude provisional, I5).
SPARQL_GET_PRECEDENT = (
    _PREFIXES
    + """
SELECT ?vIri ?vName ?tIri ?tLabel ?conf ?decision ?at ?srcHash ?srcAudit WHERE {
  ?vIri a mds:VendorProduct ;
        mds:vendor    %(vendor)s ;
        mds:productId %(pid)s .
  OPTIONAL { ?vIri mds:name ?vName }
  ?vIri mds:mappedTo ?edge .
  ?edge mds:target      ?tIri ;
        mds:decision    ?decision ;
        mds:confidence  ?conf ;
        mds:decidedAt   ?at .
  OPTIONAL { ?tIri skos:prefLabel ?tLabel }
  OPTIONAL { ?edge mds:provisional ?prov }
  OPTIONAL { ?edge mds:sourceContentHash ?srcHash }
  OPTIONAL { ?edge mds:sourceFileAuditId ?srcAudit }
  FILTER ( !BOUND(?prov) || ?prov = false )
} ORDER BY DESC(?at) LIMIT 1
"""
)

# find_similar_products — placeholder per the brief: every taxonomy node with
# similarity 0.0. Production wires Neptune Analytics / Bedrock vector search.
SPARQL_LIST_TAXONOMY_NODES = (
    _PREFIXES
    + """
SELECT ?iri ?label ?parent WHERE {
  ?iri a skos:Concept .
  OPTIONAL { ?iri skos:prefLabel ?label }
  OPTIONAL { ?iri skos:broader   ?parent }
} ORDER BY ?iri LIMIT %(cap)d
"""
)

# upsert_precedent (approve / override) — single-positive-precedent invariant
# enforced by deleting ALL prior mds:mappedTo edges from this VendorProduct,
# then inserting the new one. Negative edge on the chosen target is cleared.
SPARQL_UPSERT_PRECEDENT_POSITIVE = (
    _PREFIXES
    + """
WITH <%(graph)s>
DELETE {
  <%(v_iri)s> mds:mappedTo ?old_edge .
  ?old_edge ?op ?ov .
  <%(v_iri)s> mds:notMappedTo ?neg_edge .
  ?neg_edge mds:target <%(t_iri)s> .
  ?neg_edge ?np ?nv .
}
WHERE {
  OPTIONAL { <%(v_iri)s> mds:mappedTo ?old_edge . ?old_edge ?op ?ov }
  OPTIONAL { <%(v_iri)s> mds:notMappedTo ?neg_edge .
             ?neg_edge mds:target <%(t_iri)s> .
             ?neg_edge ?np ?nv }
} ;
INSERT DATA { GRAPH <%(graph)s> {
  <%(v_iri)s> a mds:VendorProduct ;
              mds:vendor      %(vendor)s ;
              mds:productId   %(pid)s ;
              mds:name        %(name)s ;
              mds:description %(desc)s ;
              mds:signature   %(sig)s .
  <%(t_iri)s> a skos:Concept ;
              skos:prefLabel %(label)s .
  <%(edge_iri)s> a mds:PrecedentEdge ;
              mds:target          <%(t_iri)s> ;
              mds:decision        %(decision)s ;
              mds:decidedBy       %(by)s ;
              mds:confidence      %(conf)s ;
              mds:provisional     %(prov)s ;
              mds:decidedAt       %(at)s ;
              mds:sourceContentHash %(src_hash)s ;
              mds:sourceFileAuditId %(src_audit)s .
  <%(v_iri)s> mds:mappedTo <%(edge_iri)s> .
} }
"""
)

# upsert_precedent (reject) — record a NOT_MAPPED_TO edge and wipe any prior
# positive edge to THIS exact target (so a reject of a previously-approved
# node is recorded as a reversal). Other positive precedents on different
# targets stay.
SPARQL_UPSERT_PRECEDENT_NEGATIVE = (
    _PREFIXES
    + """
WITH <%(graph)s>
DELETE {
  <%(v_iri)s> mds:mappedTo ?pos_edge .
  ?pos_edge mds:target <%(t_iri)s> .
  ?pos_edge ?pp ?pv .
}
WHERE {
  OPTIONAL { <%(v_iri)s> mds:mappedTo ?pos_edge .
             ?pos_edge mds:target <%(t_iri)s> .
             ?pos_edge ?pp ?pv }
} ;
INSERT DATA { GRAPH <%(graph)s> {
  <%(v_iri)s> a mds:VendorProduct ;
              mds:vendor      %(vendor)s ;
              mds:productId   %(pid)s ;
              mds:name        %(name)s ;
              mds:description %(desc)s ;
              mds:signature   %(sig)s .
  <%(t_iri)s> a skos:Concept ;
              skos:prefLabel %(label)s .
  <%(edge_iri)s> a mds:NegativePrecedentEdge ;
              mds:target          <%(t_iri)s> ;
              mds:decision        %(decision)s ;
              mds:decidedBy       %(by)s ;
              mds:confidence      %(conf)s ;
              mds:provisional     %(prov)s ;
              mds:decidedAt       %(at)s ;
              mds:sourceContentHash %(src_hash)s ;
              mds:sourceFileAuditId %(src_audit)s .
  <%(v_iri)s> mds:notMappedTo <%(edge_iri)s> .
} }
"""
)

# rank_signals_for — count distinct VendorProducts whose signature matches
# and that have a CONFIRMED MAPPED_TO edge into the node.
SPARQL_RANK_SIGNALS = (
    _PREFIXES
    + """
SELECT ?tIri (COUNT(DISTINCT ?vIri) AS ?count) WHERE {
  ?vIri a mds:VendorProduct ;
        mds:signature %(sig)s ;
        mds:mappedTo ?edge .
  ?edge mds:target ?tIri .
  OPTIONAL { ?edge mds:provisional ?prov }
  FILTER ( !BOUND(?prov) || ?prov = false )
} GROUP BY ?tIri
"""
)

# get_negative_precedents — node IRIs that the human rejected for this
# (vendor, product_id).
SPARQL_NEGATIVE_PRECEDENTS = (
    _PREFIXES
    + """
SELECT DISTINCT ?tIri WHERE {
  ?vIri a mds:VendorProduct ;
        mds:vendor    %(vendor)s ;
        mds:productId %(pid)s ;
        mds:notMappedTo ?edge .
  ?edge mds:target ?tIri .
}
"""
)

# list_confirmed_precedents — every CONFIRMED MAPPED_TO edge, flat.
SPARQL_LIST_CONFIRMED = (
    _PREFIXES
    + """
SELECT ?vendor ?pid ?name ?desc ?tIri ?tLabel
       ?decision ?by ?at ?conf ?srcHash ?srcAudit WHERE {
  ?vIri a mds:VendorProduct ;
        mds:vendor    ?vendor ;
        mds:productId ?pid ;
        mds:mappedTo  ?edge .
  ?edge mds:target     ?tIri ;
        mds:decision   ?decision ;
        mds:decidedBy  ?by ;
        mds:decidedAt  ?at ;
        mds:confidence ?conf .
  OPTIONAL { ?vIri mds:name        ?name }
  OPTIONAL { ?vIri mds:description ?desc }
  OPTIONAL { ?tIri skos:prefLabel  ?tLabel }
  OPTIONAL { ?edge mds:provisional ?prov }
  OPTIONAL { ?edge mds:sourceContentHash ?srcHash }
  OPTIONAL { ?edge mds:sourceFileAuditId ?srcAudit }
  FILTER ( !BOUND(?prov) || ?prov = false )
} ORDER BY ?vendor ?pid
"""
)

# list_taxonomy_nodes — every skos:Concept the store knows about.
SPARQL_LIST_ALL_TAXONOMY = (
    _PREFIXES
    + """
SELECT ?iri ?label ?parent WHERE {
  ?iri a skos:Concept .
  OPTIONAL { ?iri skos:prefLabel ?label }
  OPTIONAL { ?iri skos:broader   ?parent }
} ORDER BY ?iri
"""
)


# ────────────────────────────────────────────────────────────────────
# SigV4-signed SPARQL transport
# ────────────────────────────────────────────────────────────────────


def _iri(value: str) -> str:
    """Escape an IRI for inline substitution inside <...>.

    Defence against template injection: only the characters that would close
    the angle brackets or break the SPARQL grammar are stripped. Real Neptune
    SPARQL takes percent-encoded IRIs cleanly.
    """
    return (value or "").replace("\\", "\\\\").replace(">", "%3E").replace("<", "%3C")


def _lit(value) -> str:
    """Return a SPARQL string literal for ``value``.

    JSON-encoding gives us the right escapes for backslash, quotes and
    control characters in one step, which is exactly what SPARQL needs
    around the body of a string literal. ``None`` becomes an empty literal
    so the templates stay total — the read side filters on bound/unbound,
    not on empty.
    """
    if value is None:
        return '""'
    if isinstance(value, bool):
        return '"true"^^xsd:boolean' if value else '"false"^^xsd:boolean'
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return f'"{int(value)}"^^xsd:integer'
    if isinstance(value, float):
        return f'"{float(value)}"^^xsd:double'
    return json.dumps(str(value), ensure_ascii=False)


def _opt_iri_triple(subject: str, predicate: str, obj_iri: Optional[str]) -> str:
    """Render an optional IRI triple inline; empty string when obj_iri is None.

    Lets the upsert templates stay total without UNION / OPTIONAL gymnastics.
    """
    if not obj_iri:
        return ""
    return f"<{_iri(subject)}> {predicate} <{_iri(obj_iri)}> ."


class _NeptuneSparqlClient:
    """Thin SigV4-signed urllib3 client for Neptune's SPARQL HTTP endpoint.

    Two contract methods:
      * ``query_select(sparql)``  → list[dict] of SPARQL JSON result bindings
      * ``query_update(sparql)``  → None (raises on non-2xx)

    The request shape matches Neptune's expectations: form-encoded body with
    a single ``query`` (SELECT) or ``update`` (INSERT/DELETE) parameter,
    POSTed to ``/sparql`` on port 8182. SigV4 is computed against the
    ``neptune-db`` service.
    """

    SERVICE = "neptune-db"
    PORT = 8182
    TIMEOUT_SECONDS = 30

    def __init__(
        self,
        endpoint: str,
        *,
        region: Optional[str] = None,
        verify_tls: bool = True,
    ) -> None:
        # Lazy imports — keep the package importable in environments without
        # boto3 wired (the FakeStore-backed smoke does not exercise this).
        import boto3
        import urllib3
        from botocore.auth import SigV4Auth

        host = (endpoint or "").strip()
        if not host:
            raise RuntimeError(
                "NeptuneStore requires NEPTUNE_ENDPOINT. Set it in the Atlas "
                "environment; locally use STORE_BACKEND=falkordb."
            )
        # Strip an accidental scheme; Neptune is always https.
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix) :]
        # Strip an accidental port suffix; we always go to 8182.
        host = host.split(":", 1)[0].rstrip("/")
        self._host = host
        self._url = f"https://{host}:{self.PORT}/sparql"

        # Default credential chain (env vars / instance profile / role).
        session = boto3.Session(region_name=region) if region else boto3.Session()
        self._creds = session.get_credentials()
        if self._creds is None:
            raise RuntimeError(
                "NeptuneStore could not resolve AWS credentials via the "
                "default chain. In the Atlas environment this comes from the "
                "task role; locally use STORE_BACKEND=falkordb."
            )
        self._region = region or session.region_name or "eu-west-2"
        self._signer = SigV4Auth(self._creds, self.SERVICE, self._region)

        cert_reqs = "CERT_REQUIRED" if verify_tls else "CERT_NONE"
        self._http = urllib3.PoolManager(
            cert_reqs=cert_reqs,
            timeout=urllib3.Timeout(
                connect=self.TIMEOUT_SECONDS, read=self.TIMEOUT_SECONDS
            ),
        )

    # ------------------------------------------------------------------
    def _signed_post(self, body: bytes, *, content_type: str) -> bytes:
        """Issue a SigV4-signed POST to ``self._url``. Returns the response
        body bytes on 2xx, raises RuntimeError on anything else."""
        from botocore.awsrequest import AWSRequest

        aws_req = AWSRequest(
            method="POST",
            url=self._url,
            data=body,
            headers={
                "Content-Type": content_type,
                "Host": f"{self._host}:{self.PORT}",
            },
        )
        self._signer.add_auth(aws_req)
        # Materialise the signed headers for urllib3.
        headers = dict(aws_req.headers.items())

        resp = self._http.request(
            "POST",
            self._url,
            body=body,
            headers=headers,
        )
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(
                f"Neptune SPARQL POST failed: HTTP {resp.status} {resp.data[:512]!r}"
            )
        return resp.data

    # ------------------------------------------------------------------
    def query_select(self, sparql: str) -> list[dict]:
        """Run a SPARQL SELECT and return the binding rows (``[{var: cell}]``).

        Each cell is the raw SPARQL JSON ``{"type", "value", ...}`` dict; the
        caller unpacks ``["value"]`` per binding it cares about.
        """
        body = ("query=" + _urlencode(sparql)).encode("utf-8")
        data = self._signed_post(
            body,
            content_type="application/x-www-form-urlencoded",
        )
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Neptune SELECT returned non-JSON body: {data[:512]!r}"
            ) from e
        return list(payload.get("results", {}).get("bindings", []))

    def query_update(self, sparql: str) -> None:
        """Run a SPARQL INSERT/DELETE. No return value; raises on non-2xx."""
        body = ("update=" + _urlencode(sparql)).encode("utf-8")
        self._signed_post(
            body,
            content_type="application/x-www-form-urlencoded",
        )


def _urlencode(value: str) -> str:
    """URL-encode the SPARQL body for ``application/x-www-form-urlencoded``."""
    import urllib.parse

    return urllib.parse.quote(value, safe="")


def _cell(row: dict, var: str) -> Optional[str]:
    """Pull the string value of a binding, or None if the variable is absent."""
    binding = row.get(var)
    if not binding:
        return None
    val = binding.get("value")
    return val if val not in (None, "") else None


def _cell_or(row: dict, var: str, default: str = "") -> str:
    val = _cell(row, var)
    return val if val is not None else default


# ────────────────────────────────────────────────────────────────────
# Store
# ────────────────────────────────────────────────────────────────────


class NeptuneStore(RetrievalStore):
    def __init__(self, endpoint: str, graph_name: str):
        self._endpoint = endpoint
        self._graph = graph_name
        self._client: Optional[_NeptuneSparqlClient] = None
        # Named graph URI — provenance lives here. Stable, derived from the
        # logical graph_name so a downstream auditor can trace every triple
        # back to the configuration that wrote it.
        self._graph_iri = f"https://jpmc.example/mds/graphs/{graph_name}"

    def _require_client(self) -> _NeptuneSparqlClient:
        """Lazily build and cache a SigV4-signed SPARQL client.

        The build itself may raise (no credentials, no endpoint); the caller
        sees the same loud failure they used to see from the stub. Subsequent
        calls reuse the cached instance.
        """
        if self._client is None:
            self._client = _NeptuneSparqlClient(self._endpoint)
        return self._client

    def health(self) -> bool:
        try:
            client = self._require_client()
            client.query_select("SELECT (1 AS ?ok) WHERE {}")
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        # urllib3 PoolManager cleans up on GC; nothing to do explicitly.
        self._client = None

    # ----------------------------------------------------------------
    # Write side
    # ----------------------------------------------------------------
    def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
        client = self._require_client()
        parent_triple = _opt_iri_triple(node.iri, "skos:broader", node.parent_iri)
        sparql = SPARQL_UPSERT_TAXONOMY_NODE % {
            "graph": _iri(self._graph_iri),
            "iri": _iri(node.iri),
            "label": _lit(node.label or ""),
            "parent_triple": parent_triple,
        }
        client.query_update(sparql)

    def upsert_vendor_product(self, ref: VendorProductRef) -> None:
        client = self._require_client()
        sig = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        sparql = SPARQL_UPSERT_VENDOR_PRODUCT % {
            "graph": _iri(self._graph_iri),
            "iri": _iri(ref.iri),
            "vendor": _lit(ref.vendor),
            "pid": _lit(ref.product_id),
            "name": _lit(ref.name or ""),
            "desc": _lit(ref.description or ""),
            "sig": _lit(sig),
        }
        client.query_update(sparql)

    # ----------------------------------------------------------------
    # Read side
    # ----------------------------------------------------------------
    def get_taxonomy_node(self, node_iri: str) -> Optional[TaxonomyNode]:
        client = self._require_client()
        rows = client.query_select(
            SPARQL_TAXONOMY_NODE % {"iri": _iri(node_iri)},
        )
        if not rows:
            return None
        row = rows[0]
        label = _cell_or(row, "label", "")
        if not label:
            return None
        children_cell = _cell(row, "children") or ""
        children = [c for c in children_cell.split(",") if c]
        return TaxonomyNode(
            iri=node_iri,
            label=label,
            parent_iri=_cell(row, "parent"),
            children_iris=children,
        )

    def find_similar_products(
        self,
        ref: VendorProductRef,
        max_results: int = 10,
        min_similarity: float = 0.0,
        *,
        candidate_filter: Optional[CandidateFilter] = None,
    ) -> list[Candidate]:
        """Placeholder retrieval. Production semantic search lives in M9.

        Returns every taxonomy node with similarity=0.0 so the seam contract
        (non-empty when nodes exist; clamped count; negative-precedent drop;
        ``candidate_filter`` applied) is honoured.

        ``Candidate.similarity`` stays the raw oracle score — here, 0.0 — so
        the matcher's 0.80 floor sees the calibrated quantity (I5). The
        production swap will return real dense scores from Neptune Analytics
        / Bedrock vector search; the contract above this line does not change.
        """
        client = self._require_client()
        limit = self.clamp_results(max_results)
        rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))
        rows = client.query_select(
            SPARQL_LIST_TAXONOMY_NODES % {"cap": self.clamp_nodes(100)},
        )
        candidates: list[Candidate] = []
        for row in rows:
            iri = _cell(row, "iri")
            if not iri or iri in rejected:
                continue
            label = _cell_or(row, "label", "")
            parent = _cell(row, "parent")
            similarity = 0.0
            if similarity < min_similarity:
                continue
            cand = Candidate(
                node=TaxonomyNode(iri=iri, label=label, parent_iri=parent),
                similarity=similarity,
            )
            if candidate_filter is not None and not candidate_filter(cand):
                continue
            candidates.append(cand)
            if len(candidates) >= limit:
                break
        return candidates

    def get_ontology_neighbourhood(
        self,
        node_iri: str,
        max_depth: int = 2,
        max_nodes: int = 50,
    ) -> Subgraph:
        client = self._require_client()
        rows = client.query_select(
            SPARQL_NEIGHBOURHOOD
            % {
                "iri": _iri(node_iri),
                "depth": self.clamp_depth(max_depth),
                "cap": self.clamp_nodes(max_nodes),
            },
        )
        seen: dict[str, TaxonomyNode] = {}
        edges: set[tuple[str, str]] = set()
        for row in rows:
            iri = _cell(row, "node")
            if not iri:
                continue
            label = _cell_or(row, "label", "")
            parent = _cell(row, "parent")
            seen.setdefault(
                iri,
                TaxonomyNode(
                    iri=iri,
                    label=label,
                    parent_iri=parent,
                ),
            )
            if parent:
                edges.add((parent, iri))
        return Subgraph(
            root_iri=node_iri,
            nodes=list(seen.values()),
            edges=list(edges),
        )

    def get_precedent_mapping(
        self,
        vendor: str,
        product_id: str,
    ) -> Optional[MappingResult]:
        client = self._require_client()
        rows = client.query_select(
            SPARQL_GET_PRECEDENT
            % {
                "vendor": _lit(vendor),
                "pid": _lit(product_id),
            },
        )
        if not rows:
            return None
        row = rows[0]
        v_iri = _cell(row, "vIri") or ""
        t_iri = _cell(row, "tIri")
        if not t_iri:
            return None
        decision = (_cell_or(row, "decision", "") or "").strip().lower()
        conf_cell = _cell(row, "conf")
        try:
            conf = float(conf_cell) if conf_cell is not None else 1.0
        except ValueError:
            conf = 1.0
        status = (
            MappingStatus.OVERRIDDEN
            if decision == "override"
            else MappingStatus.APPROVED
        )
        return MappingResult(
            vendor_product_iri=v_iri,
            vendor=vendor,
            product_id=product_id,
            product_name=_cell_or(row, "vName", ""),
            mapped_node_iri=t_iri,
            mapped_node_label=_cell_or(row, "tLabel", ""),
            confidence=max(0.0, min(1.0, conf)),
            status=status,
            rationale="precedent",
            source_content_hash=_cell(row, "srcHash"),
            source_file_audit_id=_cell(row, "srcAudit"),
        )

    # ----------------------------------------------------------------
    # HITL write side
    # ----------------------------------------------------------------
    def upsert_precedent(
        self,
        *,
        ref: VendorProductRef,
        node: TaxonomyNode,
        decision: str,
        decided_by: str,
        confidence: float,
        provisional: bool = False,
        decided_at_ms: Optional[int] = None,
    ) -> None:
        client = self._require_client()
        d = (decision or "").strip().lower()
        if d not in {"approve", "override", "reject"}:
            raise ValueError(
                f"decision must be 'approve' | 'override' | 'reject', got {decision!r}"
            )
        signature = self.vendor_signature(ref.vendor, ref.name, ref.product_id)
        at_ms = int(decided_at_ms) if decided_at_ms is not None else self._now_ms()
        # Edge IRI: deterministic per (v, t, decision_kind) so re-applying the
        # same decision MERGEs onto the same edge node, and a prior delete
        # actually clears the right one.
        edge_iri = (
            f"{self._graph_iri}/edges/"
            f"{'pos' if d != 'reject' else 'neg'}/"
            f"{_safe_segment(ref.iri)}/{_safe_segment(node.iri)}"
        )
        params = {
            "graph": _iri(self._graph_iri),
            "v_iri": _iri(ref.iri),
            "t_iri": _iri(node.iri),
            "edge_iri": _iri(edge_iri),
            "vendor": _lit(ref.vendor),
            "pid": _lit(ref.product_id),
            "name": _lit(ref.name or ""),
            "desc": _lit(ref.description or ""),
            "sig": _lit(signature),
            "label": _lit(node.label or ""),
            "decision": _lit(d),
            "by": _lit(decided_by),
            "conf": _lit(float(confidence)),
            "prov": _lit(bool(provisional)),
            "at": _lit(int(at_ms)),
            "src_hash": _lit(ref.source_content_hash or ""),
            "src_audit": _lit(ref.source_file_audit_id or ""),
        }
        template = (
            SPARQL_UPSERT_PRECEDENT_NEGATIVE
            if d == "reject"
            else SPARQL_UPSERT_PRECEDENT_POSITIVE
        )
        client.query_update(template % params)

    def rank_signals_for(self, vendor_signature: str) -> dict[str, int]:
        client = self._require_client()
        rows = client.query_select(
            SPARQL_RANK_SIGNALS % {"sig": _lit(vendor_signature)},
        )
        out: dict[str, int] = {}
        for row in rows:
            iri = _cell(row, "tIri")
            count_cell = _cell(row, "count")
            if not iri or count_cell is None:
                continue
            try:
                out[iri] = int(count_cell)
            except ValueError:
                continue
        return out

    def get_negative_precedents(
        self,
        vendor: str,
        product_id: str,
    ) -> list[str]:
        client = self._require_client()
        rows = client.query_select(
            SPARQL_NEGATIVE_PRECEDENTS
            % {
                "vendor": _lit(vendor),
                "pid": _lit(product_id),
            },
        )
        return [iri for iri in (_cell(r, "tIri") for r in rows) if iri]

    # ----------------------------------------------------------------
    # M6 — bundle export surface
    # ----------------------------------------------------------------
    def list_confirmed_precedents(self) -> list[dict]:
        client = self._require_client()
        rows = client.query_select(SPARQL_LIST_CONFIRMED)
        out: list[dict] = []
        for row in rows:
            at_cell = _cell(row, "at")
            try:
                at_ms = int(at_cell) if at_cell is not None else 0
            except ValueError:
                at_ms = 0
            conf_cell = _cell(row, "conf")
            try:
                conf = float(conf_cell) if conf_cell is not None else 1.0
            except ValueError:
                conf = 1.0
            out.append(
                {
                    "vendor": _cell_or(row, "vendor", ""),
                    "product_id": _cell_or(row, "pid", ""),
                    "product_name": _cell_or(row, "name", ""),
                    "description": _cell_or(row, "desc", ""),
                    "mapped_node_iri": _cell_or(row, "tIri", ""),
                    "mapped_node_label": _cell_or(row, "tLabel", ""),
                    "decision": (_cell_or(row, "decision", "") or "").strip().lower(),
                    "decided_by": _cell_or(row, "by", ""),
                    "decided_at_ms": at_ms,
                    "confidence": conf,
                    "source_content_hash": _cell(row, "srcHash") or None,
                    "source_file_audit_id": _cell(row, "srcAudit") or None,
                }
            )
        return out

    def list_taxonomy_nodes(self) -> list[TaxonomyNode]:
        client = self._require_client()
        rows = client.query_select(SPARQL_LIST_ALL_TAXONOMY)
        out: list[TaxonomyNode] = []
        for row in rows:
            iri = _cell(row, "iri")
            if not iri:
                continue
            out.append(
                TaxonomyNode(
                    iri=iri,
                    label=_cell_or(row, "label", ""),
                    parent_iri=_cell(row, "parent"),
                )
            )
        return out

    # ----------------------------------------------------------------
    # M10 — conceptual enrichment layer (explicit stub, same precedent as
    # find_similar_products above: no RDF mapping has been designed for the
    # cat:/dcat: enrichment terms yet, so writes are documented no-ops and
    # reads return an empty ConceptualGraph rather than silently pretending
    # to persist. Do not treat this as real on Neptune.
    # ----------------------------------------------------------------
    def upsert_conceptual_node(self, node: ConceptualNode) -> None:
        pass

    def upsert_conceptual_edge(self, edge: ConceptualEdge) -> None:
        pass

    def get_conceptual_graph(
        self, concept_iri: str, max_depth: int = 2, max_nodes: int = 50
    ) -> ConceptualGraph:
        _ = self.clamp_depth(max_depth), self.clamp_nodes(max_nodes)
        return ConceptualGraph(root_concept_iri=concept_iri)

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------
    @staticmethod
    def _now_ms() -> int:
        import time

        return int(time.time() * 1000)


def _safe_segment(value: str) -> str:
    """Slug an IRI into a URL-safe path segment for deterministic edge IRIs."""
    import urllib.parse

    return urllib.parse.quote(value or "", safe="")
