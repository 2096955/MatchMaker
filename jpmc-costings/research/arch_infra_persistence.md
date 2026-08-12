# SCUDO Architecture Inventory — Sections A, C, D

(Section B — LLM call model — covered by a separate agent; skipped here.)

## A. Component Inventory

Two co-existing IaC surfaces exist in-repo, both scudo-poc/dev scale, neither JPMC-prod scale:

1. **SAM stack** — `backend/scudo/template.yaml` (Lambda + S3 + SQS + EventBridge + HttpApi), paired with `backend/scudo/data-platform.yaml` (Aurora/Neptune/OpenSearch/AppSync/console-MySQL) and `backend/scudo/network-falkordb.yaml` (VPC + FalkorDB-on-Fargate). This is the "Lambda-orchestrator" deploy path (`us-east-1`, account 954976331678 per AWS_HANDOFF.md).
2. **Raw CFN dev stack** — `infra/scudo-dev-foundation.yaml` + `infra/scudo-dev-deploy.yaml` — an ECS-Fargate-only deploy (5 services behind one ALB: Flask, Ingestion MCP, Match-Verify MCP, Persistence MCP, FalkorDB), `eu-west-2`, explicitly labelled "dev / demo loop. Not the JPMC production target" (`infra/scudo-dev-deploy.yaml:17`). Has its own Neptune cluster + DynamoDB reviewer-queue table not present in the SAM stack.

These are **not the same running system** — they are alternative deploy targets for the same codebase (Lambda-centric vs ECS-centric). Sizing below is kept per-stack.

### A.1 Lambda functions (SAM stack, `backend/scudo/template.yaml`)

4 functions, all `PackageType: Image`, `x86_64`. Global defaults `Timeout: 90`, `MemorySize: 3008` (`template.yaml:129-131`); memory is never overridden per-function (SAM template sets no per-function `MemorySize`), only `Timeout` is overridden on 2 of the 4:

| Function | Logical id | Memory | Timeout | Trigger | VPC |
|---|---|---|---|---|---|
| `${Stack}-orchestrator` (matching/agent Lambda) | `ScudoFn` | 3008 MB (global default) | 90s (global default; API-GW route itself caps at 29000ms, `template.yaml:431`) | HttpApi `POST /run`, `GET /health` | Yes (private subnets + lambda-sg) |
| `${Stack}-etl-worker` | `EtlFn` | 3008 MB (default) | 90s (default) | SQS (`EtlQueue`, BatchSize 5) | No (needs to reach vendor endpoints, comment at `template.yaml:488-491`) |
| `${Stack}-vendor-poller` | `PollerFn` | 3008 MB (default) | **300s override** (`template.yaml:498`) | EventBridge Schedule `cron(0 6 1,15 * ? *)` — twice-monthly | No |
| `${Stack}-projection-worker` | `ProjectionFn` | 3008 MB (default) | **180s override** (`template.yaml:538`) | EventBridge Schedule `rate(5 minutes)` — outbox sweep | Yes |

Note: 3008 MB is a notably large default for all 4 functions incl. the lightweight poller/ETL workers — likely inherited rather than tuned; no per-function memory sizing evidence exists in the template.

### A.2 SQS queues (SAM stack)

| Queue | Purpose | Visibility timeout | Redrive |
|---|---|---|---|
| `EtlQueue` | Raw S3 ObjectCreated → ETL Lambda | 180s | DLQ after 3 receives (`EtlDeadLetterQueue`, 14-day retention) |
| `EtlDeadLetterQueue` | DLQ for above | — | — |
| `PersistenceProjectionQueue` | Legacy EventBridge→SQS projection feed | 180s | DLQ after 3 receives (`PersistenceProjectionDeadLetterQueue`) |
| `PersistenceProjectionDeadLetterQueue` | DLQ for above | — | — |

