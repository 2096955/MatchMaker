"""
M9-shaped agent runner for the SCUDO mapping demo.

PURPOSE
-------
The MCP server exposes four bounded, read-only tools. An AI client (an LLM
agent) can pick those tools, call them, and reason about the candidates —
THAT is the story the brief tells. This module is the seam where that
agent runs, behind a single HTTP endpoint (POST /api/mapping/agent/run)
that streams its tool calls + reasoning + final result to the frontend.

CONTRACT — pinned, non-negotiable, mirrors the M9 SCORER CONTRACT in
matching.py:

    The agent EXPLORES the MCP tool surface and emits a recommendation.
    The deterministic matcher (matching.map_vendor_product) ALWAYS runs
    alongside and produces the AUTHORITATIVE result the rest of the
    system consumes (HITL, bundle, audit). The agent never bypasses:
      - scope_gate  (I3)
      - confidence_floor 0.80  (I4)
      - validations  (I6)
      - the store write side  (I5 — HITL-only)
    If the agent's recommendation conflicts with the matcher's status
    (e.g. agent proposes a node the matcher would reject), the matcher
    wins. The agent's role is to make the MCP exercise VISIBLE, not to
    decide.

TWO BACKENDS BEHIND ONE SEAM
----------------------------
ScriptedMappingAgent — deterministic walk over the 4 MCP tools, fake
    reasoning, fast, no AWS dependency. The default until Bedrock access
    in the Cognizant sandbox (954976331678 / eu-west-2) is confirmed.

BedrockMappingAgent — Strands Agents SDK + Claude Opus 4.8 on Bedrock.
    The agent reads the product, picks tools, reasons, recommends.
    Required env when wiring it up:

      SCUDO_AGENT_BACKEND=bedrock
      AWS_REGION=eu-west-2
      SCUDO_BEDROCK_MODEL_ID=eu.anthropic.claude-opus-4-8   # cross-region
                                                            # inference profile
                                                            # for eu-west-2

    Required IAM on the calling principal (ECS task role / dev user):
      bedrock:InvokeModel
      bedrock:InvokeModelWithResponseStream
      bedrock:GetInferenceProfile
      bedrock:ListInferenceProfiles
    scoped to the model + inference-profile ARN, e.g.:
      arn:aws:bedrock:eu-west-2::foundation-model/anthropic.claude-opus-4-8
      arn:aws:bedrock:eu-west-2:954976331678:inference-profile/eu.anthropic.claude-opus-4-8

    Model access: must be GRANTED for Claude Opus 4.8 in the Bedrock
    console for the eu-west-2 region of account 954976331678. The other
    agent provisioning the stack owns this step; without it the runtime
    raises AccessDeniedException at first invoke.

    Package: pip install strands-agents (already pinned via
    requirements.txt; lazy-imported here so mock-only runs don't need it).

The factory selects the backend from SCUDO_AGENT_BACKEND
({"scripted", "bedrock"}, default "scripted").
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .matching import map_vendor_product
from .models import (
    Candidate,
    MappingResult,
    MappingStatus,
    Subgraph,
    TaxonomyNode,
    VendorProductRef,
)
from .store import get_store


# ──────────────────────────────────────────────────────────────────────────
# Event shape — the wire contract between agent runner and frontend
# ──────────────────────────────────────────────────────────────────────────
# The five event types below are what the SSE endpoint streams. The
# frontend renders them as a live activity log. Keep this contract stable;
# both ScriptedMappingAgent and BedrockMappingAgent emit the same shape.


@dataclass
class AgentEvent:
    """One event in the agent's run. Serialised as JSON per SSE frame."""

    type: str           # "start" | "tool_call" | "tool_result" |
                        # "agent_message" | "final_result" | "error" | "done"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, **self.payload})


