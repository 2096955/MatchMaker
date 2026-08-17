# SCUDO 5-Zone Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the MatchMaker codebase and CloudFormation with the Nigel-approved 5-zone architecture (spec: `docs/superpowers/specs/2026-07-04-scudo-5zone-alignment-design.md`).

**Architecture:** Bands move to pass ≥ 0.80 / borderline ≥ 0.70; a config-driven vendor-API poller becomes the only Zone-1 component we build; all persistence consolidates onto the one Aurora PostgreSQL cluster via the RDS Data API (DynamoDB tables and the console MySQL cluster are retired); the cost-ladder borderline band consults the Bedrock specialist+verifier orchestrator through a backend seam; new SCUDO API endpoints publish canonical RDF + ODRL; observability is structured CloudWatch EMF only.

**Tech Stack:** Python 3.11 (local) / 3.12 (Lambda runtime), boto3 `rds-data` + `secretsmanager` + `s3`, Flask console (psycopg 3), Strands agents on Bedrock, CloudFormation (`backend/scudo/template.yaml`), rdflib/pyshacl serialisers, pytest 8.

## Global Constraints

- Band edges MUST round to 2dp via `pass_threshold()` / `borderline_threshold()` (exact-edge float bug guard).
- Gates are the standalone smoke runners, NOT pytest collection: `python -m scudo_mapping_mcp.tests.smoke` and `python -m scudo.tests.smoke` (orchestrator one needs strands installed; skip locally if absent).
- Aurora writes are FAIL-LOUD (raise, fail the request). ETL bad-file quarantine stays fail-soft.
- ≥ pass-band NEVER calls an LLM; < borderline goes straight to human review; off-list specialist picks fail closed to NEEDS_REVIEW.
- No new observability vendor SDKs; CloudWatch EMF + structured JSON logs only.
- Run every changed Python file through `ruff check` before committing (repo autoformat hook expects it).
- All pytest and smoke commands run from `backend/` with `STORE_BACKEND=memory FRAME_SOURCE=mock` in the env (repo root has no pytest config — collection only works from `backend/`).

---

## File Structure

**Create:**
- `backend/scudo/aurora_store.py` — RDS Data API writers (audit/review/outbox/facts/job-status), fail-loud. One responsibility: durable Aurora persistence.
- `backend/scudo/poller_handler.py` — one config-driven vendor-API poller Lambda for all vendors.
- `backend/scudo/metrics.py` — CloudWatch EMF emitter, no-ops outside Lambda.
- `backend/scudo/config/poller_vendors.example.json` — stub per-vendor poller config.
- `backend/scudo/tests/test_aurora_store.py`, `backend/scudo/tests/test_poller.py`, `backend/scudo/tests/test_catalogue_endpoints.py`, `backend/scudo/tests/test_metrics.py`
- `backend/scudo_mapping_mcp/tests/test_specialist_backend.py`
- `infra/HANDOVER_5zone_alignment.md`

**Modify:**
- `backend/scudo_mapping_mcp/config.py:47` — `CONFIDENCE_FLOOR` 0.80 → 0.75
- `backend/scudo/build_matching_graph.py:33-34` — threshold comments
- `backend/scudo/tests/test_bands.py` — re-pin all boundary tests to 0.80/0.70
- `backend/scudo/aws_resources.py` — writers delegate to `aurora_store` (fail-loud)
- `backend/scudo/etl_handler.py` — real per-vendor sanity check + Aurora job/facts/audit
- `backend/scudo_mapping_mcp/matching.py` — `resolve_specialist()` backend seam
- `backend/scudo/lambda_handler.py` — `GET /catalogue`, `GET /catalogue/{iri}`, `POST /api/mapping/decision`
- `backend/scudo/projection_handler.py` — feed from Aurora `publish_outbox` sweep
- `backend/scudo/template.yaml` — remove 5 DynamoDB tables; add poller Lambda + EventBridge schedule + Secrets Manager; env-var contract
- `backend/db.py`, `backend/init_db.sql` — MySQL → PostgreSQL console port (Task 10, separable)

**Dependency order:** Task 1 (bands) is independent. Tasks 2→3→4/7/8 form the Aurora chain. Task 5 (poller) and Task 6 (specialist seam) and Task 9 (metrics) are independent. Task 10 (console port) is separable — see the Scope note at the end. Task 11 (handover) is last.

---

## Task 1: Confidence bands 0.85/0.75 → 0.80/0.70

> **Note added 2026-08-17.** This plan is a point-in-time record and is left as
> written; this note corrects a factual claim it carries, without rewriting the
> history. **Covers the two sites in this Task 1 section: the test-docstring
> block below (the line reading `floating-point defect (0.75 + 0.05 ==
> 0.8000000000000001, which would push a` / `score of exactly 0.80 into
> BORDERLINE)`) and the Step 4 derived-comment line `PASS_THRESHOLD =
> pass_threshold()  # 0.80 (rounded — avoids 0.8000000000000001)`.**
>
> **The claim is false.** `0.75 + 0.05` is exactly `0.8` in IEEE-754 double
> precision, and `(0.75 + 0.05) == 0.80` is `True`. There is no drift at the
> canonical config, so no score of exactly 0.80 was ever at risk of being pushed
> into BORDERLINE. Measured:
>
> ```
> $ python3 -c "print(repr(0.75+0.05), (0.75+0.05)==0.80, repr(0.75-0.05), repr(0.80+0.05), repr(0.85-0.05))"
> 0.8 True 0.7 0.8500000000000001 0.7999999999999999
> ```
>
> **The 2 dp rounding is still correct — for a different reason.** `floor` and
> `half` are overridable per call and via the `CONFIDENCE_FLOOR` /
> `BORDERLINE_HALF_WIDTH` env vars, and neighbouring windows are *not* exact:
> `0.80 + 0.05` yields `0.8500000000000001` (would misclassify an exact-0.85
> PASS as BORDERLINE) and `0.85 - 0.05` yields `0.7999999999999999` (misclassifies
> in the opposite direction). The rounding protects the overridden windows, not
> the default one.
>
> **Live source of truth:** `backend/scudo_mapping_mcp/config.py`
> (`CONFIDENCE_FLOOR` / `BORDERLINE_HALF_WIDTH` / `PASS_CUT` / `FAIL_CUT` /
> `pass_threshold()` / `borderline_threshold()`) and the corrected docstring on
> `_gate_thresholds()` in `backend/scudo_mapping_mcp/matching.py`. Note the code
> has since moved past this plan in two ways: the canonical config now
> short-circuits to the `PASS_CUT` / `FAIL_CUT` constants rather than computing
> the sum, and the Step 4 comment now reads `# 0.80 (2dp-rounded in config, not
> computed here)`.

**Files:**
- Modify: `backend/scudo_mapping_mcp/config.py:47`
- Modify: `backend/scudo/build_matching_graph.py:33-34`
- Test: `backend/scudo/tests/test_bands.py` (rewrite the pins)

**Interfaces:**
- Consumes: `config.pass_threshold()`, `config.borderline_threshold()` (unchanged signatures; both round to 2dp).
- Produces: new live band edges — `pass_threshold()` == 0.80, `borderline_threshold()` == 0.70; `build_matching_graph.PASS_THRESHOLD`/`BORDERLINE_THRESHOLD` derive from these automatically.

