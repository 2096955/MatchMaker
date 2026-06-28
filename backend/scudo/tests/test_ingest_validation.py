"""Malformed-input handling (Codex A2) + row cap (A3) for ingest_bytes.

Bad JSON shapes must raise ValueError (routes map that → HTTP 400), never a
500. Oversize row counts must be rejected.
"""

from __future__ import annotations

import os

import pytest


def _ingest():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp.ingest import ingest_bytes

    return ingest_bytes


@pytest.mark.parametrize(
    "body",
    [
        b"[1, 2, 3]",  # list of non-objects
        b'"just a string"',  # bare scalar
        b"42",  # bare number
        b'{"products": "nope"}',  # products not a list
        b"{not even json",  # invalid JSON
    ],
)
def test_bad_json_shapes_raise_valueerror(body):
    with pytest.raises(ValueError):
        _ingest()("LSEG", "t.json", body, upsert=False)


def test_valid_json_list_ok():
    body = b'[{"product_id":"A-1","name":"Alpha"}]'
    frames = _ingest()("LSEG", "t.json", body, upsert=True)
    assert len(frames) == 1 and frames[0].product_id == "A-1"


def test_valid_json_products_wrapper_ok():
    body = b'{"products":[{"product_id":"B-2","name":"Bravo"}]}'
    frames = _ingest()("LSEG", "t.json", body, upsert=True)
    assert len(frames) == 1 and frames[0].product_id == "B-2"


def test_row_cap_enforced(monkeypatch):
    import scudo_mapping_mcp.ingest as ing

    monkeypatch.setattr(ing, "_MAX_ROWS", 3)
    csv = b"product_id,name\n" + b"".join(f"P-{i},n{i}\n".encode() for i in range(5))
    with pytest.raises(ValueError):
        ing.ingest_bytes("LSEG", "t.csv", csv, upsert=False)