# ──────────────────────────────────────────────────────────────────────────
# Bedrock model id — single swap point
# ──────────────────────────────────────────────────────────────────────────
# Default chosen for the Cognizant sandbox in eu-west-2 (cross-region
# inference profile for Claude Opus 4.8 — model id confirmed via the
# claude-api skill). Override via SCUDO_BEDROCK_MODEL_ID for other regions
# or for swapping to Sonnet (e.g. eu.anthropic.claude-sonnet-4-6).
DEFAULT_BEDROCK_MODEL_ID = "eu.anthropic.claude-opus-4-8"


# ──────────────────────────────────────────────────────────────────────────
# Scripted agent — the default
# ──────────────────────────────────────────────────────────────────────────
class ScriptedMappingAgent:
    """Deterministic walk over the four MCP tools.

    Emits the same event sequence a real LLM agent would, but with no
    reasoning loop and no AWS dependency. Useful for:
      - Local dev (no Bedrock access)
      - CI smoke gates (replay-safe, no model variance)
      - Demo days when Bedrock is throttled / down

    Behaviour is intentionally a thin narration of the deterministic
    matcher's own steps. The "final_result" is the matcher's MappingResult
    verbatim — agent and matcher cannot disagree by construction.
    """

    NAME = "scripted"

    def run(
        self,
        ref: VendorProductRef,
        *,
        max_candidates: int = 5,
    ) -> Iterator[AgentEvent]:
        yield AgentEvent(
            type="start",
            payload={
                "vendor": ref.vendor,
                "product_id": ref.product_id,
                "product_name": ref.name,
                "agent_backend": self.NAME,
            },
        )

        yield AgentEvent(
            type="agent_message",
            payload={
                "content": (
                    f"Mapping {ref.vendor} product {ref.product_id!r}. "
                    f"I'll use the MCP tools to find candidate CDAO nodes, "
                    f"inspect the best match, and recommend a mapping."
                ),
            },
        )

        # Step 1 — find_similar_products
        yield AgentEvent(
            type="tool_call",
            payload={
                "tool": "find_similar_products",
                "args": {
                    "vendor": ref.vendor,
                    "product_id": ref.product_id,
                    "name": ref.name,
                    "max_results": max_candidates,
                },
            },
        )
        store = get_store()
        candidates = store.find_similar_products(ref, max_results=max_candidates)
        yield AgentEvent(
            type="tool_result",
            payload={
                "tool": "find_similar_products",
                "result": {
                    "count": len(candidates),
                    "candidates": [_candidate_dict(c) for c in candidates],
                },
            },
        )

        if not candidates:
            yield AgentEvent(
                type="agent_message",
                payload={
                    "content": (
                        "No candidate CDAO nodes — the matcher will return "
                        "NEEDS_REVIEW for human triage."
                    ),
                },
            )
        else:
            best = candidates[0]
            yield AgentEvent(
                type="agent_message",
                payload={
                    "content": (
                        f"Top candidate: {best.node.label!r} at similarity "
                        f"{best.similarity:.2f}. Let me inspect its taxonomy "
                        f"context."
                    ),
                },
            )

            # Step 2 — get_taxonomy_node for the top candidate
            yield AgentEvent(
                type="tool_call",
                payload={
                    "tool": "get_taxonomy_node",
                    "args": {"node_iri": best.node.iri},
                },
            )
            node = store.get_taxonomy_node(best.node.iri)
            yield AgentEvent(
                type="tool_result",
                payload={
                    "tool": "get_taxonomy_node",
                    "result": _node_dict(node),
                },
            )

            # Step 3 — get_ontology_neighbourhood for shape context
            yield AgentEvent(
                type="tool_call",
                payload={
                    "tool": "get_ontology_neighbourhood",
                    "args": {"node_iri": best.node.iri, "max_depth": 2},
                },
            )
            subgraph = store.get_ontology_neighbourhood(
                best.node.iri, max_depth=2, max_nodes=20,
            )
            yield AgentEvent(
                type="tool_result",
                payload={
                    "tool": "get_ontology_neighbourhood",
                    "result": _subgraph_dict(subgraph),
                },
            )

        # Step 4 — map_vendor_product is the authoritative step.
        # NOTE: this is the seam where the MCP tool and the matcher
        # produce the same MappingResult. The agent does not score; it
        # asks the matcher to score.
        yield AgentEvent(
            type="tool_call",
            payload={
                "tool": "map_vendor_product",
                "args": {
                    "vendor": ref.vendor,
                    "product_id": ref.product_id,
                    "name": ref.name,
                    "description": ref.description,
                },
            },
        )
        result = map_vendor_product(ref)
        yield AgentEvent(
            type="tool_result",
            payload={
                "tool": "map_vendor_product",
                "result": _mapping_result_dict(result),
            },
        )

        yield AgentEvent(
            type="agent_message",
            payload={
                "content": _summary_for(result),
            },
        )

        yield AgentEvent(
            type="final_result",
            payload={"mapping": _mapping_result_dict(result)},
        )

        yield AgentEvent(type="done")


