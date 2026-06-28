"""SSE ingest cancellation contract (Codex A4).

ingest_bytes calls on_stage between every pipeline stage, so a callback that
raises (the route does this when the client disconnects) unwinds the pipeline
promptly instead of finishing the parse/upsert off a dead request.
"""

from __future__ import annotations

import os

import pytest


def _ingest():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp.ingest import ingest_bytes

    return ingest_bytes


def test_cancel_via_on_stage_stops_pipeline():
    """If on_stage raises (cancel signalled), ingest unwinds and does NOT reach
    later stages — proving cancellation is cooperative and prompt."""
    seen = []

    class _Cancel(Exception):
        pass

    def on_stage(stage, detail):
        seen.append(stage)
        if stage == "parse":  # simulate client-gone after the first couple stages
            raise _Cancel()

    csv = b"product_id,name\nA-1,Alpha\nB-2,Bravo\n"
    with pytest.raises(_Cancel):
        _ingest()("LSEG", "t.csv", csv, upsert=True, on_stage=on_stage)

    # Stopped at parse — never emitted validate/sink (no further work done).
    assert seen == ["received", "parse"], seen
    assert "validate" not in seen and "sink" not in seen


def test_stream_route_worker_is_not_daemon():
    """Codex A4: the SSE worker thread must not be daemon (so it can be joined
    on generator close, not killed mid-mutation). Assert via source."""
    import inspect

    from routes import mapping

    src = inspect.getsource(mapping.ingest_vendor_file_stream)
    assert "daemon=False" in src, "ingest stream worker should be non-daemon"
    assert "cancel" in src and "GeneratorExit" in src, (
        "needs cancel + GeneratorExit handling"
    )
    assert "maxsize=" in src, "queue should be bounded for backpressure"
