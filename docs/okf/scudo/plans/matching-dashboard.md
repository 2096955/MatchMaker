---
type: Plan
title: Matching Dashboard Plan
description: Plan for honest matching UI on understand-anything dashboard (implemented).
tags:
- plan
- matching
staleness: historical
timestamp: '2026-08-17T09:02:03Z'
---

# SCUDO Matching Dashboard + Data Correctness Implementation Plan

> **Status (2026-06-24):** **Implemented** via subagent-driven development. Tasks 0–6 complete; Task 7 awaiting **owner sign-off** before AWS deploy. Changes are **uncommitted** in both MatchMaker and understand-anything dashboard repos.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SCUDO matching UI on the understand-anything dashboard (`:5177`) honest, coherent, and expositional — then wire the backend graph builder and API as source of truth.

**Architecture:** Ship `packages/dashboard` (React 19 + `@xyflow/react`), NOT `MatchMaker/frontend/`. `build_matching_graph.py` emits **KnowledgeGraph** JSON (dashboard contract) from fixed `cdao_catalogue.json`. A sync script copies the artifact to the dashboard `public/` folder. Matching mode hides code-comprehension chrome. `GET /api/mapping/graph` returns MatchPayload for API clients; static demo uses the KnowledgeGraph file.

**Tech Stack:** Python 3.12 / Flask, understand-anything dashboard (React 19, Vite 6, Tailwind 4, zustand), pytest.

**Spec:** `docs/superpowers/specs/2026-06-24-scudo-matching-frontend-spec.md` (binding — especially D-1 dashboard target).

**Hard gate:** Do NOT start `docs/superpowers/plans/2026-06-24-scudo-matching-deploy.md` until owner approves `:5177` screenshots from this plan.

---

## Implementation summary (what was done)

| Task | Status | Delivered |
|------|--------|-----------|
| **0** KnowledgeGraph builder | ✅ Done | `build_matching_graph.py` emits `kind: "codebase"` graph (42 nodes, 7 layers, 6 tour steps at Phase 0; 57 nodes / 8 layers since the 2026-07 M10 conceptual-enrichment layer); `build_match_payload()` retained for API; `meta.json` sidecar; band text uses 0.85/0.75 |
| **1** Sync script | ✅ Done | `backend/scudo/scripts/sync_matching_graph_to_dashboard.sh` (default path `../../Understand-Anything/...`; override with `DASHBOARD_DIR`) |
| **2** ILLUSTRATIVE banner | ✅ Done | New `IllustrativeDataBanner.tsx`; provenance from `meta.json` + graph project name fallback |
| **3** Matching-mode chrome | ✅ Done | Hides PathFinder, file explorer, persona/domain toggles, detail-level toggles (desktop + mobile) |
| **4** Revert frontend bolt-on | ✅ Done | All `frontend/src/pages/mapping/*` removed; routes/nav/styles cleaned; `reactflow` removed; `npm run build` passes |
| **5** API route | ✅ Done | `GET /api/mapping/graph` → 200 via `build_match_payload()` + `MemoryStore`; 2 pytest tests pass |
| **6** FalkorDB cleanup | ✅ Script only | `backend/scudo/scripts/cleanup_stale_cdao.py` with `--dry-run`; live cleanup not run |
| **7** Owner verification | ⏳ Pending | Graph + tour verified in browser; **owner sign-off** still required |

### Post-implementation fixes (same session)

- `App.tsx`: fallback fetch `matching-graph.json` when `knowledge-graph.json` fails (dev middleware returns invalid JSON for missing graph)
- `App.tsx`: set `dataProvenance: "synthetic"` when project name contains "SCUDO Matching"
- `packages/dashboard/.env.local`: `VITE_MATCHING_MODE=true` for local dev
- `AGENTS.md` created via continual-learning (durable prefs + workspace facts)

### Not done yet

- Git commits (user has not requested)
- Live FalkorDB cleanup + re-seed (`cleanup_stale_cdao.py` ready)
- Owner screenshot sign-off (Task 7 gate)
- Phase 3–4 AWS deploy (dashboard build in CodeBuild — separate plan)

### Verify locally

```bash
bash backend/scudo/scripts/sync_matching_graph_to_dashboard.sh
cd ../../Understand-Anything/understand-anything-plugin/packages/dashboard
VITE_MATCHING_MODE=true pnpm dev --port 5177
# Open printed URL with ?token=...
```

```bash
cd backend && PYTHONPATH=. pytest scudo/tests/test_provenance.py tests/test_mapping_graph_route.py -v
```

---

## File structure