# ──────────────────────────────────────────────────────────────────────────
# Bedrock agent — the live LLM path
# ──────────────────────────────────────────────────────────────────────────
class BedrockMappingAgent:
    """Strands Agents SDK + Bedrock Claude Opus 4.8.

    Constructed lazily so that the package keeps loading when boto3 /
    strands_agents are not installed (mock-only / local FalkorDB runs).
    The factory below picks this class only when SCUDO_AGENT_BACKEND is
    set explicitly to "bedrock".

    The agent's TOOL SURFACE is the four read-only MCP tools — same
    function calls the deterministic matcher would make. The LLM decides
    the ORDER and the REASONING, then the matcher runs as the final step
    (same as the scripted backend) to produce the authoritative result.
    """

    NAME = "bedrock"

    SYSTEM_PROMPT = (
        "You are the SCUDO mapping agent. Your job is to map a vendor "
        "product to one CDAO taxonomy node using the four read-only MCP "
        "tools available to you: find_similar_products, get_taxonomy_node, "
        "get_ontology_neighbourhood, and map_vendor_product.\n\n"
        "RULES (load-bearing — never violate):\n"
        "  1. You do NOT decide the final mapping status. The deterministic "
        "     matcher (which runs after you) applies a 0.80 confidence floor "
        "     and a set of validations. Your role is to EXPLORE and "
        "     RECOMMEND, not to gate.\n"
        "  2. You MUST NOT bypass the scope gate. If find_similar_products "
        "     returns no candidates, recommend NEEDS_REVIEW and stop.\n"
        "  3. Use the smallest number of tool calls that produce a confident "
        "     recommendation. Three or four calls is typical: similarity "
        "     search, inspect the top node, optionally inspect its "
        "     neighbourhood, then call map_vendor_product to commit.\n"
        "  4. End your turn with a one-paragraph rationale citing the "
        "     winning node's IRI, the similarity score, and any validations "
        "     you considered."
    )

    def __init__(
        self,
        *,
        model_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self._model_id = (
            model_id
            or os.getenv("SCUDO_BEDROCK_MODEL_ID")
            or DEFAULT_BEDROCK_MODEL_ID
        )
        self._region = (
            region
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "eu-west-2"
        )
        self._agent: Any = None  # lazy-initialised on first run()

    def _build_agent(self) -> Any:
        """Lazy-import strands_agents and construct the underlying agent.

        Kept in a method so the import error (when strands_agents is not
        yet installed in the sandbox env) only fires when the bedrock
        backend is actually selected — not on every package import.
        """
        try:
            from strands import Agent  # type: ignore
            from strands.models import BedrockModel  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "strands_agents is required for SCUDO_AGENT_BACKEND=bedrock. "
                "Install with: pip install strands-agents>=0.1"
            ) from e

        model = BedrockModel(
            model_id=self._model_id,
            region_name=self._region,
        )
        return Agent(
            model=model,
            system_prompt=self.SYSTEM_PROMPT,
            tools=_strands_tools_for_mapping(),
        )

    def run(
        self,
        ref: VendorProductRef,
        *,
        max_candidates: int = 5,
    ) -> Iterator[AgentEvent]:
        if self._agent is None:
            self._agent = self._build_agent()

        yield AgentEvent(
            type="start",
            payload={
                "vendor": ref.vendor,
                "product_id": ref.product_id,
                "product_name": ref.name,
                "agent_backend": self.NAME,
                "model_id": self._model_id,
                "region": self._region,
            },
        )

        prompt = (
            f"Map this vendor product to a CDAO taxonomy node:\n"
            f"  vendor       = {ref.vendor}\n"
            f"  product_id   = {ref.product_id}\n"
            f"  name         = {ref.name}\n"
            f"  description  = {ref.description}\n\n"
            "Use the tools, recommend a node, then end with one paragraph "
            "of rationale."
        )

        # Strands' Agent.stream_async / stream surface is the right home
        # for forwarding tool_use + reasoning events into our AgentEvent
        # stream. Implementation lives behind this method so it can be
        # filled in when Bedrock access lands and the SDK is pinned; the
        # ScriptedMappingAgent covers every test until then.
        for event in _iter_strands_events(self._agent, prompt):
            yield event

        # Authoritative final step — matcher runs regardless of what the
        # LLM recommended. Same contract as the scripted backend.
        result = map_vendor_product(ref)
        yield AgentEvent(
            type="final_result",
            payload={"mapping": _mapping_result_dict(result)},
        )
        yield AgentEvent(type="done")


