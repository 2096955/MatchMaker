from __future__ import annotations

import importlib
import sys

from scudo_mapping_mcp import frames as original_frames
from scudo_mapping_mcp import ingest


def test_session_registry_survives_partial_module_reimport() -> None:
    """Model Streamlit retaining ingest.py while re-importing frames.py.

    AppTest executes in one stable import graph. A real Streamlit server can
    retain ``ingest_bytes`` from the old graph while importing a fresh frames
    module on a watched-file rerun. The returned frames must remain readable
    from that browser session even though the two module globals diverge.
    """
    original_frames.clear_frames()
    registry = {}
    try:
        streamlit_frames = importlib.import_module("scudo_mapping_mcp.streamlit_frames")
        add_session_frames = getattr(streamlit_frames, "add_session_frames", None)
        read_session_frame = getattr(streamlit_frames, "read_session_frame", None)
        assert callable(add_session_frames) and callable(read_session_frame)

        refs = ingest.ingest_bytes(
            "LSEG",
            "vendor-q.csv",
            b"product_id,name,description\n"
            b"Q-CONTRACT-X,Equity Prices Historical Series,Historical equities\n",
            upsert=False,
        )
        add_session_frames(registry, refs)

        del sys.modules["scudo_mapping_mcp.frames"]
        rerun_frames = importlib.import_module("scudo_mapping_mcp.frames")

        assert rerun_frames is not original_frames
        assert rerun_frames._read_vendor_frame("LSEG", "Q-CONTRACT-X") is None
        assert (
            read_session_frame(
                registry,
                "LSEG",
                "Q-CONTRACT-X",
                fallback=rerun_frames._read_vendor_frame,
                frame_source="mock",
            )
            == refs[0]
        )
    finally:
        sys.modules["scudo_mapping_mcp.frames"] = original_frames
        original_frames.clear_frames()


def test_registry_partitions_identical_product_keys_by_session() -> None:
    streamlit_frames = importlib.import_module("scudo_mapping_mcp.streamlit_frames")
    registry = streamlit_frames.StreamlitFrameRegistry()
    first = ingest.ingest_bytes(
        "LSEG",
        "first.csv",
        b"product_id,name\nSHARED-1,First session\n",
        upsert=False,
    )[0]
    second = first.model_copy(update={"name": "Second session"})

    registry.add("session-a", [first])
    registry.add("session-b", [second])

    assert registry.read("session-a", "LSEG", "SHARED-1") == first
    assert registry.read("session-b", "LSEG", "SHARED-1") == second
    assert registry.read("session-c", "LSEG", "SHARED-1") is None


def test_s3_source_never_uses_session_registry() -> None:
    streamlit_frames = importlib.import_module("scudo_mapping_mcp.streamlit_frames")
    registry = {("LSEG", "S3-1"): object()}
    calls = []

    result = streamlit_frames.read_session_frame(
        registry,
        "LSEG",
        "S3-1",
        fallback=lambda vendor, product_id: calls.append((vendor, product_id)),
        frame_source="s3",
    )

    assert result is None
    assert calls == [("LSEG", "S3-1")]
