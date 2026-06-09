"""
SCUDO enrichment box — Strands wiring scaffold (v0.2, + GraphRAG retrieval)
===========================================================================
Adds graph-aware candidate retrieval (AWS GraphRAG Toolkit) to the mapping
worker. Change vs v0.1 is read-side only — the orchestration loop, the publish
gate, and the invariants are unchanged.

WHAT CHANGED IN v0.2
--------------------
- `graphrag_retrieve` is now a tool in the worker's loop. It returns CDAO
  candidates by *relatedness* (taxonomic/structural), not similarity alone —
  the no-exact-match cases pure vector search misses [clarif. 83]. It replaces
  the naive `neptune_candidate_nodes` term-match.
- It reads a NON-AUTHORITATIVE lexical sidecar (its own graph + vector store),
  built OFFLINE by `build_lexical_index()` from the CDAO ontology text + vendor
  catalogue. The RDF graph stays the system of record; nothing publishes from
  the sidecar; it is rebuildable. Replay-safety [G39] is untouched — retrieval
  feeds judgement, it never writes.
- `neptune_node_by_iri` added: authoritative confirmation of a candidate's real
  position/definition in the RDF graph before the specialist relies on it.

How the plan maps onto Strands (unchanged from v0.1)
----------------------------------------------------
- Skills → `AgentSkills` over ./skills/ (now 5: taxonomy-mapping, rights-odrl,
  rdf-serialisation, neptune-io, graphrag-retrieval).
- LLM-facing invariants → Strands hooks (`BeforeToolCallEvent.cancel_tool`).
- Orchestration invariants (publish gate, 0.80 floor, RESEARCH-never-publishes)
  → deterministic guard in the orchestrator.
- Agents → mapping + rights specialists (judgement) and the verifier.

VERIFY against your installed versions:
  - Strands: `Agent(..., plugins=, hooks=)`, `AgentSkills`, hook events, MCP
    transport import, `structured_output(...)`.
  - GraphRAG Toolkit: import path and the retriever accessor. The toolkit is
    installed from GitHub (not PyPI) and reorganises between releases — the
    `from graphrag_toolkit ...` paths below follow the Jan-2025 blog; confirm
    against the repo you pin. We use RETRIEVAL only (candidates), not the query
    engine's generation step — the specialist makes the judgement.

Offline note: not import-tested here (no network); syntax-checked only.
"""
from __future__ import annotations

import logging
from enum import Enum

from strands import Agent, AgentSkills, tool
from strands.models import BedrockModel
from strands.hooks import (
    HookProvider,
    HookRegistry,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    AfterToolCallEvent,
)

from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient
# from mcp.client.streamable_http import streamablehttp_client  # JPMC AWS path

# from modules.schemas import BriefBundle, MappingResult, VerifierReport, ProposedTriple
# from modules.neptune import queries as nq, publish as npub
# from modules.rdf import serialiser as rdf

log = logging.getLogger("scudo")

# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────
SONNET_46_BEDROCK_ID = "REPLACE_WITH_BEDROCK_MODEL_ID"
CATALOGUE_MCP_CMD = ["python", "vendor_catalogue_mcp.py"]

# The authoritative store (RDF + ODRL + DCAT) is reached via neptune-io's
# parameterised SPARQL tools — its connection lives in modules.neptune.

# The GraphRAG sidecar is a SEPARATE, non-authoritative store: a lexical graph +
# vector index. Distinct endpoints from the authoritative RDF graph on purpose.
GRAPHRAG_GRAPH_STORE_URI = "neptune-db://REPLACE.cluster-xxxx.<region>.neptune.amazonaws.com"
GRAPHRAG_VECTOR_STORE_URI = "aoss://https://REPLACE.<region>.aoss.amazonaws.com"

VERIFIER_AUTOPUBLISH = 16
VERIFIER_RETRY_LO, VERIFIER_RETRY_HI = 12, 15
CONFIDENCE_FLOOR = 0.80  # clarif. F31


class Route(str, Enum):
    NEW_MAPPING = "NEW_MAPPING"
    EXTEND_MAPPING = "EXTEND_MAPPING"
    RECONCILE_CONFLICT = "RECONCILE_CONFLICT"
    RESEARCH = "RESEARCH"


