# SCUDO 5-Zone Architecture Alignment — Design

**Date:** 2026-07-04
**Branch:** `scudo-phase0-foundations`
**Status:** Approved in conversation; written for user review
**Deliverable split:** this repo produces code + CloudFormation + a deploy handover; a
separate agent with AWS access to `954976331678` / `us-east-1` executes the deployment.
The amended architecture diagram (due Monday 2026-07-06 for Pierre) is produced
elsewhere; §10 lists the facts the diagram and this codebase must agree on.

---

## 1. Context and goal

JPM (Nigel Phelan) signed off the 5-zone SCUDO Market Data Catalogue architecture on
2026-07-03, incorporating Pierre's review corrections. This design aligns the MatchMaker
codebase and its CloudFormation with that agreed architecture:

- **Zone 1** — vendor sources land in S3 via three black-box routes (MFT→FTP gateway,
  vendor-S3→DMS, poll-API→push). API Gateway for vendor ingress is dropped.
- **Zone 2** — a single S3-triggered sanity-check Lambda routes files to a processing
  (canonical) or rejected bucket and writes audit records to the database.
- **Zone 3** — matching engine: Parse & Normalise → Semantic Matching → Rank & Score,
  three steps in one component, ending at a confidence gate (80% floor).
- **Zone 4** — Bedrock dual-agent (specialist + verifier) under an orchestration layer,
  consulted only for the 70–79% band.
- **Zone 5** — ONE Aurora PostgreSQL as single source of truth (catalogue, CDAO
  taxonomy, lineage, audit); SCUDO API endpoints publish canonical RDF + ODRL; human
  review (SCUDO UI) handles the <70% band.
- **Observability** — structured CloudWatch only; JPM's framework (Datadog → Dynatrace)
  forwards. No Grafana, no OTel, no vendor SDKs.

### Decisions locked during brainstorming

| Question | Decision |
|---|---|
| Scope depth | Code + templates verified locally; deploy executed by another agent from our handover notes |
| Confidence bands | Adopt diagram numbers: pass ≥ 0.80, borderline ≥ 0.70 (from 0.85/0.75) |
| Zone 1 SFTP | No Transfer Family — MFT gateway is a JPM-owned black box; we build only the poller |
| Aurora | Full migration: ONE Aurora PostgreSQL cluster; DynamoDB tables removed; console MySQL retired |
| Observability | Structured CloudWatch logs + EMF metrics only |
| Zone 3→4 join | Cost-ladder borderline band consults the Strands specialist+verifier orchestrator |
| Diagram | Out of scope here; spec records the facts it must reflect |

---

## 2. Current state (verified in code)

> **Stale as of 2026-08-17 — historical record.** This section states the bands as of
> 2026-07-04, **before** the change this very document designs. "Current state" here means
> the **pre-change** state: `CONFIDENCE_FLOOR = 0.80` → pass `0.85` / borderline `0.75` was
> the starting point that §1's decision table ("Adopt diagram numbers: pass ≥ 0.80,
> borderline ≥ 0.70 (from 0.85/0.75)") moved away from. The floor moved to `0.75`
> (PASS `0.80` / FAIL `0.70`) under
> `docs/superpowers/plans/2026-07-04-scudo-5zone-alignment.md` Task 1
> ("Confidence bands 0.85/0.75 → 0.80/0.70"). Retained unedited as the record of what was
> true then — **rewriting these numbers would erase the evidence that the change ever
> happened**, since this doc's before-state is the only in-repo statement of the values the
> change started from. Live values: `docs/superpowers/matching-data-provenance.md`.

- `backend/scudo_mapping_mcp/` — the Zone-3 cost-ladder engine (Flask/ECS console
  pipeline): parse/normalise, dense+BM25 semantic match, rank/score, 3-band gate.
  Bands come from `config.py`: `CONFIDENCE_FLOOR = 0.80`, `BORDERLINE_HALF_WIDTH = 0.05`
  → pass = 0.85, borderline = 0.75 (2dp-rounded in `pass_threshold()` /
  `borderline_threshold()`). The borderline band calls a single in-process specialist
  (`matching.py` Rung 5).
- `backend/scudo/` — the Lambda orchestrator (Zone 4): Strands specialist + verifier
  agents on Bedrock, deterministic publish gate (`orchestrator.py`: verifier ≥ 16,
  confidence ≥ 0.80, IRI shape, named graph). `etl_handler.py` is the sanity-check
  Lambda (validate → clean/quarantine buckets). `lambda_handler.py` serves `/health`
  and the mapping POST.