`PersistenceProjectionQueue`'s feeder rule (`PersistenceProjectionRule`) is **`State: DISABLED`** (`template.yaml:343`) — replaced by the Aurora `publish_outbox` sweep on `ProjectionFn`. Queue + DLQ are provisioned but orphaned ("harmless" per inline comment, `template.yaml:340-342`).

### A.3 EventBridge

| Resource | Type | Role |
|---|---|---|
| `ScudoEventBus` | custom bus (`${Stack}-events`) | carries `scudo.matchmaker` / `MappingCompleted` / `CanonicalMetadataReady` detail-types |
| `RawFeedIngressRule` | rule on **default** bus | S3 `Object Created` on `RawFeedBucket` → `EtlQueue` (real, enabled) |
| `PersistenceProjectionRule` | rule on `ScudoEventBus` | disabled (see above) |
| `PollerFn` Schedule | EventBridge Scheduler | `cron(0 6 1,15 * ? *)` |
| `ProjectionFn` OutboxSweepSchedule | EventBridge Scheduler | `rate(5 minutes)` — this is the live outbox-drain mechanism, not the disabled rule |

### A.4 S3 buckets (SAM stack)

5 buckets, all with `BlockPublicAcls`, AES256 SSE, versioning enabled: `RawFeedBucket` (EventBridge-notification-enabled), `CleanCanonicalBucket`, `QuarantineBucket`, `VendorCatalogBucket`, `CdaoCatalogBucket` (`template.yaml:176-257`). Dev-stack (`scudo-dev-foundation.yaml`) has a separate, single `FramesBucket` (M8 working-set bucket, `infra/scudo-dev-foundation.yaml:345-360`) — not the same bucket set.

### A.5 Aurora

- **SAM/data-platform stack** (`backend/scudo/data-platform.yaml`): two Aurora clusters —
  - `AuroraCluster` (aurora-postgresql, Serverless v2, `MinCapacity 0.5` / `MaxCapacity 1` ACU by default, `EnableHttpEndpoint: true` for RDS Data API, `AuroraWriter` instance class `db.serverless`) — the Zone-5 system of record (`scudo` schema).
  - `ConsoleAuroraCluster` (aurora-**mysql**, Serverless v2, 0.5–2 ACU, `ConsoleAuroraInstance` class `db.serverless`) — backs the Flask console. ZONES.md/MEMORY note this was later ported MySQL→PostgreSQL in a different, undocumented-here migration; data-platform.yaml as written still provisions MySQL.
  - Both are `db.serverless` (Aurora Serverless v2), **not a fixed instance class** like `db.r6g.2xlarge` — this is a material divergence from the client list's "Aurora 2xlarge" expectation (see Section D).
- No `infra/scudo-dev-*.yaml` Aurora resource was found — the dev/ECS stack has no Aurora of its own; it presumably points at the same data-platform Aurora via imports (not evidenced in the two files read).

### A.6 ECS task CPU/memory

**SAM/network-falkordb.yaml** (`us-east-1`, single-service, no ALB): `FalkorTaskDef` — `Cpu: '512'`, `Memory: '1024'`, Fargate, 1 desired count, ephemeral (no EFS) (`network-falkordb.yaml:200-222`).

**Dev/ECS stack** (`infra/scudo-dev-deploy.yaml`, `eu-west-2`, 5 services behind one ALB):

| Task | Cpu | Memory | Notes |
|---|---|---|---|
| `FalkorTaskDefinition` | `FalkorTaskCpu` = **1024** (default) | `FalkorTaskMemory` = **2048** (default) | no AWS IAM perms needed |
| `FlaskTaskDefinition` | hardcoded **512** | hardcoded **1024** | gunicorn, 4 threads, 300s app timeout |
| `IngestionTaskDefinition` | `TaskCpu` = **256** (default; allowed 256/512/1024) | `TaskMemory` = **512** (default; allowed 512/1024/2048) | tier-1 trust, no signing key |
| `MatchVerifyTaskDefinition` | `TaskCpu` (256 default) | `TaskMemory` (512 default) | tier-2, reads verdict signing key |
| `PersistenceTaskDefinition` | `TaskCpu` (256 default) | `TaskMemory` (512 default) | tier-3 sole writer, reads signing key |

