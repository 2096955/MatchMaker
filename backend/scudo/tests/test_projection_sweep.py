"""Aurora publish_outbox sweep feeds projections at-least-once (Task 8).

The scheduled projection Lambda drains scudo.publish_outbox instead of an SQS
feed: each undispatched row is projected into the sinks and marked dispatched
ONLY after its projection succeeds. A row whose projection raises is left
undispatched (retried next sweep); a row with nothing to project is still marked
so it can never become a poison row. sweep_outbox(*, execute=None) threads an
optional Data API runner through fetch/mark; the default is aurora_store._execute.
"""

from __future__ import annotations

import json


def test_sweep_marks_dispatched_only_after_projection(monkeypatch):
    from scudo import projection_handler as ph

    rows = [{"event_id": "e1", "detail_type": "MappingCompleted", "detail": {}}]
    dispatched = []
    monkeypatch.setattr(
        ph, "_fetch_undispatched", lambda limit=100, *, execute=None: rows
    )
    monkeypatch.setattr(ph, "_project_one", lambda row: True)
    monkeypatch.setattr(
        ph, "_mark_dispatched", lambda eid, *, execute=None: dispatched.append(eid)
    )

    out = ph.sweep_outbox()
    assert dispatched == ["e1"]
    assert [r["event_id"] for r in out] == ["e1"]


def test_sweep_leaves_undispatched_on_projection_failure(monkeypatch):
    from scudo import projection_handler as ph

    monkeypatch.setattr(
        ph,
        "_fetch_undispatched",
        lambda limit=100, *, execute=None: [{"event_id": "e2", "detail": {}}],
    )

    def _boom(row):
        raise RuntimeError("neptune down")

    monkeypatch.setattr(ph, "_project_one", _boom)
    marked = []
    monkeypatch.setattr(
        ph, "_mark_dispatched", lambda eid, *, execute=None: marked.append(eid)
    )

    out = ph.sweep_outbox()
    assert marked == []  # at-least-once: left undispatched, retried next sweep
    assert out == []


def test_sweep_marks_skipped_rows_to_avoid_poison(monkeypatch):
    from scudo import projection_handler as ph

    monkeypatch.setattr(
        ph,
        "_fetch_undispatched",
        lambda limit=100, *, execute=None: [{"event_id": "e3", "detail": {}}],
    )
    monkeypatch.setattr(ph, "_project_one", lambda row: False)  # nothing to project
    marked = []
    monkeypatch.setattr(
        ph, "_mark_dispatched", lambda eid, *, execute=None: marked.append(eid)
    )

    ph.sweep_outbox()
    assert marked == ["e3"]  # skipped rows are still marked — never poison


def test_project_one_published_calls_all_sinks(monkeypatch):
    from scudo import projection_handler as ph

    calls = []
    monkeypatch.setattr(ph, "_write_aurora", lambda d, m: calls.append("aurora"))
    monkeypatch.setattr(ph, "_publish_neptune", lambda m: calls.append("neptune"))
    monkeypatch.setattr(
        ph, "_index_opensearch", lambda d, m: calls.append("opensearch")
    )
    monkeypatch.setattr(ph, "_publish_appsync", lambda d, m: calls.append("appsync"))

    row = {"event_id": "e", "detail": {"mapping_object": {"outcome": "published"}}}
    assert ph._project_one(row) is True
    assert calls == ["aurora", "neptune", "opensearch", "appsync"]


def test_project_one_skips_non_published(monkeypatch):
    from scudo import projection_handler as ph

    called = []
    monkeypatch.setattr(ph, "_write_aurora", lambda d, m: called.append("aurora"))
    row = {"event_id": "e", "detail": {"mapping_object": {"outcome": "hitl"}}}
    assert ph._project_one(row) is False
    assert called == []


