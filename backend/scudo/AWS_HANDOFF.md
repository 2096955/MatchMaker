# SCUDO `scudo-poc` AWS Handoff — Verified Review Notes

> **For the next agent.** This supplements `DEPLOY.md` (how to deploy) with an
> **independent verification** of what was actually built, produced via a
> self-verifying loop (maker swarm → independent verifier → hand-verify of
> load-bearing findings). Every claim below was checked against the committed
> code/template. **Live AWS was NOT re-queried** — the local shell has no
> credentials (`aws sts get-caller-identity` → `NoCredentials`); re-verify the
> running stack from CloudShell. The deploy success itself is self-reported by
> the deploying agent (ran from an authenticated CloudShell).

Verified 2026-06-22 against the working tree (this work is **uncommitted** — see §6).

## 1. Target

- **Account:** `954976331678` (`cb4115669a-genaipocs-aw`, Cognizant cloudboost sandbox — *not* JPMC prod)
- **Region:** `us-east-1`
- **Stack:** `scudo-poc` (SAM, `backend/scudo/template.yaml`)
- **Bedrock model:** `us.anthropic.claude-opus-4-8` (us cross-region inference profile — matches us-east-1; resolves the older eu-west-2/`eu.` targeting for this slice).
- ⚠️ The **older** `infra/scudo-dev-foundation.yaml` / `scudo-dev-deploy.yaml` templates still name **eu-west-2** (Fargate/Neptune foundation). Only this new `scudo-poc` slice is us-east-1. Don't conflate the two.

## 2. Provisioned by the template (confirmed in `template.yaml`)

- **5 S3 buckets** (all SSE-AES256, public-access-blocked, versioned): raw-feed (EventBridge-enabled), clean-canonical, quarantine, vendor-catalog, cdao-catalog.
- **Event backbone:** EventBridge rule on raw-feed `Object Created` → `EtlQueue` (SQS, +DLQ, maxReceive 3); custom bus `scudo-poc-events`; `PersistenceProjectionQueue` (+DLQ) fed by a rule matching `MappingCompleted`/`CanonicalMetadataReady`.
- **5 DynamoDB tables** (PAY_PER_REQUEST, SSE, PITR): `-jobs`, `-facts`, `-audit-log`, `-human-review`, `-transaction-outbox`.
- **2 Lambdas** (container image, 3008 MB, 90 s): `scudo-poc-orchestrator` (HTTP API `GET /health`, `POST /run` @ 29 s gateway timeout) and `scudo-poc-etl-worker` (SQS-triggered from `EtlQueue`).
- **HTTP API** stage `prod` (throttle 5/5), 2 CloudWatch log groups (14-day retention).
- **IAM:** verified least-privilege and **complete** — every S3/DynamoDB/EventBridge/Bedrock call the code makes is granted; no call is unauthorized.

## 3. Wired & confirmed (in code)

- **ETL path** (`etl_handler.py`): raw S3 object → SHA-256 hash → clean canonical-**metadata** JSON to clean bucket (or error doc to quarantine on failure); writes `-jobs` (PROCESSING→PASSED/FAILED), `-facts`, audit `ETL_PASSED`/`ETL_FAILED`, emits `CanonicalMetadataReady`. Handles SQS-wrapped EventBridge, raw EventBridge, and native S3 shapes.
- **`/health`** returns `env_resource_summary()` (the provisioned resource names).
- **Publish path** (`lambda_handler.py`): writes audit (`MAPPING_<OUTCOME>`), outbox, EventBridge, and a human-review record when an HITL ticket exists.
- **RDF**: deterministic DCAT triples serialised **before** the publish gate (`orchestrator.py:142` → `rdf/fake.py`).
- **Fail-soft AWS adapter** (`aws_resources.py`): boto3 is lazy; every `put_*` no-ops on missing env and swallows exceptions → local smoke runs with no creds; `python -m scudo.tests.smoke` passes (`SCUDO SMOKE OK`).
- **Store seams** keep working with empty `Neptune/OpenSearch/Aurora` endpoints (mock fallback).