(Parameter defaults at `infra/scudo-dev-deploy.yaml:70-85`.) These are dev-scale Fargate sizes (0.25–1 vCPU, 0.5–2 GB) — far below any JPMC-prod sizing assumption.

### A.7 OpenSearch / Neptune / DynamoDB / AppSync

- **OpenSearch**: `OpenSearchDomain` in `data-platform.yaml` — `OpenSearch_2.13`, 1 node, `t3.small.search` (default, param `OpenSearchInstanceType`), 10 GB gp3, VPC-joined, HTTPS-enforced. **Template-real** (a real domain gets created) but the write/read path is a seam: `matcher_bridge.py:1-45` documents the Lambda `/run` path today runs over an **in-memory sidecar mock**, not this domain; `scudo_mapping_mcp/dense_scorer.py:23-30` states "Titan embeddings are PARKED for the demo build" and the wired dense arm (`opus_dense.py`) uses Claude-as-dense-scorer instead of Titan+kNN. `projection_handler.py:454-487` DOES write real OpenSearch documents (`_index_opensearch`) including a Titan embedding call (`_titan_embedding`, line 483) when the outbox sweep fires — so the write side is real, the retrieval/candidate-generation side is not.
- **Neptune**: `NeptuneCluster`/`NeptuneWriter` in `data-platform.yaml` (`db.t3.medium` default) and a second, independent `NeptuneCluster`/`NeptuneInstance` in `infra/scudo-dev-foundation.yaml` (`db.t4g.medium` default, `neptune1.4` family, IAM-auth enabled). **Template-real** resources, but ZONES.md states Neptune is "the dormant cutover" — FalkorDB is the actual retrieval store in both deploy paths today (`STORE_BACKEND=falkordb` hardcoded into every ECS task's env in `scudo-dev-deploy.yaml` and into `ScudoFn`'s env in `template.yaml:384-387`).
- **DynamoDB**: the SAM stack's comment block (`template.yaml:365-370`) states DynamoDB tables were **removed** in the 5-zone persistence consolidation (Aurora is now sole store) — none provisioned in `template.yaml`/`data-platform.yaml`. However `infra/scudo-dev-foundation.yaml:401-418` provisions a real `ReviewerQueueTable` (DynamoDB, PAY_PER_REQUEST, PITR+SSE enabled) as an append-only Match-Verify→Persistence handoff queue — so the two deploy paths disagree: SAM stack = no DynamoDB, dev/ECS stack = 1 DynamoDB table. AppSync's `StewardReviewDataSource` (below) also targets a DynamoDB table by name (`ReviewTableName` param, default `scudo-poc-human-review`) that is referenced but not itself defined in `data-platform.yaml` — an external/undeclared dependency.
- **AppSync**: `StewardApi` (`AWS::AppSync::GraphQLApi`, API_KEY auth, X-Ray on) in `data-platform.yaml:242-384` — full schema with `Query.listReviewTickets`, `Mutation.publishMapping`, `Subscription.onPublish`. **Template-real**, wired to a DynamoDB data source (real resolver VTL) for reads and a `NONE` (pass-through) data source for the publish mutation — i.e., the publish mutation is not itself durable, it just re-emits payloads to subscribers.

### A.8 Template-real vs seams — summary