def test_project_one_projects_hitl_decision_shaped_rows(monkeypatch):
    """Cross-module contract: the outbox detail the HITL decision route emits
    (via lambda_handler._decision_publish_payload) must take _project_one's
    published path — not the skip path that permanently marks it dispatched.
    Regression for the gap where raw decision payloads (no mapping_object)
    were silently swallowed."""
    from scudo import projection_handler as ph
    from scudo.lambda_handler import _decision_publish_payload

    decision_body = {
        "ticket": "HITL-9",
        "decision": "approve",
        "iri": "mds.lseg:9",
        "mapping_result": {
            "vendor_product_iri": "mds.lseg:9",
            "proposed_target_iri": "jpmorgan:data:cdao:EquityResearch",
            "confidence": 0.78,
            "proposed_triples": [
                {
                    "subject": "mds.lseg:9",
                    "predicate": "rdf:type",
                    "object": "jpmorgan:data:cdao:EquityResearch",
                    "graph": "urn:graph:hitl",
                }
            ],
        },
    }
    detail = _decision_publish_payload(decision_body, "HITL-9")

    calls = []
    monkeypatch.setattr(ph, "_write_aurora", lambda d, m: calls.append("aurora"))
    monkeypatch.setattr(ph, "_publish_neptune", lambda m: calls.append("neptune"))
    monkeypatch.setattr(
        ph, "_index_opensearch", lambda d, m: calls.append("opensearch")
    )
    monkeypatch.setattr(ph, "_publish_appsync", lambda d, m: calls.append("appsync"))

    row = {"event_id": "HITL-9:approved", "detail": detail}
    assert ph._project_one(row) is True
    assert calls == ["aurora", "neptune", "opensearch", "appsync"]
    assert detail["mapping_object"]["published_graph"] == "urn:graph:hitl"


def test_sparql_iri_percent_encodes_iriref_illegal_chars():
    """SPARQL 1.1 IRIREF grammar excludes <>"{}|^`\\ and control chars/space
    (#x00-#x20). Vendor-controlled ``graph``/subject values reach this
    function via POST /api/mapping/decision -> catalogue.upsert_record ->
    outbox -> sweep_outbox, so every illegal character must be neutralised
    rather than just the two originally handled (backslash, angle brackets).
    """
    from scudo import projection_handler as ph

    illegal_chars = ' "{}|^`\\\x00\x01\x1f'
    encoded = ph._sparql_iri(f"mds.acme:widget{illegal_chars}end")

    for ch in illegal_chars:
        assert ch not in encoded, f"{ch!r} leaked into IRIREF unescaped"

    # Backslash must be percent-encoded, not doubled (doubling still leaves
    # illegal backslash characters in the IRIREF).
    assert "%5C" in encoded
    assert "%20" in encoded  # space
    assert "%00" in encoded  # NUL


def test_sparql_iri_still_escapes_angle_brackets():
    from scudo import projection_handler as ph

    encoded = ph._sparql_iri("mds.acme:<script>")
    assert "<" not in encoded
    assert ">" not in encoded
    assert "%3C" in encoded
    assert "%3E" in encoded


def test_sweep_end_to_end_with_injected_execute(monkeypatch):
    """execute= threads the injected runner through fetch + mark (no rds-data),
    and a published row is marked dispatched once its (env-disabled) sinks no-op."""
    from scudo import projection_handler as ph

    # Disable every sink so _project_one no-ops to True without AWS.
    for var in (
        "SCUDO_AURORA_CLUSTER_ARN",
        "SCUDO_NEPTUNE_ENDPOINT",
        "NEPTUNE_ENDPOINT",
        "SCUDO_OPENSEARCH_ENDPOINT",
        "SCUDO_APPSYNC_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    detail = {"mapping_object": {"outcome": "published", "mapping_result": {}}}
    updates = []

    def fake_execute(sql, params):
        s = sql.strip().lower()
        if s.startswith("select"):
            return {
                "records": [
                    [
                        {"stringValue": "e9"},
                        {"stringValue": "MappingCompleted"},
                        {"stringValue": json.dumps(detail)},
                    ]
                ]
            }
        if s.startswith("update"):
            updates.append(params[0]["value"]["stringValue"])
        return {}

    out = ph.sweep_outbox(execute=fake_execute)
    assert [r["event_id"] for r in out] == ["e9"]
    assert updates == ["e9"]  # marked dispatched via the injected runner


def test_default_execute_uses_aurora_store(monkeypatch):
    from scudo import aurora_store
    from scudo import projection_handler as ph

    seen = {}
    monkeypatch.setattr(
        aurora_store,
        "_execute",
        lambda sql, params: seen.update(sql=sql) or {"records": []},
    )
    ph._fetch_undispatched()  # no execute -> defaults to aurora_store._execute
    assert "publish_outbox" in seen["sql"].lower()
