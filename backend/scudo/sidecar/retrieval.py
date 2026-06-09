"""Candidate retrieval via LlamaIndex PropertyGraphIndex over the sidecar.

Pattern from amazon-neptune-generative-ai-samples/graphrag-with-neptune:
build a PropertyGraphIndex over the SIDECAR's CDAO-derived graph + vector
store, serve queries through CypherTemplateRetriever (parameterised).
Embeddings + LLM both via Bedrock (scudo.shared.bedrock).

This module hits the LEXICAL SIDECAR ONLY — a separate store from the
authoritative Neptune cluster, per the graphrag-retrieval and neptune-io
SKILL.md files. Anything publishing or confirming authoritative truth
goes through scudo.authoritative, not here.

If the embedding/Bedrock stack isn't installed (local dev), import errors
surface as MissingDependencyError so the sidecar facade falls back to mock.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from ..shared import MissingDependencyError, get_bedrock_embedding, get_bedrock_llm
from .client import get_sidecar_property_graph_store

log = logging.getLogger("scudo.sidecar.retrieval")


# Parameterised Cypher template. `$query_str` is bound from the user's NL
# query — never string-concatenated. Lives in code so the hook + reviewer
# can see exactly what runs against the sidecar.
CDAO_CANDIDATE_TEMPLATE = """
MATCH (n:CdaoNode)
WHERE toLower(n.label) CONTAINS toLower($query_str)
   OR any(syn IN n.synonyms WHERE toLower(syn) CONTAINS toLower($query_str))
RETURN n.iri AS iri, n.label AS label
LIMIT $top_k
"""


@lru_cache(maxsize=1)
def _retriever():
    """Build (once) the CypherTemplateRetriever bound to the sidecar."""
    try:
        from llama_index.core import PropertyGraphIndex
        from llama_index.core.indices.property_graph import CypherTemplateRetriever
    except ImportError as e:
        raise MissingDependencyError("pip install llama-index-core") from e

    store = get_sidecar_property_graph_store()
    llm = get_bedrock_llm()
    embed = get_bedrock_embedding()

    PropertyGraphIndex.from_existing(
        property_graph_store=store,
        llm=llm,
        embed_model=embed,
    )
    return CypherTemplateRetriever(
        graph_store=store,
        cypher_template=CDAO_CANDIDATE_TEMPLATE,
        llm=llm,
        embed_model=embed,
    )


def candidate_nodes(*, term: str, limit: int = 25) -> list[dict]:
    """Embed-then-traverse candidate retrieval. Returns {iri, label, score}.

    Sidecar-only: do NOT treat results as ground truth. The Mapping Specialist
    must confirm a favoured candidate via scudo.authoritative.node_by_iri
    before relying on it.
    """
    retriever = _retriever()
    nodes_with_score = retriever.retrieve(term)  # LlamaIndex API
    out: list[dict] = []
    for nws in nodes_with_score[:limit]:
        meta = getattr(nws.node, "metadata", {}) or {}
        iri = meta.get("iri") or meta.get("n.iri")
        label = meta.get("label") or meta.get("n.label") or ""
        if iri:
            out.append({
                "iri": iri,
                "label": label,
                "score": float(getattr(nws, "score", 0.0) or 0.0),
            })
    return out


__all__ = ["candidate_nodes", "CDAO_CANDIDATE_TEMPLATE"]