**Context:** `pass_threshold()` = `round(floor + half, 2)`, `borderline_threshold()` = `round(floor - half, 2)`. Current `CONFIDENCE_FLOOR = 0.80`, `BORDERLINE_HALF_WIDTH = 0.05` → 0.85/0.75. Changing the floor to **0.75** (half unchanged) → 0.80/0.70. `orchestrator.py`'s separate `CONFIDENCE_FLOOR = 0.80` (the auto-approve publish gate) is a different constant and does NOT change.

- [ ] **Step 1: Rewrite the boundary tests to the new edges (they currently pin 0.85/0.75)**

Replace the body of `backend/scudo/tests/test_bands.py` from the module docstring's contract block and every numeric assertion:

```python
"""Confidence-band boundary tests.

Pins the exact-boundary behaviour of the PASS/BORDERLINE/FAIL gate so the
floating-point defect (0.75 + 0.05 == 0.8000000000000001, which would push a
score of exactly 0.80 into BORDERLINE) cannot regress.

Canonical contract (5-zone alignment, 2026-07-04):
    PASS       similarity >= 0.80
    BORDERLINE 0.70 <= similarity < 0.80
    FAIL       similarity < 0.70
"""

from __future__ import annotations

import os


def _band():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo.build_matching_graph import _band_from_score

    return _band_from_score


def test_exact_pass_boundary_is_pass():
    """A score of exactly 0.80 must be PASS, not BORDERLINE (the float bug)."""
    assert _band()(0.80) == "pass"


def test_exact_borderline_lower_boundary_is_borderline():
    """A score of exactly 0.70 must be BORDERLINE, not FAIL."""
    assert _band()(0.70) == "borderline"


def test_just_below_pass_is_borderline():
    assert _band()(0.7999) == "borderline"


def test_just_below_borderline_is_fail():
    assert _band()(0.6999) == "fail"


def test_clear_pass_and_fail():
    band = _band()
    assert band(0.95) == "pass"
    assert band(0.10) == "fail"


def test_pass_threshold_is_exactly_0_80():
    """The computed pass threshold must equal 0.80 exactly (no float drift)."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo.build_matching_graph import PASS_THRESHOLD, BORDERLINE_THRESHOLD

    assert PASS_THRESHOLD == 0.80, f"PASS_THRESHOLD drifted: {PASS_THRESHOLD!r}"
    assert BORDERLINE_THRESHOLD == 0.70, (
        f"BORDERLINE_THRESHOLD drifted: {BORDERLINE_THRESHOLD!r}"
    )


def test_runtime_gate_thresholds_are_exact():
    """The live matcher gate (matching.py) must compute exact band edges.

    The float defect lived at the runtime call site (``floor + half``). With
    the live 0.75/0.05 config, an exact-0.80 score must clear the PASS gate.
    """
    from scudo_mapping_mcp.matching import _gate_thresholds

    pass_t, border_t = _gate_thresholds(0.75, 0.05)
    assert pass_t == 0.80, f"runtime pass threshold drifted: {pass_t!r}"
    assert border_t == 0.70, f"runtime borderline threshold drifted: {border_t!r}"
    assert 0.80 >= pass_t, "score of exactly 0.80 must clear the PASS gate"
```

- [ ] **Step 2: Run the tests to verify they FAIL against the old 0.85/0.75 config**

Run: `cd backend && STORE_BACKEND=memory FRAME_SOURCE=mock python -m pytest scudo/tests/test_bands.py -v`
Expected: FAIL — `test_exact_pass_boundary_is_pass` etc. fail because the live floor is still 0.80 (edges 0.85/0.75).

- [ ] **Step 3: Change the floor in config.py**

In `backend/scudo_mapping_mcp/config.py:47`, change:

```python
CONFIDENCE_FLOOR: float = 0.80
```
to:
```python
CONFIDENCE_FLOOR: float = 0.75
```

Leave `BORDERLINE_HALF_WIDTH: float = 0.05` unchanged.

- [ ] **Step 4: Update the derived-comment lines in build_matching_graph.py:33-34**

```python
PASS_THRESHOLD = pass_threshold()  # 0.80 (rounded — avoids 0.8000000000000001)
BORDERLINE_THRESHOLD = borderline_threshold()  # 0.70
```

- [ ] **Step 5: Run tests to verify they PASS**

Run: `cd backend && STORE_BACKEND=memory FRAME_SOURCE=mock python -m pytest scudo/tests/test_bands.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Regenerate the dashboard matching-graph fixture and re-run the mapping smoke gate**

Run: `cd backend && STORE_BACKEND=memory FRAME_SOURCE=mock python -m scudo.build_matching_graph`
Then the gate: `cd backend && python -m scudo_mapping_mcp.tests.smoke`
Expected: smoke prints its OK line; the regenerated graph fixture shows `"pass": 0.8, "borderline": 0.7`.

- [ ] **Step 7: Ripple-check docs and commit**

Grep for stale numbers: `grep -rn "0.85\|0\.75" backend/scudo/build_matching_graph.py README* AGENTS* 2>/dev/null` — update prose that states the live bands (leave `infra/HANDOVER_hitl_bands_2026-06-26.md` historical examples untouched). Then:

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo_mapping_mcp/config.py backend/scudo/build_matching_graph.py backend/scudo/tests/test_bands.py
git add backend/scudo_mapping_mcp/config.py backend/scudo/build_matching_graph.py backend/scudo/tests/test_bands.py
git commit -m "feat(matcher): move confidence bands to 0.80/0.70"
```

---

## Task 2: Aurora Data API store (`aurora_store.py`), fail-loud

**Files:**
- Create: `backend/scudo/aurora_store.py`
- Test: `backend/scudo/tests/test_aurora_store.py`

**Interfaces:**
- Consumes: env `SCUDO_AURORA_CLUSTER_ARN`, `SCUDO_AURORA_SECRET_ARN`, `SCUDO_AURORA_DATABASE_NAME` (already listed in `aws_resources.env_resource_summary()`).
- Produces (drop-in replacements for the `aws_resources` DynamoDB writers, SAME keyword signatures so Task 3 is a re-point):
  - `put_audit_record(*, item_id: str, event_type: str, payload: Mapping[str, Any]) -> None`
  - `put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None`
  - `put_outbox_record(*, event_id: str, detail_type: str, detail: Mapping[str, Any]) -> None`
  - `put_facts_record(*, source_bucket: str, source_key: str, content_hash: str, payload: Mapping[str, Any]) -> None`
  - `update_job_status(*, job_id: str, status: str, fields: Mapping[str, Any] | None = None) -> None`
  - `_execute(sql: str, params: list[dict]) -> dict` (RDS Data API `execute_statement` wrapper; the one place boto3 is touched)

