---
type: Spec
title: SCUDO Matching Frontend Spec
description: 'Binding spec: ship understand-anything dashboard, honest synthetic data,
  dual graph schemas, deploy gate.'
tags:
- spec
- matching
- frontend
staleness: current
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO Matching Front-End — Revised Design Spec

**Date:** 2026-06-24 · **Status:** implemented (Phases 0–2); owner sign-off + AWS deploy pending  
**Supersedes:** implicit target in `docs/superpowers/plans/2026-06-24-scudo-matching-frontend-and-deploy.md` (port into `frontend/`)

---

## Intent

Ship a **stakeholder-safe SCUDO matching comprehension UI** that:

1. Tells a coherent end-to-end story (vendor product → ETL → A/B/C matcher → confidence gate → band routing → JAPI persist).
2. Labels all synthetic data unmistakably as **ILLUSTRATIVE**.
3. Uses the **polished understand-anything dashboard** the owner already approved on `:5177`, not the generic ingestion console at `:3000/mapping`.

Mock data is acceptable. Incoherent mock data presented as real bank taxonomy is not.

---

## Owner decisions (binding)

| ID | Decision | Rationale |
|----|----------|-----------|
| **D-1** | **Ship `understand-anything-plugin/packages/dashboard`** as the matching UI | Owner rejected `:3000/mapping` ("awful", "shadcn looking thing"). `:5177` dashboard already has layered graph, tour, band colouring, Cognizant polish. |
| **D-2** | **Confidence bands follow matcher config** (not architecture diagram copy) | PASS ≥ 0.85, BORDERLINE 0.75–0.85, FAIL < 0.75 — see `docs/superpowers/matching-data-provenance.md`. |
| **D-3** | **IRI scheme `jpmorgan:data:cdao:*`** for all CDAO nodes in shipped artifacts | `urn:cdao:*` and bare `cdao:*` are legacy demo pollution. |
| **D-4** | **Drop Marketing domain** from taxonomy and graph | Incoherent for LSEG / ICE / S&P Global financial-data demo. |
| **D-5** | **Phase 3–4 (AWS deploy) gated** on owner screenshots of Phases 0–2 on `:5177` | Codex C-8: no public demo without origin secret / auth hardening. |

---

## What ships vs. what does not

| Artifact | Role | Ship? |
|----------|------|-------|
| `Understand-Anything/.../packages/dashboard` | Matching comprehension SPA (React 19, `@xyflow/react`, tour, layers) | **Yes — primary UI** |
| `MatchMaker/frontend/` | Data Ingestion Framework console | **No for matching** — keep for ingestion; revert `/mapping` bolt-on |
| `MatchMaker/backend/` | Flask API, matcher, graph builder, fixtures | **Yes — data + API source of truth** |
| `MatchMaker/infra/scudo-poc-*.yaml` | Console deploy stacks | **Yes — revised to build dashboard, not `frontend/`** |

---

## Two graph schemas (do not conflate)

The codebase currently has **two** graph shapes. The spec locks which is canonical for each consumer:

### A. KnowledgeGraph (dashboard contract)

Consumed by `packages/dashboard` via `validateGraph()` from `@understand-anything/core/schema`.

- File: `matching-graph.json` (served as `knowledge-graph.json` in matching demo mode)
- Fields: `version`, `kind: "codebase"`, `project`, `nodes[]` (id, type, name, summary, tags, …), `edges[]`, `layers[]`, `tour[]`
- Node `summary` carries expositional caption text
- `meta.json` may carry `dataProvenance: "synthetic"` for the ILLUSTRATIVE banner

**This is the UI contract.** `build_matching_graph.py` MUST emit this shape.

### B. MatchPayload (API contract)

Consumed by `GET /api/mapping/graph` and programmatic clients.

- Documented in `docs/superpowers/matching-data-provenance.md` and the original plan
- Slimmer: `meta`, `nodes[]` with `kind`, `caption`, `provenance`, `edges[]` with `band` / `weight`

**Phase 2 approach:** API route joins captions from `cdao_catalogue.json` + fixture at response time (Codex C-5). For the dashboard demo, either:

- **Preferred:** Dashboard loads static `matching-graph.json` (KnowledgeGraph) built by `build_matching_graph.py`; API is for drill-down / future live mode.
- **Later:** Add `?format=knowledge-graph` on the API or a thin adapter in the dashboard store.

Do **not** replace KnowledgeGraph emission with MatchPayload-only output (regression that broke `:5177` compatibility).

---

