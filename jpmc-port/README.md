# SCUDO JPMC port (day-one)

Slim rewrite of Capone `backend/scudo` for manual JPMC entry — full agent intelligence on Bedrock.

## Agent modes

| Mode | When | Behaviour |
|------|------|-----------|
| `bedrock` (JPMC default) | `SCUDO_LOCAL` unset, or `SCUDO_AGENT_MODE=bedrock` | Strands + **Claude Opus 4.8** Bedrock pin (`us.anthropic.claude-opus-4-8`) — needs AWS IAM |
| `anthropic` | `SCUDO_AGENT_MODE=anthropic` | Same agents via Anthropic Messages API (`claude-opus-4-8`) — local shim or cloud |
| `deterministic` | `SCUDO_LOCAL=1` (default) or `SCUDO_AGENT_MODE=deterministic` | Credential-free e2e fakes — **not** Opus evidence |

Intelligence pack (Bedrock path):
- **Load-bearing agents:** Mapping Specialist + Verifier on **Claude Opus 4.8** (`us.anthropic.claude-opus-4-8`), multi-turn **agentic loops** (`agent_loop.run_agentic_structured`) — tool use + reasoning, then structured output. `max_tokens=128000` (model ceiling). Override via `SCUDO_BEDROCK_LLM_ID` / `BEDROCK_LLM_MODEL_ID`. Publish gate stays in Python.
- Mapping tools: `describe_system_context`, catalogue lookup, `graphrag_retrieve`, `neptune_*`, `rdf_*`
- Verifier tools (investigative, no remap): `neptune_node_by_iri`, priors/conflicts, catalogue lookup, `rdf_validate_shapes`
- System prompts: mapping/verifier/rights + DCAT Dataset signals + rights/contract split + agentic procedure
- Hooks: version pins, reject raw SPARQL/Cypher, deny publish, read cap 12, telemetry
- Skills: Capone SKILL.md packs under `scudo/skills/`
- Aurora CONSULT/DISTILL: precedents, promoted rules, quarantined skill doc (`skill:matching:best`), trajectories
- **Teach → learn (protected):** every `POST /decision` (approve/reject/correct) calls `learn_from_teaching` fail-loud. Approve/correct write an exact-product precedent; reject writes no positive precedent. Generalized lessons are stored as quarantined `rule_candidate` evidence and are not consulted by `/run` until incorporated into a protected promoted artifact.
- **Offline rollback monitor:** a separate monitoring authority signs immutable prediction/outcome observations with Ed25519. `scudo/promotion_monitor.py` accepts only that `SignedMonitoringEnvelope` plus `SCUDO_MONITORING_PUBLIC_KEY`, verifies the exact artifact digest/sequence, and in one locked callback claims source events and records no-action or rollback under fixed `monitor-v1` thresholds (20 total and 20 auto-pass observations). The private key never enters runtime. An external scheduler submits authority-signed envelopes unchanged and retries only the exact same envelope for a window ID.
- Ontology model: 11 `ContentDeliveryModel` values, catalogue + rights `ConceptualNodeKind`s (UML-aligned)
- Zone context: 5-zone + catalogue/DCAT vs rights/contract injected into every mapping prompt + callable tool
- CatalogueOntology v0.1 Deontic: fixture `scudo/fixtures/catalogue_ontology_v0_1_deontic.ttl` (canonical prefixes), `POST /fill` → `CatalogueFillResult`, tools `lookup_catalogue_term` / `list_catalogue_dataset_fields`, skill `catalogue-ontology-fill`

## Matching dashboard (Understand-Anything)

Vendored Capone SPA at `dashboard-dist/` (`base:/demo/`, includes `matching-graph.json`).
Live panels talk Capone-shaped APIs implemented in `scudo/dashboard_api.py` over jpmc-port agents.

```bash
cd jpmc-port
pip install -r requirements.txt

# Ship surface: SPA + API on one origin
SCUDO_LOCAL=1 SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py
# → http://127.0.0.1:5001/demo/

# Refresh vendored dist from Capone (after infra/build_dashboard_dist.sh)
bash scripts/sync_dashboard_dist.sh
```

Dashboard API façade (same contracts as Capone `backend/routes/mapping.py` subset):
- `GET /api/mapping/vendors`
- `POST /api/mapping/ingest/stream` (SSE ETL stages)
- `POST /api/mapping/agent/run` (SSE → Mapping Specialist + Verifier)
- `POST /api/mapping/decision` (approve/override/reject → teach→learn)

Optional live-reload UI against this backend:
`cd …/packages/dashboard && VITE_MATCHING_MODE=true VITE_API_BASE=http://localhost:5001 pnpm dev`

## Run (API-only / tests)

```bash
cd jpmc-port
pip install -r requirements.txt

# credential-free e2e
SCUDO_LOCAL=1 python run_e2e.py
SCUDO_LOCAL=1 python -m pytest tests/ -q

# Atlas / JPMC (real agents)
unset SCUDO_LOCAL
export SCUDO_AGENT_MODE=bedrock
export AWS_REGION=us-east-1
export SCUDO_AURORA_CLUSTER_ARN=... SCUDO_AURORA_SECRET_ARN=... SCUDO_AURORA_DATABASE_NAME=scudo
export NEPTUNE_ENDPOINT=...   # optional; mock authoritative graph if unset
SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py
```

## A/B vs Capone (`backend/scudo`)

Both packages are named `scudo`, so arms run in **isolated subprocesses**. Capone is launched with `python -P backend/scudo/scripts/ab_capone_arm.py` so the script directory cannot shadow Capone on `sys.path`. Same golden cases, **multi-candidate shortlists**, then pairwise + vs-golden metrics.

```bash
# Plumbing / gate A/B (deterministic fakes — CI-safe; not Opus)
SCUDO_LOCAL=1 python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode deterministic \
  --out /tmp/scudo-ab

# Live Opus 4.8 (Anthropic Messages / local shim)
unset SCUDO_LOCAL
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_API_KEY="$(tr -d '[:space:]' < ~/.codex/shim-router/router.key)"
export SCUDO_ANTHROPIC_MODEL_ID=claude-opus-4-8
PYTHONPATH=. python run_opus_smoke.py --out /tmp/scudo-opus-smoke.json
python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode anthropic \
  --out /tmp/scudo-ab-opus

# Native AWS Bedrock (IAM required)
unset SCUDO_LOCAL
python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode bedrock \
  --out /tmp/scudo-ab-bedrock
```

Outputs under `--out`:
- `predictions_capone.jsonl` / `predictions_port.jsonl` (Capone rows include `scudo_module` path)
- `ab_report.json` — target/outcome agreement, confidence deltas, per-arm vs golden, `evidence_provenance`

Capone arm: `backend/scudo/scripts/ab_capone_arm.py` (`python -P`).  
Port arm: `scudo.ab_compare.run_port_arm`.

Optional offline golden eval (Capone MSI):  
`cd backend && PYTHONPATH=. python -m scudo.scripts.evaluate_matching_golden …`

Do not type `agents_local.py` / `local_state.py` as production — Bedrock path is `agents.py`.
