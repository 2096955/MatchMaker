# Client Demo — sterile front end over the full stack

**Date:** 2026-08-07
**Status:** Design approved, ready for implementation planning
**Source repo (read-only):** `/Users/anthonylui/MatchMaker/MatchMaker` @ `f345fc6`
**Fork target:** `client-demo`

## Goal

A standalone, forked project that presents the existing matching system as a
neutral, reusable product. The front end carries no SCUDO branding. The backend
is copied whole and unmodified — it runs inside the container, where the user
never sees it.

This is a reusable asset on the Cognizant side, not a one-off demo build.

## Scope decisions

These were settled during brainstorming and are binding on the implementation.

| Decision | Value |
|---|---|
| Purpose | Neutral demo / reusable product shell |
| Base | Both the console (`frontend/`) and the viz dashboard (`dashboard-dist/`) |
| Sterility depth | Chrome, visible text, and demo data. Backend payloads unchanged |
| Identity | Generic product name, no company name on screen |
| Backend | Forked into the container, **copied verbatim** |
| Backend capability | **Full system** — Postgres, FalkorDB, Neptune, Bedrock all live |
| Sample vendor names | **Kept** — LSEG, S&P, Bloomberg etc. stay as-is |
| SCUDO handling | Removed, or masked to "HITL", everywhere user-facing |
| `JPMC-LOCAL` comments | **Left alone** — invisible to users |
| Container | Ships as its own image, separate from SCUDO's |
| Relationship to MatchMaker | Read-only source. The fork never writes back |

### Explicitly out of scope

- Renaming `SCUDO_*` environment variables.
- Renaming the `scudo/` and `scudo_mapping_mcp/` packages.
- Any change to backend logic, tests, or fixtures.
- Any modification to the MatchMaker repo.

Identifiers remain `SCUDO_*` internally. The fork is screen-clean, not
identifier-clean. `docker inspect` and the source tree still show the original
names; that is accepted.

## Verified findings

Measured against `f345fc6`, not assumed. These drive the design.

1. **The console is already ~90% neutral.** `frontend/index.html:5` titles it
   "Data Ingestion Framework". Only four user-visible SCUDO strings exist, all
   in `frontend/src/pages/catalogue/CatalogueDetail.jsx:240,276`. The footer at
   `frontend/src/components/Layout.jsx:56` reads "© 2026 Cognizant".

2. **The console forks cleanly.** Four runtime dependencies (react, react-dom,
   react-router-dom, axios), no workspace links.

3. **The viz dashboard does NOT fork cleanly.** Its source is
   `@understand-anything/dashboard` inside a pnpm workspace depending on
   `@understand-anything/core: workspace:*`, which pulls in the analyzer and
   tree-sitter grammars. Forking the source means adopting a monorepo. The
   *built* bundle in `dashboard-dist/` is 3 `SCUDO` + 5 `cognizant` +
   6 `Understand Anything` strings, plus the fixture JSON, away from neutral.

4. **`project.name` is a functional switch, not just a label.** In the built
   bundle, `"SCUDO Matching"` appears in two live positions: a `document.title`
   fallback, and a gate `project.name.includes("SCUDO Matching") &&
   setMode("synthetic")`. Two further gates seen in dashboard source
   (`GraphView.tsx:423,445`) compiled out because `vite.config.matching.ts` pins
   `VITE_MATCHING_MODE=true`. **Renaming the fixture's `project.name` without
   patching the surviving call site silently changes the graph's mode.**

5. **The front end deliberately renders backend error text.**
   `frontend/src/api/index.js:20-30` installs an interceptor folding `detail`
   into `error` so users see the actionable sentence; pages render it
   (`MatchingTest.jsx:159`). One such sentence contains a SCUDO env var:
   `"no ingested frame for LSEG/X1; ingest it first, or set
   SCUDO_MV_ALLOW_INLINE_FRAME to score caller-supplied text"`. Roughly eleven
   more exist (`scudo_mapping_mcp/config.py:253,268,292,299`,
   `hydrate.py:110,116`, `ingest.py:266`, `opus_dense.py:106,130`,
   `specialist.py:211`). Most are config-validation errors a demo will not
   reach; the frame refusal is reachable in normal use.