| File | Responsibility |
|------|----------------|
| `backend/scudo/fixtures/cdao_catalogue.json` | Canonical CDAO taxonomy (no Marketing, `jpmorgan:data:cdao:*`) |
| `backend/scudo/build_matching_graph.py` | Emit KnowledgeGraph `matching-graph.json` + optional MatchPayload sidecar |
| `backend/scudo/fixtures/matching-graph.json` | Committed graph (KnowledgeGraph schema) |
| `backend/scudo/scripts/sync_matching_graph_to_dashboard.sh` | Copy graph to dashboard `public/` |
| `backend/scudo/tests/test_provenance.py` | Grep gates + band constants |
| `backend/routes/mapping.py` | `GET /api/mapping/graph` |
| `packages/dashboard/public/matching-graph.json` | Static demo graph (synced) |
| `packages/dashboard/public/meta.json` | `dataProvenance: "synthetic"` for banner |
| `packages/dashboard/src/components/IllustrativeDataBanner.tsx` | ILLUSTRATIVE banner (new component) |
| `packages/dashboard/src/App.tsx` | Matching-mode UI profile + graph/meta fallbacks |

---

### Task 0: Restore KnowledgeGraph emission in build_matching_graph.py

**Files:**
- Modify: `backend/scudo/build_matching_graph.py`
- Reference template: `Understand-Anything/.../packages/dashboard/public/matching-graph.json` (structure only — IRIs must come from catalogue)
- Test: `backend/scudo/tests/test_provenance.py`

The current script emits MatchPayload only — this broke `:5177`. Restore KnowledgeGraph output as the primary artifact.

- [x] **Step 1: Write failing test for graph schema**

Add to `backend/scudo/tests/test_provenance.py`:

```python
def test_matching_graph_is_knowledge_graph_schema():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "matching-graph.json"
    raw = json.loads(path.read_text())
    assert raw.get("kind") == "codebase"
    assert "layers" in raw and len(raw["layers"]) >= 4
    assert "tour" in raw and len(raw["tour"]) >= 3
    assert "project" in raw
    node = raw["nodes"][0]
    assert "summary" in node and "name" in node
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. pytest scudo/tests/test_provenance.py::test_matching_graph_is_knowledge_graph_schema -v`  
Expected: FAIL (`kind` missing or file has MatchPayload `meta` top-level)

- [x] **Step 3: Rewrite build_matching_graph.py**

Emit structure matching the dashboard template:
- Top-level: `version`, `kind: "codebase"`, `project`, `nodes`, `edges`, `layers`, `tour`
- Pipeline nodes: `etl-*`, `match-*`, `orch-*`, `store-*` (reuse ids from template)
- CDAO nodes: read from `cdao_catalogue.json` with `jpmorgan:data:cdao:*` ids; `summary` = caption
- Vendor products: `mds.<vendor>:<uuid>` or `vendor:<vendor>:<product_id>` ids matching existing dashboard edges
- Edge `data.band` + `weight` on candidate edges; band thresholds from `config.py` (0.85 / 0.75)
- **Remove** all Marketing nodes, layers, tour steps, vendor `LSEG-MKT-ASSETS`
- Gate/orchestration summaries use **matcher bands** (0.85 / 0.75), not diagram "80% / 70%"

Also write `fixtures/meta.json` sidecar:

```json
{ "dataProvenance": "synthetic", "bands": { "pass": 0.85, "borderline": 0.75 } }
```

> **Stale as of 2026-08-17 — historical record.** Step 3 above (and this JSON block) states
> the bands as of 2026-06-24. The floor moved to `0.75` (PASS `0.80` / FAIL `0.70`) under
> `docs/superpowers/plans/2026-07-04-scudo-5zone-alignment.md` Task 1. Retained unedited as
> the record of what was true then. Live values:
> `docs/superpowers/matching-data-provenance.md`.
>
> **The instruction was superseded by a better outcome — do not re-apply it.** The builder no
> longer hardcodes band values at all: `backend/scudo/build_matching_graph.py:33-34` derives
> them from config —
> `PASS_THRESHOLD = pass_threshold()` / `BORDERLINE_THRESHOLD = borderline_threshold()`
> (imported from `scudo_mapping_mcp.config` at lines 29-30) — and every emission site uses
> those constants (`:90` caption, `:129`/`:131` banding, `:515-516`, `:1371-1372` the
> `meta.json` sidecar). So the shipped artifact tracks config automatically. Measured
> 2026-08-17 — `cat backend/scudo/fixtures/meta.json` really contains:
>
> ```json
> {
>   "dataProvenance": "synthetic",
>   "bands": {
>     "pass": 0.8,
>     "borderline": 0.7
>   }
> }
> ```

