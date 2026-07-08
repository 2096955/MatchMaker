"""Strands stream → AgentEvent coalescing (finding 2: reasoning-panel dup).

The live "Agent reasoning" panel rendered garbage because the bridge was
stateless and emitted one AgentEvent per *stream chunk*:

  - every ``TextStreamEvent`` ``data`` delta became its own agent_message
    ("Agent: DAO cand", "Agent: idate", ...) AND the completed
    ``ModelMessageEvent`` re-emitted the full text one more time; and
  - every ``ToolUseStreamEvent`` ``current_tool_use`` chunk became its own
    tool_call ("Calling get_taxonomy_node" x29) with no dedupe by
    toolUseId.

These fixtures use the REAL Strands 1.44 event shapes (verified against
strands/types/_events.py):

  TextStreamEvent      -> {"data": <text>, "delta": {...}}
  ToolUseStreamEvent   -> {"type": "tool_use_stream", "delta": {...},
                           "current_tool_use": {"toolUseId","name","input"}}
  ModelMessageEvent    -> {"message": {"role":"assistant",
                           "content":[{"text":...},{"toolUse":{...}}]}}
  ToolResultMessageEvent -> {"message": {"role":"user",
                           "content":[{"toolResult":{"toolUseId","content","status"}}]}}
  AgentResultEvent     -> {"result": <AgentResult>}

The coalesced contract: ONE agent_message per assistant text turn, ONE
tool_call per distinct tool use (full input from the completed message),
ONE tool_result per result, no per-chunk spam, no delta/message dup.

This file goes THROUGH the bridge entry point (_iter_strands_events with a
fake ``.stream()`` agent) — the per-event unit coverage lives in
scudo_mapping_mcp/tests/test_strands_event_coalescing.py.

Run PER-FILE (mapping suite has known store-cache cross-file pollution):

    PYTHONPATH=. python3 -m pytest scudo/tests/test_strands_event_coalescing.py -q
"""

from __future__ import annotations

from scudo_mapping_mcp import agent as agent_mod


class _FakeStreamAgent:
    """Agent whose ``.stream(prompt)`` replays a canned Strands event list.

    ``_strands_stream`` prefers ``.stream`` when present, so this exercises
    the real bridge path (_strands_stream -> coalescer) end to end.
    """

    def __init__(self, events):
        self._events = events

    def stream(self, prompt):  # noqa: ARG002 - prompt unused in replay
        return iter(self._events)


def _text_delta(text: str) -> dict:
    return {"data": text, "delta": {"text": text}}


def _tool_use_snapshots(
    tool_use_id: str, name: str, final_input: dict, n: int
) -> list[dict]:
    """N ToolUseStreamEvents that FAITHFULLY model the SDK.

    Verified against strands 1.44 event_loop/streaming.py:
      - handle_content_block_delta (line 224) appends the input JSON string
        IN PLACE onto ONE ``state["current_tool_use"]`` dict and wraps a
        *reference* to it in each ToolUseStreamEvent;
      - handle_content_block_stop (line 288) then ``json.loads``-es that
        string into a dict, still in place on the same object.

    Because ``_strands_stream`` collects the whole stream into a list before
    translation, every snapshot in the list is the SAME object and by that
    time its ``input`` is the final PARSED dict. So all N events here share
    one ``current_tool_use`` dict carrying ``final_input``.
    """
    shared = {"toolUseId": tool_use_id, "name": name, "input": final_input}
    return [
        {
            "type": "tool_use_stream",
            "delta": {"toolUse": {}},
            "current_tool_use": shared,
        }
        for _ in range(n)
    ]


def _assistant_message(text: str, tool_use: dict | None = None) -> dict:
    content: list[dict] = [{"text": text}]
    if tool_use is not None:
        content.append({"toolUse": tool_use})
    return {"message": {"role": "assistant", "content": content}}


def _tool_result_message(tool_use_id: str, result_text: str) -> dict:
    return {
        "message": {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_text}],
                        "status": "success",
                    }
                }
            ],
        }
    }


def _bridge(events) -> list:
    return list(agent_mod._iter_strands_events(_FakeStreamAgent(events), "prompt"))


def _types(events) -> list[str]:
    return [e.type for e in events]