6. **All four store backends stay wired.**
   `backend/scudo_mapping_mcp/store/factory.py:32-52` constructs falkordb,
   neptune, memory, or local_file by lazy import. The fork keeps all four and
   installs the full `requirements.txt`.

7. **Catalogue fixtures are already clean.** `cdao_catalogue.json` contains one
   `CDAO` string; `conceptual_layer.json` has zero brand hits.
   `backend/init_db.sql` seeds only roles and users — no vendor names.

8. **The backend 401s every `/api/*` call** unless the dev-auth environment is
   set before `app.py` is imported. This is the documented cause of "only one
   page opens" (`start_local.py:1-30`). The fork's container must set its
   environment correctly at startup or it will look broken.

9. **`docker ps`-visible names are branded.** `docker-compose.yml` defines
   `container_name: scudo-postgres`, volume `scudo_pgdata`, user `scudo`,
   database `scudo_console`.

## Architecture

```
client-demo/
  console/            fork of frontend/         — React 18, sterile
  backend/            fork of backend/          — verbatim, unmodified
  viz/                sterilised artifact       — generated, not forked
  brand/brand.json    single source of visible names
  scripts/sterilize-viz.mjs
  Dockerfile
  docker-compose.yml
  README.md           records source SHA f345fc6 for future re-sync
```

Three components, three treatments, because they have three different
couplings:

| Part | Treatment | Rationale |
|---|---|---|
| console | source fork | four deps, no workspace links — copies clean |
| backend | source fork, verbatim | self-contained Python; user never sees it |
| viz | artifact sterilisation | source is workspace-coupled (finding 3) |

### Data flow

```
MatchMaker (read-only)
    ├── frontend/            ──copy──▶ console/   ──┐
    ├── backend/             ──copy──▶ backend/   ──┼──▶ single container image
    ├── dashboard-dist/      ──┐                    │
    └── fixtures/                                   │
          matching-graph.json ─┴─▶ sterilize-viz ──▶ viz/ ──┘
                                        ▲
                                  brand/brand.json
```

At runtime the container serves console, viz, and the API. Credentials for
Bedrock, Neptune, FalkorDB, and Postgres are injected at runtime via task role,
environment, or mounted secret — never baked into image layers, never displayed.

## Components

### 1. `brand/brand.json`

One file holding every visible name:

```json
{
  "productName": "...",
  "tagline": "...",
  "mark": "...",
  "footer": "...",
  "accentPresetName": "...",
  "graphProjectName": "...",
  "layerNames": { }
}
```

The exact strings are an implementation-time choice; the constraint is that no
company name and no SCUDO reference appears in any value.

### 2. Console (forked source)

- `Layout.jsx` and `index.html` read from brand config instead of hard-coding.
  Removes the Cognizant footer and the "Data Ingestion Framework" chrome.
- The four visible SCUDO strings in `CatalogueDetail.jsx:240,276` become neutral
  or HITL-framed — e.g. "Semantic Matcher Console", "Running Matcher Agent…".
- Sample vendor names (LSEG, S&P, Bloomberg) are left in place.
- `JPMC-LOCAL` comments are left in place.

### 3. Error sanitiser (new, in the fork's `api/index.js`)

Extends the existing response interceptor. Before display, it removes SCUDO
references from `detail` text.

**Masking rule.** Prose mentions of "SCUDO" become "HITL". Identifiers —
`SCUDO_MV_ALLOW_INLINE_FRAME` and similar — are **removed, not renamed**:
masking an env var name to `HITL_MV_ALLOW_INLINE_FRAME` would instruct the
viewer to set a variable that does not exist. The actionable half of the
sentence is preserved:

