"""Queue/sink protocols + in-memory implementations for Lambda wiring."""

from __future__ import annotations

import itertools
from typing import Optional, Protocol

from .schemas import MappingResult, VerifierReport


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
