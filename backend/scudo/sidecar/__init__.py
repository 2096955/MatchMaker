"""Lexical sidecar — candidate discovery only (graphrag-retrieval skill).

The lexical sidecar is a *non-authoritative, rebuildable* graph + vector index
built offline by `build_lexical_index()` from the CDAO ontology text + vendor
catalogue. Per the graphrag-retrieval and neptune-io SKILL.md files, it is
distinct from the authoritative RDF graph in three ways:

  1. Different store (potentially Neptune Analytics + OpenSearch Serverless;
     see scudo.sidecar.client) — distinct from the authoritative Neptune.
  2. Read-only and rebuildable — nothing publishes to it from the live path.
  3. Retrieval ONLY — candidates flow back to the Mapping Specialist as
     evidence; the specialist must CONFIRM the favoured candidate against the
     authoritative graph (scudo.authoritative.node_by_iri) before relying.

Selection rule: if `SCUDO_SIDECAR_GRAPH_ENDPOINT` is unset → in-memory mock;
else → LlamaIndex PropertyGraphIndex over the configured stores.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("scudo.sidecar")


def _is_real_mode() -> bool:
    """True when env says the sidecar store is configured."""
    return bool(os.environ.get("SCUDO_SIDECAR_GRAPH_ENDPOINT"))


def candidate_nodes(term: str, limit: int = 25) -> list[dict]:
    """Retrieve candidate CDAO taxonomy nodes related to a vendor term.

    Real mode: PropertyGraphIndex + CypherTemplateRetriever over the sidecar
    stores (via LlamaIndex; embeddings via Bedrock).
    Mock mode: lexical scoring over the in-memory CDAO fixture.

    Returns {iri, label, score} — the score reflects retrieval relatedness.
    Candidates ONLY: the specialist must confirm via
    scudo.authoritative.node_by_iri before relying on them.
    """
    if _is_real_mode():
        from . import retrieval
        return retrieval.candidate_nodes(term=term, limit=limit)
    from . import mock
    return mock.candidate_nodes(term=term, limit=limit)


__all__ = ["candidate_nodes"]