- Persistence today: audit/facts/review/outbox/jobs in **DynamoDB**
  (`aws_resources.py`, `template.yaml`); Aurora PG provisioned but barely used;
  a **separate Aurora MySQL** cluster backs the Flask console (`backend/db.py`,
  `init_db.sql`); Neptune/OpenSearch/FalkorDB hold graph/search projections.
- Zone 1 today: nothing before the raw S3 bucket. `template.yaml` already wires
  raw-bucket ObjectCreated → EventBridge → SQS → ETL Lambda.
- RDF/ODRL serialisers exist (`backend/scudo/rdf/real.py`, `tools.py`) but no public
  catalogue-read endpoint exposes them.
- No APM instrumentation; CloudWatch logs only.
- Known gotchas that constrain this work: pytest collects nothing (standalone smoke
  runners are the gates); the 0.85-edge float bug was fixed by 2dp rounding — the new
  band edges must keep that; the reviewer-tunable band window (floor/half kwargs
  threaded through `matching.py`, `agent.py`, `routes/mapping.py`) must keep working.

---

## 3. Zone 1 — ingress: one poller, two black boxes

Our contract with all three ingress routes is: **files appear in the landing (raw)
bucket; ObjectCreated events fire**. The existing template wiring consumes them
unchanged.

### 3.1 Config-driven vendor-API poller (built here)

New `backend/scudo/poller_handler.py`, one Lambda for all vendors:

- **Trigger:** EventBridge schedule, default twice a month (`cron`), per-vendor cadence
  override in config.
- **Config:** JSON document in S3, located by the `SCUDO_POLLER_CONFIG` env var
  (`s3://bucket/key`), keyed per vendor:
  `{vendor, endpoint, auth_style, pagination, cadence, enabled}`. Onboarding a new
  vendor is a config change, never a new Lambda.
- **Secrets:** API keys fetched at runtime from Secrets Manager
  (`scudo/poller/<vendor>`), never in code or config.
- **Memory guard:** pages stream straight to S3 multipart under
  `api/<vendor>/<date>/page-<n>.json` — flat memory regardless of catalogue size.
- **Reserve path (documented, not built):** Step Functions fan-out across invocations,
  or a Fargate task, for a vendor whose pull cannot finish in one Lambda window.
- Ships with a stub vendor config (httpbin-style endpoint) because no real vendor
  credentials exist in the PoC account.

### 3.2 MFT→FTP gateway and vendor-S3→DMS (black boxes, not built)

Both are JPM-owned patterns (MFT = existing MDS EDI pattern in the JPM account).
Documented in the handover as external routes whose landing prefix is
`sftp/<vendor>/` and `dms/<vendor>/` respectively, with a parameterised
bucket-policy + replication-role CloudFormation snippet (disabled by default) for the
DMS route. No Transfer Family server in our templates.

---

## 4. Zone 2 — single sanity-check Lambda

`backend/scudo/etl_handler.py` already matches the agreed shape (one Lambda with a
function list; S3-triggered; routes to processing/rejected buckets). Two changes:

1. **Audit to Aurora.** `put_audit_record` and the job/facts writes re-point at the
   Aurora store (§6). Bucket names stay (`SCUDO_CLEAN_BUCKET` = processing/canonical,
   `SCUDO_QUARANTINE_BUCKET` = rejected).
2. **Real sanity check.** Today a file "passes" if S3 metadata can be read. Add
   per-vendor payload validation before canonicalisation: parseability (JSON/CSV),
   required-field presence per vendor adapter, size/row sanity. Failures land in the
   rejected bucket with a machine-readable reason — same fail-soft quarantine
   behaviour as now. Fail-soft applies to *bad files* only; a failed **audit write**
   raises (fail-loud, §6.2), so the SQS message retries and lands in the DLQ rather
   than silently losing the audit trail.

All three ingress prefixes (`sftp/`, `dms/`, `api/`) feed this one Lambda.

---

## 5. Zones 3→4 — confidence gate and the dual-agent band

### 5.1 Band change (0.85/0.75 → 0.80/0.70)

`backend/scudo_mapping_mcp/config.py`: `CONFIDENCE_FLOOR = 0.75`,
`BORDERLINE_HALF_WIDTH = 0.05` → `pass_threshold()` = **0.80**,
`borderline_threshold()` = **0.70**. The 2dp rounding stays (guards the exact-edge
float bug). Ripples, all updated in the same change:

- band-edge tests (a score of exactly 0.80 must land PASS; exactly 0.70 must land
  BORDERLINE);