## 4. Load-bearing caveats (VERIFIED — fix before treating as more than a PoC)

1. **`MappingCompleted` fires on EVERY outcome** (`lambda_handler.py:243-265`). Outbox + event-bus `detail_type` is hardcoded `"MappingCompleted"` even for HITL / RETRY / RESEARCH_QUEUED — only the *audit* `event_type` is outcome-aware. Downstream projection consumers would treat a human-review case as a completed mapping. **Gate emission on `obj.outcome == PUBLISHED`.**
2. **The matcher runs over MOCK candidates** (`lambda_handler.py:54,91`). `/run` builds candidates from `sidecar_mock.candidate_nodes(...)` unconditionally — real Bedrock specialist+verifier judgement, but a **mock candidate set**. No FalkorDB/Neptune/OpenSearch retrieval. The dense/vector (Titan + OpenSearch k-NN) arm is **not** implemented here.
3. **Silent persistence loss** (`aws_resources.py` + `lambda_handler.py:269`). Audit/outbox/event writes are fail-soft and never checked, so a write failure still returns HTTP 200 — client sees "published", audit trail may be empty.

## 5. Lower-severity findings

- **`PersistenceProjectionQueue` has no consumer Lambda** — `MappingCompleted`/`CanonicalMetadataReady` events accumulate unconsumed (this is the seam where the Neptune/OpenSearch/Aurora projector attaches; expected, since those stores aren't created).
- **6 env vars set-but-unread** by live code (queue URLs, vendor/cdao buckets, the 3 store endpoints) — introspection-only via `/health`.
- **`env_resource_summary()` omits `SCUDO_JOB_TABLE`** — `/health` under-reports one of the 5 tables.
- **`etl_handler.py:71-72`** uses `os.environ[...]` (no fallback) for clean/quarantine buckets — crashes if unset (they are set in the deployed Lambda).
- **No offline tests** for `aws_resources.py` / `etl_handler.py` — the new AWS code has zero unit coverage in `tests/smoke.py`.
- `AWS_REGION_OVERRIDE` (template) is unread; code reads `AWS_REGION` (Lambda auto-sets it) — harmless.

## 6. NOT done / explicitly out of scope

- **Neptune, OpenSearch, Aurora are seam parameters only** (`NeptuneSparqlEndpoint`/`OpenSearchEndpoint`/`AuroraClusterArn`, default `""`) — **not created** by this stack. The full diagram's always-on stores remain unprovisioned.
- **Work is uncommitted** in the working tree: `template.yaml`, `etl_handler.py`, `aws_resources.py`, `lambda_handler.py`, `orchestrator.py`, `tests/smoke.py`, `requirements-lambda.txt`, `README.md`. Commit before relying on it.
- Cold `/run` can exceed the 29 s gateway timeout (Lambda cold init + Bedrock). Warm calls succeed (~19 s observed). Fix = provisioned concurrency or async job API.

## 7. Next-agent actions

1. Re-verify the live stack from CloudShell (see `DEPLOY.md` §Smoke Checks: `describe-stacks` outputs + `curl $HealthUrl`).
2. If acting on the matcher: caveats #1–#3 are the priority fixes.
3. `/run` needs `x-api-key`; never commit the key.

## 8. Related Claude sessions (resume for context)

| Session | Resume |
|---|---|
| scudo-matching-durability-plan | `claude --resume 213a8b4a-6b65-488f-8d02-e3fe8362e3d0` |
| ultracode (UA dogfood) | `claude --resume 202d635a-f9ff-4510-857d-3a38e0119254` |
| scudo-phase0-foundations | `claude --resume 1096f0c5-ca44-45f5-83d3-c899f0f411c8` (context only) |
