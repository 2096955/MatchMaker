"""ETL Lambda: real per-vendor sanity check + Aurora job/facts/audit.

Bad files are quarantined (fail-soft on the FILE); the job/facts/audit trail
goes to the single Aurora cluster via aurora_store (fail-loud). No DynamoDB.
"""

from __future__ import annotations

import io

import pytest


# ── _validate_payload: pure, no AWS ──────────────────────────────────────────
def test_validate_rejects_empty_object():
    from scudo.etl_handler import _validate_payload

    ok, reason = _validate_payload("dms/ice/file.json", b"")
    assert ok is False
    assert "empty" in reason.lower()


def test_validate_rejects_unparseable_json():
    from scudo.etl_handler import _validate_payload

    ok, reason = _validate_payload("api/lseg/2026-07-04/page-1.json", b"{not json")
    assert ok is False
    assert "parse" in reason.lower()


def test_validate_accepts_wellformed_json():
    from scudo.etl_handler import _validate_payload

    ok, reason = _validate_payload("api/lseg/2026-07-04/page-1.json", b'{"rows": []}')
    assert ok is True
    assert reason == ""


# ── _process_object: routes to clean/quarantine + Aurora writers ──────────────
class _FakeS3:
    def __init__(self, body: bytes):
        self._body = body
        self.puts: list[dict] = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self._body), "ContentType": "application/json"}

    def put_object(self, **kw):
        self.puts.append(kw)


class _FakeBoto3:
    def __init__(self, s3):
        self._s3 = s3

    def client(self, service):
        assert service == "s3"
        return self._s3


def _wire(monkeypatch, s3):
    monkeypatch.setenv("SCUDO_CLEAN_BUCKET", "clean-bkt")
    monkeypatch.setenv("SCUDO_QUARANTINE_BUCKET", "quar-bkt")
    from scudo import etl_handler

    calls = {"jobs": [], "facts": [], "audit": [], "events": []}
    monkeypatch.setattr(etl_handler, "_boto3", lambda: _FakeBoto3(s3))
    monkeypatch.setattr(
        etl_handler, "update_job_status", lambda **kw: calls["jobs"].append(kw)
    )
    monkeypatch.setattr(
        etl_handler, "put_facts_record", lambda **kw: calls["facts"].append(kw)
    )
    monkeypatch.setattr(
        etl_handler, "put_audit_record", lambda **kw: calls["audit"].append(kw)
    )
    monkeypatch.setattr(
        etl_handler, "put_eventbridge_event", lambda **kw: calls["events"].append(kw)
    )
    return etl_handler, calls


def test_process_object_valid_routes_to_clean_and_aurora(monkeypatch):
    s3 = _FakeS3(b'{"rows": [1, 2, 3]}')
    etl_handler, calls = _wire(monkeypatch, s3)

    result = etl_handler._process_object("raw-bkt", "api/lseg/2026-07-04/page-1.json")

    assert result["status"] == "PASSED"
    assert any(p["Bucket"] == "clean-bkt" for p in s3.puts), "canonical not written"
    assert calls["jobs"][-1]["status"] == "PASSED"
    assert calls["facts"], "facts/lineage record must be written on PASS"
    assert any(a["event_type"] == "ETL_PASSED" for a in calls["audit"])
    assert calls["events"], "CanonicalMetadataReady event must fire on PASS"


def test_process_object_invalid_quarantines_no_facts(monkeypatch):
    s3 = _FakeS3(b"{not json")
    etl_handler, calls = _wire(monkeypatch, s3)

    result = etl_handler._process_object("raw-bkt", "api/lseg/2026-07-04/page-1.json")

    assert result["status"] == "FAILED"
    assert any(p["Bucket"] == "quar-bkt" for p in s3.puts), "not quarantined"
    assert not any(p["Bucket"] == "clean-bkt" for p in s3.puts), (
        "bad file reached clean"
    )
    assert calls["jobs"][-1]["status"] == "FAILED"
    assert not calls["facts"], "a rejected file must not produce a lineage fact"
    assert any(a["event_type"] == "ETL_FAILED" for a in calls["audit"])


@pytest.mark.parametrize("failing", ["put_facts_record", "put_audit_record"])
def test_process_object_raises_when_trail_write_fails(monkeypatch, failing):
    """A persistence failure (Aurora facts/job or audit) on a GOOD file must
    RAISE so the SQS message retries / DLQs — it must NOT be swallowed into a
    quarantine/FAILED return, which would mistake an outage for a bad file."""
    s3 = _FakeS3(b'{"rows": [1, 2, 3]}')
    etl_handler, _calls = _wire(monkeypatch, s3)

    def _boom(**kw):
        raise RuntimeError("aurora down")

    monkeypatch.setattr(etl_handler, failing, _boom)

    with pytest.raises(RuntimeError, match="aurora down"):
        etl_handler._process_object("raw-bkt", "api/lseg/2026-07-04/page-1.json")

    # The good file reached the clean bucket and was NEVER quarantined by the
    # persistence outage — fail-loud, not fail-soft.
    assert any(p["Bucket"] == "clean-bkt" for p in s3.puts), "good file not written"
    assert not any(p["Bucket"] == "quar-bkt" for p in s3.puts), (
        f"{failing} outage quarantined a good file — fail-loud violated"
    )
