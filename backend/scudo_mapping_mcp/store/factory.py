"""
The process-lifetime store construction point.

Configuration is read once when ``config.settings`` is imported. The cached
store is not an environment hot-swap mechanism.

One function decides the backend, from config. This mirrors the AWS
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
    return _build_store()


def _build_store() -> RetrievalStore:
    backend = settings.store_backend
    # JPMC-LOCAL: the two branches below are the ONLY places FalkorDB and
    # Neptune are ever constructed. They are lazy imports, so on a local run
    # (STORE_BACKEND=local_file) neither module is loaded and neither package
    # needs to be installed -- nothing here has to be commented out to run
    # without them. They are deliberately LEFT ACTIVE because the AWS deploys
    # set STORE_BACKEND=falkordb and would break if these were removed.
    #
    # Do NOT delete store/falkordb_store.py even though FalkorDB is unused:
    # opus_dense.py imports _jaro_winkler from that FILE, so the default
    # scoring path needs it on disk. (The falkordb PIP PACKAGE is not needed;
    # its import lives inside a method.) Verified: matching works end-to-end
    # with the falkordb and requests-aws4auth packages absent.
    if backend == "falkordb":
        from .falkordb_store import FalkorDBStore

        return FalkorDBStore(url=settings.falkordb_url, graph_name=settings.graph_name)
    if backend == "neptune":
        from .neptune_store import NeptuneStore

        return NeptuneStore(
            endpoint=settings.neptune_endpoint, graph_name=settings.graph_name
        )
    if backend == "memory":
        # DEV/DEMO ONLY — full ladder on a laptop, no containers. Real
        # JW+BM25+RRF scoring over in-memory nodes (see memory_store.py).
        from .memory_store import MemoryStore

        return MemoryStore()
    # JPMC-LOCAL: same scoring as 'memory', but HITL precedents are journalled
    # to a readable JSONL file so the learning loop survives a restart.
    if backend == "local_file":
        from .local_file_store import LocalFileStore

        return LocalFileStore()
    if backend == "scipy_sqlite":
        from .scipy_sqlite_store import ScipySQLiteStore

        return ScipySQLiteStore(settings.scipy_sqlite_path)
    raise ValueError(
        f"Unknown STORE_BACKEND '{backend}'. Use 'falkordb' (local), "
        f"'neptune' (prod), 'memory' (laptop demo), or "
        f"'local_file' (laptop demo with durable memory), or "
        f"'scipy_sqlite' (single-host durable matching)."
    )


def reset_store_cache() -> None:
    """Close and clear the process store; intended for tests and shutdown."""
    close_store()


def close_store() -> None:
    """Close the cached store, clearing the cache even when close raises."""
    store = get_store() if get_store.cache_info().currsize else None
    try:
        if store is not None:
            store.close()
    finally:
        get_store.cache_clear()