**Context:** Writers are FAIL-LOUD — they RAISE on failure (unlike today's `aws_resources` versions which `return` on missing env and swallow exceptions). Insert JSON columns via `jsonb`. Use parameterised statements only (never string-interpolate values).

- [ ] **Step 1: Write the failing test with a fake Data API client**

```python
# backend/scudo/tests/test_aurora_store.py
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

    monkeypatch.setattr(aurora_store, "_rds_data", lambda: _FakeRdsData())
    with pytest.raises(RuntimeError, match="SCUDO_AURORA_CLUSTER_ARN"):
        aurora_store.put_outbox_record(
            event_id="e1", detail_type="MappingCompleted", detail={}
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_aurora_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scudo.aurora_store'`.

- [ ] **Step 3: Implement `aurora_store.py`**

```python
# backend/scudo/aurora_store.py
"""Durable persistence on the single Aurora PostgreSQL cluster via the RDS
Data API. FAIL-LOUD: every writer raises on failure (missing config or a
Data API error), so the caller's request fails instead of silently dropping
the audit/lineage trail. Boto3 imports stay lazy for credential-free tests.
"""
from __future__ import annotations

import json
import time
from typing import Any, Mapping


def _rds_data():
    import boto3  # type: ignore

    return boto3.client("rds-data")


def _require(name: str) -> str:
    import os

    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set — Aurora persistence is required")
    return val


def _str_param(name: str, value: str) -> dict:
    return {"name": name, "value": {"stringValue": value}}


def _json_param(name: str, value: Mapping[str, Any]) -> dict:
    return {
        "name": name,
        "value": {"stringValue": json.dumps(dict(value), default=str)},
        "typeHint": "JSON",
    }


def _execute(sql: str, params: list[dict]) -> dict:
    return _rds_data().execute_statement(
        resourceArn=_require("SCUDO_AURORA_CLUSTER_ARN"),
        secretArn=_require("SCUDO_AURORA_SECRET_ARN"),
        database=_require("SCUDO_AURORA_DATABASE_NAME"),
        sql=sql,
        parameters=params,
    )


def put_audit_record(*, item_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
    _execute(
        "insert into scudo.audit_events (item_id, event_type, created_at_ms, payload) "
        "values (:item_id, :event_type, :created_at_ms, :payload::jsonb)",
        [
            _str_param("item_id", item_id),
            _str_param("event_type", event_type),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("payload", payload),
        ],
    )


def put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None:
    if not ticket:
        raise RuntimeError("review record requires a ticket")
    _execute(
        "insert into scudo.mapping_decisions (ticket, status, created_at_ms, payload) "
        "values (:ticket, 'OPEN', :created_at_ms, :payload::jsonb)",
        [
            _str_param("ticket", ticket),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("payload", payload),
        ],
    )


def put_outbox_record(*, event_id: str, detail_type: str, detail: Mapping[str, Any]) -> None:
    _execute(
        "insert into scudo.publish_outbox (event_id, detail_type, dispatched, created_at_ms, detail) "
        "values (:event_id, :detail_type, false, :created_at_ms, :detail::jsonb) "
        "on conflict (event_id) do nothing",
        [
            _str_param("event_id", event_id),
            _str_param("detail_type", detail_type),
            _str_param("created_at_ms", str(int(time.time() * 1000))),
            _json_param("detail", detail),
        ],
    )


def put_facts_record(
    *, source_bucket: str, source_key: str, content_hash: str, payload: Mapping[str, Any]
) -> None:
    _execute(
        "insert into scudo.lineage_facts (source_bucket, source_key, content_hash, payload) "
        "values (:source_bucket, :source_key, :content_hash, :payload::jsonb)",
        [
            _str_param("source_bucket", source_bucket),
            _str_param("source_key", source_key),
            _str_param("content_hash", content_hash),
            _json_param("payload", payload),
        ],
    )


def update_job_status(
    *, job_id: str, status: str, fields: Mapping[str, Any] | None = None
) -> None:
    _execute(
        "insert into scudo.etl_jobs (job_id, status, updated_at_ms, fields) "
        "values (:job_id, :status, :updated_at_ms, :fields::jsonb) "
        "on conflict (job_id) do update set status = excluded.status, "
        "updated_at_ms = excluded.updated_at_ms, fields = excluded.fields",
        [
            _str_param("job_id", job_id),
            _str_param("status", status),
            _str_param("updated_at_ms", str(int(time.time() * 1000))),
            _json_param("fields", fields or {}),
        ],
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest scudo/tests/test_aurora_store.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the DDL for these tables to `init_data_platform.py`**

Open `backend/scudo/init_data_platform.py`, find where it issues `CREATE SCHEMA` / `CREATE TABLE` statements, and add (idempotent) DDL for schema `scudo` with tables `audit_events(item_id text, event_type text, created_at_ms bigint, payload jsonb)`, `mapping_decisions(ticket text primary key, status text, created_at_ms bigint, payload jsonb)`, `publish_outbox(event_id text primary key, detail_type text, dispatched boolean default false, created_at_ms bigint, detail jsonb)`, `lineage_facts(source_bucket text, source_key text, content_hash text, payload jsonb)`, `catalogue_products(iri text primary key, payload jsonb)`, `cdao_taxonomy(iri text primary key, payload jsonb)`, `etl_jobs(job_id text primary key, status text, updated_at_ms bigint, fields jsonb)`. Read the existing file first to match its DDL-issuing style (Data API vs psql).

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/aurora_store.py backend/scudo/tests/test_aurora_store.py
git add backend/scudo/aurora_store.py backend/scudo/tests/test_aurora_store.py backend/scudo/init_data_platform.py
git commit -m "feat(scudo): fail-loud Aurora Data API persistence store"
```

---

## Task 3: Re-point writers to Aurora; remove DynamoDB tables

**Files:**
- Modify: `backend/scudo/aws_resources.py`
- Modify: `backend/scudo/template.yaml`
- Test: `backend/scudo/tests/test_aurora_store.py` (add a re-point assertion)

**Interfaces:**
- Consumes: Task 2's `aurora_store` writers.
- Produces: `aws_resources.put_audit_record` / `put_review_record` / `put_outbox_record` now delegate to `aurora_store` (call sites in `etl_handler.py`, `lambda_handler.py` unchanged — they import from `aws_resources`). `put_eventbridge_event` stays as-is (event bus, not a DB).

**Context:** `aws_resources` is imported by `etl_handler` (`from .aws_resources import ... put_audit_record`) and `lambda_handler` (`put_audit_record`, `put_outbox_record`, `put_review_record`). Delegating inside `aws_resources` keeps both call sites working with zero edits there.

- [ ] **Step 1: Add a delegation test**

Append to `backend/scudo/tests/test_aurora_store.py`:

```python
def test_aws_resources_delegates_to_aurora(monkeypatch):
    calls = {}
    from scudo import aurora_store, aws_resources

    monkeypatch.setattr(
        aurora_store, "put_audit_record", lambda **kw: calls.setdefault("audit", kw)
    )
    aws_resources.put_audit_record(item_id="j", event_type="E", payload={"a": 1})
    assert calls["audit"] == {"item_id": "j", "event_type": "E", "payload": {"a": 1}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_aurora_store.py::test_aws_resources_delegates_to_aurora -v`
Expected: FAIL — `aws_resources.put_audit_record` still writes DynamoDB (no delegation).

- [ ] **Step 3: Replace the three DynamoDB writers in `aws_resources.py` with delegators**

Replace the bodies of `put_audit_record`, `put_review_record`, `put_outbox_record` (lines 65-111) with:

```python
def put_audit_record(*, item_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
    from . import aurora_store

    aurora_store.put_audit_record(item_id=item_id, event_type=event_type, payload=payload)


def put_review_record(*, ticket: str, payload: Mapping[str, Any]) -> None:
    from . import aurora_store

    aurora_store.put_review_record(ticket=ticket, payload=payload)


def put_outbox_record(*, event_id: str, detail_type: str, detail: Mapping[str, Any]) -> None:
    from . import aurora_store

    aurora_store.put_outbox_record(event_id=event_id, detail_type=detail_type, detail=detail)
```

Drop the now-dead `SCUDO_AUDIT_TABLE`/`SCUDO_REVIEW_TABLE`/`SCUDO_OUTBOX_TABLE` reads from `env_resource_summary()` (keep the Aurora keys).

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest scudo/tests/test_aurora_store.py -v`
Expected: PASS.

- [ ] **Step 5: Remove the five DynamoDB tables from `template.yaml`**

In `backend/scudo/template.yaml`, delete the `AWS::DynamoDB::Table` resources for audit/review/outbox/facts/jobs and any `SCUDO_*_TABLE` env vars + IAM `dynamodb:*` grants that reference them. Add IAM grants for `rds-data:ExecuteStatement` on the cluster and `secretsmanager:GetSecretValue` on the Aurora secret to every Lambda that persists. Read the file first; make the deletion self-contained and validate:

Run: `cd backend/scudo && python -c "import yaml,sys; yaml.safe_load(open('template.yaml')); print('yaml ok')"`
Expected: `yaml ok` (no residual `!Ref` to a deleted table — grep `grep -n SCUDO_.*_TABLE template.yaml` returns nothing).

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/aws_resources.py
git add backend/scudo/aws_resources.py backend/scudo/template.yaml backend/scudo/tests/test_aurora_store.py
git commit -m "refactor(scudo): route audit/review/outbox to Aurora, drop DynamoDB tables"
```

---

## Task 4: ETL real sanity-check + Aurora job/facts/audit

**Files:**
- Modify: `backend/scudo/etl_handler.py`
- Test: `backend/scudo/tests/test_ingest_validation.py` (extend — it already exists)

**Interfaces:**
- Consumes: `aurora_store.put_facts_record`, `aurora_store.update_job_status`, `aws_resources.put_audit_record` (now Aurora-backed).
- Produces: `_validate_payload(key: str, body: bytes) -> tuple[bool, str]` — returns `(ok, reason)`; a false result routes the object to the quarantine bucket with `reason`.

**Context:** Today `_process_object` "passes" any object whose S3 metadata reads; DynamoDB job/facts writes are inline via `_table(...)`. Change: (1) add real validation before canonicalisation; (2) replace `_table(...)` DynamoDB writes with `aurora_store.update_job_status` / `put_facts_record`; (3) audit write stays via `put_audit_record` and is now fail-loud (a failed audit must raise so SQS retries → DLQ, not silent loss). Bad *files* stay fail-soft (quarantined).

- [ ] **Step 1: Write failing validation tests**

```python
# add to backend/scudo/tests/test_ingest_validation.py
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


def test_validate_rejects_empty_object():
    from scudo.etl_handler import _validate_payload

    ok, reason = _validate_payload("dms/ice/file.json", b"")
    assert ok is False
    assert "empty" in reason.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_ingest_validation.py -k validate -v`
Expected: FAIL — `_validate_payload` not defined.

- [ ] **Step 3: Add `_validate_payload` and call it in `_process_object`**

Add to `etl_handler.py`:

```python
def _validate_payload(key: str, body: bytes) -> tuple[bool, str]:
    """Deterministic sanity check before canonicalisation. Bad files are data,
    not outages — a False result quarantines with a machine-readable reason."""
    if not body:
        return False, "empty object"
    suffix = PurePosixPath(key).suffix.lower()
    if suffix == ".json" or key.startswith("api/"):
        try:
            json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return False, f"json parse error: {exc}"
    if len(body) > 512 * 1024 * 1024:
        return False, "object exceeds 512MiB sanity ceiling"
    return True, ""
```

In `_process_object`, after `body = obj["Body"].read()` (line 91), branch on validation before writing the canonical object:

```python
        ok, reason = _validate_payload(key, body)
        if not ok:
            raise ValueError(f"sanity check failed: {reason}")
```

(The existing `except Exception` already quarantines with the error string — this routes failed validation to the rejected bucket with a readable reason, matching today's fail-soft quarantine.)

- [ ] **Step 4: Re-point the job/facts writes to Aurora**

Replace the `_table("SCUDO_JOB_TABLE")` / `_table("SCUDO_FACTS_TABLE")` blocks with `aurora_store` calls:

```python
    from .aurora_store import put_facts_record, update_job_status

    update_job_status(
        job_id=job_id,
        status="PROCESSING",
        fields={"source_bucket": bucket, "source_key": key, "created_at_ms": now_ms},
    )
```
and on success:
```python
    put_facts_record(
        source_bucket=bucket, source_key=key, content_hash=content_hash, payload=canonical
    )
    update_job_status(
        job_id=job_id,
        status="PASSED",
        fields={"canonical_bucket": clean_bucket, "canonical_key": canonical_key},
    )
```
and in the failure branch `update_job_status(job_id=job_id, status="FAILED", fields={"quarantine_key": quarantine_key, "error": str(exc)})`. Delete the now-unused `_table` helper if nothing else uses it.

- [ ] **Step 5: Run tests + smoke to verify green**

Run: `cd backend && python -m pytest scudo/tests/test_ingest_validation.py -v`
Expected: PASS.
Run: `cd backend && python -m pytest scudo/tests/ -k "ingest" -v`
Expected: PASS (existing ingest tests still green).

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/etl_handler.py
git add backend/scudo/etl_handler.py backend/scudo/tests/test_ingest_validation.py
git commit -m "feat(scudo): real ETL sanity-check + Aurora job/facts/audit"
```

---

## Task 5: Config-driven vendor-API poller

**Files:**
- Create: `backend/scudo/poller_handler.py`
- Create: `backend/scudo/config/poller_vendors.example.json`
- Modify: `backend/scudo/template.yaml`
- Test: `backend/scudo/tests/test_poller.py`

**Interfaces:**
- Consumes: env `SCUDO_POLLER_CONFIG` (`s3://bucket/key` to the per-vendor JSON), `SCUDO_RAW_BUCKET`; Secrets Manager `scudo/poller/<vendor>`.
- Produces:
  - `load_config(s3=None) -> list[dict]` — reads + parses the config document.
  - `poll_vendor(vendor_cfg: dict, *, s3, secrets, http) -> dict` — streams pages to `api/<vendor>/<date>/page-<n>.json` in the raw bucket; returns `{"vendor": ..., "pages": n, "skipped": bool}`.
  - `handler(event, context) -> dict` — EventBridge entry; iterates enabled vendors.

**Context:** ONE Lambda for all vendors; onboarding = a config change. API keys fetched at runtime from Secrets Manager, never in config. Pages stream straight to S3 (flat memory). The heavy-pull Step Functions/Fargate reserve path is documented, NOT built (spec §3.1). Ships a stub httpbin-style vendor because no real vendor creds exist in the PoC account.

- [ ] **Step 1: Write failing tests with stubbed S3 / Secrets / HTTP**

```python
# backend/scudo/tests/test_poller.py
from __future__ import annotations

import json


class _FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kw):
        self.puts.append(kw)


class _FakeSecrets:
    def get_secret_value(self, SecretId):
        return {"SecretString": json.dumps({"api_key": f"KEY-{SecretId}"})}


class _FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.seen_keys = []

    def get(self, url, headers=None):
        self.seen_keys.append(headers.get("x-api-key"))
        page = self.pages.pop(0)
        return page  # {"items": [...], "next": bool}


def test_poll_vendor_streams_each_page_to_raw_bucket(monkeypatch):
    monkeypatch.setenv("SCUDO_RAW_BUCKET", "raw-bkt")
    from scudo import poller_handler

    s3, secrets = _FakeS3(), _FakeSecrets()
    http = _FakeHttp([{"items": [1], "next": True}, {"items": [2], "next": False}])
    cfg = {"vendor": "lseg", "endpoint": "https://x", "pagination": "next",
           "secret_id": "scudo/poller/lseg", "enabled": True}
    out = poller_handler.poll_vendor(cfg, s3=s3, secrets=secrets, http=http)
    assert out["pages"] == 2
    assert all(p["Bucket"] == "raw-bkt" for p in s3.puts) or all(
        p["Bucket"] == "raw-bkt" for p in s3.puts
    )  # bucket from env
    assert all(p["Key"].startswith("api/lseg/") for p in s3.puts)
    assert http.seen_keys == ["KEY-scudo/poller/lseg", "KEY-scudo/poller/lseg"]


def test_disabled_vendor_is_skipped(monkeypatch):
    monkeypatch.setenv("SCUDO_RAW_BUCKET", "raw-bkt")
    from scudo import poller_handler

    s3 = _FakeS3()
    out = poller_handler.poll_vendor(
        {"vendor": "off", "enabled": False}, s3=s3, secrets=_FakeSecrets(), http=_FakeHttp([])
    )
    assert out["skipped"] is True
    assert s3.puts == []
```

(Fix the bucket typo in your local copy — the asserted bucket is `os.environ["SCUDO_RAW_BUCKET"]`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_poller.py -v`
Expected: FAIL — no module `scudo.poller_handler`.

- [ ] **Step 3: Implement `poller_handler.py`**

```python
# backend/scudo/poller_handler.py
"""Config-driven vendor-API poller — ONE Lambda for all vendors.

Onboarding a new vendor API is a config change (an entry in the JSON at
SCUDO_POLLER_CONFIG), never a new Lambda. API keys are fetched at runtime from
Secrets Manager and never live in code or config. Pages stream straight to the
raw S3 bucket to keep memory flat regardless of catalogue size. A vendor whose
full pull cannot finish inside the Lambda window has a documented reserve path
(Step Functions fan-out or Fargate) — NOT built here.
"""
from __future__ import annotations

import json
import os
from typing import Any


def _s3():
    import boto3  # type: ignore

    return boto3.client("s3")


def _secrets():
    import boto3  # type: ignore

    return boto3.client("secretsmanager")


def _http():
    import urllib3  # type: ignore

    pool = urllib3.PoolManager()

    class _Client:
        def get(self, url, headers=None):
            r = pool.request("GET", url, headers=headers or {})
            return json.loads(r.data.decode("utf-8"))

    return _Client()


def load_config(s3=None) -> list[dict]:
    ref = os.environ["SCUDO_POLLER_CONFIG"]  # s3://bucket/key
    assert ref.startswith("s3://"), "SCUDO_POLLER_CONFIG must be an s3:// URI"
    bucket, _, key = ref[len("s3://"):].partition("/")
    s3 = s3 or _s3()
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    doc = json.loads(body)
    return doc["vendors"] if isinstance(doc, dict) else doc


def _api_key(secrets, secret_id: str) -> str:
    raw = secrets.get_secret_value(SecretId=secret_id)["SecretString"]
    return json.loads(raw)["api_key"]


def poll_vendor(vendor_cfg: dict, *, s3, secrets, http) -> dict:
    vendor = vendor_cfg["vendor"]
    if not vendor_cfg.get("enabled", False):
        return {"vendor": vendor, "pages": 0, "skipped": True}
    raw_bucket = os.environ["SCUDO_RAW_BUCKET"]
    date = os.environ.get("SCUDO_POLLER_DATE", "run")  # injected by handler
    api_key = _api_key(secrets, vendor_cfg["secret_id"])
    page_n, url = 0, vendor_cfg["endpoint"]
    while url:
        page = http.get(url, headers={"x-api-key": api_key})
        page_n += 1
        s3.put_object(
            Bucket=raw_bucket,
            Key=f"api/{vendor}/{date}/page-{page_n}.json",
            Body=json.dumps(page).encode("utf-8"),
            ContentType="application/json",
        )
        url = vendor_cfg["endpoint"] if page.get("next") else None
    return {"vendor": vendor, "pages": page_n, "skipped": False}


def handler(event: dict, context: Any) -> dict:
    s3, secrets, http = _s3(), _secrets(), _http()
    results = [
        poll_vendor(cfg, s3=s3, secrets=secrets, http=http) for cfg in load_config(s3)
    ]
    return {"polled": len([r for r in results if not r["skipped"]]), "results": results}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest scudo/tests/test_poller.py -v`
Expected: PASS.

- [ ] **Step 5: Add the stub config + template resources**

Create `backend/scudo/config/poller_vendors.example.json`:

```json
{
  "vendors": [
    {
      "vendor": "stub",
      "endpoint": "https://httpbin.org/json",
      "pagination": "next",
      "secret_id": "scudo/poller/stub",
      "cadence": "cron(0 6 1,15 * ? *)",
      "enabled": false
    }
  ]
}
```

In `template.yaml` add: a `PollerFunction` (`Handler: scudo.poller_handler.handler`, env `SCUDO_POLLER_CONFIG`, `SCUDO_RAW_BUCKET`); an `AWS::Scheduler::Schedule` or `AWS::Events::Rule` firing twice a month (`cron(0 6 1,15 * ? *)`) targeting it; a Secrets Manager resource `scudo/poller/stub`; IAM grants (`s3:PutObject` raw bucket, `secretsmanager:GetSecretValue`). Validate YAML as in Task 3 Step 5.

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/poller_handler.py backend/scudo/tests/test_poller.py
git add backend/scudo/poller_handler.py backend/scudo/config/poller_vendors.example.json backend/scudo/tests/test_poller.py backend/scudo/template.yaml
git commit -m "feat(scudo): config-driven vendor-API poller (Zone 1 third route)"
```

---

## Task 6: Specialist backend seam (local | strands | rest)

**Files:**
- Modify: `backend/scudo_mapping_mcp/matching.py`
- Modify: the `map_vendor_product(` call site(s) — grep `grep -rn "map_vendor_product(" backend/scudo_mapping_mcp` (spec names `agent.py`, `routes/mapping.py`)
- Test: `backend/scudo_mapping_mcp/tests/test_specialist_backend.py`

**Interfaces:**
- Consumes: `SpecialistScorer = Callable[[VendorProductRef, list[Candidate]], Optional[Candidate]]` (existing, `matching.py:126`); env `SCUDO_SPECIALIST_BACKEND`.
- Produces: `resolve_specialist(backend: str | None = None) -> Optional[SpecialistScorer]` — factory returning the specialist to pass into `map_vendor_product(..., specialist=...)`. `None`/`local` → today's in-process specialist; `strands` → in-process orchestrator adapter; `rest` → HTTP-to-Lambda adapter.

**Context:** The borderline branch (`matching.py:334`) already consults an injected `specialist` and already fails closed on off-list picks (`matching.py:355`). This task only adds the *selection* of which specialist the caller injects — the invariant code is untouched. Builds on `tests/test_rest_specialist.py`.

- [ ] **Step 1: Write the failing seam test**

```python
# backend/scudo_mapping_mcp/tests/test_specialist_backend.py
from __future__ import annotations

import os
import pytest


def test_default_and_local_backend_returns_callable_or_none():
    from scudo_mapping_mcp.matching import resolve_specialist

    for backend in (None, "local"):
        s = resolve_specialist(backend)
        assert s is None or callable(s)


def test_unknown_backend_raises():
    from scudo_mapping_mcp.matching import resolve_specialist

    with pytest.raises(ValueError, match="SCUDO_SPECIALIST_BACKEND"):
        resolve_specialist("nonsense")


def test_env_selects_backend(monkeypatch):
    from scudo_mapping_mcp import matching

    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "rest")
    monkeypatch.setattr(matching, "_rest_specialist", lambda: "REST_SENTINEL")
    assert matching.resolve_specialist() == "REST_SENTINEL"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && STORE_BACKEND=memory FRAME_SOURCE=mock python -m pytest scudo_mapping_mcp/tests/test_specialist_backend.py -v`
Expected: FAIL — `resolve_specialist` not defined.

- [ ] **Step 3: Add `resolve_specialist` to `matching.py`**

Near the `SpecialistScorer` type (after line 129), add:

```python
def _local_specialist() -> Optional["SpecialistScorer"]:
    """Today's in-process specialist (scudo_mapping_mcp.specialist)."""
    from .specialist import score_candidate  # existing in-process scorer

    return score_candidate


def _strands_specialist() -> Optional["SpecialistScorer"]:
    """Invoke the Zone-4 Strands orchestrator in-process. Deferred import so
    the local smoke gate never needs strands/bedrock."""
    from .specialist_backends import strands_scorer  # thin adapter (Step 5)

    return strands_scorer()


def _rest_specialist() -> Optional["SpecialistScorer"]:
    """Call the Lambda orchestrator over HTTP (see tests/test_rest_specialist.py)."""
    from .specialist_backends import rest_scorer

    return rest_scorer()


def resolve_specialist(backend: str | None = None) -> Optional["SpecialistScorer"]:
    backend = (backend or os.environ.get("SCUDO_SPECIALIST_BACKEND") or "local").lower()
    if backend == "local":
        return _local_specialist()
    if backend == "strands":
        return _strands_specialist()
    if backend == "rest":
        return _rest_specialist()
    raise ValueError(
        f"SCUDO_SPECIALIST_BACKEND={backend!r} unknown (local|strands|rest)"
    )
```

Confirm `import os` is present at the top of `matching.py`; add it if not. If `scudo_mapping_mcp/specialist.py` exposes a differently-named scorer, read it and use that name in `_local_specialist`.

- [ ] **Step 4: Wire the call site(s)**

At each `map_vendor_product(` caller, replace a hard-coded specialist with `specialist=resolve_specialist()`. Keep `borderline_requires_specialist` behaviour as-is. Example (adapt to the real caller):

```python
from scudo_mapping_mcp.matching import map_vendor_product, resolve_specialist

result = map_vendor_product(ref, specialist=resolve_specialist())
```

- [ ] **Step 5: Add the `specialist_backends.py` adapters (strands + rest)**

Create `backend/scudo_mapping_mcp/specialist_backends.py` with `strands_scorer()` and `rest_scorer()` returning a `SpecialistScorer`. The `rest_scorer` posts the ref+candidates to the Lambda orchestrator and maps the response IRI back onto the anchored candidate (returning `None` if the IRI is off-list, so the existing fail-closed path in `matching.py:355` fires). Read `tests/test_rest_specialist.py` to match the exact request/response contract it already asserts.

- [ ] **Step 6: Run tests + both smoke gates**

Run: `cd backend && STORE_BACKEND=memory FRAME_SOURCE=mock python -m pytest scudo_mapping_mcp/tests/test_specialist_backend.py scudo/tests/test_rest_specialist.py -v`
Expected: PASS.
Run: `cd backend && python -m scudo_mapping_mcp.tests.smoke`
Expected: OK line (local backend default — no AWS needed).

- [ ] **Step 7: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo_mapping_mcp/matching.py backend/scudo_mapping_mcp/specialist_backends.py backend/scudo_mapping_mcp/tests/test_specialist_backend.py
git add backend/scudo_mapping_mcp/matching.py backend/scudo_mapping_mcp/specialist_backends.py backend/scudo_mapping_mcp/tests/test_specialist_backend.py
git commit -m "feat(matcher): SCUDO_SPECIALIST_BACKEND seam (local|strands|rest)"
```

---

## Task 7: SCUDO API endpoints (catalogue read + human decision)

**Files:**
- Modify: `backend/scudo/lambda_handler.py`
- Test: `backend/scudo/tests/test_catalogue_endpoints.py`

**Interfaces:**
- Consumes: `catalogue.py` (read approved records from Aurora), `rdf/real.py` serialiser, `tools.py` ODRL serialiser, `aurora_store.put_review_record`.
- Produces three route branches in `handler` (inserted after the `/health` block, `lambda_handler.py:234`, and after `_check_api_key`):
  - `GET /catalogue` → `{"records": [...]}`
  - `GET /catalogue/{iri}` → canonical RDF (Turtle/JSON-LD) + adapted-ODRL rights
  - `POST /api/mapping/decision` → writes the human verdict, feeds the publish path

**Context:** Consumers NEVER touch Neptune directly (spec §5, §6.4). `handler` dispatches on `path`/`method`; the existing POST body path is the mapping intake — the new branches must come before it and return early.

- [ ] **Step 1: Write failing route tests (HTTP-envelope level)**

```python
# backend/scudo/tests/test_catalogue_endpoints.py
from __future__ import annotations

import json


def _event(path, method="GET", body=None, key="test-key"):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}},
          "headers": {"x-api-key": key}}
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def test_get_catalogue_lists_records(monkeypatch):
    monkeypatch.setenv("SCUDO_API_KEY", "test-key")
    from scudo import lambda_handler, catalogue

    monkeypatch.setattr(catalogue, "list_approved", lambda limit=100: [{"iri": "mds.lseg:1"}])
    resp = lambda_handler.handler(_event("/catalogue"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["records"][0]["iri"] == "mds.lseg:1"


def test_get_catalogue_iri_returns_rdf(monkeypatch):
    monkeypatch.setenv("SCUDO_API_KEY", "test-key")
    from scudo import lambda_handler, catalogue

    monkeypatch.setattr(catalogue, "get_record", lambda iri: {"iri": iri, "payload": {}})
    monkeypatch.setattr(lambda_handler, "_serialise_record", lambda rec: "@prefix mds: .")
    resp = lambda_handler.handler(_event("/catalogue/mds.lseg:1"), None)
    assert resp["statusCode"] == 200
    assert "@prefix" in json.loads(resp["body"])["rdf"]


def test_post_decision_writes_review_and_publishes(monkeypatch):
    monkeypatch.setenv("SCUDO_API_KEY", "test-key")
    from scudo import lambda_handler
    calls = {}
    monkeypatch.setattr(lambda_handler, "put_review_record",
                        lambda **kw: calls.setdefault("review", kw))
    monkeypatch.setattr(lambda_handler, "put_outbox_record",
                        lambda **kw: calls.setdefault("outbox", kw))
    resp = lambda_handler.handler(
        _event("/api/mapping/decision", "POST",
               {"ticket": "HITL-1", "decision": "approve", "iri": "mds.lseg:1"}),
        None,
    )
    assert resp["statusCode"] == 200
    assert calls["review"]["ticket"] == "HITL-1"
    assert "outbox" in calls  # approved decisions feed the publish path
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_catalogue_endpoints.py -v`
Expected: FAIL — routes not implemented / `catalogue.list_approved` missing.

- [ ] **Step 3: Add read helpers to `catalogue.py`**

Read `backend/scudo/catalogue.py`, then add `list_approved(limit=100) -> list[dict]` and `get_record(iri) -> dict | None` that `SELECT` from Aurora `scudo.catalogue_products` via `aurora_store._execute`. Add a `_serialise_record(rec) -> str` helper in `lambda_handler.py` that calls the existing `rdf/real.py` Turtle serialiser + `tools.py` ODRL serialiser (use their real function names — read those files).

- [ ] **Step 4: Add the three route branches in `handler`**

Insert after the `/health` block and after `_check_api_key` passes (so catalogue reads are authed), before the intake-POST path:

```python
    if path.endswith("/catalogue") and method == "GET":
        from . import catalogue
        return _resp(200, {"records": catalogue.list_approved()})

    if "/catalogue/" in path and method == "GET":
        from . import catalogue
        iri = path.split("/catalogue/", 1)[1]
        rec = catalogue.get_record(iri)
        if rec is None:
            return _resp(404, {"error": f"no catalogue record for {iri}"})
        return _resp(200, {"iri": iri, "rdf": _serialise_record(rec)})

    if path.endswith("/api/mapping/decision") and method == "POST":
        decision = json.loads(event.get("body") or "{}")
        put_review_record(ticket=decision["ticket"], payload=decision)
        if decision.get("decision") == "approve":
            put_outbox_record(
                event_id=f"{decision['ticket']}:approved",
                detail_type="MappingCompleted",
                detail=decision,
            )
        return _resp(200, {"ok": True, "ticket": decision["ticket"]})
```

Ensure `put_review_record` and `put_outbox_record` are imported in `lambda_handler.py` (they already are, from `aws_resources`).

- [ ] **Step 5: Run tests + smoke**

Run: `cd backend && python -m pytest scudo/tests/test_catalogue_endpoints.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/lambda_handler.py backend/scudo/catalogue.py backend/scudo/tests/test_catalogue_endpoints.py
git add backend/scudo/lambda_handler.py backend/scudo/catalogue.py backend/scudo/tests/test_catalogue_endpoints.py
git commit -m "feat(scudo): SCUDO API endpoints — catalogue RDF/ODRL + HITL decision"
```

---

## Task 8: Projections fed by the Aurora outbox sweep

**Files:**
- Modify: `backend/scudo/projection_handler.py`
- Modify: `backend/scudo/template.yaml`
- Test: `backend/scudo/tests/test_projection_sweep.py` (create)

**Interfaces:**
- Consumes: Aurora `scudo.publish_outbox WHERE dispatched = false`.
- Produces: `sweep_outbox(*, execute=None) -> list[dict]` — reads undispatched rows, invokes the existing projection worker per row, flips `dispatched = true` only after all targets ack (at-least-once, idempotent).

**Context:** Today the projection worker is fed by the DynamoDB outbox + an EventBridge→SQS event per publish. With the outbox on Aurora, replace that feed with an EventBridge-scheduled sweep. The worker's per-record projection logic (Neptune/OpenSearch/FalkorDB) is unchanged — only the feed. Read `projection_handler.py` first to find the existing per-record entry point and reuse it.

- [ ] **Step 1: Write the failing sweep test**

```python
# backend/scudo/tests/test_projection_sweep.py
from __future__ import annotations


def test_sweep_marks_dispatched_only_after_projection(monkeypatch):
    from scudo import projection_handler as ph

    rows = [{"event_id": "e1", "detail_type": "MappingCompleted", "detail": {}}]
    dispatched = []
    monkeypatch.setattr(ph, "_fetch_undispatched", lambda: rows)
    monkeypatch.setattr(ph, "_project_one", lambda row: True)
    monkeypatch.setattr(ph, "_mark_dispatched", lambda eid: dispatched.append(eid))
    out = ph.sweep_outbox()
    assert dispatched == ["e1"]
    assert out[0]["event_id"] == "e1"


def test_sweep_does_not_mark_on_projection_failure(monkeypatch):
    from scudo import projection_handler as ph

    monkeypatch.setattr(ph, "_fetch_undispatched",
                        lambda: [{"event_id": "e2", "detail_type": "X", "detail": {}}])
    monkeypatch.setattr(ph, "_project_one", lambda row: (_ for _ in ()).throw(RuntimeError("boom")))
    marked = []
    monkeypatch.setattr(ph, "_mark_dispatched", lambda eid: marked.append(eid))
    ph.sweep_outbox()
    assert marked == []  # at-least-once: leave undispatched for retry
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_projection_sweep.py -v`
Expected: FAIL — `sweep_outbox` / helpers not defined.

- [ ] **Step 3: Implement the sweep**

Add `_fetch_undispatched()` (SELECT via `aurora_store._execute`), `_mark_dispatched(event_id)` (UPDATE `dispatched=true`), reuse the existing per-record projection as `_project_one(row)`, and:

```python
def sweep_outbox(*, execute=None) -> list[dict]:
    rows = _fetch_undispatched()
    swept = []
    for row in rows:
        try:
            _project_one(row)
        except Exception:
            log.exception("projection failed for %s; leaving undispatched", row["event_id"])
            continue
        _mark_dispatched(row["event_id"])
        swept.append(row)
    return swept
```

Add a scheduled entrypoint `handler(event, context)` that calls `sweep_outbox()`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest scudo/tests/test_projection_sweep.py -v`
Expected: PASS.

- [ ] **Step 5: Swap the template feed**

In `template.yaml`, replace the DynamoDB-stream / per-publish SQS trigger on the projection Lambda with an EventBridge schedule (e.g. `rate(5 minutes)`) targeting the new `handler`. Validate YAML.

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/projection_handler.py backend/scudo/tests/test_projection_sweep.py
git add backend/scudo/projection_handler.py backend/scudo/tests/test_projection_sweep.py backend/scudo/template.yaml
git commit -m "feat(scudo): Aurora outbox sweep feeds projections (at-least-once)"
```

---

## Task 9: Observability — structured CloudWatch EMF

**Files:**
- Create: `backend/scudo/metrics.py`
- Test: `backend/scudo/tests/test_metrics.py`

**Interfaces:**
- Produces: `emit(metric: str, value: float, *, unit: str = "Count", dims: dict[str, str] | None = None) -> None` — prints an EMF JSON line to stdout when running in Lambda (`AWS_LAMBDA_FUNCTION_NAME` set), no-ops otherwise so smoke gates stay silent.

**Context:** No Grafana, no OTel, no vendor SDKs — JPM's CloudWatch framework (Datadog → Dynatrace) forwards from CloudWatch (spec §7).

- [ ] **Step 1: Write the failing test**

```python
# backend/scudo/tests/test_metrics.py
from __future__ import annotations

import json


def test_emit_noop_outside_lambda(monkeypatch, capsys):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    from scudo import metrics

    metrics.emit("BandPass", 1)
    assert capsys.readouterr().out == ""


def test_emit_prints_emf_in_lambda(monkeypatch, capsys):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")
    from scudo import metrics

    metrics.emit("BandPass", 1, dims={"vendor": "lseg"})
    line = json.loads(capsys.readouterr().out.strip())
    assert line["BandPass"] == 1
    assert line["vendor"] == "lseg"
    assert "_aws" in line  # EMF metadata envelope
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest scudo/tests/test_metrics.py -v`
Expected: FAIL — no module `scudo.metrics`.

- [ ] **Step 3: Implement `metrics.py`**

```python
# backend/scudo/metrics.py
"""CloudWatch EMF metric emission. Structured stdout only — JPM's CloudWatch
framework (Datadog -> Dynatrace) forwards. No-ops outside Lambda so local
smoke gates stay silent."""
from __future__ import annotations

import json
import os
import time


def emit(metric: str, value: float, *, unit: str = "Count", dims: dict | None = None) -> None:
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return
    dims = dims or {}
    line = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "SCUDO",
                    "Dimensions": [list(dims.keys())] if dims else [[]],
                    "Metrics": [{"Name": metric, "Unit": unit}],
                }
            ],
        },
        metric: value,
        **dims,
    }
    print(json.dumps(line))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest scudo/tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Wire a few call sites**

