"""Zone 4 — Agentic Layer.

Orchestrator → specialist → verifier → gate-and-decide, with auto-approve /
HITL / reject routing. ``orchestrator`` is the deterministic driver (route →
bundle → verifier → publish gate); ``agents`` builds the Strands specialist +
verifier; ``agent`` is the M9 agent runner behind the demo ``/agent/run`` SSE
stream; ``hooks`` enforces invariants as Strands hook providers; ``batch`` runs
the self-verifying batch matcher over N products; ``mcp_host`` is the transport
between agent and MCP tools.

The specialist+verifier run on **Bedrock (Opus 4.8) by default**; the deliberate
**Azure OpenAI shim** is switched on per-deploy (``SCUDO_AGENT_PROVIDER_DEFAULT
=azure``) or per-request (``agent_provider``). Both providers implement the same
specialist+verifier pair.

AWS entrypoint: ``scudo.lambda_handler.handler`` (mapping Lambda) drives this
zone; ``scudo_mapping_mcp.mcp_host`` hosts the tools.

Aurora/graph-store role: reads precedent + promoted rules from Aurora agent
memory (CONSULT) and writes verified precedent back (DISTILL); the verdict is
HMAC-signed before it crosses into Zone 5. Reasoning drives Zone 3 retrieval but
does not itself own a store.

These are re-exports — the modules physically live in ``scudo`` /
``scudo_mapping_mcp``. See ``ZONES.md``.
"""

from scudo import (
    agents,
    batch,
    hooks,
    orchestrator,
    prompts,
    tools,
)
from scudo_mapping_mcp import agent, mcp_host

__all__ = [
    "orchestrator",
    "agents",
    "tools",
    "prompts",
    "agent",
    "mcp_host",
    "hooks",
    "batch",
]