| Component | Status |
|---|---|
| S3 (5 buckets), SQS (2 queues+2 DLQ), EventBridge bus+rules, Lambda x4, HttpApi | Template-real, and the RawFeed→ETL and outbox-sweep paths are live wiring (not stubs) |
| Aurora (PostgreSQL + MySQL clusters, Serverless v2) | Template-real |
| FalkorDB on Fargate | Template-real, and it IS the live retrieval store (both deploy paths) |
| Neptune (both stacks) | Template-real resource, but dormant/unused at runtime — FalkorDB used instead |
| OpenSearch domain | Template-real resource; write path (`projection_handler.py`) is real; read/candidate-generation path is a mock/seam (Jaro-Winkler stand-in, Opus-as-dense-scorer) |
| Titan embeddings | Only invoked from the outbox-sweep write path (`projection_handler.py:483`), NOT from the matching/candidate-retrieval path, which is parked per `dense_scorer.py:23-30` |
| AppSync | Template-real GraphQL API; publish mutation has no persistence data source (`NONE` type) |
| DynamoDB reviewer queue | Real only in the dev/ECS stack (`infra/scudo-dev-foundation.yaml`), absent from the SAM stack |
| ECS (FalkorDB always; +4 more services in dev stack) | Template-real |

## C. Persistence Model

Per-product persistence spans two Lambda-boundary phases: (1) ETL ingestion (`etl_handler.py`), and (2) mapping + outbox projection (`lambda_handler.py` -> Aurora `publish_outbox` -> `projection_handler.py`). Poller (`poller_handler.py`) is upstream of ETL and per-page, not per-product.

### C.1 Ingestion / ETL phase — 1 vendor object in (`etl_handler.py:83-150`)

Per object processed (fail-loud "good file" path):
- **S3 PUTs: 1** — `clean/{job_id}/{basename}.json` into `SCUDO_CLEAN_BUCKET` (`etl_handler.py:130-135`). Bad-file path instead does 1 PUT into the quarantine bucket (`etl_handler.py:168-173`) — mutually exclusive, never both.
- **S3 GETs: 1** — read of the raw object (`etl_handler.py:106`).
- **Aurora writes: 4** on the pass path — `update_job_status` twice (`PROCESSING` at line 97, `PASSED` at line 142; each is an upsert via `on conflict (job_id) do update`, `aurora_store.py:121-135`), `put_facts_record` once (line 136-141, insert into `scudo.lineage_facts`), `put_audit_record` once (line 147, insert into `scudo.audit_events`). Quarantine path is 3 writes (2x job-status + 1 audit, no facts row).
- **EventBridge**: 1 `put_eventbridge_event(detail_type="CanonicalMetadataReady")` (line 148), pass path only — this is the event the (disabled) `PersistenceProjectionRule` used to route to SQS; fires but has no active consumer today.
- **SQS**: ETL is itself an SQS consumer (`EtlQueue`, `template.yaml:477-482`, BatchSize 5) — 1 inbound message per object, no outbound message from ETL.

### C.2 Mapping / publish phase — 1 product per `lambda_handler.py` `/run` (lines ~600-651)

Every outcome (published / hitl / retry / research_queued):
- **Aurora writes: 1 always** — `put_audit_record(item_id=bundle_ref, event_type=f"MAPPING_{outcome}")` (line 617-621, insert into `scudo.audit_events`).
- **+1 Aurora write if `outcome == published`** — `put_outbox_record(event_id, detail_type="MappingCompleted", detail=...)` (line 627-631), an idempotent insert (`on conflict (event_id) do nothing`) into `scudo.publish_outbox` (`aurora_store.py:86-99`) — the transactional-outbox row the projection worker later drains.
- **+1 Aurora write if `hitl_ticket` set** — `put_review_record(ticket=..., payload=...)` (line 651, insert into `scudo.mapping_decisions`).
- **EventBridge PutEvents: +1 if published** — `put_eventbridge_event(detail_type="MappingCompleted")` (line 632-635) on `ScudoEventBus` — feeds the now-`DISABLED` `PersistenceProjectionRule`, so has no live consumer; the outbox row (not this event) is what the projection Lambda reads.
- **Aurora precedent write (DISTILL): +1 more if published** — `_record_precedent_if_published(...)` (line 638-649), unconditional on the published branch.
- Net: **published** = up to 4 Aurora writes (audit + outbox + precedent, no review row) + 1 EventBridge PutEvents; **hitl** = 2 Aurora writes (audit + review), no outbox/EventBridge; **retry/research_queued** = 1 Aurora write (audit only).