# ──────────────────────────────────────────────────────────────────────────
# Strands integration helpers (filled in when Bedrock access lands)
# ──────────────────────────────────────────────────────────────────────────
def _strands_tools_for_mapping() -> list[Any]:
    """Return the four MCP-equivalent Strands tools for the live agent.

    Each tool calls into the same package functions the MCP server uses,
    so the agent's tool surface and the MCP client's tool surface are
    behaviourally identical. Lazy-imported with strands itself.
    """
    from strands import tool  # type: ignore

    @tool
    def find_similar_products(
        vendor: str, product_id: str, name: str = "", max_results: int = 10,
    ) -> str:
        """Return top-N candidate CDAO nodes for a vendor product."""
        ref = VendorProductRef(
            vendor=vendor, product_id=product_id, name=name,
        )
        cands = get_store().find_similar_products(ref, max_results=max_results)
        return json.dumps({
            "count": len(cands),
            "candidates": [_candidate_dict(c) for c in cands],
        })

    @tool
    def get_taxonomy_node(node_iri: str) -> str:
        """Return one CDAO node with its parent and children."""
        node = get_store().get_taxonomy_node(node_iri)
        return json.dumps(_node_dict(node))

    @tool
    def get_ontology_neighbourhood(
        node_iri: str, max_depth: int = 2, max_nodes: int = 50,
    ) -> str:
        """Return a bounded subgraph around a node."""
        sg = get_store().get_ontology_neighbourhood(
            node_iri, max_depth=max_depth, max_nodes=max_nodes,
        )
        return json.dumps(_subgraph_dict(sg))

    @tool
    def map_vendor_product_tool(
        vendor: str, product_id: str,
        name: str = "", description: str = "",
    ) -> str:
        """Apply scope gate + matcher + floor; return authoritative MappingResult."""
        ref = VendorProductRef(
            vendor=vendor, product_id=product_id,
            name=name, description=description,
        )
        return json.dumps(_mapping_result_dict(map_vendor_product(ref)))

    return [
        find_similar_products,
        get_taxonomy_node,
        get_ontology_neighbourhood,
        map_vendor_product_tool,
    ]


