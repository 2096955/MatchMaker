# SCUDO — 5-zone alignment: deploy handover

**Date:** 2026-07-05
**Branch:** `scudo-phase0-foundations`
**Commits:** `eb48d67`..`bf2f50c` (Tasks 1–10 of `docs/superpowers/plans/2026-07-04-scudo-5zone-alignment.md`)
**For:** the deploying agent (CloudShell, account `954976331678` / `us-east-1`).

This is the Task 11 handover named by spec §9
(`docs/superpowers/specs/2026-07-04-scudo-5zone-alignment-design.md`). It
documents what actually shipped on this branch — including Task 10 (the
console MySQL→PostgreSQL port), which the plan's Scope Check recommended
splitting into a separate PR but which landed here instead (commit
`bf2f50c`). Everything below accounts for that.

---

## 1. Template diffs (`backend/scudo/template.yaml`)

- **New poller Lambda + EventBridge schedule + Secrets Manager entries**
  (Zone 1 third ingress route, Task 5): a config-driven vendor-API poller,
  scheduled via EventBridge, reading per-vendor credentials from Secrets
  Manager. This sits alongside the two existing black-box routes
  (MFT→FTP gateway, vendor-S3→DMS) — no API Gateway for vendor ingress, per
  spec §10.2.
- **DynamoDB tables removed.** `template.yaml:310` comment: "DynamoDB tables
  that were here are removed by the 5-zone persistence [move]." Verified
  clean: `rg "SCUDO_.*_TABLE|AWS::DynamoDB|dynamodb" backend/scudo/template.yaml`
  returns zero matches.
- **`SCUDO_*_TABLE` env vars dropped; Aurora env now mandatory.**
  `template.yaml:41-42`: "Aurora is mandatory since the DynamoDB tables were
  removed, so an empty value [for `SCUDO_AURORA_CLUSTER_ARN`] ... [fails
  loud]." `SCUDO_AURORA_CLUSTER_ARN`, `SCUDO_AURORA_SECRET_ARN`,
  `SCUDO_AURORA_DATABASE_NAME` are wired via CloudFormation `!Ref` Globals
  across all four Lambdas that touch persistence: `orchestrator`,
  `etl-worker`, `vendor-poller`, `projection-worker`. No defaults — an empty
  value must fail the stack, not silently no-op.

---

## 2. Aurora DDL bootstrap

`backend/scudo/init_data_platform.py` runs two idempotent bootstraps on the
one Aurora PostgreSQL cluster:

- `projection_handler._ensure_aurora_schema()` — creates
  `scudo_audit_events`, `scudo_catalog_projection`, and related unqualified
  tables (legacy naming, no `scudo.` schema prefix — predates the `scudo`
  schema convention below; left as-is, not a regression from this plan).
- `aurora_store.ensure_schema()` — creates schema `scudo` and its 7 tables
  (`scudo.audit_events`, `scudo.mapping_decisions`, `scudo.publish_outbox`,
  `scudo.lineage_facts`, `scudo.catalogue_products`, and two more) — this is
  the schema the fail-loud writers (`put_audit_record`, `put_review_record`,
  `put_outbox_record`, `catalogue.upsert_record`) target.
- **Console schema** (Task 10, `backend/init_db.sql`): `CREATE SCHEMA IF NOT
  EXISTS console;` and `CREATE SCHEMA IF NOT EXISTS ingestion;` on the same
  cluster. `console` holds the Flask console's own metadata (providers,
  datasets, run logs); `ingestion` holds the dynamically-created physical
  data tables. `backend/db.py` selects between them via `search_path` at
  connect time (`get_conn()` → `console`, `get_ingestion_conn()` →
  `ingestion`).

Run order for a fresh environment:
```bash
cd backend
python -m scudo.init_data_platform      # scudo + (legacy unqualified) tables
psql "$CONSOLE_DSN" -f init_db.sql      # console + ingestion schemas
```

**One cluster, four schemas total** (`public` default, `scudo`, `console`,
`ingestion`) — satisfies spec §10.1 ("one Aurora PostgreSQL... no separate
CDAO-catalogue database").

---

## 3. Console cutover (Task 10 — landed on this branch, not a separate PR)

The plan's Scope Check recommended executing Task 10 as an independent
PR/branch after Tasks 1–9 were verified green, because retiring console
MySQL is spec §9's named largest-blast-radius risk. That recommendation was
**not followed** — Task 10 is commit `bf2f50c` on this same branch, after
Tasks 1–9. This handover therefore folds the Task 10 cutover into the same
ordered rollout below rather than treating it as a separate release.

Steps, in order:
1. Point the Flask console (`backend/db.py`, now psycopg v3) at the Aurora
   `console` schema via `CONSOLE_DB_HOST`/`CONSOLE_DB_PORT`/`CONSOLE_DB_USER`/
   `CONSOLE_DB_PASSWORD`/`CONSOLE_DB_NAME` env vars (Secrets Manager-backed
   in the deployed stack). `db.py` now **refuses to connect** if
   `CONSOLE_DB_HOST` is non-local and `CONSOLE_DB_PASSWORD` is unset —
   confirms the secret is actually wired before any traffic hits Aurora.
2. Run the console route tests against the new DDL:
   `cd backend && python -m pytest tests/ -k "provider or dataset or readiness or db_connect" -v`
3. Smoke the console's read/write paths manually (add a provider, list
   datasets) against the Aurora-backed console before cutting traffic over.
