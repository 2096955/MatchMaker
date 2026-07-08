"""aurora_store writes go through the RDS Data API and are fail-loud."""

from __future__ import annotations

import json
import pytest


class _FakeRdsData:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def execute_statement(self, **kwargs):
        if self.fail:
            raise RuntimeError("data api down")
        self.calls.append(kwargs)
        return {"numberOfRecordsUpdated": 1}


def _store(monkeypatch, client):
    monkeypatch.setenv("SCUDO_AURORA_CLUSTER_ARN", "arn:cluster")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("SCUDO_AURORA_DATABASE_NAME", "scudo")
    from scudo import aurora_store

    monkeypatch.setattr(aurora_store, "_rds_data", lambda: client)
    return aurora_store


def test_put_audit_record_issues_parameterised_insert(monkeypatch):
    client = _FakeRdsData()
    store = _store(monkeypatch, client)
    store.put_audit_record(
        item_id="job-1", event_type="ETL_PASSED", payload={"size": 12}
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["resourceArn"] == "arn:cluster"
    assert call["secretArn"] == "arn:secret"
    assert "insert into scudo.audit_events" in call["sql"].lower()
    # payload must be a bound parameter, not interpolated
    names = {p["name"] for p in call["parameters"]}
    assert {"event_type", "payload"} <= names
    payload_param = next(p for p in call["parameters"] if p["name"] == "payload")
    assert json.loads(payload_param["value"]["stringValue"]) == {"size": 12}


def test_put_audit_record_is_fail_loud(monkeypatch):
    client = _FakeRdsData(fail=True)
    store = _store(monkeypatch, client)
    with pytest.raises(RuntimeError):
        store.put_audit_record(item_id="x", event_type="E", payload={})


def test_missing_aurora_env_raises(monkeypatch):
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    from scudo import aurora_store

    # Missing config must fail loud BEFORE any boto3 client is constructed:
    # if _execute reached _rds_data, this stub would blow the test up.
    def _rds_data_must_not_be_touched():
        raise AssertionError(
            "_rds_data was called despite missing Aurora env — config must be "
            "validated before the client is constructed"
        )

    monkeypatch.setattr(aurora_store, "_rds_data", _rds_data_must_not_be_touched)
    with pytest.raises(RuntimeError, match="SCUDO_AURORA_CLUSTER_ARN"):
        aurora_store.put_outbox_record(
            event_id="e1", detail_type="MappingCompleted", detail={}
        )


def test_aws_resources_delegates_to_aurora(monkeypatch):
    """aws_resources.* writers are thin delegators to aurora_store.* with the
    exact same keyword contract (so call sites needed no edits)."""
    calls = {}
    from scudo import aurora_store, aws_resources

    monkeypatch.setattr(
        aurora_store, "put_audit_record", lambda **kw: calls.setdefault("audit", kw)
    )
    monkeypatch.setattr(
        aurora_store, "put_review_record", lambda **kw: calls.setdefault("review", kw)
    )
    monkeypatch.setattr(
        aurora_store, "put_outbox_record", lambda **kw: calls.setdefault("outbox", kw)
    )

    aws_resources.put_audit_record(item_id="j", event_type="E", payload={"a": 1})
    aws_resources.put_review_record(ticket="HITL-1", payload={"b": 2})
    aws_resources.put_outbox_record(
        event_id="e1", detail_type="MappingCompleted", detail={"c": 3}
    )

    assert calls["audit"] == {"item_id": "j", "event_type": "E", "payload": {"a": 1}}
    assert calls["review"] == {"ticket": "HITL-1", "payload": {"b": 2}}
    assert calls["outbox"] == {
        "event_id": "e1",
        "detail_type": "MappingCompleted",
        "detail": {"c": 3},
    }
