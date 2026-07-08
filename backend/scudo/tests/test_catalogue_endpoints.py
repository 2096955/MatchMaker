"""SCUDO API endpoints on the mapping Lambda: catalogue read (canonical RDF +
adapted-ODRL) and the HITL decision write. The handler consumes scudo.catalogue
(never Neptune/aurora_store directly); auth uses the API_KEY env (unset = open).
"""

from __future__ import annotations

import json


def _event(path, method="GET", body=None, headers=None):
    ev = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers or {},
    }
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def test_get_catalogue_lists_records(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    monkeypatch.setattr(
        catalogue,
        "list_approved",
        lambda limit=100: [{"iri": "mds.lseg:1", "payload": {}}],
    )
    resp = lambda_handler.handler(_event("/catalogue"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["records"][0]["iri"] == "mds.lseg:1"


def test_get_catalogue_iri_returns_rdf(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    monkeypatch.setattr(
        catalogue, "get_record", lambda iri: {"iri": iri, "payload": {"a": 1}}
    )
    monkeypatch.setattr(
        lambda_handler,
        "_serialise_record",
        lambda rec: {"rdf": {"triples": []}, "rights": {"triples": []}},
    )
    resp = lambda_handler.handler(_event("/catalogue/mds.lseg:1"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["iri"] == "mds.lseg:1" and "rdf" in body and "rights" in body


def test_get_catalogue_iri_404_when_missing(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    monkeypatch.setattr(catalogue, "get_record", lambda iri: None)
    resp = lambda_handler.handler(_event("/catalogue/nope"), None)
    assert resp["statusCode"] == 404


def test_post_decision_approve_writes_review_catalogue_and_outbox(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    calls = {}
    monkeypatch.setattr(
        lambda_handler, "put_review_record", lambda **kw: calls.setdefault("review", kw)
    )
    monkeypatch.setattr(
        lambda_handler, "put_outbox_record", lambda **kw: calls.setdefault("outbox", kw)
    )
    monkeypatch.setattr(
        catalogue,
        "upsert_record",
        lambda iri, payload: calls.setdefault("cat", (iri, payload)),
    )

    resp = lambda_handler.handler(
        _event(
            "/api/mapping/decision",
            "POST",
            {
                "ticket": "HITL-1",
                "decision": "approve",
                "iri": "mds.lseg:1",
                "mapping_object": {
                    "schema_version": "0.1.0",
                    "route": "NEW_MAPPING",
                    "bundle_ref": "bundle-1",
                    "mapping_result": {
                        "vendor_product_iri": "mds.lseg:1",
                        "proposed_target_iri": "jpmorgan:data:cdao:EquityResearch",
                        "confidence": 0.78,
                        "proposed_triples": [
                            {
                                "subject": "mds.lseg:1",
                                "predicate": "rdf:type",
                                "object": "jpmorgan:data:cdao:EquityResearch",
                                "graph": "urn:graph:hitl",
                            }
                        ],
                    },
                    "outcome": "hitl",
                    "published_graph": None,
                    "hitl_ticket": "HITL-1",
                },
            },
        ),
        None,
    )
    assert resp["statusCode"] == 200
    assert calls["review"]["ticket"] == "HITL-1"
    assert calls["cat"][0] == "mds.lseg:1"
    assert calls["cat"][1]["mapping_object"]["outcome"] == "published"
    assert calls["cat"][1]["mapping_object"]["published_graph"] == "urn:graph:hitl"
    assert calls["outbox"]["detail_type"] == "MappingCompleted"
    assert calls["outbox"]["detail"]["mapping_object"]["outcome"] == "published"
    assert calls["outbox"]["detail"]["mapping_object"]["mapping_result"][
        "vendor_product_iri"
    ] == "mds.lseg:1"


def test_post_decision_reject_does_not_publish(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    calls = {}
    monkeypatch.setattr(
        lambda_handler, "put_review_record", lambda **kw: calls.setdefault("review", kw)
    )
    monkeypatch.setattr(
        lambda_handler, "put_outbox_record", lambda **kw: calls.setdefault("outbox", kw)
    )
    monkeypatch.setattr(
        catalogue,
        "upsert_record",
        lambda iri, payload: calls.setdefault("cat", (iri, payload)),
    )

    resp = lambda_handler.handler(
        _event(
            "/api/mapping/decision", "POST", {"ticket": "HITL-2", "decision": "reject"}
        ),
        None,
    )
    assert resp["statusCode"] == 200
    assert calls["review"]["ticket"] == "HITL-2"
    assert "outbox" not in calls and "cat" not in calls  # rejects don't publish


def test_decision_requires_ticket(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import lambda_handler

    resp = lambda_handler.handler(
        _event("/api/mapping/decision", "POST", {"decision": "approve"}), None
    )
    assert resp["statusCode"] == 400


def test_decision_approve_requires_iri(monkeypatch):
    """An approve decision without an iri must not silently 200 and skip
    publish — the ticket is still recorded for audit, but the caller needs
    to know the record never reached the catalogue/outbox."""
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    calls = {}
    monkeypatch.setattr(
        lambda_handler, "put_review_record", lambda **kw: calls.setdefault("review", kw)
    )
    monkeypatch.setattr(
        lambda_handler, "put_outbox_record", lambda **kw: calls.setdefault("outbox", kw)
    )
    monkeypatch.setattr(
        catalogue,
        "upsert_record",
        lambda iri, payload: calls.setdefault("cat", (iri, payload)),
    )

    resp = lambda_handler.handler(
        _event(
            "/api/mapping/decision",
            "POST",
            {"ticket": "HITL-3", "decision": "approve"},
        ),
        None,
    )
    assert resp["statusCode"] == 400
    assert calls["review"]["ticket"] == "HITL-3"  # audit trail still written
    assert "outbox" not in calls and "cat" not in calls


def test_decision_approve_requires_projection_payload(monkeypatch):
    """An approve decision must carry enough mapping data for projection."""
    monkeypatch.delenv("API_KEY", raising=False)
    from scudo import catalogue, lambda_handler

    calls = {}
    monkeypatch.setattr(
        lambda_handler, "put_review_record", lambda **kw: calls.setdefault("review", kw)
    )
    monkeypatch.setattr(
        lambda_handler, "put_outbox_record", lambda **kw: calls.setdefault("outbox", kw)
    )
    monkeypatch.setattr(
        catalogue,
        "upsert_record",
        lambda iri, payload: calls.setdefault("cat", (iri, payload)),
    )

    resp = lambda_handler.handler(
        _event(
            "/api/mapping/decision",
            "POST",
            {"ticket": "HITL-4", "decision": "approve", "iri": "mds.lseg:1"},
        ),
        None,
    )
    assert resp["statusCode"] == 400
    assert "mapping_object" in json.loads(resp["body"])["error"]
    assert calls["review"]["ticket"] == "HITL-4"
    assert "outbox" not in calls and "cat" not in calls


def test_catalogue_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    from scudo import lambda_handler

    resp = lambda_handler.handler(
        _event("/catalogue", headers={"x-api-key": "wrong"}), None
    )
    assert resp["statusCode"] == 401
