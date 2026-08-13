"""Thread-safe lifecycle management for immutable taxonomy snapshots."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..models import TaxonomyNode
from .taxonomy_snapshot import TaxonomySnapshot, build_taxonomy_snapshot


@dataclass(frozen=True)
class SnapshotStatus:
    ready: bool
    rebuilding: bool
    stale: bool
    revision: int | None
    durable_revision: int
    last_error: str | None


class SnapshotManager:
    """Single-flight builder with atomic reference publication."""

    def __init__(
        self,
        read_revision: Callable[[], int],
        load_taxonomy: Callable[[], tuple[int, list[TaxonomyNode]]],
    ) -> None:
        self._read_revision = read_revision
        self._load_taxonomy = load_taxonomy
        self._current: TaxonomySnapshot | None = None
        self._condition = threading.Condition()
        self._rebuilding = False
        self._last_error: str | None = None

    @property
    def current(self) -> TaxonomySnapshot | None:
        return self._current

    def status(self) -> SnapshotStatus:
        durable_revision = self._read_revision()
        current = self._current
        return SnapshotStatus(
            ready=current is not None and current.revision == durable_revision,
            rebuilding=self._rebuilding,
            stale=current is not None and current.revision != durable_revision,
            revision=current.revision if current is not None else None,
            durable_revision=durable_revision,
            last_error=self._last_error,
        )

    def capture(self) -> TaxonomySnapshot:
        """Return exactly one current snapshot, rebuilding if stale."""

        while True:
            durable_revision = self._read_revision()
            current = self._current
            if current is not None and current.revision == durable_revision:
                return current
            with self._condition:
                current = self._current
                durable_revision = self._read_revision()
                if current is not None and current.revision == durable_revision:
                    return current
                if self._rebuilding:
                    self._condition.wait()
                    continue
                self._rebuilding = True
            try:
                while True:
                    loaded_revision, nodes = self._load_taxonomy()
                    candidate = build_taxonomy_snapshot(
                        nodes,
                        revision=loaded_revision,
                    )
                    if self._read_revision() != loaded_revision:
                        continue
                    self.publish(candidate)
                    return candidate
            except BaseException as exc:
                with self._condition:
                    self._last_error = str(exc)
                raise
            finally:
                with self._condition:
                    self._rebuilding = False
                    self._condition.notify_all()

    def publish(self, snapshot: TaxonomySnapshot) -> None:
        """Atomically publish a newer complete snapshot."""

        with self._condition:
            current = self._current
            if current is not None and snapshot.revision < current.revision:
                raise ValueError("cannot publish stale taxonomy snapshot")
            self._current = snapshot
            self._last_error = None