# ────────────────────────────────────────────────────────────────────────────
# GraphRAG retrieval — reads the NON-AUTHORITATIVE lexical sidecar
# ────────────────────────────────────────────────────────────────────────────
_graphrag_retriever = None


def _get_graphrag_retriever():
    """Build/cache the sidecar retriever once. Retrieval-only: we want candidates,
    not a generated answer — the specialist judges."""
    global _graphrag_retriever
    if _graphrag_retriever is None:
        from graphrag_toolkit import LexicalGraphQueryEngine          # verify import path
        from graphrag_toolkit.storage import GraphStoreFactory, VectorStoreFactory
        gs = GraphStoreFactory.for_graph_store(GRAPHRAG_GRAPH_STORE_URI)
        vs = VectorStoreFactory.for_vector_store(GRAPHRAG_VECTOR_STORE_URI)
        _graphrag_retriever = LexicalGraphQueryEngine.for_traversal_based_search(gs, vs)
    return _graphrag_retriever


@tool
def graphrag_retrieve(query: str, top_k: int = 10) -> list[dict]:
    """Retrieve candidate CDAO taxonomy nodes related to a vendor product.

    Reads the non-authoritative lexical sidecar and returns candidates by
    RELATEDNESS (taxonomic / structural), not similarity alone — surfacing nodes
    that pure vector search misses [clarif. 83]. Candidates only: confirm a chosen
    node's authoritative position with `neptune_node_by_iri` before relying on it,
    and never publish anything sourced from here.
    """
    # retriever = _get_graphrag_retriever()
    # results = retriever.retrieve(query)   # verify retriever accessor vs toolkit docs
    raise NotImplementedError(
        "→ run the cached toolkit retriever for `query`; map results to candidates "
        "{iri, label, definition, why_related, score}. Retrieval-only — the engine "
        "must not generate the answer; the specialist judges."
    )


# ────────────────────────────────────────────────────────────────────────────
# Authoritative RDF reads (system of record) + deterministic serialiser
# ────────────────────────────────────────────────────────────────────────────
@tool
def neptune_node_by_iri(iri: str) -> dict:
    """Authoritative lookup: one CDAO node + its definition and immediate edges,
    from the RDF graph. Used to confirm a GraphRAG candidate's real position."""
    raise NotImplementedError("→ modules.neptune.queries.node_by_iri")


@tool
def neptune_existing_mapping(vendor: str, vendor_product_ref: str) -> dict | None:
    """Authoritative: prior mapping for a vendor product, if any. NEW vs EXTEND."""
    raise NotImplementedError("→ modules.neptune.queries.existing_mapping")


@tool
def neptune_conflicts(vendor_product_ref: str) -> list[dict]:
    """Authoritative: equivalent products from other vendors with differing targets."""
    raise NotImplementedError("→ modules.neptune.queries.conflicts")


@tool
def rdf_serialise_mapping(mapping_result: dict) -> dict:
    """Serialise a MappingResult to DCAT Turtle via the serialiser (NOT by hand),
    then SHACL-validate. Returns {'triples': [...], 'conforms': bool, 'report': ...}."""
    raise NotImplementedError("→ modules.rdf.serialiser.serialise_mapping + validate_shapes")


# Specialist-facing tools. Candidate discovery = graphrag_retrieve (sidecar);
# authoritative facts = the neptune_* reads. Publish is NOT here — only the
# orchestrator publishes, to the RDF graph.
SPECIALIST_RETRIEVAL_TOOLS = [
    graphrag_retrieve, neptune_node_by_iri, neptune_existing_mapping, neptune_conflicts,
]
SERIALISER_TOOLS = [rdf_serialise_mapping]