- `backend/scudo/build_matching_graph.py` band labels (reads config — verify it
  picks up the new values, then regenerate the dashboard graph fixture);
- dashboard default review-band display (dashboard repo — handover note; the
  reviewer-tunable window itself is unchanged, only its defaults move);
- docs citing 0.85/0.75 (`infra/HANDOVER_hitl_bands_2026-06-26.md` examples stay
  historical; README/AGENTS references updated).

The Zone-4 orchestrator's separate publish gate (`backend/scudo/orchestrator.py`
`CONFIDENCE_FLOOR = 0.80`) already matches the diagram's "≥ 80% auto-approve" and
does not change.

### 5.2 Borderline band consults the Bedrock dual-agent orchestrator

Today `scudo_mapping_mcp/matching.py`'s BORDERLINE branch calls a single in-process
specialist. Change: introduce a `SCUDO_SPECIALIST_BACKEND` seam
(`local` | `strands` | `rest`, default `local`):

- **local** — current in-process specialist; smoke gates keep passing with no AWS.
- **strands** — invoke the Zone-4 orchestrator in-process (Lambda deployment):
  specialist proposes a mapping, verifier scores the 10-dimension rubric, and the
  existing deterministic publish gate (verifier ≥ 16, confidence ≥ 0.80, IRI shape,
  named graph) decides auto-approve vs HITL.
- **rest** — the ECS console calls the Lambda orchestrator over HTTP (builds on
  `tests/test_rest_specialist.py`), so both deployments use the same Zone-4 brain.

