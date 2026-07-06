"""Strands→AgentEvent bridge coalescing (reasoning-panel duplication fix).

Strands streams one event per token delta and one current_tool_use snapshot
per chunk while a tool call is being assembled, then re-emits the assembled
message at each cycle stop. Forwarding those 1:1 flooded the SSE consumers:
the dashboard rendered every delta as its own transcript turn and each tool
call up to ~30 times. The bridge must instead emit ONE agent_message per
assistant turn and ONE tool_call per toolUseId, and must not re-emit the
assembled message when its text already streamed as deltas.

Shapes below mirror strands-agents 1.44.0 (strands/types/_events.py):
  TextStreamEvent      {"data": <token>, "delta": {...}}
  ToolUseStreamEvent   {"type": "tool_use_stream", "delta": {...},
                        "current_tool_use": {toolUseId, name, input}}
  ToolResultEvent      {"type": "tool_result", "tool_result": {toolUseId,
                        status, content: [{"text"|"json": ...}]}}
  ModelMessageEvent    {"message": {role, content}}
(ModelStopReason has is_callback_event=False in the SDK, so stream_async
never surfaces it — the assembled message arrives as ModelMessageEvent.)
The legacy dict shapes ({"kind": "tool_use", ...}) stay supported.
"""

from __future__ import annotations

from scudo_mapping_mcp.agent import _coalesce_strands_events


def _events(stream):
    return list(_coalesce_strands_events(stream))


def _of_type(events, type_):
    return [e for e in events if e.type == type_]


# ── text deltas ───────────────────────────────────────────────────────────


