"""D2 — a malformed upload is a CLIENT error, not a server fault.

``csv.Error`` subclasses ``Exception``, NOT ``ValueError``, so a binary/
non-CSV upload escaped every ``except (UnicodeDecodeError, ValueError)``
handler in ``backend/routes/mapping.py``, fell through to the catch-all in
``backend/app.py`` and surfaced as an opaque HTTP 500 + correlation id. Same
class of gap on the URL route, where ``lxml``'s ``ParserError`` escaped.

These tests pin the CLASSIFICATION only. They deliberately do NOT assert that
``.xlsx`` parses — which upload formats are supported is an open business
decision (JPMC_UPLOAD_AND_MATCH_REVIEW.md §4.1), and nothing here should be
read as settling it. The contract under test is narrower: a file this service
cannot parse must produce a 4xx whose message says what was wrong.

Run PER-FILE:

    rtk proxy python -m pytest \
        backend/scudo/tests/test_ingest_malformed_upload.py -q
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")
os.environ.setdefault("SCUDO_DENSE_BACKEND", "jaro_winkler")  # no network

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import app  # noqa: E402

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_XLSX = (
    _REPO_ROOT / "sample_data" / "provider" / "factset" / "company_fundamentals_v1.xlsx"
)
_XML = _REPO_ROOT / "sample_data" / "provider" / "sp_global" / "credit_ratings_v1.xml"


def _upload(path: str, data: bytes, vendor: str, filename: str):
    return client.post(
        path,
        data={"vendor": vendor, "file": (io.BytesIO(data), filename)},
        headers=AUTH,
        content_type="multipart/form-data",
    )


def _sse_events(body: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


# ── the reported defect: real .xlsx fixture through POST /api/mapping/ingest ──


def test_xlsx_upload_is_4xx_not_500():
    """The exact reproduction from the review brief."""
    assert _XLSX.is_file(), f"tracked fixture missing: {_XLSX}"
    r = _upload("/api/mapping/ingest", _XLSX.read_bytes(), "FactSet", _XLSX.name)

    assert r.status_code == 400, (
        f"expected 400 for an unparseable upload, got {r.status_code}: "
        f"{r.get_data(as_text=True)[:300]}"
    )


def test_xlsx_upload_error_message_says_what_was_wrong():
    """A 4xx is only useful if it names the file and the failure."""
    r = _upload("/api/mapping/ingest", _XLSX.read_bytes(), "FactSet", _XLSX.name)
    body = r.get_json()

    # Never the opaque catch-all shape.
    assert body.get("error") != "internal server error"
    assert "error_id" not in body, (
        "a client error must not carry a server correlation id"
    )

    err = body["error"]
    assert _XLSX.name in err, f"error should name the offending file: {err}"
    assert "CSV" in err, f"error should say which parse was attempted: {err}"


def test_binary_upload_is_4xx_regardless_of_extension():
    """Not extension-driven: any byte payload csv.DictReader chokes on is a 400."""
    blob = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(256)) * 4
    r = _upload("/api/mapping/ingest", blob, "LSEG", "looks_like.csv")
    assert r.status_code == 400
    assert r.get_json().get("error") != "internal server error"


# ── sibling route: SSE stream must deliver a well-formed error EVENT ──


def test_stream_upload_emits_parse_error_event_not_a_500():
    """The stream route reports failures as an in-band {"type":"error"} frame
    followed by {"type":"done"} — this asserts the malformed upload takes that
    same documented path, with the informative parse-rejection message."""
    r = _upload("/api/mapping/ingest/stream", _XLSX.read_bytes(), "FactSet", _XLSX.name)

    # SSE is already committed by the time the parse fails; the transport stays 200.
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"

    events = _sse_events(r.get_data(as_text=True))
    types = [e["type"] for e in events]
    assert "error" in types, f"expected an error event, got {types}"
    assert types[-1] == "done", f"stream must still terminate cleanly: {types}"
    assert "final_result" not in types, "a failed parse must not report success"

    err = next(e for e in events if e["type"] == "error")["error"]
    assert err.startswith("cannot parse vendor file:"), err
    assert _XLSX.name in err, err
    assert "CSV" in err, err


# ── sibling route: /mapping/ingest/url ──


def test_url_ingest_unparseable_document_is_4xx_not_500(monkeypatch):
    """An empty/unparseable remote document raised lxml's ParserError, which is
    not a ValueError and not a RequestException — so it escaped both handlers."""
    import scudo_mapping_mcp.url_ingest as url_ingest

    class _Resp:
        content = b""

        def raise_for_status(self):
            pass

    monkeypatch.setattr(url_ingest, "_default_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, stream, allow_redirects: _Resp()
    )

    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG", "url": "http://example.com/empty"},
        headers=AUTH,
    )

    assert r.status_code == 400, (
        f"expected 400, got {r.status_code}: {r.get_data(as_text=True)[:300]}"
    )
    body = r.get_json()
    assert body.get("error") != "internal server error"
    assert "error_id" not in body
    assert "parse" in body["error"].lower(), body["error"]


# ── unit level: the classification lives at the parse site ──


def test_ingest_bytes_raises_valueerror_on_unparseable_bytes():
    """Routes map ValueError -> 400 by convention; the parser must honour it.
    Pinned as ValueError (not csv.Error) precisely because csv.Error is the
    bug: it is a bare Exception subclass."""
    from scudo_mapping_mcp.ingest import ingest_bytes

    with pytest.raises(ValueError):
        ingest_bytes("FactSet", _XLSX.name, _XLSX.read_bytes(), upsert=False)


# ── guard rail: §4.3 (is a zero-row upload an error?) is NOT decided here ──


def test_xml_upload_behaviour_is_unchanged_still_200_zero_rows():
    """The .xml sample parses as a single degenerate CSV row that is rejected
    for having no product_id, so it returns 200 with ingested=0. Whether that
    SHOULD be an error is JPMC_UPLOAD_AND_MATCH_REVIEW.md §4.3 — an open human
    decision. This test exists to prove the D2 fix did not silently make it."""
    assert _XML.is_file(), f"tracked fixture missing: {_XML}"
    r = _upload("/api/mapping/ingest", _XML.read_bytes(), "S&P Global", _XML.name)

    assert r.status_code == 200
    assert r.get_json() == {"ingested": 0, "products": []}
