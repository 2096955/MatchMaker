# ZONES.md — the SCUDO 5-zone map

The architecture JPMC approved (Nigel, 2026-07-03) is organised as **five
zones**. This file is the authoritative map from those zones to the code that
implements them.

```
Zone 1  Vendor Sources & Ingestion   →  Zone 2  Ingestion Processing (ETL)
        →  Zone 3  Matching Engine    →  Zone 4  Agentic Layer
        →  Zone 5  Persistence & Human Review   (Aurora = single source of truth)
```

Diagram: [`docs/architecture/scudo-5zone-architecture.png`](docs/architecture/scudo-5zone-architecture.png).

## Zones are logical, not physical

The zones are a **layering that cuts across the two runtime packages** — neither
`backend/scudo/` nor `backend/scudo_mapping_mcp/` equals one zone. `scudo/`
alone holds entrypoints for Zone 1 (`poller_handler`), Zone 2 (`etl_handler`),
Zone 3 (`matcher_bridge`), Zone 4 (`orchestrator`, `lambda_handler`), and Zone 5
(`aurora_store`, `projection_handler`). The Flask console Human-Review surface
(`backend/routes/mapping.py`, `backend/db.py`) is a Zone 5 surface living
*outside* both packages.

Because the split is orthogonal to the zones, the code is **not physically
reorganised** into zone folders — doing so would rewrite ~250–350 import/config
lines across 150+ files and break the seven Lambda/ECS entrypoints that hard-code
module paths. Instead, the zones are made **importable** by a thin re-export
façade at [`backend/scudo/zones/`](backend/scudo/zones/):

```python
from scudo.zones.z3_matching import matching, matcher_bridge
from scudo.zones.z5_persistence import aurora_store, feedback
```

Each name is the *real* module object (identity-equal, no wrapping). The façade
is a leaf — nothing at runtime imports it, and `scudo/__init__.py` does not load
it — so it does not amplify the known `scudo` ↔ `scudo_mapping_mcp` import cycle.
A physical move is deferred until after the pending redeploy and until that cycle
is broken.

---

## Zone 1 — Vendor Sources & Ingestion

Vendor product metadata enters here. Onboarding a vendor is a **config change**,
not a new Lambda.

| Module | Purpose | AWS entrypoint |
|--------|---------|----------------|
| `scudo/poller_handler.py` | Config-driven vendor-API poller (one Lambda for all vendors) | `scudo.poller_handler.handler` (EventBridge-scheduled Lambda) |
| `scudo_mapping_mcp/ingestion_mcp.py` | Ingestion MCP — trust-gradient tier 1 (untrusted vendor in, no signing key) | ECS, port 8001 |
| `scudo_mapping_mcp/ingest.py` | Dropped files → vendor frames; seeds CDAO taxonomy | — |
| `scudo_mapping_mcp/url_ingest.py` | SSRF-guarded single-URL website ingestion | — |
| `scudo_mapping_mcp/turtle_ingester.py` | Turtle upload routing for taxonomy seeding | — |

> MFT→FTP gateway and vendor-S3/DMS are **JPM-owned** black boxes (per sign-off),
> deliberately not in this repo. The web-scraper box on the diagram is the
> single-URL `url_ingest`, not a standing crawler.

## Zone 2 — Ingestion Processing (ETL)

Validate → normalise → land (clean/canonical or quarantine) + audit.

| Module | Purpose | AWS entrypoint |
|--------|---------|----------------|
| `scudo/etl_handler.py` | ETL sanity-check Lambda: validate → clean/quarantine + Aurora audit | `scudo.etl_handler.handler` (SQS-backed Lambda) |
| `scudo_mapping_mcp/frames.py` | Two deterministic guards in front of the matcher (scope, frame read) | — |
| `scudo_mapping_mcp/validations.py` | M5 deterministic validations + field normalisation | — |
| `scudo_mapping_mcp/csvw_aliases.py` | CSVW column alias parsing/normalisation | — |
| `scudo_mapping_mcp/models_dcat.py` | DCAT models + projection to TaxonomyNode | — |

## Zone 3 — Matching Engine

The cost ladder: scope → precedent → hybrid retrieval → confidence gate
(PASS ≥0.80 / BORDERLINE 0.70–0.80 / FAIL <0.70). **This is the only zone whose
reads come from the graph store, not Aurora** — candidate discovery is a
retrieval index (FalkorDB now, Neptune the dormant cutover), never the system of
record.

