# SCUDO Matching Front-End Correctness + Console Deployment Implementation Plan

> **⚠️ SUPERSEDED for front-end target (2026-06-24):** Owner chose the **understand-anything dashboard** (`:5177`), not a port into `frontend/`. Binding spec: `docs/superpowers/specs/2026-06-24-scudo-matching-frontend-spec.md`. Executable plans split into:
> - **Data + dashboard:** `docs/superpowers/plans/2026-06-24-scudo-matching-dashboard.md` — **IMPLEMENTED** (Tasks 0–6; Task 7 owner sign-off pending)
> - **AWS deploy:** start only after owner approves `:5177` screenshots (Phase 3–4 below, revised for dashboard build)
>
> The body below retains Codex review findings (C-1–C-8) for reference; **C-1 decision is now D-1 in the spec (ship dashboard).**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SCUDO vendor→CDAO matching front-end *demonstrably honest and faithful to the architecture diagram*, then deploy the console to AWS so it can be shown to stakeholders without misrepresenting fabricated data as real.

**Architecture:** Two front-end views over the SCUDO matcher — a **macro "pipeline" view** that faithfully reproduces the AIA architecture diagram's deliberate flow (ETL → A/B/C → confidence gate → bands → JAPI persist), and a **micro "match drill-down"** showing one vendor product's candidates, scores, and band. Backend exposes a real `GET /api/mapping/graph`. The console (Flask + React SPA) deploys on AWS via CloudFront → ALB → ECS Fargate, consuming Codex-owned Aurora MySQL.

**Tech Stack:** Python 3.12 / Flask (backend + matcher), React 19 + Vite + `@xyflow/react` (front-end), AWS CloudFormation (CodeBuild + CloudShell deploy pattern), us-east-1.

---

## ⛔ READ FIRST — Why this plan exists

The current matching front-end was rejected by the product owner. **Mock data is acceptable** — the owner said so directly: *"I don't mind it mocked, but if you're mocking the base, you need to be able to tell a story and convey flow throughout the setup… every part should be capable of 'expositional' context."* The defects are about **coherence, honesty, and exposition**, not about the data being synthetic:

1. **Incoherent mock + presented as real (BLOCKER).** The mock CDAO taxonomy includes a **"Marketing" domain** (`backend/scudo/fixtures/cdao_catalogue.json`) that makes no sense for the financial-data vendors in the demo (LSEG, S&P Global, ICE) — it reads as a hallucination and *breaks the story*. It is also under a made-up IRI scheme `urn:cdao:*` while the canonical scheme is `jpmorgan:data:cdao:*` (`backend/scudo/orchestrator.py:44`), and nothing in the UI signals the data is illustrative. **Product owner: "we can't wire this in without getting our asses chewed."** The risk is not "mock data" — it is *incoherent* mock data shown *as if it were the real bank taxonomy*.
2. **The graph has no deliberate flow and no exposition.** The architecture diagram (reference: `docs/superpowers/scudo-agentic-architecture.drawio`, and the image the owner shared) has a deliberate left-to-right pipeline where each stage clearly leads to the next. The current dashboard uses generic auto-layout (ELK over collapsible clusters) which scatters the nodes, conveys no flow, and — critically — **no part explains itself**. The owner needs every stage/node to carry *expositional context*: what it is, what it consumes, what it produces, and how it advances the vendor→CDAO matching story.
3. **Buttons don't work.** The front-end is the `understand-anything` *code-comprehension* dashboard repurposed for matching. Its controls (`domain`/`structural` view modes, `file`/`class` detail levels, file explorer, code viewer, pathfinder — see `packages/dashboard/src/App.tsx:458-597`) are meaningless or broken in a matching context.

**The root cause of #2 and #3 is the same:** a generic code-graph explorer is being forced to do a job it was not built for. This plan treats that head-on (Phase 1 decision).

**Two disciplines apply to every task:**
- **Honesty:** synthetic data is fine, but it must be *unmistakably labelled* "ILLUSTRATIVE / SYNTHETIC" in the UI and the data file. Never let mock data read as the real CDAO taxonomy.
- **Exposition:** the mock must *tell the end-to-end matching story*. The taxonomy must be coherent with the vendor products (financial market/reference data, not Marketing), and **every part — each pipeline stage, each node, each match — must be able to explain itself in context** (a short expositional caption + a guided walkthrough). A viewer should be walked through "vendor product arrives → parsed → semantically matched → scored → gated by confidence → routed → persisted" as a narrative.

---

