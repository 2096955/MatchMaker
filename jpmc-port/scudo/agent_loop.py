"""Multi-turn agentic loop — tool use + reasoning → structured final output.

The Mapping Specialist and Verifier are the load-bearing agents. They must
run as true agent loops (not single-shot prompt→JSON). Token budget is
intentionally unbounded at this layer; Bedrock max_tokens is set high on
the model config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Type

from pydantic import BaseModel

log = logging.getLogger("scudo.agent_loop")

# Soft turn ceiling only — not a token budget. Raises if the agent never settles.
DEFAULT_MAX_TURNS = 64


@dataclass
class AgentLoopResult:
    output: Any
    turns: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_trace: list[str] = field(default_factory=list)


def run_agentic_structured(
    agent: Any,
    prompt: str,
    output_model: Type[BaseModel],
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> AgentLoopResult:
    """Run an agentic loop that terminates in a validated ``output_model``.

    Protocol (in order):
      1. Prefer ``agent.agentic_structured(output_model, prompt)`` if present
         (deterministic fakes / custom shims).
      2. Strands: ``agent(prompt, structured_output_model=output_model)`` which
         runs the native tool-calling loop then coerces structured output.
      3. Fallback: ``agent.structured_output(output_model, prompt)``.
    """
    # 1) Explicit agentic surface (local fakes record tool_trace here)
    if hasattr(agent, "agentic_structured"):
        raw = agent.agentic_structured(output_model, prompt, max_turns=max_turns)
        if isinstance(raw, AgentLoopResult):
            return raw
        return AgentLoopResult(output=raw, turns=getattr(agent, "last_turns", 1))

    # 2) Strands native tool loop + structured output
    try:
        result = agent(prompt, structured_output_model=output_model)
        out = getattr(result, "structured_output", result)
        if isinstance(out, output_model):
            validated = out
        elif isinstance(out, dict):
            validated = output_model.model_validate(out)
        elif hasattr(out, "model_dump"):
            validated = output_model.model_validate(out.model_dump())
        else:
            validated = output_model.model_validate(out)
        tool_calls = _extract_tool_calls(result)
        reasoning = _extract_reasoning(result)
        return AgentLoopResult(
            output=validated,
            turns=max(1, len(tool_calls) + 1),
            tool_calls=tool_calls,
            reasoning_trace=reasoning,
        )
    except TypeError:
        pass
    except Exception:
        log.exception(
            "agentic structured call failed; trying structured_output fallback"
        )

    # 3) Legacy single-shot
    if hasattr(agent, "structured_output"):
        out = agent.structured_output(output_model, prompt)
        return AgentLoopResult(output=out, turns=1)

    raise RuntimeError(
        f"agent {type(agent).__name__} has no agentic/structured call surface"
    )


def _extract_tool_calls(result: Any) -> list[dict]:
    calls: list[dict] = []
    for attr in ("tool_calls", "metrics", "message", "messages"):
        val = getattr(result, attr, None)
        if val is None:
            continue
        if isinstance(val, list):
            for item in val:
                name = getattr(item, "name", None) or (
                    item.get("name") if isinstance(item, dict) else None
                )
                if name:
                    calls.append({"name": name})
        elif isinstance(val, dict) and "tool_names" in val:
            for name in val.get("tool_names") or []:
                calls.append({"name": name})
    return calls


def _extract_reasoning(result: Any) -> list[str]:
    texts: list[str] = []
    for attr in ("reasoning", "thinking", "content"):
        val = getattr(result, attr, None)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip()[:4000])
    return texts
