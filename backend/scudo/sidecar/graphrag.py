"""Optional graphrag-toolkit wrapper for higher-level graph QA over the sidecar.

awslabs/graphrag-toolkit (https://github.com/awslabs/graphrag-toolkit) ships:
  • lexical-graph     — builds a hierarchical lexical graph from unstructured
                        text + composable QA strategies.
                        Real PyPI: `pip install graphrag-lexical-graph`
                        Import:    `from graphrag_toolkit.lexical_graph import …`

  • BYOKG-RAG         — bring-your-own-knowledge-graph QA. Natural fit for
                        SCUDO's RESEARCH route: ground a write-up in the
                        existing sidecar graph when an ontology gap is flagged.
                        ──── SECURITY NOTE ────
                        The PyPI name `graphrag-toolkit-byokg-rag` currently
                        resolves to a TYPOSQUAT (v0.0.1, fake metadata). DO
                        NOT pip-install it. Install from source until AWS
                        publishes the real artifact:

                            pip install \\
                              "git+https://github.com/awslabs/graphrag-toolkit.git#subdirectory=byokg-rag"

This module is import-guarded: if neither path is importable the helper
returns a structured "graphrag unavailable" payload so callers don't branch
on availability and the RESEARCH route still completes.

NOTE on the two-graphs split: this runs against the LEXICAL SIDECAR's store
(scudo.sidecar.client.get_sidecar_property_graph_store), not the authoritative
RDF graph. Output is candidate/narrative material — never published.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..shared import get_bedrock_llm
from .client import get_sidecar_property_graph_store

log = logging.getLogger("scudo.sidecar.graphrag")


def _import_byokg() -> Optional[Any]:
    try:
        from graphrag_toolkit import byokg_rag  # type: ignore[import-untyped]
        return byokg_rag
    except ImportError:
        return None


def _import_lexical_graph_query_engine() -> Optional[Any]:
    try:
        from graphrag_toolkit.lexical_graph.lexical_graph_query_engine import (
            LexicalGraphQueryEngine,
        )
        return LexicalGraphQueryEngine
    except ImportError:
        return None


def answer_with_graph(question: str, *, max_paths: int = 10) -> dict:
    """Run a graphrag-toolkit query against the sidecar.

    Returns {answer, evidence, paths, available, backend} so callers don't
    branch on availability. `backend` ∈ {'byokg', 'lexical', 'stub'}.
    """
    byokg = _import_byokg()
    if byokg is not None:
        try:
            engine = byokg.GraphRAGQueryEngine(  # type: ignore[attr-defined]
                graph_store=get_sidecar_property_graph_store(),
                llm=get_bedrock_llm(),
                max_paths=max_paths,
            )
            result = engine.query(question)
            return {
                "answer": getattr(result, "answer", str(result)),
                "evidence": getattr(result, "evidence", []),
                "paths": getattr(result, "paths", []),
                "available": True,
                "backend": "byokg",
            }
        except AttributeError:
            log.warning(
                "graphrag_toolkit.byokg_rag present but API shape differs from "
                "the expected GraphRAGQueryEngine — falling through."
            )

    LexicalEngine = _import_lexical_graph_query_engine()
    if LexicalEngine is not None:
        try:
            engine = LexicalEngine.for_traversal_search(  # API per lexical-graph docs
                graph_store=get_sidecar_property_graph_store(),
                llm=get_bedrock_llm(),
            )
            result = engine.query(question)
            return {
                "answer": getattr(result, "response", str(result)),
                "evidence": getattr(result, "source_nodes", []),
                "paths": [],
                "available": True,
                "backend": "lexical",
            }
        except Exception as e:  # pragma: no cover
            log.warning("lexical-graph query failed (%s) — falling through.", e)

    log.warning(
        "No graphrag backend available. To enable a grounded write-up: "
        "(a) pip install graphrag-lexical-graph, OR "
        "(b) pip install git+https://github.com/awslabs/graphrag-toolkit.git"
        "#subdirectory=byokg-rag "
        "(do NOT pip-install graphrag-toolkit-byokg-rag — typosquat)."
    )
    return {
        "answer": (
            "graphrag-toolkit not installed — RESEARCH write-up is "
            "unstructured scaffolding only. See scudo.sidecar.graphrag "
            "module docstring for install options."
        ),
        "evidence": [],
        "paths": [],
        "available": False,
        "backend": "stub",
    }


__all__ = ["answer_with_graph"]