### C.3 Outbox sweep — projection fan-out, 1 published product per row (`projection_handler.py`)

Runs on the `rate(5 minutes)` schedule (`template.yaml:578-583`), NOT per-product-synchronous — it batches (`_fetch_undispatched(limit=100)`, line 558-566) rows where `dispatched = false`. Per row projected (`_project_one`, line 524-543):
- **Aurora writes: 2** — `_write_aurora` upserts into `scudo_audit_events` (line 157-188) AND, only if `vendor_product_iri` is present, into `scudo_catalog_projection` (line 189-231). Note: these are **unqualified** table names (`scudo_audit_events`, not `scudo.audit_events`) — a second, divergent audit-table naming scheme from `aurora_store.py`'s schema-qualified tables (flagged in ZONES.md "Known divergences").
- **+1 Aurora write** — `_mark_dispatched(event_id)` (line 584-590), `UPDATE scudo.publish_outbox SET dispatched = true` — this one IS schema-qualified, matching `aurora_store.py`.
- **Neptune**: 1 SPARQL write per row (`_publish_neptune`, gated on `SCUDO_NEPTUNE_SPARQL_ENDPOINT`/`SCUDO_NEPTUNE_ENDPOINT` — no-op otherwise).
- **OpenSearch**: 1 indexed doc per row (`_index_opensearch`, line 454-487), including a Titan embedding call (`_titan_embedding`, line 483) when `SCUDO_OPENSEARCH_ENDPOINT` is set.
- **AppSync**: 1 GraphQL mutation (`_publish_appsync`, line 490-521), gated on `SCUDO_APPSYNC_API_URL`/`SCUDO_APPSYNC_API_KEY`.
- No S3 write happens in the projection worker itself — the canonical S3 object was already written upstream by ETL (C.1).

### C.4 Roll-up per product, end to end (best case: single ETL object -> 1 mapping run -> published, auto-approve, no HITL)

| Op | Count | Where |
|---|---|---|
| S3 PUT | 1 | ETL clean-canonical write |
| S3 GET | 1 | ETL raw read |
| SQS consumed | 1 | ETL's own trigger (RawFeed -> EventBridge -> SQS) |
| Aurora writes (ingestion) | 4 | 2x job-status upsert, 1x lineage_facts insert, 1x audit_events insert |
| Aurora writes (mapping publish) | 3 | audit_events insert, publish_outbox insert, precedent insert |
| Aurora writes (outbox sweep, async, batched) | 3 | scudo_audit_events upsert, scudo_catalog_projection upsert, publish_outbox dispatched=true update |
| EventBridge PutEvents | 2 | CanonicalMetadataReady (ETL) + MappingCompleted (mapping) — both currently feed a disabled rule with no live SQS consumer |
| Neptune write | 0 or 1 | gated; real retrieval target is FalkorDB not Neptune today |
| OpenSearch index (+ Titan embed) | 0 or 1 | gated on `SCUDO_OPENSEARCH_ENDPOINT` |
| AppSync mutation | 0 or 1 | gated on AppSync env vars |

**Total Aurora `ExecuteStatement` calls for one fully-published product: ~10** (4 ingestion + 3 publish + 3 sweep), all via the RDS Data API (billed per-request in addition to Aurora Serverless v2 ACU-seconds). HITL/retry/research-queued products stop after 1-2 Aurora writes and never generate an outbox row, so they cost materially less.

### C.5 Steady-state storage — canonical JSON-LD KB estimate per product

No single "one product, one canonical JSON-LD document" fixture exists in the repo. Two adjacent JSON-LD artefacts were measured directly and a synthetic `MappingObject` (the schema that IS persisted per product, `schemas.py:260-274`) was built and serialized to get a grounded estimate:

