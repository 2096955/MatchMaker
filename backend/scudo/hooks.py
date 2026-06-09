"""Strands HookProviders translating hooks.md into the SDK.

hooks.md is written against the Claude Agent SDK shell-hook model (settings.json
+ `.claude/hooks/*.py`). The invariants are identical here; the mechanism is
Strands HookProvider/HookRegistry. The load-bearing invariants — publish gate,
confidence floor, verifier-always-runs — live in the deterministic orchestrator
because that is stronger than a hook (it runs unconditionally in code, never
inside an agent loop). These Strands hooks are the per-tool-call enforcement
layer and the telemetry layer.

What lives where:
  Orchestrator (orchestrator.py)         | Strands hooks (this file)
  -------------------------------------- | --------------------------------
  Publish gate (verifier ≥ 16, floor,    | Defence-in-depth: deny any agent
   RESEARCH-never-publishes, IRI/graph)  |  call to neptune_publish_triples
  Verifier always runs                   | (orchestrator owns this)
  Bundle schema validation               | (orchestrator owns this)
  Mapping Object completeness            | (orchestrator owns this)
  Routing                                | (orchestrator owns this)
  —                                      | Reject hand-written SPARQL/Turtle
  —                                      | Cap Neptune reads per invocation
  —                                      | Pin versions into invocation_state
  —                                      | Per-tool telemetry
"""
from __future__ import annotations

import logging
from typing import Optional

from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

log = logging.getLogger("scudo.hooks")

# SPARQL / Turtle markers (case-insensitive substring match).
_SPARQL_TURTLE_MARKERS = ("select ", "construct ", "insert data", "delete data", "@prefix")
# openCypher markers — anchored on the keyword followed by `(` or `[` to avoid
# false positives on prose ("match the candidate", "delete the row" etc.).
# These signatures are distinctive of openCypher and virtually impossible in
# normal English. Added because the Neptune MCP path makes openCypher the
# query language at issue, not SPARQL (per neptune-prototyping-with-agents).
_CYPHER_MARKERS = (
    "match (", "match[", "merge (", "merge[",
    "create (", "create[", "detach delete", "optional match ",
)
_RAW_QUERY_MARKERS = _SPARQL_TURTLE_MARKERS + _CYPHER_MARKERS


# ────────────────────────────────────────────────────────────────────────────
# 1) VersionPinHook — BeforeInvocationEvent
#    Stamps ontology_snapshot, rubric_version, schema_version into
#    invocation_state so the agent's tool calls + telemetry carry them.
# ────────────────────────────────────────────────────────────────────────────
class VersionPinHook(HookProvider):
    """Replay-safety primitive (clarif. G39 + L69). Defence in depth: the
    orchestrator passes the same pins via the agent call, but pinning them
    again here means a forgetful caller still gets a stamped invocation."""

    def __init__(
        self,
        ontology_snapshot: str,
        rubric_version: str,
        schema_version: str,
    ) -> None:
        self.pins = {
            "ontology_snapshot": ontology_snapshot,
            "rubric_version": rubric_version,
            "schema_version": schema_version,
        }

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._stamp)

    def _stamp(self, event: BeforeInvocationEvent) -> None:
        for k, v in self.pins.items():
            event.invocation_state.setdefault(k, v)


# ────────────────────────────────────────────────────────────────────────────
# 2) RejectRawQueryHook — BeforeToolCallEvent
#    Denies any neptune_* / rdf_* tool call carrying raw SPARQL/Turtle text.
#    Kills the hallucinated-Turtle/SPARQL failure mode.
# ────────────────────────────────────────────────────────────────────────────
class RejectRawQueryHook(HookProvider):
    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._check)

    def _check(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if not (name.startswith("neptune_") or name.startswith("rdf_")):
            return
        # Stringify the full input payload (recursive dicts/lists) and scan
        # for raw-query markers. Case-insensitive.
        blob = repr(event.tool_use.get("input", {})).lower()
        for marker in _RAW_QUERY_MARKERS:
            if marker in blob:
                event.cancel_tool = (
                    f"Refused: tool {name!r} received raw query text "
                    f"(marker {marker!r}). Use the parameterised arguments only — "
                    "never author SPARQL or Turtle."
                )
                log.warning("RejectRawQueryHook denied %s (marker=%r)", name, marker)
                return


# ────────────────────────────────────────────────────────────────────────────
# 3) PublishGateHook — BeforeToolCallEvent (matcher: neptune_publish_triples)
#    Specialists must NEVER reach publish. The orchestrator publishes after the
#    deterministic gate, not through the agent loop. This is defence in depth.
# ────────────────────────────────────────────────────────────────────────────
class PublishGateHook(HookProvider):
    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._deny)

    def _deny(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] == "neptune_publish_triples":
            event.cancel_tool = (
                "Publish is orchestrator-only. Specialists must not call "
                "neptune_publish_triples; the deterministic publish gate runs "
                "outside the agent loop after the verifier."
            )
            log.warning("PublishGateHook denied publish attempt from agent loop")