## 🔬 CODEX REVIEW — verified findings & incorporated revisions (2026-06-24, thread 019ef87c)

An independent Codex pass verified the repo and found errors in the first draft. All findings below were **hand-verified against the repo** and are now binding corrections — they OVERRIDE any contradicting detail later in this doc until that detail is rewritten.

**C-1 (CRITICAL — front-end target was wrong).** The matching visualisation I built earlier lives in a **separate repo**: `Understand-Anything/understand-anything-plugin/packages/dashboard` (React 19 + `@xyflow/react`). The **deployed console front-end is THIS repo's `frontend/`** — React **18.3** + Vite 6, **no `@xyflow/react`** (`frontend/package.json:13`), entry `frontend/src/App.jsx`, matching demo at `frontend/src/pages/mapping/MappingDemo.jsx`. The CodeBuild deploy does `cd frontend && npm run build`. **DECISION REQUIRED (Task 1.0):** either (a) port the macro/micro matching views INTO `frontend/` (React 18 — use a React-18-compatible graph lib: `reactflow` v11 or `cytoscape`, per the original deployment plan which already specced `cytoscape`), or (b) deploy the understand-anything dashboard as a separate artifact and point CodeBuild at it. Do NOT assume `packages/dashboard` is what ships. Every `packages/dashboard/...:line` reference below is from the OTHER repo and is illustrative only.

**C-2 (CRITICAL — confidence bands were wrong).** Matcher reality (`backend/scudo_mapping_mcp/config.py:44` `CONFIDENCE_FLOOR=0.80`; `matching.py:44-55`): **PASS ≥ floor+0.05 = 0.85; BORDERLINE within ±0.05 of floor = 0.75–0.85; FAIL < floor−0.05 = 0.75.** The architecture diagram says "80% floor / 70-79% / ≥80%" — that DISAGREES with the code. **Reconcile (Task 0.0):** decide with the owner whether the diagram narrative or the config is canonical, then make UI band labels + edge colouring use the SAME numbers as the matcher. Do not hardcode 0.80/0.70 anywhere.