- `backend/scudo/fixtures/cdao_catalogue.json` (4.4 KB, 14 `@graph` taxonomy nodes) — average **261 bytes/node** for a flat CDAO concept node (`@id`, `@type`, `label`, `caption`, provenance).
- `backend/scudo/fixtures/conceptual_layer.json` (4.5 KB) — M10 conceptual-enrichment nodes for ONE CDAO concept, similar per-node density.
- Constructed a realistic `MappingObject` in-process (`schemas.py` `MappingObject`/`MappingResult`/`VerifierReport`/`Evidence`, full 10-dim verifier report all scored, 2 evidence items with quotes, 1 proposed triple, populated rationale/outcome_reason) and called `.model_dump_json()`: **1,989 bytes (~1.9 KB)**.

This ~2 KB figure is the size of the **audit/outbox payload** actually written per product (the JSON blob inside `scudo.audit_events.payload` / `scudo.publish_outbox.detail`, and re-serialized again into `scudo_audit_events`/`scudo_catalog_projection` at sweep time — i.e. the same ~2 KB blob is stored 2-3x across tables). The upstream ETL "canonical" object (`etl_handler.py:113-122`) is much smaller — just source/hash/size metadata (`job_id`, `source_bucket`, `source_key`, `source_content_hash`, `content_type`, `size`, `loaded_at_ms`), well under 1 KB, and is NOT a JSON-LD document (no `@context`/`@id`) — it is a plain lineage-fact JSON blob; the canonical CDAO-taxonomy-flavoured JSON-LD only exists for taxonomy nodes (`cdao_catalogue.json`), not per vendor product.

**Estimate: ~2-4 KB of durable JSON per fully-published product** (one ~2 KB MappingObject-shaped payload, persisted 2-3x across `scudo.audit_events`, `scudo.publish_outbox`, `scudo_audit_events`, `scudo_catalog_projection`, plus a sub-1 KB ETL lineage-fact row and a sub-1 KB canonical S3 object) — small enough that storage cost is dominated by Aurora/OpenSearch/Neptune baseline instance cost, not by per-product payload volume, at any realistic vendor-catalogue scale (thousands to low millions of products).

## D. Gaps vs Client Component List

### D.1 Client-list item -> repo status

