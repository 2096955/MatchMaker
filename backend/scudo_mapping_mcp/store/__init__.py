from .base import RetrievalStore
from .factory import close_store, get_store, reset_store_cache


def storage_ready(store: RetrievalStore) -> bool:
    """Probe bootstrap liveness, falling back for legacy store backends."""

    probe = getattr(store, "storage_ready", None)
    return bool(probe()) if callable(probe) else bool(store.health())


__all__ = [
    "RetrievalStore",
    "close_store",
    "get_store",
    "reset_store_cache",
    "storage_ready",
]