Invariants preserved: ≥ 0.80 never calls an LLM; < 0.70 goes straight to human
review; required-validation failures still hard-FAIL without consulting the
specialist; the off-list-pick fail-closed rule stays (an orchestrator answer whose
IRI is not in the ladder's candidate set → NEEDS_REVIEW, pick discarded).

---

## 6. Zone 5 — ONE Aurora PostgreSQL

The lead review item ("draw it once and route all interactions to the same
instance — even if messier"). Storage consolidation, made literal:

### 6.1 Single cluster, two schemas

One Aurora PostgreSQL Serverless v2 cluster (`scudo-poc-aurora`, already
provisioned), accessed via the **RDS Data API** (no VPC drivers in Lambda):

- schema `scudo`: `audit_events`, `lineage_facts`, `catalogue_products`,
  `cdao_taxonomy` (folded in — no separate catalogue DB), `mapping_decisions`
  (HITL verdicts), `publish_outbox`.
- schema `console`: the Flask console's state, ported from MySQL
  (`backend/init_db.sql` + `backend/db.py` move to PostgreSQL dialect).

The separate `scudo-poc-console-mysql` Aurora MySQL cluster is **retired**.

### 6.2 Writers move, DynamoDB goes

New `backend/scudo/aurora_store.py` implements `put_audit_record`,
`put_review_record`, `put_outbox_record`, facts/lineage and job-status writes.
`aws_resources.py` call sites re-point to it; the five DynamoDB tables come out of
`template.yaml`. PoC data is disposable — no backfill, just ordered cutover (§9).

**Fail-loud:** Aurora writes raise on failure and fail the request. Today's
fail-soft hiding of persistence errors is a known handover caveat; the migration is
the moment it gets fixed. (The ETL quarantine path stays fail-soft — a bad vendor
file is data, not an outage.)

### 6.3 Projections and the outbox sweep

Neptune (RDF), OpenSearch (search) and FalkorDB (dev graph) become explicit
**projections** of Aurora records. Today the projection worker is fed by the
DynamoDB outbox + an EventBridge→SQS event per publish; with the outbox moving to
Aurora, that feed is replaced by an EventBridge-scheduled sweep of
`publish_outbox WHERE dispatched = false` (at-least-once, idempotent projections;
`dispatched` flipped only after all targets ack). The existing projection worker
(`projection_handler.py`) keeps its job; only its feed changes.

### 6.4 SCUDO API endpoints (canonical RDF + ODRL publish)

New read routes on the Lambda API — consumers never touch Neptune directly:

- `GET /catalogue` — list approved catalogue records (from Aurora).
- `GET /catalogue/{iri}` — one record as canonical RDF (Turtle / JSON-LD via
  `rdf/real.py`) plus its adapted-ODRL rights policy (`tools.py` serialiser).

Human decisions (`POST /api/mapping/decision` from the SCUDO UI) write to
`mapping_decisions` and feed the same publish path — approved records flow to the
catalogue and outbox exactly like auto-approved ones, closing the diagram's
"human decision → approved records" loop.

---

## 7. Observability — structured CloudWatch only

JSON structured logs plus CloudWatch EMF metrics from the Lambdas and the console:
band distribution, auto-approve rate, ETL pass/fail counts, agent latency, HITL
queue depth, poller pull sizes. No Grafana, no OTel, no vendor SDKs — JPM's
CloudWatch framework (Datadog → Dynatrace) forwards. A small
`backend/scudo/metrics.py` helper wraps EMF emission and no-ops outside Lambda so
smoke gates stay green.

---

## 8. Testing

TDD throughout, extending the standalone smoke-runner pattern (pytest collects
nothing in this repo — the smoke runners are the gates):

- **Band edges:** exactly 0.80 → PASS, exactly 0.70 → BORDERLINE, just below 0.70 →
  FAIL; reviewer-tunable window still round-trips at 4dp.
- **Poller:** unit tests with stubbed Secrets Manager + HTTP (pagination, streaming
  writes, per-vendor config selection, disabled vendors skipped).
- **Aurora store:** tests against a fake Data API client (SQL + parameter shape
  asserted; fail-loud verified).
- **Specialist seam:** contract tests for local/strands/rest backends; off-list-pick
  fail-closed test at the seam boundary.
- **End-to-end:** memory-backend run of upload → ladder → (fake) dual-agent → gate →
  publish → catalogue RDF/ODRL fetch.
- **Console port:** the Flask console's route tests run against the PostgreSQL DDL.

---

## 9. Deploy handover (`infra/HANDOVER_5zone_alignment.md`)

Written for the deploying agent (CloudShell, `954976331678` / `us-east-1`):

1. **Template diffs:** poller Lambda + EventBridge schedule + Secrets Manager
   entries; DynamoDB table removal; env-var contract changes (new `SCUDO_AURORA_*`
   usage, dropped `SCUDO_*_TABLE` vars).
2. **Aurora DDL bootstrap:** `init_data_platform.py` extended to create the `scudo`
   and `console` schemas.
3. **Console cutover:** point the Flask console at Aurora PG, verify, then retire
   `scudo-poc-console-mysql`.
4. **Ordered, separately-revertible steps:** Aurora writes land first (verified by
   smoke), storage retirement (DynamoDB tables, MySQL cluster) last and only after
   the smoke gate passes.
5. **Smoke extension:** `scudo_post_deploy_smoke.sh` covers poller dry-run → S3
   drop → ETL → match → gate → publish → `GET /catalogue/{iri}` RDF/ODRL fetch →
   HITL decision write.

**Named risk:** retiring console MySQL + DynamoDB in one release is the largest
blast radius in this plan. The sequencing above keeps each retirement independently
revertible; nothing is deleted until the Aurora-backed smoke passes.

---

## 10. Facts the amended diagram and this codebase must agree on

The Monday diagram is produced elsewhere; these are the invariants both must state:

1. **One Aurora PostgreSQL**, drawn once, all DB interactions routed to it — audit,
   catalogue, CDAO taxonomy, lineage, decisions, outbox, console state. No separate
   CDAO-catalogue database.
2. **Three ingress routes, all black boxes landing in one S3 bucket:** MFT→FTP
   gateway (JPM-owned, existing MDS EDI pattern), vendor-S3→DMS, EventBridge-scheduled
   config-driven poller (Secrets Manager keys). **No API Gateway for vendor ingress.**
3. **One sanity-check Lambda** with a function list, routing to processing vs
   rejected buckets, audit to Aurora.
4. **Matching engine = A/B/C in one component**; confidence gate at **0.80 floor**;
   **0.70–0.79 → Bedrock specialist+verifier under an orchestration layer**;
   **< 0.70 → human review (SCUDO UI)**.
5. **Neptune is never shown as a direct dependency** — consumers call SCUDO API
   endpoints (canonical RDF + ODRL publish).
6. **Observability:** CloudWatch framework (Datadog → Dynatrace), no Grafana.
7. **Non-Lambda compute exists:** FalkorDB runs on Fargate; the poller's oversized
   pulls have a Step Functions/Fargate reserve path (answers Pierre's serverless
   concern — worth annotating on the diagram).

---

## 11. Out of scope

- The amended diagram itself (produced elsewhere).
- Executing the AWS deployment (deploying agent, from §9's handover).
- Real vendor credentials/endpoints for the poller (stub config until onboarding).
- The Step Functions/Fargate heavy-pull path (documented reserve, not built).
- Two-way HITL chat (remains spec-only, per the HITL handover).
- AppSync steward-channel auth hardening (pre-existing PoC caveat, unchanged here).
