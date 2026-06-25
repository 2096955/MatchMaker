"""Truthful ETL reject-count: rows without a usable product_id are rejected,
not silently synthesized into frames (Codex review A1)."""

from __future__ import annotations

import os


def _ingest():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp.ingest import ingest_bytes

    return ingest_bytes


def _stages(csv: bytes):
    events = []
    _ingest()(
        "LSEG", "t.csv", csv, upsert=True, on_stage=lambda s, d: events.append((s, d))
    )
    return {s: d for s, d in events}


def test_idless_row_is_rejected_not_synthesized():
    # one good row + one row with no product_id (blank) → valid:1, rejected:1
    csv = b"product_id,name,description\nLSEG-EQ-PX,Global Equity Prices,EOD\n,Nameless Row,desc\n"
    stages = _stages(csv)
    assert stages["parse"]["rows"] == 2
    assert stages["validate"]["valid"] == 1, stages["validate"]
    assert stages["validate"]["rejected"] == 1, stages["validate"]


def test_valid_plus_rejected_equals_rows():
    csv = b"product_id,name\nA-1,Alpha\n,Blank\nB-2,Bravo\n,\n"
    stages = _stages(csv)
    v = stages["validate"]
    assert v["valid"] + v["rejected"] == stages["parse"]["rows"]
    assert v["valid"] == 2 and v["rejected"] == 2, v


def test_all_good_rows_zero_rejected():
    csv = b"product_id,name\nA-1,Alpha\nB-2,Bravo\n"
    stages = _stages(csv)
    assert stages["validate"]["rejected"] == 0
    assert stages["validate"]["valid"] == 2