Add `metrics.emit(...)` at the band decision (`matching.py` gate: `BandPass`/`BandBorderline`/`BandFail`), ETL pass/fail (`etl_handler.py`), and poller page counts (`poller_handler.py`). Keep it to counters — no new deps.

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
ruff check backend/scudo/metrics.py backend/scudo/tests/test_metrics.py
git add backend/scudo/metrics.py backend/scudo/tests/test_metrics.py backend/scudo/etl_handler.py backend/scudo/poller_handler.py backend/scudo_mapping_mcp/matching.py
git commit -m "feat(scudo): CloudWatch EMF metrics (no Grafana/OTel)"
```

---

## Task 10: Console MySQL → PostgreSQL port (separable — see Scope note)

**Files:**
- Modify: `backend/db.py`, `backend/init_db.sql`
- Test: the Flask console's existing route tests (grep `grep -rln "def test_" backend | xargs grep -l "console\|routes"`)

**Interfaces:**
- Consumes: Aurora `console` schema (Task 2 Step 5 DDL scope: create `console` schema alongside `scudo`).
- Produces: `backend/db.py` uses `psycopg` (v3) against the one Aurora cluster's `console` schema instead of MySQL.

**Context:** This retires the separate `scudo-poc-console-mysql` cluster — the single largest blast radius in the plan (spec §9 named risk). It is INDEPENDENT of the `scudo`-schema work (Tasks 2–4, 7, 8): those already land audit/catalogue/outbox on Aurora PG. Recommend executing this as its own branch/PR after Tasks 1–9 are green. **Read `backend/db.py` and `backend/init_db.sql` first** — the port is mechanical (driver swap + dialect: `AUTO_INCREMENT`→`GENERATED ... AS IDENTITY`, backticks→double-quotes, `%s`→`%s` psycopg params) but must match the console's actual queries.

- [ ] **Step 1: Port `init_db.sql` to PostgreSQL dialect** (translate types/identifiers; target the `console` schema).
- [ ] **Step 2: Swap `backend/db.py` to psycopg 3** (connection from the Aurora secret; keep the same function signatures the routes call).
- [ ] **Step 3: Run the console route tests** against the PG DDL; fix dialect breaks.
Run: `cd backend && python -m pytest -k "console or route" -v`
- [ ] **Step 4: Commit** `git commit -m "feat(console): port console DB from MySQL to Aurora PostgreSQL"`

---

## Task 11: Deploy handover doc

**Files:**
- Create: `infra/HANDOVER_5zone_alignment.md`

**Context:** Written for the deploying agent (CloudShell, `954976331678` / `us-east-1`). This is a documentation task — fold it into no other task. Content mirrors spec §9, made concrete against the template diffs produced above.

- [ ] **Step 1: Write the handover** covering, in order: (1) template diffs (poller Lambda + schedule + Secrets Manager; DynamoDB removal; `SCUDO_*_TABLE` env vars dropped, Aurora env in use); (2) Aurora DDL bootstrap via `init_data_platform.py` (`scudo` + `console` schemas); (3) console cutover (point Flask at Aurora PG, verify, then retire `scudo-poc-console-mysql` — only if Task 10 shipped); (4) ordered, separately-revertible steps — Aurora writes land first (smoke-verified), storage retirement (DynamoDB tables, MySQL cluster) last and only after the smoke gate passes; (5) smoke extension `scudo_post_deploy_smoke.sh`: poller dry-run → S3 drop → ETL → match → gate → publish → `GET /catalogue/{iri}` RDF/ODRL fetch → HITL decision write. State the named risk: retiring console MySQL + DynamoDB in one release is the largest blast radius; the sequencing keeps each retirement independently revertible; nothing deleted until the Aurora-backed smoke passes.

- [ ] **Step 2: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
git add infra/HANDOVER_5zone_alignment.md
git commit -m "docs(scudo): 5-zone alignment deploy handover"
```

