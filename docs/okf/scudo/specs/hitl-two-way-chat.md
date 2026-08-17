---
type: Spec
title: HITL Two-Way Chat Spec
description: Human-in-the-loop two-way chat design for reviewer adjudication.
tags:
- spec
- hitl
staleness: current
timestamp: '2026-08-17T09:02:03Z'
---

# Spec — HITL two-way chat (ask the agent)

**Status:** design only (not built). The reasoning transcript + decision UI
(approve/override/reject on the `hitl-review` node) shipped; this spec covers the
remaining piece: letting a reviewer **ask the agent questions** / give guidance,
which needs new backend capability.

## Why this is a backend build, not frontend wiring

The agent today is **one-shot and stateless**. Both `ScriptedMappingAgent` and
`BedrockMappingAgent` (`backend/scudo_mapping_mcp/agent.py`) expose
`run(ref) -> Iterator[AgentEvent]`: read product → run a fixed tool sequence →
`final_result` → `done`. There is **no** message history, no session, and no
endpoint that accepts a follow-up message. So two-way chat requires:

1. a conversational endpoint,
2. session/context state across turns,
3. a multi-turn agent loop (Bedrock) that can take free-form reviewer input.

## Proposed design

### Endpoint
`POST /api/mapping/agent/chat` (SSE, same framing as `/agent/run`).
Body: `{ session_id, vendor, product_id, message }`.
Streams the same event vocabulary (`agent_message` / `tool_call` /
`tool_result` / `final_result` / `error` / `done`) so the existing frontend
transcript renders it with no new event handling.

### Session state
- A `session_id` (UUID minted by the client on first turn) keys a
  conversation: the prior turns + the mapping context (the `ref`, the candidate
  set, the last `MappingResult`).
- Store: start with an **in-process TTL dict** (PoC; single-instance, like the
  current working set) keyed by `session_id`; note explicitly that multi-worker
  / multi-task scaling needs a shared store (Redis/Dynamo) — same caveat as the
  working set (Codex flagged that unsynchronized in-memory state).
- Bound: max turns per session + TTL eviction (don't grow unbounded — mirrors
  the upload row/byte caps).

### Agent loop
- Extend `BedrockMappingAgent` with a `chat(session, message) -> Iterator[...]`
  that appends the user message to the conversation and runs a bounded Bedrock
  multi-turn loop with the SAME MCP tools (`find_similar_products`,
  `get_taxonomy_node`, `get_ontology_neighbourhood`, `map_vendor_product`).
- INVARIANT (must hold): the chat can **explain** and **re-retrieve/re-rank**,
  but a mapping it commits still goes through the deterministic
  `map_vendor_product` gate — the LLM cannot bypass the cost-ladder/validations
  or auto-confirm a precedent. Chat guidance influences retrieval, not the gate.
- Scripted fallback (`SCUDO_AGENT_BACKEND=scripted`): a canned Q&A over the
  last result so the feature demos with no Bedrock.

### Constraints / risks (carry over from the review)
- **Auth:** same gate as the rest of `/api/*` — the chat must not be a way to
  write precedents under a forged identity. Blocked on the same
  strip+inject auth gate (`infra/AUTH_GATE_SPEC_strip_inject.md`).
- **SSE / ALB idle timeout:** a multi-turn Bedrock chat can be slow → needs the
  heartbeat/keepalive that long `/agent/run` calls already want. Address
  together.
- **Cost:** multi-turn Bedrock per reviewer message — gate behind the same
  `SCUDO_AGENT_BACKEND=bedrock` flag; scripted otherwise.

### Frontend (small, once the endpoint exists)
- Add a text input to `ReasoningPanel` (currently read-only) that POSTs to
  `/agent/chat` and appends the streamed turns to the same transcript.
- Reuse the existing `postSse` async-iterator client (`src/api/mapping.ts`) and
  `transcriptTurn` mapping — both already handle the event vocabulary.

## Acceptance (when built)
- A reviewer types a question on a NEEDS_REVIEW result and gets a streamed,
  reasoned answer in the transcript.
- Re-running with guidance changes retrieval but any committed mapping still
  passes the deterministic gate (test: chat cannot force AUTO_MAPPED below floor).
- Session bounded (max turns + TTL); scripted backend works with no Bedrock.
