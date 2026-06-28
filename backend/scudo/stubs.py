"""In-memory queues + publish sink for local smoke tests.

Production wiring:
  • HITL queue        → catalogue review queue [clarif. E27]
  • Research queue    → ontology-owner queue [blueprint §4.2]
  • Publish sink      → neptune_publish_triples (orchestrator-only)

All three are interfaces the orchestrator depends on; this module is the
behind-a-protocol mock.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Optional, Protocol

from .schemas import BatchLedger, MappingResult, VerifierReport


# ────────────────────────────────────────────────────────────────────────────
# Protocols — what the orchestrator binds to
# ────────────────────────────────────────────────────────────────────────────
class HitlQueue(Protocol):
    def enqueue(
        self,
        *,
        mapping_result: Optional[MappingResult],
        verifier_report: Optional[VerifierReport],
        reason: str,
    ) -> str: ...


class ResearchQueue(Protocol):
    def enqueue(self, *, writeup: str, bundle_ref: str) -> str: ...


class PublishSink(Protocol):
    def publish(self, *, named_graph: str, triples: list[dict]) -> str: ...


# ────────────────────────────────────────────────────────────────────────────
# In-memory mocks
# ────────────────────────────────────────────────────────────────────────────
class InMemoryHitlQueue:
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self.items: list[dict] = []

    def enqueue(self, *, mapping_result, verifier_report, reason) -> str:
        ticket = f"HITL-{next(self._counter):05d}"
        self.items.append(
            {
                "ticket": ticket,
                "reason": reason,
                "mapping_result": mapping_result.model_dump()
                if mapping_result
                else None,
                "verifier_report": verifier_report.model_dump()
                if verifier_report
                else None,
            }
        )
        return ticket


class InMemoryResearchQueue:
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self.items: list[dict] = []

    def enqueue(self, *, writeup, bundle_ref) -> str:
        ticket = f"RESEARCH-{next(self._counter):05d}"
        self.items.append(
            {"ticket": ticket, "bundle_ref": bundle_ref, "writeup": writeup}
        )
        return ticket


class InMemoryPublishSink:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, *, named_graph, triples) -> str:
        self.published.append({"graph": named_graph, "triples": triples})
        return named_graph


# ────────────────────────────────────────────────────────────────────────────
# Durable-runtime helpers (the `loops` skill)
# ────────────────────────────────────────────────────────────────────────────
def _triples_fingerprint(triples: list[dict]) -> str:
    """Stable hash of a triple set, order-independent. Two publishes of the same
    logical action (same triples in any order) collapse to one fingerprint."""
    canonical = sorted(json.dumps(t, sort_keys=True) for t in triples)
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()


class IdempotentPublishSink:
    """Wrap any PublishSink so a *replayed* publish is a no-op (the `loops`
    idempotency property): re-running a unit that already published must not
    double-write.

    Key = (named_graph, triples-fingerprint). Three cases:
      • identical (graph, triples) seen before → REPLAY: skip the inner sink,
        return the same graph. (`replays_deduped`)
      • same graph, *different* triples → REPLACE: a corrected re-map. A named
        graph is a set, so replace its contents rather than blindly appending a
        second copy. Forward to the inner sink (production Neptune publish is
        itself idempotent on the deterministic graph) and record it. (`replaced`)
      • new graph → normal publish. (`publishes`)

    The inner sink is the real side-effect (InMemoryPublishSink here,
    neptune_publish_triples in production).
    """

    def __init__(self, inner: PublishSink) -> None:
        self._inner = inner
        # named_graph -> fingerprint of the triples last published to it.
        self._seen: dict[str, str] = {}
        self.publishes = 0
        self.replays_deduped = 0
        self.replaced = 0

    @property
    def inner(self) -> PublishSink:
        return self._inner

    def publish(self, *, named_graph, triples) -> str:
        fp = _triples_fingerprint(triples)
        prior = self._seen.get(named_graph)
        if prior == fp:
            # Pure replay of the same logical action — no-op.
            self.replays_deduped += 1
            return named_graph
        if prior is not None:
            # Same graph, revised triples — a corrected re-map replaces the graph.
            self.replaced += 1
        else:
            self.publishes += 1
        self._seen[named_graph] = fp
        return self._inner.publish(named_graph=named_graph, triples=triples)


class LedgerStore(Protocol):
    """Persistence seam for the batch ledger — checkpoint after each pass and
    load on resume so a crashed/interrupted batch resumes from terminal units
    instead of recomputing them."""

    def save(self, run_id: str, ledger: BatchLedger) -> None: ...
    def load(self, run_id: str) -> Optional[BatchLedger]: ...


class InMemoryLedgerStore:
    """In-memory LedgerStore for smoke tests. Round-trips through model_dump/
    model_validate so it behaves like a real (serialising) store."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def save(self, run_id: str, ledger: BatchLedger) -> None:
        self._runs[run_id] = ledger.model_dump(mode="json")

    def load(self, run_id: str) -> Optional[BatchLedger]:
        raw = self._runs.get(run_id)
        return BatchLedger.model_validate(raw) if raw is not None else None


__all__ = [
    "HitlQueue",
    "ResearchQueue",
    "PublishSink",
    "InMemoryHitlQueue",
    "InMemoryResearchQueue",
    "InMemoryPublishSink",
    "IdempotentPublishSink",
    "LedgerStore",
    "InMemoryLedgerStore",
]