---

## Scope note (writing-plans Scope Check)

Tasks 1–9 + 11 land the aligned architecture and are safe to execute in sequence on this branch; each ends green and independently revertible. **Task 10 (console MySQL→PostgreSQL)** is a separable subsystem — the spec's own "largest blast radius." Recommended split: execute Tasks 1–9 + 11 first (audit/catalogue/outbox already move onto Aurora PG, satisfying the diagram's single-Aurora lead item), then do Task 10 as its own plan/PR so the console cutover is reviewed and reverted in isolation.

## Self-Review

- **Spec coverage:** Zone 1 §3 → Task 5; Zone 2 §4 → Task 4; Zone 3 §5.1 → Task 1; Zone 3→4 §5.2 → Task 6; Zone 5 §6.1–6.2 → Tasks 2–3; §6.3 → Task 8; §6.4 → Task 7; §7 → Task 9; §9 → Task 11; §6.1 console schema → Task 10. §10 diagram facts are assertions for the diagram (out of code scope, per spec §11).
- **Type consistency:** `put_audit_record`/`put_review_record`/`put_outbox_record` keep identical kw-signatures across `aurora_store` (Task 2), `aws_resources` delegators (Task 3), and call sites (Tasks 4, 7). `SpecialistScorer` used consistently in Task 6. `sweep_outbox` helpers named identically in test + impl (Task 8).
- **Known follow-ups for the executor (files to read before coding, not placeholders):** exact scorer name in `scudo_mapping_mcp/specialist.py` (Task 6 Step 3); real serialiser function names in `rdf/real.py` + `tools.py` (Task 7 Step 3); existing per-record projection entrypoint in `projection_handler.py` (Task 8 Step 3); the console's actual queries in `backend/db.py` (Task 10). Each task names the file and the interface it must satisfy.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-04-scudo-5zone-alignment.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