# ────────────────────────────────────────────────────────────────────────────
# 4) ReadCapHook — BeforeToolCallEvent
#    Bounded read budget per invocation, across BOTH the authoritative graph
#    (neptune_*) and the lexical sidecar (graphrag_*). Climbing reads = bundle
#    assembly is failing → cap it; the orchestrator routes to HITL.
#    READ_PREFIXES tracks the v0.2 scaffold's ("neptune_", "graphrag_").
# ────────────────────────────────────────────────────────────────────────────
class NeptuneReadCapHook(HookProvider):
    INVOCATION_STATE_KEY = "_read_count"
    READ_PREFIXES = ("neptune_", "graphrag_")

    def __init__(self, max_reads: int = 12) -> None:
        self.max_reads = max_reads

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._cap)

    def _reset(self, event: BeforeInvocationEvent) -> None:
        event.invocation_state[self.INVOCATION_STATE_KEY] = 0

    def _cap(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if not name.startswith(self.READ_PREFIXES):
            return
        state = event.invocation_state
        state[self.INVOCATION_STATE_KEY] = state.get(self.INVOCATION_STATE_KEY, 0) + 1
        if state[self.INVOCATION_STATE_KEY] > self.max_reads:
            event.cancel_tool = (
                f"Read budget ({self.max_reads}) exhausted for this request "
                "across the authoritative graph + lexical sidecar — the bundle "
                "should already carry the slice you need. Decide on what you "
                "have, or surface a defect for HITL."
            )


# ────────────────────────────────────────────────────────────────────────────
# 5) TelemetryHook — AfterToolCallEvent + AfterInvocationEvent
#    Per-tool name + duration + Neptune read count. Aggregates emit on
#    invocation end so the orchestrator can write a single row per run.
# ────────────────────────────────────────────────────────────────────────────
class TelemetryHook(HookProvider):
    INVOCATION_STATE_KEY = "_telemetry"

    def __init__(self, sink=None) -> None:
        # `sink` is a callable(dict) → None — defaults to logger.info.
        self.sink = sink or (lambda payload: log.info("scudo.telemetry %s", payload))

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._init)
        registry.add_callback(AfterToolCallEvent, self._record_tool)
        registry.add_callback(AfterInvocationEvent, self._flush)

    def _init(self, event: BeforeInvocationEvent) -> None:
        event.invocation_state[self.INVOCATION_STATE_KEY] = {
            "tool_calls": 0,
            "neptune_reads": 0,    # authoritative-graph reads
            "graphrag_reads": 0,   # lexical-sidecar reads
            "tool_names": [],
            "denied_tools": [],
        }

    def _record_tool(self, event: AfterToolCallEvent) -> None:
        tel = event.invocation_state.get(self.INVOCATION_STATE_KEY)
        if tel is None:
            return
        name = event.tool_use["name"]
        tel["tool_calls"] += 1
        tel["tool_names"].append(name)
        if name.startswith("neptune_"):
            tel["neptune_reads"] += 1
        elif name.startswith("graphrag_"):
            tel["graphrag_reads"] += 1
        if event.cancel_message:
            tel["denied_tools"].append({"name": name, "reason": event.cancel_message})

    def _flush(self, event: AfterInvocationEvent) -> None:
        tel = event.invocation_state.get(self.INVOCATION_STATE_KEY)
        if tel is None:
            return
        pins = {
            k: event.invocation_state.get(k)
            for k in ("ontology_snapshot", "rubric_version", "schema_version", "route")
        }
        self.sink({"telemetry": tel, "pins": pins})


# ────────────────────────────────────────────────────────────────────────────
# Convenience: assemble the standard hook set for a specialist
# ────────────────────────────────────────────────────────────────────────────
def specialist_hooks(
    *,
    ontology_snapshot: str,
    rubric_version: str,
    schema_version: str,
    max_neptune_reads: int = 12,
    telemetry_sink=None,
) -> list[HookProvider]:
    return [
        VersionPinHook(ontology_snapshot, rubric_version, schema_version),
        RejectRawQueryHook(),
        PublishGateHook(),
        NeptuneReadCapHook(max_reads=max_neptune_reads),
        TelemetryHook(sink=telemetry_sink),
    ]


__all__ = [
    "VersionPinHook", "RejectRawQueryHook", "PublishGateHook",
    "NeptuneReadCapHook", "TelemetryHook", "specialist_hooks",
]