def _iter_strands_events(agent: Any, prompt: str) -> Iterator[AgentEvent]:
    """Run the Strands agent and translate its stream into AgentEvent.

    This is the wire bridge between Strands' native event surface and our
    SSE contract. The structure below maps Strands' tool_use / reasoning /
    final_response events onto our five AgentEvent types. Tuned when the
    SDK pin is finalised in the sandbox.
    """
    try:
        for event in agent.stream(prompt):  # type: ignore[attr-defined]
            kind = getattr(event, "kind", None) or event.get("kind")
            if kind == "tool_use":
                yield AgentEvent(
                    type="tool_call",
                    payload={
                        "tool": event["name"],
                        "args": event.get("input", {}),
                    },
                )
            elif kind == "tool_result":
                yield AgentEvent(
                    type="tool_result",
                    payload={
                        "tool": event["name"],
                        "result": event.get("output"),
                    },
                )
            elif kind in ("text", "message", "thinking"):
                content = event.get("text") or event.get("content") or ""
                if content:
                    yield AgentEvent(
                        type="agent_message",
                        payload={"content": content},
                    )
    except Exception as e:  # noqa: BLE001
        yield AgentEvent(
            type="error",
            payload={
                "error": f"{type(e).__name__}: {e}",
                "hint": (
                    "Bedrock invoke failed. Check IAM (bedrock:InvokeModel*), "
                    "model access in the Bedrock console, region "
                    "(SCUDO_BEDROCK_MODEL_ID + AWS_REGION), and that "
                    "strands-agents is installed."
                ),
            },
        )


# ──────────────────────────────────────────────────────────────────────────
# Factory — the single swap point
# ──────────────────────────────────────────────────────────────────────────
def get_agent() -> Any:
    """Return the configured mapping agent.

    SCUDO_AGENT_BACKEND selects between "scripted" (default) and "bedrock".
    The frontend / route layer is identical for both — they consume the
    same AgentEvent stream.
    """
    backend = (os.getenv("SCUDO_AGENT_BACKEND") or "scripted").strip().lower()
    if backend == "bedrock":
        return BedrockMappingAgent()
    return ScriptedMappingAgent()


# ──────────────────────────────────────────────────────────────────────────
# Serialisation helpers — kept here, not on the models, so the agent's
# wire format is self-contained and the Pydantic models stay clean.
# ──────────────────────────────────────────────────────────────────────────
def _candidate_dict(c: Candidate) -> dict:
    return {
        "node": _node_dict(c.node),
        "similarity": c.similarity,
    }


def _node_dict(n: Optional[TaxonomyNode]) -> Optional[dict]:
    if n is None:
        return None
    return {
        "iri": n.iri,
        "label": n.label,
        "parent_iri": n.parent_iri,
        "children_iris": list(n.children_iris),
    }


def _subgraph_dict(sg: Subgraph) -> dict:
    return {
        "root_iri": sg.root_iri,
        "nodes": [_node_dict(n) for n in sg.nodes],
        "edges": [list(e) for e in sg.edges],
    }


def _mapping_result_dict(r: MappingResult) -> dict:
    # Pydantic's model_dump already does the right thing — kept as a small
    # wrapper so callers don't import pydantic just for this.
    return r.model_dump(mode="json")


def _summary_for(result: MappingResult) -> str:
    """One-line agent narration of what the matcher decided."""
    if result.status == MappingStatus.OUT_OF_SCOPE:
        return f"Out of scope: {result.rationale}"
    if result.status == MappingStatus.AUTO_MAPPED:
        return (
            f"Recommend AUTO_MAPPED to {result.mapped_node_label!r} "
            f"({result.mapped_node_iri}) — confidence {result.confidence:.2f}, "
            f"all required validations passed."
        )
    if result.status in (MappingStatus.APPROVED, MappingStatus.OVERRIDDEN):
        return (
            f"Precedent reuse: {result.status.value.upper()} mapping to "
            f"{result.mapped_node_label!r} from a prior HITL decision."
        )
    return (
        f"Recommend NEEDS_REVIEW — {result.rationale} HITL queue is the "
        f"correct next step."
    )