| Client-list item | Repo status | Evidence |
|---|---|---|
| API Gateway | Present, real | `HttpApi ScudoApi`, `template.yaml:589-599` (`/run`, `/health`) |
| Lambda authorizer JWT | Absent | `grep -n "JWT\|authorizer"` across `template.yaml`/`scudo-dev-deploy.yaml` = 0 matches; auth today is an API-key header (`ApiKey` SAM param), not a JWT Lambda authorizer |
| Ingestion Coordinator | Present, real, differently named | `EtlFn` (SAM) / `IngestionTaskDefinition` (dev-ECS) fill this role; no component literally named "coordinator" |
| Sanity Check | Present, real | validate-then-route in `etl_handler.py` `_process_object`/`_quarantine` (pass -> clean bucket, fail -> quarantine bucket) |
| CloudWatch | Present, real | default Lambda/ECS log groups; EMF metrics added per MEMORY, not itself a distinct template resource |
| S3 raw landing | Present, real | `RawFeedBucket`, `template.yaml:176-198` |
| EventBridge Scheduler | Present, real | `PollerFn` cron schedule + `ProjectionFn` OutboxSweepSchedule (`rate(5 minutes)`) |
| EventBridge ObjectCreated rule | Present, real | `RawFeedIngressRule` on default bus, S3 Object Created -> `EtlQueue` |
| SQS | Present, real | `EtlQueue`+DLQ live; `PersistenceProjectionQueue`+DLQ provisioned but orphaned (feeder rule disabled) |
| ETL worker VPC | Present, but inverted | `EtlFn` explicitly has NO VPC (`template.yaml:488-491`, needs internet egress); `ScudoFn`/`ProjectionFn` are the ones in-VPC |
| S3 canonical sink | Present, real | `CleanCanonicalBucket`, written by `etl_handler.py` pass path |
| S3 quarantine | Present, real | `QuarantineBucket`, written by `etl_handler.py` `_quarantine` |
| AIA Matching Engine gates A/B/C | Present, real, differently framed | orchestrator confidence-gate + verifier 10-dim rubric play this role; repo has no literal "gate A/B/C" naming — see Section B (skipped here) for the actual gate mechanics |
| Titan Embeddings | Present but seam for retrieval, real for indexing | `dense_scorer.py` "PARKED for the demo build"; wired dense arm is Opus-as-scorer (`opus_dense.py`); Titan IS called in `projection_handler.py:483` on the write/index path only |
| OpenSearch k-NN | Present, template-real, seam for read/candidate-gen | domain exists (`data-platform.yaml`), write path real (`_index_opensearch`), but matching reads from FalkorDB/in-memory mock, not OpenSearch kNN |
| Aurora 2xlarge | Present, but wrong shape | Aurora IS the system of record, but it is Aurora **Serverless v2** (`db.serverless`, 0.5-2 ACU), not a fixed `db.r6g.2xlarge`-class instance — material sizing/cost divergence |
| Strands Orchestrator Opus 4.8 | Present, real | `ScudoFn` orchestrator Lambda; Bedrock model id `eu.anthropic.claude-opus-4-8` default in `scudo-dev-deploy.yaml:37-40` (Section B covers model wiring in detail) |
| AgentCore Memory | Absent as AWS Bedrock AgentCore | grep for `AgentCore`/`agentcore` across `backend/scudo*/*.py`, `*.yaml`, `infra/*.yaml` only matches `build_matching_graph.py` (diagram-label text, e.g. "reads from AgentCore Memory... dispatches a Specialist") — this is descriptive prose for a diagram node, not a real AWS AgentCore resource or SDK call anywhere in the repo. The actual memory substrate is `aurora_memory.py` (Aurora-backed precedent/skill store), a bespoke implementation, not the managed AgentCore Memory service |
| Specialist agent | Present, real | Mapping Specialist role produces `MappingResult` (`schemas.py:189-213`); invoked from the orchestrator |
| Verifier agent 10-dim | Present, real | `VerifierDimension` enum, exactly 10 dims, `VerifierReport` (`schemas.py:219-247`), `min_length=10, max_length=10` scores, total <= 20 |
| Bedrock Evaluations | Absent as AWS Bedrock Evaluations | Same grep pattern as AgentCore — only `build_matching_graph.py` diagram text matches; no `EvaluationJob`/Bedrock-Evaluations API call anywhere. The repo's own eval/self-improvement loop (per MEMORY: `run_sleep_cycle`/`promote_skill`) is a bespoke offline harness, not the managed Bedrock Evaluations product |
| Transactional outbox EventBridge+SQS | Present, but different shape | actual outbox is an **Aurora table** (`scudo.publish_outbox`) drained by an EventBridge **Schedule** (`rate(5 minutes)` cron-style trigger calling `ProjectionFn` directly), not an EventBridge **rule** routing to SQS. The literal EventBridge-rule-to-SQS wiring for projection (`PersistenceProjectionRule` -> `PersistenceProjectionQueue`) exists in the template but is `State: DISABLED` and orphaned |
| HITL AppSync WebSockets | Present, real, partially wired | `StewardApi` GraphQL API with `Subscription.onPublish` (AppSync-managed WebSocket transport) exists in `data-platform.yaml:242-384`; the publish mutation itself uses a `NONE` (pass-through) data source, so the WebSocket fan-out is real but not backed by its own persistence — durability comes from the Aurora outbox path instead |
| ECS | Present, real | FalkorDB-on-Fargate in both stacks; 4 more ECS services (Flask/Ingestion/MatchVerify/Persistence) in the dev stack only |