| Module | Purpose | AWS entrypoint |
|--------|---------|----------------|
| `scudo_mapping_mcp/matching.py` | The matcher — strict cost-ladder gates | — |
| `scudo_mapping_mcp/retrieval.py` | Multi-path retrieval orchestrator | — |
| `scudo_mapping_mcp/match_verify_mcp.py` | Match & Verify MCP — trust-gradient tier 2 | ECS, port 8002 |
| `scudo/matcher_bridge.py` | Lambda-side bridge: real cost ladder over the graph store | in-process from `scudo.lambda_handler` |
| `scudo_mapping_mcp/dense_scorer.py`, `opus_dense.py` | Opus-as-dense-scorer arm | — |
| `scudo_mapping_mcp/store/` (`factory.py`, `falkordb_store.py`, `neptune_store.py`, `memory_store.py`) | Retrieval store seam + backends; `STORE_BACKEND` selects `falkordb`\|`neptune`\|`memory` | — |

## Zone 4 — Agentic Layer

Orchestrator → specialist → verifier → gate-and-decide, with auto-approve / HITL
/ reject routing. Specialist+verifier run on **Bedrock (Opus 4.8) by default**;
the deliberate **Azure OpenAI shim** switches on per-deploy or per-request.

| Module | Purpose | AWS entrypoint |
|--------|---------|----------------|
| `scudo/lambda_handler.py` | Mapping Lambda: API-GW event in, MappingObject out; drives this zone | `scudo.lambda_handler.handler` |
| `scudo/orchestrator.py` | Deterministic orchestrator: route → bundle → verifier → publish gate | — |
| `scudo/agents.py`, `tools.py`, `prompts.py` | Build the Strands specialist + verifier and their deterministic tools | — |
| `scudo_mapping_mcp/agent.py` | M9 agent runner behind the demo `/agent/run` SSE stream | — |
| `scudo_mapping_mcp/mcp_host.py` | MCP host transport between agent and tools | — |
| `scudo/hooks.py` | Strands hook providers enforcing invariants | — |
| `scudo/batch.py` | Self-verifying batch matcher over N products | — |

## Zone 5 — Persistence & Human Review

The system of record and the human loop. **Aurora PostgreSQL is the single
source of truth** — one cluster, four schemas (`public`, `scudo`, `console`,
`ingestion`). The DynamoDB tables the older design used were consolidated into
Aurora and removed.

| Module | Purpose | AWS entrypoint |
|--------|---------|----------------|
| `scudo/aurora_store.py` | Durable persistence via RDS Data API (audit, decisions, outbox, catalogue, lineage, ETL jobs, taxonomy) | — |
| `scudo/aurora_memory.py` | CONSULT/DISTILL/SkillOpt agent memory (`scudo.agent_memory`) | — |
| `scudo/catalogue.py` | Approved-catalogue API surface (the API consumes this, never Aurora/Neptune directly) | `GET /catalogue`, `GET /catalogue/{iri}` |
| `scudo/projection_handler.py` | Async projection Lambda: drains the transactional outbox | `scudo.projection_handler.handler` |
| `scudo_mapping_mcp/persistence_mcp.py` | Persistence MCP — trust-gradient tier 3, **sole writer**, publish gate (I5) | ECS, port 8003 |
| `scudo_mapping_mcp/feedback.py` | M4 HITL decision write-back + precedent rank tilt | — |
| `scudo_mapping_mcp/verdict.py` | HMAC-SHA256 verdict seal (integrity contract, v=2) | — |
| `scudo_mapping_mcp/rights_odrl.py` | Fail-closed ODRL 2.2 rights evaluator | — |
| `backend/routes/mapping.py` | Flask console HITL review + mapping UI backend (Human Review surface, outside both packages) | Flask `/api/mapping/*` |
| `backend/db.py` | Aurora `console` + `ingestion` schema connections (psycopg v3) | — |

## Cross-cutting (belong to no single zone)

| Module | Purpose |
|--------|---------|
| `scudo_mapping_mcp/models.py` | Typed contracts shared across store / matcher / MCP |
| `scudo_mapping_mcp/config.py` | Single source of truth for the store swap + confidence bands |
| `scudo/schemas.py` | SCUDO Pydantic contracts |
| `scudo/metrics.py` | CloudWatch EMF metric emission (used by `matching.py`) |
| `scudo/shared/bedrock.py` | Shared Bedrock LLM/embedding factories |
| `backend/auth.py` | Request-principal resolver for `/api/*` |

## Known divergences carried in code

These are the deliberate gaps between the diagram and the code — see the root
[`README.md`](README.md) "Where this diverges from the code on git, and why".

- **Graph store retained but dormant** as the canonical store; it is the Zone 3
  retrieval index only. Aurora is source of truth.
- **Two audit-table names coexist**: `scudo.audit_events` (schema-qualified,
  `aurora_store.py`) and unqualified `scudo_audit_events` (`projection_handler.py`)
  — legacy, flagged in `infra/HANDOVER_5zone_alignment.md`, not yet converged.
- **Two HITL decision contracts** at the `…/decision` path: the Flask console
  shape vs the 5-zone Lambda shape — convergence pending.
- **`ContentDeliveryModel`** enumerates only 3 of ~11 diagram values on purpose
  (citation-guarded — see `test_rights_contract_model.py`).