def test_repeated_current_tool_use_chunks_emit_one_tool_call():
    """29 tool_use_stream snapshots for one toolUseId -> exactly ONE
    tool_call, carrying the FINAL parsed input (the shared snapshot dict is
    json.loads-ed in place at block-stop, so by translation time it holds
    the full args)."""
    final_input = {"node_iri": "jpmorgan:data:cdao:EquityPrices"}
    tool_use = {"toolUseId": "t1", "name": "get_taxonomy_node", "input": final_input}
    events = [
        # 29 snapshots of the one in-place-mutated tool-use dict (the x29 spam)
        *_tool_use_snapshots("t1", "get_taxonomy_node", final_input, 29),
        _assistant_message("I'll fetch the node.", tool_use=tool_use),
    ]
    out = _bridge(events)

    tool_calls = [e for e in out if e.type == "tool_call"]
    assert len(tool_calls) == 1, (
        f"expected 1 tool_call, got {len(tool_calls)}: {_types(out)}"
    )
    assert tool_calls[0].payload["tool"] == "get_taxonomy_node"
    # full, parsed input from the message — not a partial JSON string chunk
    assert tool_calls[0].payload["args"] == {
        "node_iri": "jpmorgan:data:cdao:EquityPrices"
    }


def test_text_deltas_plus_final_message_emit_one_agent_message():
    """Many text deltas + the completed message re-carrying the same text ->
    exactly ONE agent_message with the full assembled text."""
    events = [
        _text_delta("Equity "),
        _text_delta("Prices "),
        _text_delta("looks "),
        _text_delta("right."),
        _assistant_message("Equity Prices looks right."),
    ]
    out = _bridge(events)

    msgs = [e for e in out if e.type == "agent_message"]
    assert len(msgs) == 1, (
        f"expected 1 agent_message, got {len(msgs)}: {[m.payload for m in msgs]}"
    )
    assert msgs[0].payload["content"] == "Equity Prices looks right."


def test_tool_result_message_emits_one_tool_result_with_resolved_name():
    """A toolResult block carries only toolUseId; the tool NAME is resolved
    from the preceding toolUse so the panel can label it."""
    tool_use = {
        "toolUseId": "t1",
        "name": "find_similar_products",
        "input": {"vendor": "LSEG"},
    }
    events = [
        _assistant_message("Searching.", tool_use=tool_use),
        _tool_result_message("t1", '{"count": 3}'),
    ]
    out = _bridge(events)

    results = [e for e in out if e.type == "tool_result"]
    assert len(results) == 1, (
        f"expected 1 tool_result, got {len(results)}: {_types(out)}"
    )
    assert results[0].payload["tool"] == "find_similar_products"


def test_full_realistic_stream_has_no_duplicates():
    """The end-to-end sequence the reviewer observed: text deltas, x15
    tool_use chunks, completed assistant message, tool-result message, more
    deltas, final rationale message, terminal AgentResult. The coalesced
    output must have exactly: 2 agent_message (2 assistant text turns), 1
    tool_call (1 distinct tool use), 1 tool_result, and no consecutive
    duplicate agent_message / tool_call."""
    final_input = {"vendor": "LSEG"}
    tool_use = {
        "toolUseId": "t1",
        "name": "find_similar_products",
        "input": final_input,
    }
    events = [
        # turn 1: reasoning deltas + tool-call assembly
        _text_delta("Let me "),
        _text_delta("search."),
        *_tool_use_snapshots("t1", "find_similar_products", final_input, 15),
        _assistant_message("Let me search.", tool_use=tool_use),
        # tool executes
        _tool_result_message("t1", '{"count": 3}'),
        # turn 2: final rationale deltas + completed message
        _text_delta("Equity "),
        _text_delta("Prices."),
        _assistant_message("Equity Prices."),
        # terminal result (must NOT re-emit the final message)
        {"result": object()},
    ]
    out = _bridge(events)
    types = _types(out)

    assert types.count("agent_message") == 2, types
    assert types.count("tool_call") == 1, types
    assert types.count("tool_result") == 1, types

    # no two consecutive identical agent_message / tool_call payloads
    for a, b in zip(out, out[1:]):
        if a.type == b.type in {"agent_message", "tool_call"}:
            assert a.payload != b.payload, (
                f"consecutive duplicate {a.type}: {a.payload}"
            )


def test_deltas_only_stream_without_message_flushes_once():
    """SDK/edge case: text deltas and tool chunks with NO completed message
    event. The accumulated deltas must still flush as ONE agent_message and
    ONE tool_call at end of stream (not be silently dropped)."""
    events = [
        _text_delta("Partial "),
        _text_delta("thought."),
        *_tool_use_snapshots("t9", "get_taxonomy_node", {"node_iri": "x"}, 5),
    ]
    out = _bridge(events)
    types = _types(out)

    assert types.count("agent_message") == 1, types
    assert types.count("tool_call") == 1, types
    msgs = [e for e in out if e.type == "agent_message"]
    assert msgs[0].payload["content"] == "Partial thought."


def test_agent_result_event_does_not_emit_agent_message():
    """A terminal AgentResultEvent ({"result": ...}) must not be mistaken for
    reasoning text — the authoritative final_result is emitted by the run()
    method from the deterministic matcher, not from the LLM's result."""
    events = [
        _assistant_message("Done."),
        {"result": object()},
    ]
    out = _bridge(events)
    assert _types(out) == ["agent_message"], _types(out)