### D.2 Real repo components the client list misses entirely

| Component | Where | Notes |
|---|---|---|
| FalkorDB on ECS/Fargate | `network-falkordb.yaml` (SAM), `scudo-dev-deploy.yaml` (dev) | The ACTUAL live retrieval/graph store today, replacing the client-list's implied OpenSearch-kNN/Neptune retrieval path |
| Neptune (2 independent clusters) | `data-platform.yaml`, `scudo-dev-foundation.yaml` | Provisioned in both stacks, dormant at runtime |
| CloudFront | `infra/scudo-dev-frontend.yaml`, `infra/scudo-poc-app.yaml`, `infra/scudo-poc-build.yaml`, `infra/scudo-poc-frontend.yaml` | Frontend/dashboard distribution — outside the brief's primary reading list but real and deployed (per MEMORY: `dp4ji14se0pct.cloudfront.net`) |
| Application Load Balancer | `infra/scudo-dev-deploy.yaml:220-354` | Fronts the 5 ECS services in the dev stack, path-based routing |
| NAT Gateway | `network-falkordb.yaml`, `scudo-dev-foundation.yaml` | 1 NAT gateway for private-subnet egress |
| Secrets Manager | `scudo-dev-foundation.yaml` (`VerdictSigningKey` HMAC secret), console DB password secret, vendor-poller API keys | Signing key gates Match-Verify/Persistence tiers; explicitly Denied to the Ingestion tier |
| VPC interface/gateway endpoints | `scudo-dev-foundation.yaml:202-272` | S3 Gateway, CloudWatch Logs, ECR API, ECR DKR, Bedrock, Bedrock Runtime — keeps traffic off the public internet |
| CodeBuild | `infra/scudo-dev-build.yaml`, `infra/scudo-poc-build.yaml`, `backend/scudo/build-pipeline.yaml` | Build/deploy pipeline (e.g. `scudo-poc-console-build` per MEMORY) — CI/CD infra with no client-list counterpart |
| DynamoDB reviewer-queue table | `scudo-dev-foundation.yaml:401-418` (`ReviewerQueueTable`) | Dev-stack-only append-only Match-Verify->Persistence handoff; explicitly "NOT the graph of record" per inline comment |
| Console Aurora (aurora-mysql) | `data-platform.yaml` (`ConsoleAuroraCluster`) | Second Aurora cluster backing the Flask console, separate from the `scudo` system-of-record cluster; client list's single "Aurora 2xlarge" line item does not distinguish these two clusters |
| Trust-gradient IAM Deny statements | `scudo-dev-foundation.yaml:440-527`+ | 3-tier explicit-Deny model (Ingestion/Match-Verify/Persistence) — a security control with no client-list counterpart at all |

### D.3 Summary

The client component list maps well onto the ingestion/ETL half of the SAM stack (API Gateway, S3 buckets, EventBridge, SQS, ECS, CloudWatch) but diverges materially on 3 fronts: (1) sizing assumptions (Aurora "2xlarge" vs actual Serverless v2 `db.serverless`), (2) the outbox pattern (EventBridge+SQS assumed vs actual Aurora-table+Schedule, with the EventBridge+SQS path present but disabled), and (3) two client-list items (AgentCore Memory, Bedrock Evaluations) that name specific managed AWS services with no real implementation anywhere in the repo — both only appear as descriptive prose inside a diagram-generation module (`build_matching_graph.py`), where the actual substrate is bespoke (`aurora_memory.py` for memory, an offline harness for evaluation). Separately, the repo carries a materially larger real footprint than the client list acknowledges — CloudFront, ALB, NAT, Secrets Manager, VPC endpoints, CodeBuild, a second Aurora cluster, a DynamoDB table, and an explicit-Deny trust-gradient IAM model, none of which appear on the client list at all.
