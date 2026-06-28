"""POST /api/mapping/ingest/stream emits real ETL stage events over SSE."""

from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


def _post_csv(csv: bytes, vendor: str = "LSEG"):
    return client.post(
        "/api/mapping/ingest/stream",
        data={"vendor": vendor, "file": (io.BytesIO(csv), "t.csv")},
        headers=AUTH,
        content_type="multipart/form-data",
    )


def test_stream_emits_etl_stages_in_order_with_node_ids():
    csv = b"product_id,name,description\nLSEG-EQ-PX,Global Equity Prices,EOD\n"
    r = _post_csv(csv)
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    events = _parse_sse(r.get_data(as_text=True))

    stages = [e["stage"] for e in events if e["type"] == "stage"]
    assert stages == ["received", "parse", "validate", "sink"], stages

    # Every ETL stage maps to real graph node ids.
    by_stage = {e["stage"]: e for e in events if e["type"] == "stage"}
    assert "etl-eventbridge" in by_stage["received"]["nodeIds"]
    assert by_stage["parse"]["nodeIds"] == ["etl-lambda"]
    assert "etl-s3-sink" in by_stage["sink"]["nodeIds"]

    # Real counts, not placeholders.
    assert by_stage["parse"]["detail"]["rows"] == 1
    assert by_stage["validate"]["detail"]["valid"] == 1


def test_stream_ends_with_final_result_and_done():
    csv = b"product_id,name\nLSEG-EQ-PX,Global Equity Prices\n"
    events = _parse_sse(_post_csv(csv).get_data(as_text=True))
    types = [e["type"] for e in events]
    assert "final_result" in types
    assert types[-1] == "done"
    final = next(e for e in events if e["type"] == "final_result")
    assert final["ingested"] == 1
    assert final["products"][0]["product_id"] == "LSEG-EQ-PX"


def test_stream_requires_vendor_and_file():
    r = client.post(
        "/api/mapping/ingest/stream",
        data={"file": (io.BytesIO(b"x"), "t.csv")},
        headers=AUTH,
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