4. **Only after step 3 passes**, retire `scudo-poc-console-mysql`. Do not
   delete the MySQL cluster or its snapshot ahead of this — see §4.

---

## 4. Ordered, separately-revertible rollout

Per spec §9's named risk ("retiring console MySQL + DynamoDB in one release
is the largest blast radius in this plan"), storage retirement is
deliberately split from the code rollout and sequenced last:

1. **Deploy code** (this branch's Lambdas + console image). Both DynamoDB
   writers and MySQL writers are already gone from the code — this step is
   Aurora-only from the application's point of view.
2. **Aurora writes land first, verified by smoke** (§5 below). Every
   persistence path — audit, catalogue, outbox, lineage, console metadata —
   must round-trip through Aurora successfully before anything is deleted.
3. **Storage retirement last, gated on the smoke passing:**
   - Take a final snapshot of `scudo-poc-console-mysql` before deleting the
     cluster.
   - Confirm no Lambda alias/version still references the removed DynamoDB
     table ARNs before deleting the tables (check CloudFormation drift, not
     just the current template).
   - Delete DynamoDB tables and the MySQL cluster **only after** the smoke
     gate in §5 is green against the deployed environment — not against a
     local/dev run.
4. Each retirement (DynamoDB, then MySQL) is independently revertible up
   until its delete step: nothing is destroyed until its Aurora replacement
   has been smoke-verified in the target environment.

---

## 5. Smoke extension (`infra/scudo_post_deploy_smoke.sh`)

The current script only probes the four ALB-backed HTTP paths and
target-group health. Before deleting any legacy storage, extend it (or run
the following as a manual pre-retirement gate) to cover the full path this
plan added:

1. **Poller dry-run** → confirm `vendor-poller` Lambda can invoke and log a
   dry-run pull without writing (Secrets Manager creds resolve).
2. **S3 drop** → land a test payload in the ingress bucket.
3. **ETL** → confirm the sanity-check Lambda processes it, writes
   `scudo.audit_events` + job status to Aurora (fail-loud — a write failure
   here must raise, not swallow).
4. **Match** → confirm the matching engine scores it and the confidence gate
   routes correctly at the 0.80/0.70 bands.
5. **Gate** → for a borderline (0.70–0.79) case, confirm the Bedrock
   specialist+verifier orchestrator is consulted (per `SCUDO_SPECIALIST_BACKEND`).
6. **Publish** → confirm an approved decision reaches `scudo.catalogue_products`
   and `scudo.publish_outbox`, and that `sweep_outbox` drains it (Neptune +
   OpenSearch + AppSync sinks, or however many are configured in the target
   environment).
7. **`GET /catalogue/{iri}`** → confirm the canonical RDF + adapted-ODRL
   response for the record just published.
8. **HITL decision write** → `POST /api/mapping/decision` with `ticket` +
   `decision=approve` + `iri`; confirm `scudo.mapping_decisions` and the
   catalogue/outbox both update. (An approve without `iri` must now 400 —
   verify that too, since it used to silently 200.)

Do not proceed to §4 step 3 (storage deletion) until all eight steps pass
against the deployed environment.

### 5a. HITL decision contract drift — review UI ↔ Lambda (open, pre-convergence)

The review UI's approve currently posts to the **Flask** console backend
(`backend/routes/mapping.py::record_decision`), whose contract is
`{vendor, product_id, decision, node_iri, suggested_confidence}`. Identity
(`decided_by`) is bound to the authenticated principal, never the body.

The **5-zone Lambda** route at the SAME path
(`backend/scudo/lambda_handler.py::handler`, the smoke target in step 8) has
a DIFFERENT contract: `{ticket, iri, mapping_object | mapping_result}`. An
approve returns **400** without a `ticket` ("decision requires a ticket"),
without an `iri` ("approve decision requires an iri"), and without
`mapping_object`/`mapping_result` ("approve decision requires a
mapping_object or mapping_result"); on success it publishes to
`scudo.catalogue_products` + `scudo.publish_outbox`.

These are two different backends today, so nothing breaks — but the payloads
are **incompatible**, and neither front-end (deployed React `frontend/`, viz
`dashboard/`) sends a `ticket` or `mapping_object` anywhere. **When zones
converge on the Lambda path, the review UI needs new wiring** to send
`ticket` + `iri` + `mapping_object` (or `mapping_result`); until then a
front-end pointed at the Lambda route 400s on every approve. Track this as an
explicit pre-convergence task — it is NOT covered by the step-8 smoke, which
hand-builds a Lambda-shaped body.

---

## Named risk (spec §9, restated)

Retiring console MySQL + DynamoDB in the same release as the code that
replaces them is the largest blast radius in this plan. The sequencing in
§4 keeps each retirement independently revertible — code deploy, then
Aurora-write verification, then storage deletion, in that order, with a
snapshot taken before the MySQL cluster is removed. **Nothing is deleted
until the Aurora-backed smoke in §5 passes against the target environment.**
