"""Process-local durable state for SCUDO_LOCAL=1 (not typed into JPMC Aurora)."""

from __future__ import annotations

import os
import threading
from typing import Callable, TypeVar


CATALOGUE: dict[str, dict] = {}
MEMORY: dict[str, dict] = {}
OUTBOX: list[dict] = []
AUDIT: list[dict] = []
REVIEWS: list[dict] = []
NEPTUNE_GRAPHS: dict[str, list[dict]] = {}
OPENSEARCH_DOCS: dict[str, dict] = {}
# Dashboard ingest working set: (vendor, product_id) → {name, description, …}
WORKING_SET: dict[tuple[str, str], dict] = {}
MEMORY_LOCK = threading.RLock()
T = TypeVar("T")


def memory_snapshot() -> dict[str, dict]:
    with MEMORY_LOCK:
        return dict(MEMORY)


def read_memory(key: str) -> dict | None:
    with MEMORY_LOCK:
        value = MEMORY.get(key)
        return dict(value) if value is not None else None


def atomic_memory_update(callback: Callable[[dict[str, dict]], T]) -> T:
    """Apply a copy-on-write update only when the callback returns truthy.

    False/None is an explicit no-op; exceptions propagate. In both cases the
    original MEMORY object and contents remain unchanged.
    """

    global MEMORY
    with MEMORY_LOCK:
        candidate = dict(MEMORY)
        result = callback(candidate)
        if result:
            MEMORY = candidate
        return result


def reset() -> None:
    global MEMORY
    CATALOGUE.clear()
    with MEMORY_LOCK:
        MEMORY = {}
    OUTBOX.clear()
    AUDIT.clear()
    REVIEWS.clear()
    NEPTUNE_GRAPHS.clear()
    OPENSEARCH_DOCS.clear()
    WORKING_SET.clear()


def is_local() -> bool:
    return os.environ.get("SCUDO_LOCAL", "").strip() in {"1", "true", "yes"}