## Phase 0 — Honest, coherent mock data (BLOCKER)

### Taxonomy (`backend/scudo/fixtures/cdao_catalogue.json`)

- [x] Remove Marketing domain and all `urn:cdao:*` / `cdao:*` nodes
- [x] Use `jpmorgan:data:cdao:domain|subdomain|concept:<slug>` IRIs
- [x] Every node: `caption` (≤140 chars) + `provenance: { "source": "synthetic" }`
- [x] Regenerate `matching-graph.json` (KnowledgeGraph) from fixed catalogue — synced to dashboard `public/` via `sync_matching_graph_to_dashboard.sh`

### Ingest & seed

- [x] `backend/scudo_mapping_mcp/ingest.py` seeds from catalogue fixture
- [x] `backend/scudo/seed_falkordb.py` defaults to catalogue fixture
- [ ] FalkorDB cleanup: delete stale Marketing / `urn:cdao` / `cdao:` nodes in running graph — `cleanup_stale_cdao.py` ready, not run live
- [x] Grep gates in `backend/scudo/tests/test_provenance.py`: no `Marketing`, `urn:cdao`, bare `cdao:` in fixtures or emitted graph

### UI honesty

- [x] Persistent **ILLUSTRATIVE DATA** banner when `meta.dataProvenance === "synthetic"` (`IllustrativeDataBanner.tsx`)
- [x] Node panel shows `caption` / `summary` as expositional context

---

## Phase 1 — Matching-mode dashboard (not `frontend/` port)

Work happens in `packages/dashboard`, triggered from MatchMaker via sync script or monorepo path.

### 1.1 Matching demo profile

When `VITE_DEMO_MODE=true` and graph is SCUDO matching (`project.name` contains "SCUDO Matching" or env `VITE_MATCHING_MODE=true`):

| Control | Action |
|---------|--------|
| Domain / structural view toggles | Hide or disable |
| File explorer, code viewer, pathfinder | Hide |
| Persona selector (developer-focused) | Hide or default to "Executive tour" |
| Layer legend | Keep — exposes ETL / Matcher / Gate / Persist bands |
| Project overview + guided tour | Keep — primary exposition vehicle |
| Learn panel | Keep if tour steps reference matcher stages |

### 1.2 Macro pipeline fidelity

- Graph uses **pinned layout** for architecture stages (ETL → parse → semantic → rank → gate → bands → orchestration → HITL → JAPI), not random ELK scatter for those nodes
- Existing `layers[]` in `matching-graph.json` group nodes; `tour[]` walks the story left-to-right
- Edge colouring: PASS green, BORDERLINE amber, FAIL red per D-2

### 1.3 Micro match drill-down

- Selecting a **vendor product** node opens NodeInfo with: candidates, scores, band, chosen CDAO target
- Data from graph node `summary` / edge `weight` / `band` metadata already embedded by `build_matching_graph.py`
- No separate React page required if NodeInfo + tour suffice; optional `MatchDetail` drawer if owner wants isolation

### 1.4 Revert wrong target

In `MatchMaker/frontend/`:

- [x] Remove routes `/mapping`, `/mapping/drilldown`, `/mapping/lab` and associated pages added in the rejected bolt-on
- [x] Restore nav to ingestion-only scope
- [x] Keep `GET /api/mapping/graph` on backend — API is still valid

---

## Phase 2 — Real `GET /api/mapping/graph`

- Route: `backend/routes/mapping.py` — join captions from fixture (C-5)
- Tests: `backend/tests/test_mapping_graph_route.py`
- Response shape: MatchPayload (schema B) unless `format=knowledge-graph` query added later
- **Sync pipeline:** after `python backend/scudo/build_matching_graph.py`:
  1. Write `backend/scudo/fixtures/matching-graph.json` (KnowledgeGraph)
  2. Copy to `packages/dashboard/public/matching-graph.json`
  3. Optionally copy to `.understand-anything/knowledge-graph.json` for local `GRAPH_DIR` dev

### Local dev recipe (`:5177`)

```bash
# MatchMaker repo
python backend/scudo/build_matching_graph.py
cp backend/scudo/fixtures/matching-graph.json \
   ../Understand-Anything/understand-anything-plugin/packages/dashboard/public/matching-graph.json

# Dashboard repo
cd packages/dashboard
GRAPH_DIR=/path/to/MatchMaker pnpm dev --port 5177
# Or: VITE_DEMO_MODE=true with matching-graph.json in public/
```

---

