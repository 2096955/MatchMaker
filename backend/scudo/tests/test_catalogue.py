"""scudo.catalogue approved-record reads/writes via aurora_store._execute."""

from __future__ import annotations

import json


def test_list_approved_selects_and_parses_rows(monkeypatch):
    from scudo import aurora_store, catalogue

    captured = {}

    def _fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return {
            "records": [
                [{"stringValue": "mds.lseg:1"}, {"stringValue": json.dumps({"a": 1})}],
                [{"stringValue": "mds.spg:2"}, {"stringValue": json.dumps({"b": 2})}],
            ]
        }

    monkeypatch.setattr(aurora_store, "_execute", _fake_execute)

    recs = catalogue.list_approved(limit=50)
    assert "scudo.catalogue_products" in captured["sql"].lower()
    assert captured["params"][0]["value"]["longValue"] == 50
    assert recs == [
        {"iri": "mds.lseg:1", "payload": {"a": 1}},
        {"iri": "mds.spg:2", "payload": {"b": 2}},
    ]


def test_get_record_hit_and_miss(monkeypatch):
    from scudo import aurora_store, catalogue

    monkeypatch.setattr(
        aurora_store,
        "_execute",
        lambda sql, params: {
            "records": [
                [{"stringValue": "mds.lseg:1"}, {"stringValue": json.dumps({"x": 9})}]
            ]
        },
    )
    assert catalogue.get_record("mds.lseg:1") == {
        "iri": "mds.lseg:1",
        "payload": {"x": 9},
    }

    monkeypatch.setattr(aurora_store, "_execute", lambda sql, params: {"records": []})
    assert catalogue.get_record("nope") is None


def test_upsert_record_is_parameterised_upsert(monkeypatch):
    from scudo import aurora_store, catalogue

    captured = {}
    monkeypatch.setattr(
        aurora_store,
        "_execute",
        lambda sql, params: captured.update(sql=sql, params=params),
    )

    catalogue.upsert_record("mds.lseg:1", {"a": 1})

    assert "on conflict (iri)" in captured["sql"].lower()
    names = {p["name"] for p in captured["params"]}
    assert {"iri", "payload"} <= names
    payload_param = next(p for p in captured["params"] if p["name"] == "payload")
    assert json.loads(payload_param["value"]["stringValue"]) == {"a": 1}
