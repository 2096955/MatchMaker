"""
The swap point.

One function decides the backend, from config, once. This mirrors the AWS
GraphRAG Toolkit's GraphStoreFactory.register / for_graph_store pattern: the
caller asks for "a store" and gets whichever one config names. Flip
STORE_BACKEND from falkordb to neptune and nothing above this line changes.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import RetrievalStore


@lru_cache(maxsize=1)
def get_store() -> RetrievalStore:
    backend = settings.store_backend
    if backend == "falkordb":
        from .falkordb_store import FalkorDBStore
        return FalkorDBStore(url=settings.falkordb_url, graph_name=settings.graph_name)
    if backend == "neptune":
        from .neptune_store import NeptuneStore
        return NeptuneStore(endpoint=settings.neptune_endpoint, graph_name=settings.graph_name)
    if backend == "memory":
        # DEV/DEMO ONLY — full ladder on a laptop, no containers. Real
        # JW+BM25+RRF scoring over in-memory nodes (see memory_store.py).
        from .memory_store import MemoryStore
        return MemoryStore()
    raise ValueError(
        f"Unknown STORE_BACKEND '{backend}'. Use 'falkordb' (local), "
        f"'neptune' (prod), or 'memory' (laptop demo)."
    )


def reset_store_cache() -> None:
    """For tests / backend hot-swap."""
    get_store.cache_clear()