# ────────────────────────────────────────────────────────────────────────────
# Enforcement hooks — LLM-facing invariants (cancel_tool = hard deny)
# ────────────────────────────────────────────────────────────────────────────
class EnforcementHooks(HookProvider):
    RAW_QUERY_MARKERS = ("select ", "construct ", "insert data", "@prefix")
    READ_PREFIXES = ("neptune_", "graphrag_")  # cap both authoritative + sidecar reads

    def __init__(self, max_reads: int = 12):
        self.max_reads = max_reads
        self._reads = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._reject_raw_query)
        registry.add_callback(BeforeToolCallEvent, self._cap_reads)
        registry.add_callback(AfterToolCallEvent, self._telemetry)

    def _reset(self, event: BeforeInvocationEvent) -> None:
        self._reads = 0

    def _reject_raw_query(self, event: BeforeToolCallEvent) -> None:
        # graphrag_retrieve takes a natural-language query, so it is exempt; this
        # guards the SPARQL/serialiser tools against hand-written query text.
        name = event.tool_use["name"]
        if not (name.startswith("neptune_") or name.startswith("rdf_")):
            return
        blob = str(event.tool_use.get("input", {})).lower()
        if any(m in blob for m in self.RAW_QUERY_MARKERS):
            event.cancel_tool = (
                f"Refused: '{name}' received raw query text. Use parameterised "
                f"arguments only — never author SPARQL or Turtle."
            )

    def _cap_reads(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"].startswith(self.READ_PREFIXES):
            self._reads += 1
            if self._reads > self.max_reads:
                event.cancel_tool = (
                    "Read budget exhausted for this request — decide on the "
                    "candidates and facts you already have."
                )

    def _telemetry(self, event: AfterToolCallEvent) -> None:
        log.info("tool=%s reads=%d", event.tool_use["name"], self._reads)


# ────────────────────────────────────────────────────────────────────────────
# Specialists + verifier
# ────────────────────────────────────────────────────────────────────────────
def build_specialists(catalogue_tools: list):
    model = BedrockModel(model_id=SONNET_46_BEDROCK_ID, temperature=0.2)
    skills = AgentSkills(skills="./skills/")  # incl. graphrag-retrieval

    mapping_specialist = Agent(
        model=model,
        system_prompt=(
            "You are the Mapping Specialist. Map the vendor product in the brief "
            "bundle to a CDAO node. Activate the taxonomy-mapping skill. Find "
            "candidates with the graphrag-retrieval skill, confirm a candidate's "
            "authoritative position via neptune-io, and emit triples via "
            "rdf-serialisation. You do not route, assemble context, or publish."
        ),
        tools=catalogue_tools + SPECIALIST_RETRIEVAL_TOOLS + SERIALISER_TOOLS,
        plugins=[skills],
        hooks=[EnforcementHooks()],
    )

    rights_specialist = Agent(
        model=model,
        system_prompt=(
            "You are the Rights Specialist. Express extracted licence terms as "
            "adapted-ODRL (RDFS semantics, simplified constraints). Activate the "
            "rights-odrl skill; serialise via rdf-serialisation. You do not publish."
        ),
        tools=SERIALISER_TOOLS,
        plugins=[skills],
        hooks=[EnforcementHooks()],
    )

    verifier = Agent(
        model=BedrockModel(model_id=SONNET_46_BEDROCK_ID, temperature=0.0),
        system_prompt=(
            "You are the Verifier. Score the specialist's MappingResult against the "
            "10-dimension rubric (0/1/2 each, max 20). Do NOT re-do the mapping; "
            "return defects. Output a VerifierReport."
        ),
    )
    return mapping_specialist, rights_specialist, verifier


# ────────────────────────────────────────────────────────────────────────────
# Deterministic orchestrator — routing rule + publish gate (unchanged in v0.2)
# ────────────────────────────────────────────────────────────────────────────
class Orchestrator:
    def __init__(self, mapping, rights, verifier):
        self.mapping, self.rights, self.verifier = mapping, rights, verifier

    @staticmethod
    def route(request: dict) -> Route:
        if request.get("ontology_gap"):
            return Route.RESEARCH
        if request.get("has_conflict"):
            return Route.RECONCILE_CONFLICT
        if request.get("has_precedent"):
            return Route.EXTEND_MAPPING
        return Route.NEW_MAPPING

    def run(self, request: dict, *, ontology_snapshot: str, rubric_version: str) -> dict:
        route = self.route(request)
        bundle = self._assemble_bundle(request, route)
        inv = {"route": route.value, "ontology_snapshot": ontology_snapshot,
               "rubric_version": rubric_version}

        if route is Route.RESEARCH:
            writeup = self.mapping(self._research_prompt(bundle), **inv)
            return self._enqueue_research(writeup)

        result = self.mapping.structured_output(
            "MappingResult", self._mapping_prompt(bundle), **inv,
        )
        report = self.verifier.structured_output(
            "VerifierReport", self._verifier_prompt(result, rubric_version),
        )
        return self._gate_and_publish(route, result, report)

    def _gate_and_publish(self, route: Route, result, report) -> dict:
        total = report.total_score
        conf = result.confidence
        if total < VERIFIER_RETRY_LO or conf < CONFIDENCE_FLOOR or result.requires_human_review:
            return self._enqueue_hitl(result, report, reason="below floor / self-flagged")
        if VERIFIER_RETRY_LO <= total <= VERIFIER_RETRY_HI:
            return self._retry(result)  # bounded retry with the verifier defect as feedback
        for t in result.proposed_triples:
            assert getattr(t, "graph", None), "triple missing named graph (provenance)"
            assert self._is_deterministic_iri(t.subject), f"non-deterministic IRI {t.subject!r}"
        raise NotImplementedError("→ modules.neptune.publish.publish_triples (authoritative RDF)")

    @staticmethod
    def _is_deterministic_iri(iri: str) -> bool:
        return iri.startswith("mds.") or iri.startswith("jpmorgan:data:cdao:")

    def _assemble_bundle(self, request, route): raise NotImplementedError
    def _mapping_prompt(self, bundle): raise NotImplementedError
    def _research_prompt(self, bundle): raise NotImplementedError
    def _verifier_prompt(self, result, rubric_version): raise NotImplementedError
    def _enqueue_research(self, writeup): raise NotImplementedError
    def _enqueue_hitl(self, result, report, reason): raise NotImplementedError
    def _retry(self, result): raise NotImplementedError


# ────────────────────────────────────────────────────────────────────────────
# OFFLINE — build the lexical sidecar (run as a SEPARATE job, not the live path)
# ────────────────────────────────────────────────────────────────────────────
def build_lexical_index(corpus_docs) -> None:
    """Build/refresh the non-authoritative lexical sidecar that graphrag_retrieve
    reads. Corpus = the CDAO ontology rendered as text (node labels, definitions,
    scope notes, SKOS relations) [clarif. 11] + vendor catalogue text. Rebuildable;
    not the system of record. Re-run when CDAO or the catalogue changes.
    """
    from graphrag_toolkit import LexicalGraphIndex                    # verify import path
    from graphrag_toolkit.storage import GraphStoreFactory, VectorStoreFactory
    gs = GraphStoreFactory.for_graph_store(GRAPHRAG_GRAPH_STORE_URI)
    vs = VectorStoreFactory.for_vector_store(GRAPHRAG_VECTOR_STORE_URI)
    index = LexicalGraphIndex(gs, vs)
    index.extract_and_build(corpus_docs, show_progress=True)
    # corpus_docs: build with a LlamaIndex reader over rendered CDAO TTL + catalogue.


# ────────────────────────────────────────────────────────────────────────────
# Live entrypoint
# ────────────────────────────────────────────────────────────────────────────
def main(request: dict) -> dict:
    catalogue_mcp = MCPClient(
        lambda: stdio_client(StdioServerParameters(command=CATALOGUE_MCP_CMD[0],
                                                    args=CATALOGUE_MCP_CMD[1:]))
    )
    with catalogue_mcp:
        catalogue_tools = catalogue_mcp.list_tools_sync()
        mapping, rights, verifier = build_specialists(catalogue_tools)
        orch = Orchestrator(mapping, rights, verifier)
        return orch.run(request, ontology_snapshot="cdao-2026-05-19", rubric_version="v1")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main({"vendor": "lseg", "vendor_product_ref": "LSEG-IBES-EST-001",
          "has_precedent": False, "has_conflict": False, "ontology_gap": False})