```
before: no ingested frame for LSEG/X1; ingest it first, or set
        SCUDO_MV_ALLOW_INLINE_FRAME to score caller-supplied text
after:  no ingested frame for LSEG/X1; ingest it first
```

Sanitising in the interceptor rather than in backend source keeps the backend
byte-identical to upstream, so re-syncs stay a readable diff. It also fails
safe: it catches strings not enumerated in finding 5.

### 4. `scripts/sterilize-viz.mjs`

Reads `dashboard-dist/` and `backend/scudo/fixtures/matching-graph.json` from
MatchMaker, applies `brand.json`, writes `viz/`. Re-runnable when the dashboard
is rebuilt upstream.

Responsibilities:

- Rewrite the bundle's `document.title` fallback.
- Rewrite fixture `project.name`, `project.description`, and layer names.
- **Patch the surviving `project.name.includes("SCUDO Matching")` gate in the
  same pass as the fixture rename** (finding 4). These two edits are coupled;
  doing either alone breaks the graph's mode.
- Replace the `cognizant-blue` / `light-cognizant` theme preset names with
  colour-derived names. Hex values are unchanged.
- Replace `favicon-scudo.svg`.
- Neutralise the six `Understand Anything` strings.

**Error handling: fail loudly.** If an expected brand string is not found, the
script exits non-zero and names the missing pattern. A silent no-match is how a
bundle ships still saying SCUDO after an upstream rebuild changes the minified
shape.

### 5. Container

- Own image name; no shared tags or ECR repository with SCUDO, so a sterile
  build can never land on a SCUDO task definition.
- Full `requirements.txt` — falkordb, requests-aws4auth, boto3, strands
  included. All four store backends selectable via `STORE_BACKEND`.
- Real Postgres, not the SQLite fallback.
- Startup sets the auth environment before `app.py` is imported (finding 8).
- `docker ps`-visible names renamed: `scudo-postgres`, `scudo_pgdata`,
  user `scudo`, database `scudo_console` (finding 9).
- `SCUDO_*` environment variables keep their names.

## Testing

Three gates. String-absence alone is insufficient — it would have missed the
`project.name` mode coupling in finding 4.

1. **Grep gate.** Zero occurrences of `SCUDO`, `Scudo`, or `scudo` in `viz/`
   output and the console build output.
2. **Functional gate.** The sterile viz loads, the story tour runs, and all
   eight layers render. This is what catches a broken `project.name` gate.
3. **Console gate.** Pages work against the real backend, and the
   frame-not-found path renders sanitised text with no env-var name.

The transform script's fail-loud behaviour is itself tested: feed it a bundle
missing an expected pattern and assert a non-zero exit.

## Risks and accepted trade-offs

- **Fork divergence.** From the copy onward, backend fixes in MatchMaker do not
  propagate to `client-demo`. Two codebases. The README records source SHA
  `f345fc6` to give a future re-sync a diff base. Accepted deliberately.
- **Identifier visibility.** `SCUDO_*` env vars and `scudo/` package names
  remain visible to anyone with container or repository access. Accepted; the
  full rename was considered and declined as a separate future project.
- **Upstream dashboard rebuilds** change minified variable names, so
  `sterilize-viz.mjs` patterns may need updating. Mitigated by fail-loud
  behaviour rather than silent no-match.
- **Auth posture.** If the container is run with dev-auth enabled, it trusts a
  forged principal header. Correct for a self-contained demo, wrong for
  internet-facing deployment. The README must say so.

## Resolved during design

- **One port.** `backend/app.py:95-101,129-139` already serves both SPAs from a
  single Flask process — the viz at `/demo/` and the console at `/app/` — gated
  by `SCUDO_SERVE_DASHBOARD_DIST` and `SCUDO_SERVE_FRONTEND_DIST`. The fork sets
  both and needs no new static-serving plumbing.
- **Fork location:** `/Users/anthonylui/client-demo`.

## Open items for the implementation plan

- Final product name, tagline, and mark for `brand.json`. A neutral default
  ships; changing it is a one-file edit by design.
