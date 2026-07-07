# CLAUDE.md — MatchMaker / SCUDO

Project-root agent instructions. State last updated **2026-07-06** (branch `scudo-phase0-foundations`).

## What this is

SCUDO: vendor→CDAO market-data product mapping. Vendor product metadata is ingested (ETL), matched against the CDAO catalogue (sparse + dense arms, confidence gate), routed through HITL review where needed, and published to projections. "MatchMaker" is the repo name; SCUDO is the product.

## Two-repo topology

| Repo | Role |
|------|------|
| `/Users/anthonylui/MatchMaker/MatchMaker` (this repo) | Backend: `backend/scudo/` (ETL, matcher, orchestrator, Lambda handlers, projections), `backend/scudo_mapping_mcp/` (mapping MCP + conceptual ontology models), infra, docs |
| `/Users/anthonylui/Understand-Anything/understand-anything-plugin/packages/dashboard` | Matching dashboard front-end (React 19 + Vite, `VITE_MATCHING_MODE=true`). Built output is vendored back into this repo at `dashboard-dist/` via `infra/build_dashboard_dist.sh` |

Both repos work on branch `scudo-phase0-foundations`. A separate deployed React 18 front-end lives in `frontend/` — the dashboard is the visualisation/story surface, not a replacement.

## Key contracts (do not drift)

- **Confidence bands: passCut 0.80 / failCut 0.70** (5-zone contract, 2026-07-04). FE `DEFAULT_BANDS` and backend gates must agree. Older docs/diagrams saying 0.85/0.75 are stale.
- **Dashboard vocabulary is a closed z.enum** (`packages/core/src/schema.ts` in the dashboard repo: 21 node types, 35 edge types). Anything outside it is silently dropped with a yellow banner. The graph builder (`backend/scudo/build_matching_graph.py`) must map M10 conceptual kinds through `_CONCEPTUAL_NODE_TYPE` / `_CONCEPTUAL_EDGE_TYPE`; the true kind is preserved in tags/description. Gate: `backend/scudo/tests/test_dashboard_enum_vocabulary.py`.
- **Vendor IRIs**: `mds.<vendor>:<uuid5>`.
- **Architecture**: 5-zone design approved by Nigel (JPM) 2026-07-03; ONE Aurora for all DB interactions; MFT gateway is JPM-owned. See `docs/specs/` + `infra/HANDOVER_5zone_alignment.md`.
- **Fixture sync**: `backend/scudo/fixtures/matching-graph.json` must stay byte-identical with the dashboard repo's `public/matching-graph.json`. Test runs regenerate the fixture (`analyzedAt` timestamp churn is expected).

## State as of 2026-07-06

**Done and committed (backend HEAD `ec14ec9`, NOT pushed):**
- M10 conceptual-kind enum mapping in `build_matching_graph.py` (`e6f360b` — fixes the "30 dropped items" banner; M10 layer renders all 15 `mds.enrich:*` nodes). Fixture regenerated: 57 nodes / 78 edges / 8 layers. `dashboard-dist/` rebuilt with both story-tour fixes (bundle `index-B6t_lF7x.js`).
- Confidence-band alignment to 0.80/0.70 (`0965f7c`), dev-principal HITL 403 guard (`52cbf58`), Strands reasoning-panel event coalescing (`a9dc2b7`), console DB ported MySQL→Aurora PostgreSQL (`bf2f50c`), CloudWatch EMF metrics (`778b47a`), Aurora `publish_outbox` sweep (`587f8ac`).

**Committed in the dashboard repo (HEAD `d8e9ff5`, NOT pushed):** dominant-layer story-tour navigation (`src/utils/tourNavigation.ts` + 9 vitest tests, `store.ts` delegation, `GraphView.tsx` tour-frame folding, `reviewBands.test.ts` 0.80/0.70 pins, synced `public/matching-graph.json`) on top of band alignment (`5a514e8`) and tour exposure (`64b1111`). TDD'd (79/79 vitest green, tsc + `build:matching` clean), Codex-approved, adversarially workflow-verified, live-verified in browser.

**Uncommitted in this repo (separate 5-zone hardening work stream, in flight):** `backend/db.py` (fail-fast on missing `CONSOLE_DB_PASSWORD` for non-local hosts), `backend/scudo/lambda_handler.py` (`_decision_publish_payload` normalises HITL approve into the auto-publish outbox shape), `backend/scudo/projection_handler.py` (`_sparql_iri` percent-encodes all IRIREF-illegal chars — injection guard), plus tests (`test_catalogue_endpoints.py`, `test_projection_sweep.py`, `test_calibrate_confidence_floor.py`, new `backend/tests/test_db_connect.py`).

**Deployed (AWS 954976331678, us-east-1):** PoC live at `dp4ji14se0pct.cloudfront.net/demo/` but running an **older bundle** — the rebuilt `dashboard-dist/` needs a CloudShell/CodeBuild redeploy (`scudo-poc-console-build`). Deployed backend also predates the event-coalescing fix. See `backend/scudo/AWS_HANDOFF.md` and `infra/HANDOVER_hitl_bands_2026-06-26.md`.

**Known open issues:**
- `backend/scudo/tests/test_provenance.py`: 2 pre-existing failures — `conceptual_layer.json` has kind `marketing_dataset` (labelled "Equity Prices Historical Series") and the test greps the JSON blob for "marketing". Fails at HEAD; unadjudicated.
- Outbox head-of-line starvation risk in `projection_handler.py` `sweep_outbox` — flagged, unadjudicated.
- Matching-mode detection is asymmetric: dashboard `store.ts` gates on `VITE_MATCHING_MODE` only; `GraphView.tsx` also accepts `project.name` containing "SCUDO Matching". Non-defect nit.
- `_fold_conceptual_match_payload` in `build_matching_graph.py` still emits raw conceptual kinds — this is the legacy MatchPayload API path (`GET /api/mapping/graph`), never dashboard-validated. Deliberate; leave alone.

## Running things

- **Local backend:** `backend/run_local.py`, Flask on :5001, `STORE_BACKEND=memory` (set env **before** import). Local loop gotchas: `vendor_signature`, `decision=` — see project memory `scudo-local-loop-run`.
- **Dashboard dev:** `pnpm dev` in the dashboard package, Vite on :5173 (tokened URL printed at startup). Build: `pnpm build:matching` (tsc -b + `vite.config.matching.ts`).
- **Vendored dist rebuild:** `bash infra/build_dashboard_dist.sh` (syncs fixture → builds → copies into `dashboard-dist/`).
- **Tests:** backend `pytest backend/scudo/tests/` (bare `pytest` at root collects nothing); dashboard `pnpm vitest run`. Standalone smoke runners: `smoke.py` (mapping 111-gate, no deps), orchestrator smoke needs `strands`.
- **Graph fixture regen:** `python -m backend.scudo.build_matching_graph` (run from repo root).

## Conventions

- OKF docs bundle at `docs/okf/scudo/` has its own navigation protocol — read `index.md` first, don't glob-scan; regenerate with `okf index docs/okf/scudo` after doc changes.
- Say "task is complete and ready for review" — never claim production readiness or "fully operational" without the user's explicit approval.
- Don't commit or deploy unless asked. `dashboard-dist/` is vendored build output but IS tracked — rebuild it via the script, never hand-edit.
- Session memory (verified facts, gotchas) lives in the Claude auto-memory for this project; read it before large tasks, update it after.