- [x] **Step 4: Regenerate and run tests**

Run: `cd backend && python -m scudo.build_matching_graph && PYTHONPATH=. pytest scudo/tests/test_provenance.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** *(deferred — not requested)*

```bash
git add backend/scudo/build_matching_graph.py backend/scudo/fixtures/matching-graph.json backend/scudo/tests/test_provenance.py
git commit -m "fix: emit KnowledgeGraph matching-graph for dashboard compatibility"
```

---

### Task 1: Sync script + dashboard static files

**Files:**
- Create: `backend/scudo/scripts/sync_matching_graph_to_dashboard.sh`
- Modify: `packages/dashboard/public/matching-graph.json` (via sync)
- Create: `packages/dashboard/public/meta.json`

- [x] **Step 1: Create sync script**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DASH="${DASHBOARD_DIR:-$ROOT/../Understand-Anything/understand-anything-plugin/packages/dashboard}"
python -m scudo.build_matching_graph
cp "$ROOT/backend/scudo/fixtures/matching-graph.json" "$DASH/public/matching-graph.json"
cp "$ROOT/backend/scudo/fixtures/meta.json" "$DASH/public/meta.json"
echo "Synced to $DASH/public/"
```

- [x] **Step 2: Run sync**

Run: `bash backend/scudo/scripts/sync_matching_graph_to_dashboard.sh`  
Expected: copies without error

- [x] **Step 3: Verify no forbidden strings in dashboard file**

Run: `grep -E 'Marketing|urn:cdao' packages/dashboard/public/matching-graph.json; echo exit:$?`  
Expected: grep finds nothing (exit 1)

- [ ] **Step 4: Commit** *(deferred — not requested)* (MatchMaker repo only — dashboard may be separate repo)

```bash
git add backend/scudo/scripts/sync_matching_graph_to_dashboard.sh backend/scudo/fixtures/meta.json
git commit -m "chore: add matching-graph sync script for dashboard"
```

---

### Task 2: ILLUSTRATIVE banner in dashboard

**Files:**
- Modify: `packages/dashboard/src/components/WarningBanner.tsx` (or create if absent)
- Modify: `packages/dashboard/src/App.tsx`

- [x] **Step 1: Load meta.json in Dashboard useEffect** (alongside knowledge-graph fetch)

```typescript
const [dataProvenance, setDataProvenance] = useState<string | null>(null);
// in fetch bundle:
fetch(dataUrl("meta.json", accessToken))
  .then((r) => r.ok ? r.json() : null)
  .then((m) => setDataProvenance(m?.dataProvenance ?? null));
```

- [x] **Step 2: Render banner when synthetic** — implemented as `IllustrativeDataBanner.tsx` (separate from graph-validation `WarningBanner`)

```tsx
{dataProvenance === "synthetic" && (
  <WarningBanner variant="info">
    ILLUSTRATIVE DATA — synthetic taxonomy and scores for demonstration only.
  </WarningBanner>
)}
```

- [x] **Step 3: Manual verify**

Run dashboard: `cd packages/dashboard && pnpm dev --port 5177` with `matching-graph.json` in `public/`  
Open `http://127.0.0.1:5177/?token=...` — banner visible at top.

- [ ] **Step 4: Commit** *(deferred — not requested)* (dashboard repo)

---

### Task 3: Matching-mode chrome (hide code-comprehension controls)

**Files:**
- Modify: `packages/dashboard/src/App.tsx`

- [x] **Step 1: Detect matching mode**

```typescript
const isMatchingMode =
  import.meta.env.VITE_MATCHING_MODE === "true" ||
  graph?.project?.name?.includes("SCUDO Matching");
```

- [x] **Step 2: Conditionally hide controls** — also updated `MobileLayout.tsx`, `MobileBottomNav.tsx`, `MobileDrawer.tsx`

When `isMatchingMode`:
- Do not render: `FileExplorer`, `CodeViewer`, `PathFinderModal` trigger, domain/structural toggles if they error
- Keep: `GraphView`, `NodeInfo`, `LayerLegend`, `ProjectOverview`, tour/onboarding, `SearchBar`

- [x] **Step 3: Manual verify on :5177**

Click through toolbar — no file explorer, no broken view-mode buttons.

- [ ] **Step 4: Commit** *(deferred — not requested)* (dashboard repo)

---

### Task 4: Revert rejected frontend/ bolt-on

