"""build_match_payload must use its local MemoryStore, never the global
configured store (Codex A6). Verified at the unit level — without mutating the
process-global store cache — so it can't poison other tests.

The fix: matching.map_vendor_product now accepts store=, and
build_match_payload passes its local MemoryStore through.
"""

from __future__ import annotations

import os


def test_map_vendor_product_uses_injected_store_not_global(monkeypatch):
    """If store= is honored, get_store() is never called by the matcher."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp import matching
    from scudo_mapping_mcp.models import VendorProductRef
    from scudo_mapping_mcp.store.memory_store import MemoryStore

    def _boom():
        raise AssertionError(
            "map_vendor_product hit the GLOBAL store; store= was ignored"
        )

    monkeypatch.setattr(matching, "get_store", _boom)

    local = MemoryStore()
    ref = VendorProductRef(
        vendor="LSEG", product_id="LSEG-EQ-PX", name="Global Equity Prices"
    )
    result = matching.map_vendor_product(ref, max_candidates=6, store=local)
    # The call completed against the injected store (no _boom raised).
    assert result.vendor == "LSEG"
    assert result.product_id == "LSEG-EQ-PX"


def test_build_match_payload_does_not_call_global_get_store(monkeypatch):
    """build_match_payload builds entirely from its local MemoryStore."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp import matching
    from scudo_mapping_mcp.store import factory

    def _boom(*a, **k):
        raise AssertionError("build_match_payload reached the global store factory")

    # Trip if either the matcher or the factory reaches for the global store.
    monkeypatch.setattr(matching, "get_store", _boom)
    monkeypatch.setattr(factory, "get_store", _boom, raising=False)

    from scudo.build_matching_graph import build_match_payload

    payload = build_match_payload()
    assert payload["meta"]["dataProvenance"] == "synthetic"
    assert any(n.get("kind") == "vendor-product" for n in payload["nodes"])