**C-3 (infra already exists — revise, don't recreate).** `infra/scudo-poc-foundation.yaml`, `infra/scudo-poc-app.yaml`, `infra/scudo-poc-frontend.yaml`, `infra/scudo-poc-build.yaml` already exist (Jun 23). Phase 3 must **read and revise these in place**, not create `-console-` duplicates (duplicate ECR/export/resource names will fail against deployed stacks). First task of Phase 3 is to diff the existing files against the requirements below.

**C-4 (data cleanup scope incomplete).** `backend/scudo_mapping_mcp/ingest.py` ALSO emits `cdao:*` IRIs and Flask startup seeds from it (`backend/routes/mapping.py:39`). Phase 0 must include `ingest.py`, the regenerated `matching-graph.json`, and a **live FalkorDB cleanup** (stale Marketing/`urn:cdao`/`cdao:` nodes persist in the running graph). Add repo + API grep gates asserting none of `Marketing`, `urn:cdao`, bare `cdao:` survive.

**C-5 (caption/provenance can't round-trip the store).** `TaxonomyNode` (`backend/scudo_mapping_mcp/models.py:63`) has only `iri/label/parent_iri/children_iris` — no description/caption, and `seed_falkordb.py` does not persist one. The `GET /api/mapping/graph` route must **join caption/provenance from the fixture at response time** (or extend the store schema). Don't assume captions survive the store.

**C-6 (schema guard too weak).** Guarding only on `metadata.users` empty can still DROP populated `tp_*`. Use a **migration sentinel** (e.g. a `schema_migrations` row) AND check `tp_provider`/`tp_dataset`/`tp_dataset_col` row counts before running `init_db.sql`.

**C-7 (CloudFront `/healthz`).** Plan routes only `/api/*` to the ALB but smoke-tests `https://<cf>/healthz` — that 404s. Either add a `/healthz` ALB behaviour OR smoke via `/api/mapping/vendors`. (Existing `infra/scudo-poc-frontend.yaml:102` only forwards `/api/*` + `/mcp/*`.)

**C-8 (auth is public-demo unsafe).** `SCUDO_AUTH_ALLOW_DEV=1` authenticates EVERY caller as the dev principal; the CloudFront origin prefix-list limits direct ALB access but does NOT authenticate viewers. Add a CloudFront→ALB **secret origin header** check (or IP allowlist / real auth) before this is shown outside a controlled demo.

**Codex also recommends splitting this into two executable plans** — *Plan 1: front-end + data correctness* (Phases 0–2, gated by owner screenshots) and *Plan 2: AWS deploy* (Phases 3–4, only after Plan 1 passes). This doc keeps them together but the Phase 2→3 boundary is a HARD GATE: do not start Phase 3 until Phases 0–2 are owner-approved.

---

## Global Constraints

- **AWS account:** `954976331678` (`cb4115669a-genaipocs-aw`), region **us-east-1** only.
- **Do NOT edit Codex-owned files:** `backend/scudo/data-platform.yaml`, `backend/scudo/build-pipeline.yaml`, `backend/scudo/template.yaml`. These provision/deploy the **Lambda** runtime and the data platform. The console is a *separate* set of stacks.
- **Console deploys are a distinct stack set** named `scudo-poc-console-*` so they never collide with Codex's `scudo-poc-*` Lambda stacks. The console-build CloudFormation stack/CodeBuild project is `scudo-poc-console-build` (NOT `scudo-poc-build`, which is Codex's).
- **Repo is public:** `github.com/2096955/MatchMaker`, working branch `scudo-phase0-foundations`. Deploys clone this branch in CodeBuild.
- **Local shell has no AWS creds.** All AWS provisioning runs in the user's authenticated **CloudShell** (us-east-1). Local commands (commit/push, tests, builds) run via the session's permission prompts.
- **Canonical IRI schemes (verbatim, `orchestrator.py:44`):** vendor products `mds.<vendor>:<uuid5>`; CDAO nodes `jpmorgan:data:cdao:<...>`. The mock `urn:cdao:*` scheme is WRONG and must not appear in any shipped artifact.
- **Confidence bands (CODE REALITY — see C-2):** floor `CONFIDENCE_FLOOR=0.80`, half-width `0.05` → **PASS ≥ 0.85; BORDERLINE 0.75–0.85; FAIL < 0.75**. The architecture diagram's "80% / 70-79%" disagrees with the code; reconcile in Task 0.0 and use ONE set of numbers everywhere (matcher = source of truth unless the owner changes the config).
- **Auth (PoC):** dev-auth only — `SCUDO_AUTH_ALLOW_DEV=1`, `SCUDO_AUTH_DEV_PRINCIPAL=2096955@cognizant.com`. This is a flagged sandbox simplification, NOT real auth. Lock the ALB to the CloudFront origin prefix list; same-origin (drop wildcard CORS at `backend/app.py:53`).
- **Brand palette (Cognizant, from NeuroSAN UI — verified):** canvas `#f5f3ef`, surface `#ffffff`, text `#1a1a1a`/`#8b8680`, accent **`#1a5ecf`**, green `#66bb6a`, connector grey `#d0cbc5`. Light theme.
- **Never say "production ready" or "fully operational"** in any status, comment, or copy. Say "complete and ready for review."
- **Secrets:** never commit or paste the AWS `/run` API key or the AppSync API key. Console DB creds come from Secrets Manager only.

---

## File Structure

**Front-end — THIS repo's `frontend/` (React 18.3 + Vite 6, the artifact CodeBuild ships). See C-1 + Task 1.0 for the port-vs-separate decision.** Paths below assume porting into `frontend/` (recommended); adjust if the owner picks "deploy dashboard separately". Use a React-18 graph lib (`reactflow` v11 or `cytoscape`), NOT `@xyflow/react` (that's React 19, the other repo).
- `frontend/src/pages/mapping/PipelineView.jsx` — NEW. Bespoke macro architecture-flow view (faithful to the diagram).
- `frontend/src/pages/mapping/pipeline-layout.js` — NEW. Curated/pinned node positions + edges for the deliberate flow.
- `frontend/src/pages/mapping/MatchDrillDown.jsx` — NEW. Micro view: one vendor product's candidates, scores, band, cost-ladder rungs.
- `frontend/src/pages/mapping/MappingDemo.jsx` — MODIFY (existing). Host the two new views; this is the current matching page.
- The understand-anything `packages/dashboard` graph views are NOT what ships (they caused defects #2/#3 and are React 19).

**Backend (`backend/`):**
- `backend/scudo/fixtures/cdao_catalogue.json` — REPLACE or RELABEL (Phase 0).
- `backend/scudo/seed_falkordb.py` — `_DEFAULT_NODES` relabel/remove (Phase 0).
- `backend/scudo/build_matching_graph.py` — emit honest data + provenance flags.
- `backend/routes/mapping.py` — ADD `GET /api/mapping/graph` (Phase 2).

**Infra (`infra/` — these files ALREADY EXIST as of Jun 23; REVISE in place, do NOT create `-console-` duplicates — see C-3):**
- `infra/scudo-poc-foundation.yaml` — Stack B (ECR, IAM roles, log group). Diff against requirements, fill gaps.
- `infra/scudo-poc-app.yaml` — Stack C (ALB + ECS Fargate Flask; imports Codex's console MySQL).
- `infra/scudo-poc-frontend.yaml` — Stack D (S3 + CloudFront).
- `infra/scudo-poc-build.yaml` — Stack E (VPC CodeBuild: image→ECR, deploy C+D, schema init, SPA ship). NOTE: distinct from Codex's `backend/scudo/build-pipeline.yaml` (`scudo-poc-build` Lambda build). Confirm the CloudFormation stack/project names do not collide before deploying.

---

## Interfaces (shared contracts)

**Matching graph payload** (`GET /api/mapping/graph?vendor=<v>&ref=<id>` → JSON; also the shape `build_matching_graph.py` emits):

```ts
// src/matching/types.ts
export type Band = "pass" | "borderline" | "fail";
export interface Provenance { source: "real" | "synthetic"; note?: string; }
export interface MatchNode {
  id: string;            // canonical IRI: mds.<vendor>:<uuid> or jpmorgan:data:cdao:<...>
  label: string;
  kind: "vendor-product" | "cdao-domain" | "cdao-subdomain" | "cdao-concept" | "stage" | "store";
  provenance: Provenance;            // REQUIRED — drives the "SYNTHETIC" badge
  caption: string;                   // REQUIRED — expositional one-liner (≤140 chars)
}
export interface MatchEdge {
  source: string; target: string;
  band?: Band;                       // routing/candidate edges
  weight?: number;                   // similarity in [0,1] for candidate edges only
  kind: "candidate" | "chosen" | "route" | "hierarchy" | "flow";
}
export interface MatchPayload {
  meta: { generatedAt: string; dataProvenance: "real" | "synthetic"; bands: {pass:number; borderline:number} };
  nodes: MatchNode[];
  edges: MatchEdge[];
}
```

- **Produced by:** Task 2.1 (`build_matching_graph.py`) and Task 2.2 (`GET /api/mapping/graph`).
- **Consumed by:** Phase 1 front-end views.

---

## PHASE 0 — Coherent, Labelled, Expositional Mock Data (BLOCKER — nothing ships until this passes)

Mock data is acceptable. The bar is: (1) **coherent** with the vendor-matching story, (2) **labelled** as illustrative, (3) **expositional** — every node carries a one-line caption explaining its role.

### Task 0.0: Reconcile the confidence bands (DECISION REQUIRED — see C-2)

The matcher code says PASS ≥ 0.85 / BORDERLINE 0.75–0.85 / FAIL < 0.75 (`config.py:44`, `matching.py:44-55`); the architecture diagram says 80% / 70-79% / ≥80%. They disagree.

- [ ] **Step 1:** Use AskUserQuestion: is the matcher config canonical (update the diagram narrative + any UI copy to 0.85/0.75), or should the config change to match the diagram (edit `CONFIDENCE_FLOOR`/`BORDERLINE_HALF_WIDTH`)?
- [ ] **Step 2:** Record one canonical band definition in `docs/superpowers/matching-data-provenance.md`; every later task (UI labels, edge colours, captions) uses exactly those numbers.

**Acceptance:** A single band definition exists and matches the matcher's runtime behaviour.

### Task 0.1: Audit and classify every data value the UI shows

**Files:**
- Read: `backend/scudo/fixtures/cdao_catalogue.json`, `backend/scudo/seed_falkordb.py:62-120`, `backend/scudo/build_matching_graph.py` (`_SAMPLES`), **and `backend/scudo_mapping_mcp/ingest.py` (also emits `cdao:*` — see C-4)**.

- [ ] **Step 1:** Produce an inventory (`docs/superpowers/matching-data-provenance.md`): `value | file:line | synthetic? | coherent-with-story? | expositional-caption`.
- [ ] **Step 2:** Flag the **"Marketing" domain** and its concepts (`marketing-asset`, `marketing-campaign`, `creative-content`) as INCOHERENT for LSEG/SPG/ICE — these break the story and must be removed/replaced. The Market Data and Reference Data branches ARE coherent (equity/FX/fixed-income prices, indices, security master, corporate actions, legal entity, counterparty) — keep them.
- [ ] **Step 3:** Commit the inventory.

**Acceptance:** Every value is classified for coherence and has a proposed expositional caption.

### Task 0.2: Make the mock taxonomy coherent + add expositional captions

**Files:**
- Modify: `backend/scudo/fixtures/cdao_catalogue.json`.
- Modify: `backend/scudo/seed_falkordb.py` (`_DEFAULT_NODES` aligned to the same coherent set).

- [ ] **Step 1:** Remove the Marketing domain/subdomain/concepts. Ensure the taxonomy covers exactly the domains the demo vendors plausibly map to: **Market Data** (Pricing: equity/fixed-income/FX; Indices) and **Reference Data** (Instruments: security master, corporate actions; Entities: legal entity, counterparty). Add a third coherent branch only if a demo vendor needs it.
- [ ] **Step 2:** Switch IRIs to the canonical scheme `jpmorgan:data:cdao:<domain|subdomain|concept>:<slug>` (drop `urn:cdao:`).
- [ ] **Step 3:** Add a `"caption"` field (≤ 140 chars) to every node — its expositional one-liner (e.g. *"FX Rates — canonical store of spot/forward foreign-exchange rates; the target a vendor FX feed should map to."*).
- [ ] **Step 4:** Add `"provenance": "synthetic"` to every node and a top-level dataset label `"ILLUSTRATIVE — synthetic CDAO taxonomy for demonstration"`.
- [ ] **Step 5:** Commit `data(scudo): coherent + captioned synthetic CDAO taxonomy (drop incoherent Marketing branch)`.

**Acceptance:** No incoherent nodes remain; every node has a caption + provenance; canonical IRI scheme used.

### Task 0.3: `build_matching_graph.py` propagates provenance + captions; test enforces it

**Files:** Modify `backend/scudo/build_matching_graph.py`.

- [ ] **Step 1: Write the failing test** `backend/scudo/tests/test_provenance.py`:
```python
import json, subprocess, sys, os
def test_every_node_is_labelled_and_expositional():
    subprocess.run([sys.executable, "-m", "scudo.build_matching_graph"], cwd="backend", check=True,
                   env={**os.environ, "STORE_BACKEND": "memory", "FRAME_SOURCE": "mock"})
    g = json.load(open("backend/scudo/fixtures/matching-graph.json"))
    for n in g["nodes"]:
        assert "provenance" in n, f"{n['id']} missing provenance"
        assert n.get("caption"), f"{n['id']} missing expositional caption"
        assert not n["id"].startswith("urn:cdao:"), f"{n['id']} uses the fabricated scheme"
    assert g["meta"]["dataProvenance"] == "synthetic"
    assert "marketing" not in json.dumps(g).lower(), "incoherent Marketing branch still present"
```
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement: emit `provenance` + `caption` per node, `meta.dataProvenance`, drop `urn:cdao:`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit.

**Acceptance:** The test passes — the dataset is coherent, labelled synthetic, captioned, and free of the fabricated scheme/Marketing branch.

---

## PHASE 1 — Matching Comprehension Front-End

### Task 1.0: DECISION — bespoke matching views vs. adapting the code-comprehension dashboard (DECISION REQUIRED)

**Context for the decision (state it to the owner):** The `understand-anything` dashboard is a *code-comprehension* explorer. Its auto-layout cannot reproduce the diagram's deliberate flow (defect #2), and its controls are code-oriented and inert here (defect #3). 

**Recommended (Option A):** Build two small bespoke views (`PipelineView`, `MatchDrillDown`) using `@xyflow/react` directly, with curated layout. This gives full control over the deliberate flow and removes every irrelevant control. ~3–4 focused components. Keep the Cognizant theme tokens.

**Alternative (Option B):** Keep the dashboard; force its layered view into strict left-to-right ranks and hide all code-comprehension controls. Cheaper but still fighting the tool; the macro flow will still be approximate.

- [ ] Use AskUserQuestion to confirm A or B. The rest of Phase 1 assumes **A** (re-scope if B).

### Task 1.1: Pipeline (macro) view faithful to the architecture diagram

**Files:**
- Create: `src/matching/pipeline-layout.ts`, `src/matching/PipelineView.tsx`, `src/matching/types.ts`.
- Reference: `docs/superpowers/scudo-agentic-architecture.drawio` (the authoritative flow).

**Interfaces:**
- Consumes: `MatchPayload` (above).
- Produces: `<PipelineView payload={MatchPayload} />`.

- [ ] **Step 1:** Define the deliberate flow as ordered ranks in `pipeline-layout.ts` (left→right): `Vendor Catalog → (A) Parse & Normalise → (B) Semantic Matching → (C) Rank & Score → Confidence Gate → {≥80 Aurora/JAPI, 70-79 Orchestration, <70 HITL} → JAPI Persist (Aurora→Neptune→OpenSearch)`. Pin x by rank, y by lane. Exact node list/labels copied from the drawio.
- [ ] **Step 2:** Render with `@xyflow/react`; band edges coloured PASS `#1a5ecf` / BORDERLINE `#5a8ad5` / FAIL `#8b8680`; the three gate routes are visually distinct arrows (this is the centrepiece of the diagram).
- [ ] **Step 3 (EXPOSITION — load-bearing):** Every stage node renders an **expositional caption** (what it consumes → does → produces) inline or on hover, sourced from the node `caption` field. Add a **guided "Tell the story" walkthrough**: a stepper that advances through the pipeline (Vendor arrives → Parse → Semantic → Rank → Gate → Route → Persist), highlighting the active stage and showing a 1–2 sentence narration per step. Each step must be reachable and explain how the matching advances. Nothing in the view is unexplained.
- [ ] **Step 4:** Add a persistent provenance banner when `payload.meta.dataProvenance === "synthetic"`: "ILLUSTRATIVE DATA — synthetic CDAO taxonomy, for demonstration."
- [ ] **Step 5: Verify** with Playwright against the dev server: assert (a) the seven pipeline stages render in left-to-right x-order; (b) the three gate routes have the three band colours; (c) every stage node exposes a non-empty caption; (d) the walkthrough advances through all steps with narration. Screenshot for the owner.
- [ ] **Step 6:** Commit.

**Acceptance:** Side-by-side with the architecture diagram, the flow reads the same left-to-right; the three confidence bands are visually obvious; and a viewer can be *walked through the whole matching story*, with every part explaining itself.

### Task 1.2: Match drill-down (micro) view

**Files:** Create `src/matching/MatchDrillDown.tsx`.

- [ ] **Step 1:** On selecting a vendor product, show its ranked CDAO candidates with similarity scores, the chosen mapping, the band, and the cost-ladder rungs (scope gate → precedent → retrieval dense/lexical/RRF → specialist → 3-band gate). Scores come from the real matcher payload.
- [ ] **Step 2:** Every CDAO candidate row shows its provenance badge.
- [ ] **Step 3: Verify** with Playwright: select ICE Corporate Actions (or a real product post-Phase-0); assert candidates + band render.
- [ ] **Step 4:** Commit.

### Task 1.3: Remove/disable the broken code-comprehension controls

**Files:** `src/App.tsx` (or the new matching app shell).

- [ ] **Step 1:** Remove or hide the `domain`/`structural` view-mode toggles, `file`/`class` detail levels, file explorer, code viewer, and pathfinder for the matching app (`App.tsx:458-597`). Keep only controls meaningful to matching (theme, the macro/micro toggle, search).
- [ ] **Step 2: Verify:** click every remaining control via Playwright; assert no console errors and each produces a visible effect. No dead buttons.
- [ ] **Step 3:** Commit.

**Acceptance:** Every visible button does something correct; no code-comprehension leftovers.

---

## PHASE 2 — Backend graph endpoint (real data)

### Task 2.1: `build_matching_graph.py` emits the `MatchPayload` shape
**Files:** Modify `backend/scudo/build_matching_graph.py`.
- [ ] Emit nodes/edges per the Interfaces (provenance, band, weight, kind). Keep the offline path (`STORE_BACKEND=memory`). TDD via `test_provenance.py` extended to assert edge `band`/`weight` presence on candidate/route edges.
- [ ] Commit.

### Task 2.2: `GET /api/mapping/graph`
**Files:** Modify `backend/routes/mapping.py` (note: this endpoint does NOT yet exist — confirmed by grep).

**Interfaces:** Produces `MatchPayload` JSON for `?vendor=&ref=`. Uses the existing matcher (`find_similar_products` / `map_vendor_product`). **C-5:** `TaxonomyNode` carries no caption/provenance, so the route must JOIN those from `cdao_catalogue.json` (by IRI) at response time — do not expect them from the store. Band thresholds come from the matcher config (Task 0.0), not hardcoded.

- [ ] **Step 1: Write the failing test** `backend/tests/test_mapping_graph_route.py`:
```python
def test_mapping_graph_returns_payload(client):
    r = client.get("/api/mapping/graph?vendor=lseg&ref=LSEG-EQ-PX",
                   headers={"X-Authenticated-User": "2096955@cognizant.com"})
    assert r.status_code == 200
    body = r.get_json()
    assert "nodes" in body and "edges" in body and body["meta"]["dataProvenance"] in ("real","synthetic")
    assert all("provenance" in n for n in body["nodes"])
```
- [ ] **Step 2:** Run; expect FAIL (404/route missing).
- [ ] **Step 3:** Implement the route (MySQL-free; matcher-only, so it works on the health path).
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit.

---

## PHASE 3 — AWS Console Deployment Stacks (the rest of the build)

> Pattern for all stacks: CloudFormation deployed from **CloudShell** by curling the template from `raw.githubusercontent.com/2096955/MatchMaker/scudo-phase0-foundations/infra/<file>`. Stack C/D/schema/SPA are driven by the Stack E CodeBuild project in one `start-build`. **Verified network exports already exist** from `backend/scudo/network-falkordb.yaml`: `scudo-poc-vpc-id`, `scudo-poc-private-subnets`, `scudo-poc-public-subnets`, `scudo-poc-lambda-sg`, `scudo-poc-falkor-sg`. **Console MySQL** (Codex-owned `scudo-poc-data`) exports: `scudo-poc-console-mysql-endpoint`, `scudo-poc-console-mysql-secret-arn` (secret JSON `{username,password}`), port 3306, reachable in-VPC (CIDR `10.42.0.0/16`).

### Task 3.1: Stack B — `infra/scudo-poc-console-foundation.yaml`
**Resources:** ECR repo `scudo-poc-console-backend`; CloudWatch log group `/aws/ecs/scudo-poc-console`; `ScudoConsoleExecRole` (AmazonECSTaskExecutionRolePolicy + `secretsmanager:GetSecretValue` scoped to the imported `scudo-poc-console-mysql-secret-arn`); `ScudoConsoleTaskRole` (near-empty; add `bedrock:InvokeModel` only if the live agent is enabled — v1 uses `SCUDO_AGENT_BACKEND=scripted`). **Exports:** ECR URI, both role ARNs, log group name.
- [ ] Write template; `aws cloudformation validate-template`; deploy in CloudShell; confirm `CREATE_COMPLETE`; commit.

### Task 3.2: Stack C — `infra/scudo-poc-console-app.yaml` (ALB + ECS Fargate Flask)
**Resources:**
- SGs: `AlbSg` (ingress 443/80 from the **CloudFront origin-facing prefix list** `com.amazonaws.global.cloudfront.origin-facing`, NOT 0.0.0.0/0); `AppSg` (Flask tasks). Ingress: ALB→App:5000; App→FalkorSg:6379 (import `scudo-poc-falkor-sg`); App→console MySQL:3306.
- **No Aurora of its own** — imports Codex's console MySQL endpoint/secret.
- ALB internet-facing in `scudo-poc-public-subnets`; target group port 5000, **health check path `/healthz`** (matcher `200`), NOT `/api/*`.
- ECS cluster `scudo-poc-console`; Flask taskdef (512/1024, image `${ecr}:${ImageTag}`, gunicorn `app:app`); env per Task 3.5; DB creds via `Secrets:` from the imported secret ARN; service 1 task in private subnets, `DependsOn` the listener.
- [ ] Write; validate; (deployed by Stack E); export ALB DNS + `scudo-poc-console-app-endpoint`; commit.

### Task 3.3: Stack D — `infra/scudo-poc-console-frontend.yaml` (S3 + CloudFront)
**Resources:** private S3 (OAC) + CloudFront; two origins — S3 (SPA default, cache-optimised) and the ALB (`/api/*` only, caching disabled, all-viewer). **SPA 403/404 → `/index.html` only for non-`/api` paths** via a CloudFront Function (do NOT apply distribution-wide — it corrupts API error bodies). Drop any `/mcp/*` behaviour (Flask-only v1). `PriceClass_100`.
- [ ] Write; validate; export bucket, distribution id, domain; commit.

### Task 3.4: Stack E — `infra/scudo-poc-console-build.yaml` (VPC CodeBuild)
**Resources:** VPC-attached CodeBuild (privileged for docker build; SG reaching MySQL:3306 + ECR via NAT; **install the `mysql` client in the install phase**), broad sandbox IAM scoped to the services used (`cloudformation`, `ecr`, `ecs:*`/`ecs:UpdateService`, `elasticloadbalancing`, `rds`, `secretsmanager:GetSecretValue`, `s3`, `cloudfront:CreateInvalidation`, `iam:PassRole` (scoped to the two console roles), `logs`, `ec2` for ENIs). **Buildspec:** clone branch → `docker build -f backend/Dockerfile` → push `:${sha}`+`:latest` → `cloudformation deploy` Stack C then D → **guarded one-time schema init in-VPC** (`CREATE DATABASE IF NOT EXISTS metadata; CREATE DATABASE IF NOT EXISTS ingestion;` then run `init_db.sql` **only if a `schema_migrations` sentinel row is absent AND `tp_provider`+`tp_dataset`+`tp_dataset_col` are all empty** — `init_db.sql` DROPs `tp_*` unconditionally, so the guard is load-bearing; write the sentinel after a successful init — see C-6) → `cd frontend && npm ci && npm run build` → `aws s3 sync dist/` → `cloudfront create-invalidation` → `aws ecs update-service --force-new-deployment`.
- [ ] Write; validate; deploy the build *project*; commit.

### Task 3.5: Flask task-def env (record verbatim in Stack C)
```
AWS_REGION=us-east-1
my_sql_host=<ImportValue scudo-poc-console-mysql-endpoint>
my_sql_port=3306
my_sql_user / my_sql_password   # via Secrets: from scudo-poc-console-mysql-secret-arn
STORE_BACKEND=falkordb
FALKORDB_URL=falkordb://falkordb.scudo.local:6379
GRAPH_NAME=scudo_mapping
FRAME_SOURCE=mock
CONFIDENCE_FLOOR=0.80
BORDERLINE_HALF_WIDTH=0.05
SCUDO_AGENT_BACKEND=scripted
SCUDO_AUTH_ALLOW_DEV=1
SCUDO_AUTH_DEV_PRINCIPAL=2096955@cognizant.com
```
(`db.py` already reads `my_sql_host`/`my_sql_port` from env — verified; `/healthz` exists.)

---

## PHASE 4 — Deploy + UI/UX verification

### Task 4.1: Deploy sequence (CloudShell)
- [ ] Push branch. In CloudShell: deploy Stack B (foundation), then Stack E (build project). Run `aws codebuild start-build --project-name scudo-poc-console-build`. One run builds the image, deploys C (ALB+ECS) + D (CloudFront), runs the guarded schema init, ships the SPA, forces ECS redeploy.
- [ ] Confirm ECS service 1/1 healthy. Smoke via `https://<cloudfront-domain>/api/mapping/vendors` → 200 (CloudFront only forwards `/api/*` to the ALB; `/healthz` at the CF root would 404 unless a `/healthz` behaviour is added — see C-7). The ALB target-group health check still uses `/healthz` directly.

### Task 4.2: Playwright UI/UX pass (against the CloudFront URL)
- [ ] **Honesty check FIRST:** the synthetic banner is visible (if path B); no fabricated "Marketing"/`urn:cdao:` value appears unlabelled.
- [ ] Macro pipeline view reads left-to-right matching the diagram; three band colours present.
- [ ] Micro drill-down: candidates + scores + band render for a product.
- [ ] Every visible button produces a visible effect; zero console errors; `/api/*` returns 200.
- [ ] Deep-link hard refresh of a non-`/api` route returns 200 (SPA fallback); an `/api/*` 404 returns JSON, not `index.html`.
- [ ] Report results + remaining gaps to the owner. Do NOT call it "production ready."

---

## Risks / Notes
- **Reputational (highest):** fabricated CDAO taxonomy. Phase 0 is the gate; do not deploy past it without owner sign-off on the data path.
- **FalkorDB is ephemeral** (no EFS) — confirmed HITL precedents are lost on task restart.
- **Offline matcher returns low/`out_of_scope` confidence** without Bedrock embeddings; real bands appear only against AWS FalkorDB+Titan. Label scores accordingly in the UI.
- **Cost ~$95–120/mo** if left on 24/7 (ALB + Fargate + existing NAT). Tear down C/D between demos.
- **Schema-init re-run wipes `tp_*`** — the guard is load-bearing.
- **db.py env name must be exactly `my_sql_host`** or MySQL pages 500 while matcher/catalogue still work (confusing half-up state).

## Codex review gate
✅ **DONE (2026-06-24, thread 019ef87c).** Codex reviewed this plan against the repo; all findings were hand-verified and incorporated as the binding **🔬 CODEX REVIEW** section near the top (C-1…C-8). Those corrections OVERRIDE any stale inline detail. A second Codex pass is recommended after the executing agent resolves the Task 1.0 (front-end target) and Task 0.0 (band) decisions, since those reshape Phase 1. Bash authorization for AWS provisioning remains the user's (CloudShell); local commands run via session permission prompts.