## Phase 3–4 — AWS deploy (separate plan, hard gate)

Prerequisites: owner approves `:5177` screenshots for Phases 0–2.

### Deploy target change (from original plan)

CodeBuild currently runs `cd frontend && npm run build`. **Revise to:**

1. Submodule or clone `understand-anything-plugin` at pinned commit, OR vendor `packages/dashboard` into MatchMaker `dashboard/` subtree
2. Run `pnpm build:demo` (or `vite build` with `VITE_DEMO_MODE` + `VITE_GRAPH_URL=/api/mapping/graph` once live)
3. Upload `dist/` to S3 / CloudFront per `infra/scudo-poc-frontend.yaml`

### Infra constraints (unchanged from Codex review)

- Revise existing `infra/scudo-poc-*.yaml` in place (C-3)
- CloudFront `/healthz` behaviour or smoke via `/api/mapping/vendors` (C-7)
- Origin secret header before external demo (C-8)
- Schema migration sentinel before `init_db.sql` (C-6)

---

## Acceptance criteria

### Phase 0

- [x] `grep -r 'Marketing\|urn:cdao' backend/scudo/fixtures/ packages/dashboard/public/matching-graph.json` → no matches
- [x] Every graph node has expositional text; banner visible on load
- [x] `pytest backend/scudo/tests/test_provenance.py` passes

### Phase 1

- [x] `:5177` loads SCUDO graph with deliberate left-to-right pipeline visible without manual panning (agent-verified; tour overlay)
- [x] No broken code-comprehension buttons in matching mode
- [ ] Owner sign-off: "tells the story"

### Phase 2

- [x] `curl localhost:5001/api/mapping/graph` → 200, valid JSON, synthetic meta (pytest-covered)
- [x] Rebuild script syncs dashboard static file

### Phase 3–4

- [ ] CloudFront URL loads matching demo
- [ ] Playwright smoke: banner + tour step 1 + gate node visible

---

## File map

| Path | Responsibility |
|------|----------------|
| `backend/scudo/fixtures/cdao_catalogue.json` | Canonical synthetic CDAO taxonomy |
| `backend/scudo/build_matching_graph.py` | Emit KnowledgeGraph `matching-graph.json` |
| `backend/scudo/fixtures/matching-graph.json` | Committed graph artifact (MatchMaker) |
| `backend/routes/mapping.py` | `GET /api/mapping/graph` |
| `docs/superpowers/matching-data-provenance.md` | Bands + IRI + classification table |
| `packages/dashboard/public/matching-graph.json` | Static graph for demo build (synced) |
| `packages/dashboard/src/App.tsx` | Matching-mode chrome hiding |
| `packages/dashboard/src/components/GraphView.tsx` | Band edge colouring (exists) |
| `packages/dashboard/src/components/IllustrativeDataBanner.tsx` | ILLUSTRATIVE banner |
| `infra/scudo-poc-build.yaml` | CodeBuild — **must build dashboard** |

---

## Self-review checklist (spec author)

| Requirement | Covered? | Notes |
|-------------|----------|-------|
| C-1 frontend target | ✅ D-1 | Dashboard, not `frontend/` |
| C-2 bands | ✅ D-2 | Matcher canonical |
| C-3 infra revise in place | ✅ Phase 3–4 | |
| C-4 data cleanup + grep gates | ✅ Phase 0 | FalkorDB live cleanup still open |
| C-5 caption join at API | ✅ Phase 2 | |
| C-6 schema migration sentinel | ✅ Phase 3–4 | |
| C-7 healthz smoke | ✅ Phase 3–4 | |
| C-8 auth hardening | ✅ D-5 gate | |
| KnowledgeGraph vs MatchPayload | ✅ Two schemas section | Prevents repeat regression |
| Revert `frontend/` bolt-on | ✅ §1.4 | |
| Owner exposition / story | ✅ Intent + Phase 1 | |

**Gaps to resolve in implementation plan:** exact monorepo strategy for dashboard in CodeBuild (submodule vs subtree vs npm package).

---

## References

- Architecture diagram: `docs/superpowers/scudo-architecture-flat.mmd`
- Implementation plan (completed): `docs/superpowers/plans/2026-06-24-scudo-matching-dashboard.md`
- Original combined plan: `docs/superpowers/plans/2026-06-24-scudo-matching-frontend-and-deploy.md`
- Agent memory: `AGENTS.md`
- Deploy notes: `backend/scudo/DEPLOY.md`

## Related

- [Agent operating rules](/reference/agents.md)
- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