**Files:**
- Delete: `frontend/src/pages/mapping/PipelineView.jsx`, `MatchDrillDown.jsx`, `MappingPipelinePage.jsx`, `MappingDrillDownPage.jsx`, `MappingLayout.jsx`, `MappingGraphContext.jsx`, `MappingAgentLab.jsx`, `StageNode.jsx`, `mappingConstants.js`, `pipeline-layout.js`
- Modify: `frontend/src/App.jsx` — remove `/mapping` routes
- Modify: `frontend/src/components/Layout.jsx` — remove SCUDO nav link
- Modify: `frontend/package.json` — remove `reactflow` if only used by mapping

- [x] **Step 1: Remove routes and files**

- [x] **Step 2: Verify ingestion console still builds**

Run: `cd frontend && npm run build`  
Expected: PASS, no mapping routes

- [ ] **Step 3: Commit** *(deferred — not requested)*

```bash
git commit -m "revert: remove rejected /mapping bolt-on from ingestion console"
```

---

### Task 5: Fix GET /api/mapping/graph

**Files:**
- Modify: `backend/routes/mapping.py`
- Test: `backend/tests/test_mapping_graph_route.py`

- [x] **Step 1: Write failing test**

```python
def test_mapping_graph_returns_200(client):
    r = client.get("/api/mapping/graph")
    assert r.status_code == 200
    body = r.get_json()
    assert body["meta"]["dataProvenance"] == "synthetic"
    assert "nodes" in body
```

- [x] **Step 2: Run test — expect FAIL if 500**

Run: `cd backend && PYTHONPATH=. pytest tests/test_mapping_graph_route.py -v`

- [x] **Step 3: Fix route** — uses `build_match_payload()` with `MemoryStore` (no FalkorDB required offline)

- [x] **Step 4: Run test — expect PASS** — 2 tests including vendor drill-down

- [ ] **Step 5: Commit** *(deferred — not requested)*

---

### Task 6: FalkorDB stale node cleanup

**Files:**
- Modify: `backend/scudo/seed_falkordb.py` or add `backend/scudo/scripts/cleanup_stale_cdao.py`

- [x] **Step 1: Script deletes nodes matching** `Marketing`, `urn:cdao`, `cdao:` — `cleanup_stale_cdao.py` with `--dry-run`

- [ ] **Step 2: Re-seed from catalogue** *(script ready; not run — FalkorDB may not be up)*

Run: `python backend/scudo/seed_falkordb.py` (with FalkorDB up)

- [ ] **Step 3: Commit** *(deferred — not requested)*

---

### Task 7: Owner verification checkpoint (HARD GATE)

- [x] **Step 1: Start stack** — verified on `:5179` (5177 in use); graph loads with 42 nodes + tour

```bash
# terminal 1: backend on 5001
cd backend && FLASK_APP=app.py python -m flask run --port 5001
# terminal 2: dashboard on 5177
cd packages/dashboard && pnpm dev --port 5177
```

- [x] **Step 2: Capture screenshots** — tour welcome + pipeline visible (agent session)

- Full pipeline overview (layers visible, left-to-right flow)
- Gate node selected (band legend visible)
- ILLUSTRATIVE banner visible
- Tour step 1

- [ ] **Step 3: Owner sign-off** before any AWS deploy work.

---

## Plan self-review (vs spec)

| Spec requirement | Task | Done? |
|------------------|------|-------|
| D-1 dashboard ships | Tasks 0–3 | ✅ |
| D-2 matcher bands | Task 0 summaries + edge data | ✅ |
| D-3/D-4 IRIs, no Marketing | Task 0 + grep in test | ✅ |
| Phase 0 provenance banner | Task 2 | ✅ |
| Phase 1 hide dead controls | Task 3 | ✅ |
| Revert frontend bolt-on | Task 4 | ✅ |
| Phase 2 API | Task 5 | ✅ |
| FalkorDB cleanup | Task 6 | ⏳ script only |
| Owner gate | Task 7 | ⏳ pending sign-off |

**Open decision for deploy plan:** monorepo strategy (git submodule vs vendor copy of dashboard into MatchMaker).

---

## Execution handoff

**Plan implemented 2026-06-24** via subagent-driven development. Remaining before AWS deploy:

1. Owner sign-off on `:5177` screenshots (Task 7)
2. Git commits (MatchMaker + dashboard repos)
3. Live FalkorDB cleanup when graph is up
4. Write/execute `docs/superpowers/plans/2026-06-24-scudo-matching-deploy.md` (Phase 3–4)

## Related

- [Matching frontend spec](/specs/matching-frontend.md)
