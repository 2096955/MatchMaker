"""Sidecar graph + vector store factories.

The sidecar runs on potentially-distinct AWS resources from the authoritative
Neptune cluster:
  • SCUDO_SIDECAR_GRAPH_ENDPOINT    e.g. Neptune Analytics or another Neptune DB
  • SCUDO_SIDECAR_VECTOR_STORE_URI  e.g. aoss:// for OpenSearch Serverless

Bedrock clients are SHARED (scudo.shared.bedrock) — same region, same model
access. Only the store endpoints diverge between the two graphs.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from ..shared import MissingDependencyError, aws_region


def sidecar_graph_endpoint() -> str:
    ep = os.environ.get("SCUDO_SIDECAR_GRAPH_ENDPOINT")
    if not ep:
        raise RuntimeError(
            "SCUDO_SIDECAR_GRAPH_ENDPOINT not set — caller should use the mock "
            "(scudo.sidecar.mock) instead of constructing a real store."
        )
    return ep


def sidecar_vector_uri() -> str:
    return os.environ.get("SCUDO_SIDECAR_VECTOR_STORE_URI", "")


@lru_cache(maxsize=1)
def get_sidecar_property_graph_store() -> Any:
    """LlamaIndex NeptuneDatabasePropertyGraphStore for the sidecar cluster."""
    try:
        from llama_index.graph_stores.neptune import NeptuneDatabasePropertyGraphStore
    except ImportError as e:
        raise MissingDependencyError(
            "pip install llama-index-graph-stores-neptune"
        ) from e
    endpoint = sidecar_graph_endpoint()
    # Endpoint hostname stripping for backward-compat with full URIs like
    # "neptune-db://host" — LlamaIndex wants the plain hostname.
    if "://" in endpoint:
        endpoint = endpoint.split("://", 1)[1]
    return NeptuneDatabasePropertyGraphStore(
        host=endpoint,
        port=int(os.environ.get("SCUDO_SIDECAR_GRAPH_PORT", "8182")),
        use_iam=os.environ.get("SCUDO_SIDECAR_USE_IAM", "true").lower() in ("1", "true", "yes"),
    )


def clear_cache() -> None:
    get_sidecar_property_graph_store.cache_clear()


__all__ = [
    "sidecar_graph_endpoint", "sidecar_vector_uri",
    "get_sidecar_property_graph_store", "clear_cache",
]