def test_text_deltas_coalesce_to_single_agent_message():
    stream = [
        {"data": "I'll start ", "delta": {"text": "I'll start "}},
        {"data": "by finding similar C", "delta": {"text": "by finding similar C"}},
        {"data": "DAO candidates.", "delta": {"text": "DAO candidates."}},
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert len(messages) == 1, [e.type for e in out]
    assert messages[0].payload["content"] == (
        "I'll start by finding similar CDAO candidates."
    )


def test_assembled_message_does_not_duplicate_streamed_deltas():
    stream = [
        {"data": "Recommend Equity Prices.", "delta": {"text": "..."}},
        {
            "message": {
                "role": "assistant",
                "content": [{"text": "Recommend Equity Prices."}],
            }
        },
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert len(messages) == 1
    assert messages[0].payload["content"] == "Recommend Equity Prices."


def test_message_without_deltas_still_emits():
    # Non-streaming fallback path: agent(prompt) -> [{"message": ...}]
    stream = [
        {
            "message": {
                "role": "assistant",
                "content": [{"text": "Final rationale paragraph."}],
            }
        }
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert len(messages) == 1
    assert messages[0].payload["content"] == "Final rationale paragraph."


def test_assembled_message_supersedes_differing_deltas():
    """Guardrail redaction rewrites the final assistant message AFTER its
    text streamed as deltas. The assembled text is authoritative — the raw
    deltas must not leak (Codex finding 1)."""
    stream = [
        {"data": "unredacted secret", "delta": {"text": "unredacted secret"}},
        {
            "message": {
                "role": "assistant",
                "content": [{"text": "[content redacted]"}],
            }
        },
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert len(messages) == 1
    assert messages[0].payload["content"] == "[content redacted]"
    assert all("secret" not in str(e.payload) for e in out)


def test_no_delta_message_after_tool_result_still_emits():
    """A later assistant message with NO deltas must not be swallowed by
    state left over from an earlier streamed turn (Codex finding 2)."""
    stream = [
        {"data": "First turn.", "delta": {}},
        {
            "type": "tool_result",
            "tool_result": {
                "toolUseId": "t1",
                "status": "success",
                "content": [{"text": "ok"}],
            },
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"text": "Second turn without deltas."}],
            }
        },
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert [m.payload["content"] for m in messages] == [
        "First turn.",
        "Second turn without deltas.",
    ]


def test_sdk_double_emit_of_same_message_drops_but_new_turn_survives():
    """Strands re-emits the assembled message twice per cycle stop
    (ModelStopReason then ModelMessageEvent): the byte-identical no-delta
    re-emit must drop, while an identical text in a LATER turn (after a
    tool result) is a genuine new turn and must emit."""
    stream = [
        {"data": "Checking.", "delta": {}},
        {"message": {"role": "assistant", "content": [{"text": "Checking."}]}},
        {"message": {"role": "assistant", "content": [{"text": "Checking."}]}},
        {
            "type": "tool_result",
            "tool_result": {
                "toolUseId": "t1",
                "status": "success",
                "content": [{"text": "ok"}],
            },
        },
        {"message": {"role": "assistant", "content": [{"text": "Checking."}]}},
    ]
    out = _events(stream)
    messages = _of_type(out, "agent_message")
    assert [m.payload["content"] for m in messages] == ["Checking.", "Checking."]


# ── tool_call dedupe ──────────────────────────────────────────────────────


def test_tool_use_snapshots_dedupe_to_single_tool_call():
    # The SDK mutates ONE snapshot dict in place across chunks; by translation
    # time (the stream is collected into a list first) every snapshot shows
    # the fully-accumulated input.
    snapshot = {
        "toolUseId": "t1",
        "name": "get_taxonomy_node",
        "input": '{"node_iri": "jpmorgan:data:cdao:EquityPrices"}',
    }
    stream = [
        {"type": "tool_use_stream", "delta": {}, "current_tool_use": snapshot}
        for _ in range(29)
    ]
    out = _events(stream)
    calls = _of_type(out, "tool_call")
    assert len(calls) == 1, f"expected 1 tool_call, got {len(calls)}"
    assert calls[0].payload["tool"] == "get_taxonomy_node"
    assert calls[0].payload["args"] == {"node_iri": "jpmorgan:data:cdao:EquityPrices"}


def test_distinct_tool_use_ids_emit_distinct_tool_calls():
    s1 = {"toolUseId": "t1", "name": "find_similar_products", "input": "{}"}
    s2 = {"toolUseId": "t2", "name": "get_taxonomy_node", "input": "{}"}
    stream = [
        {"type": "tool_use_stream", "current_tool_use": s1},
        {"type": "tool_use_stream", "current_tool_use": s1},
        {"type": "tool_use_stream", "current_tool_use": s2},
        {"type": "tool_use_stream", "current_tool_use": s2},
    ]
    calls = _of_type(_events(stream), "tool_call")
    assert [c.payload["tool"] for c in calls] == [
        "find_similar_products",
        "get_taxonomy_node",
    ]


def test_legacy_kind_tool_use_still_emits():
    stream = [{"kind": "tool_use", "name": "find_similar_products", "input": {"a": 1}}]
    calls = _of_type(_events(stream), "tool_call")
    assert len(calls) == 1
    assert calls[0].payload["tool"] == "find_similar_products"
    assert calls[0].payload["args"] == {"a": 1}


def test_tool_use_snapshot_does_not_also_emit_agent_message():
    # Regression: the old bridge's current_tool_use branch fell through into
    # the content branch instead of returning.
    snapshot = {"toolUseId": "t1", "name": "get_taxonomy_node", "input": "{}"}
    stream = [{"type": "tool_use_stream", "current_tool_use": snapshot}]
    out = _events(stream)
    assert _of_type(out, "agent_message") == []


# ── tool_result ───────────────────────────────────────────────────────────


def test_tool_result_new_shape_resolves_tool_name_from_snapshot():
    snapshot = {"toolUseId": "t1", "name": "get_taxonomy_node", "input": "{}"}
    stream = [
        {"type": "tool_use_stream", "current_tool_use": snapshot},
        {
            "type": "tool_result",
            "tool_result": {
                "toolUseId": "t1",
                "status": "success",
                "content": [{"text": '{"iri": "jpmorgan:data:cdao:EquityPrices"}'}],
            },
        },
    ]
    results = _of_type(_events(stream), "tool_result")
    assert len(results) == 1
    assert results[0].payload["tool"] == "get_taxonomy_node"
    assert "EquityPrices" in results[0].payload["result"]


def test_tool_result_legacy_shape_passthrough():
    stream = [
        {"kind": "tool_result", "name": "find_similar_products", "output": "3 hits"}
    ]
    results = _of_type(_events(stream), "tool_result")
    assert len(results) == 1
    assert results[0].payload["tool"] == "find_similar_products"
    assert results[0].payload["result"] == "3 hits"


# ── full multi-cycle turn: ordering + no duplication ─────────────────────


def test_multi_cycle_run_yields_ordered_deduped_events():
    """Cycle 1: reasoning deltas + a tool call + cycle-stop message (which
    re-carries the reasoning text). Then the tool result. Cycle 2: more
    deltas + final stop message. The bridge must yield exactly:
    agent_message, tool_call, tool_result, agent_message — in order."""
    snapshot = {
        "toolUseId": "t9",
        "name": "map_vendor_product_tool",
        "input": '{"vendor": "lseg"}',
    }
    stream = [
        {"data": "Let me map ", "delta": {}},
        {"data": "this product.", "delta": {}},
        {"type": "tool_use_stream", "current_tool_use": snapshot},
        {"type": "tool_use_stream", "current_tool_use": snapshot},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Let me map this product."},
                    {"toolUse": {"toolUseId": "t9", "name": "map_vendor_product_tool"}},
                ],
            }
        },
        {
            "type": "tool_result",
            "tool_result": {
                "toolUseId": "t9",
                "status": "success",
                "content": [{"text": '{"status": "needs_review"}'}],
            },
        },
        {"data": "Confidence is 0.62, ", "delta": {}},
        {"data": "so this needs review.", "delta": {}},
        {
            "message": {
                "role": "assistant",
                "content": [{"text": "Confidence is 0.62, so this needs review."}],
            }
        },
    ]
    out = _events(stream)
    assert [e.type for e in out] == [
        "agent_message",
        "tool_call",
        "tool_result",
        "agent_message",
    ], [e.type for e in out]
    assert out[0].payload["content"] == "Let me map this product."
    assert out[1].payload["tool"] == "map_vendor_product_tool"
    assert out[3].payload["content"] == "Confidence is 0.62, so this needs review."


# ── noise events stay dropped ─────────────────────────────────────────────


def test_reasoning_and_lifecycle_events_are_dropped():
    stream = [
        {"reasoningText": "internal chain", "delta": {}, "reasoning": True},
        {"init_event_loop": True},
        {"start_event_loop": True},
    ]
    assert _events(stream) == []
