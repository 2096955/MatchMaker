# SCUDO OKF Knowledge Bundle — Design Spec

**Date:** 2026-06-27
**Status:** Draft for review
**Author:** Claude (ARB lead, ultracode session)
**Repo:** `MatchMaker/MatchMaker` (separate from the OKF toolkit repo at `/Users/anthonylui/OpenKnowledgeFormat`)

---

## 1. Problem & motivation

SCUDO/MatchMaker's domain knowledge is scattered across ~45 markdown files spread
through the repo — `backend/scudo/`, `backend/scudo_mapping_mcp/docs/`,
`docs/superpowers/`, `infra/`, and repo-root files (`README.md`, `AGENTS.md`,
`hooks.md`, `docs/ingestion_framework_spec.md`). These are handovers, specs, runbooks, agent
skills, gap analyses, and smoke results. There is no single navigable entry point,
no consistent typing, and — most damaging — **~14 of the docs are commit-pinned
point-in-time snapshots that read as authoritative current truth**. A new agent or
teammate has no way to know that `AWS_HANDOFF.md` (pinned to commit `e768284`,
2026-06-23) has been superseded by later live-deploy notes, or that the confidence
bands quoted in three diagrams disagree with the canonical source.

Open Knowledge Format (OKF) is purpose-built for exactly this: it converts a folder
of markdown into an agent-navigable **knowledge bundle** — frontmatter-typed
concepts, auto-generated `index.md`/`log.md`/`claude.md`, conformance validation,
reliability evals, and a link-graph `viz.html`. The OKF toolkit itself is complete
and verified working (the `okf` CLI is installed and importable from its venv; its
7 unit tests pass). What does **not** exist is an OKF bundle *for SCUDO*.

**This spec covers building that bundle.** It does not modify the OKF toolkit.

### Goal

Produce a single, navigable, index-first OKF bundle at `docs/okf/scudo/` that
consolidates the SCUDO/MatchMaker docs into a clean 7-folder taxonomy, with curated
types and one-line descriptions, explicit staleness/supersession metadata, fixed
cross-links, and passing conformance + automated evals — built reproducibly via the
real `okf` CLI and verified by independent agents and Codex.

### Non-goals

- **Not** modifying or extending the OKF toolkit (it is done).
- **Not** moving, deleting, or rewriting the original source docs — the bundle is
  *copies*. Originals stay where deploy configs, runbooks, and imports expect them.
- **Not** wiring auto-refresh/CI integration (explicitly deferred; a later phase).
- **Not** changing any deploy, build, or runtime behavior.

---

## 2. Deploy-safety analysis (verified)

A primary constraint is that the bundle must not make AWS deployment harder. This
was verified against the actual deploy configuration, not assumed:

| Layer | What it packages | Effect of `docs/okf/scudo/` |
|-------|------------------|------------------------------|
| **Backend image** (`backend/Dockerfile:44`) | `COPY backend/ ./backend/` only | Excluded — bundle is under `docs/`, not `backend/` |
| **`.dockerignore`** | excludes `docs/`, `*.md`, `*.SKILL.md` (with `!backend/README.md`) | Excluded **twice** — by path and by `*.md` glob |
| **ECS-dev CodeBuild** (`infra/buildspec.yml`) | builds `backend/Dockerfile`; sole artifact `imagedefinitions.json` | No repo-wholesale packaging; bundle invisible to it |
| **Live console CodeBuild** (`infra/scudo-poc-build.yaml:144`) | `Source: GITHUB, GitCloneDepth: 1`; builds `backend/Dockerfile`; syncs `dashboard-dist/` to S3; artifact `build-output.json` | Repo IS shallow-cloned, so the markdown lands in the checkout — but it is excluded from the image (Dockerfile) and from the S3 sync (`dashboard-dist/` only) |
| **Frontend deploy** | `aws s3 sync dashboard-dist/ s3://<bucket>/demo/` | Scoped to `dashboard-dist/`; never touches `docs/` |

**Conclusion: no effect on any deploy *artifact* (image or S3 assets).** The live
console build (`scudo-poc-build.yaml`) does a depth-1 git clone of the whole repo, so
the bundle adds a *negligible* amount (≈37 small markdown files, a few KB) to the
source checkout — nothing reaches the runtime image or the served assets. Net:
**additive, negligible deploy footprint, zero artifact change.** To preserve this:

- Originals are **never moved** — no deploy doc, runbook reference, or import path
  breaks.
- The intermediate staging tree `build/okf-src/` is **gitignored** and is itself
  `.md`/excluded — pure build scratch, never committed or deployed.

---

## 3. Why a staging step (the key architectural decision)

`okf convert` was read in full (`src/okf_toolkit/convert/markdown.py`). Two facts
drive the design:

1. **It mirrors the source tree** — `rglob("*.md")` → writes each file to
   `output_root / <same relative path>`. It does **not** reorganize into topic
   folders. To get the 7-folder taxonomy, the *source* must already have that
   layout.
2. **It only fills *missing* frontmatter**, using weak path heuristics (anything
   not matching a keyword → `Document`) and the first heading/paragraph for
   title/description. It **preserves existing frontmatter** (including unknown keys
   like `staleness`, per OKF spec §4.1). So curated types/descriptions/staleness
   must be authored *before* convert runs.
3. **It rewrites relative `.md` links to bundle-absolute** — but only links that
   resolve *within the source root*. Cross-doc links between files that were
   originally in different directories will dangle unless repointed during staging.

Therefore the build is **stage → curate → convert → validate → eval → visualize**,
not a one-shot `okf convert`:

```
build/okf-src/            (gitignored: curated 7-folder tree + per-file frontmatter)
   │  authored by the build (copies of originals + frontmatter + repointed links)
   ▼
okf convert build/okf-src --out docs/okf/scudo
   ▼
docs/okf/scudo/           (bundle: concepts + index.md/log.md/claude.md, links rewritten)
   ▼
okf validate  →  okf evals run  →  okf visualize
```

---

## 4. Bundle taxonomy

**Location:** `docs/okf/scudo/`. **37 concepts** in **7 folders** (39 classified source
docs − 1 merge − 1 fold; see §4.3).

```
docs/okf/scudo/
├── index.md                 (generated; carries okf_version: "0.1")
├── log.md                   (generated)
├── claude.md                (generated per-folder agent instructions)
│
├── architecture/            Canonical current design of SCUDO
│     ├── overview.md            (from README.md — project overview)
│     ├── hooks.md               (deterministic enforcement layer)
│     ├── batch.md               (BATCH.md — self-verifying loop made durable)
│     ├── arb-review-pack.md     (type: Decision Record)
│     ├── diagrams-and-sources.md (FOLDED from the 2 mapping_mcp READMEs — canonical
│     │                            .mmd diagram set, what each covers, supersession authority)
│     ├── diagram-main-flow.md   (superseded_by diagrams-and-sources → scudo-overview.mmd)
│     └── diagram-falkor-internals.md (superseded_by diagrams-and-sources → scudo-retrieval.mmd)
│
├── reference/               Cite-this-over-the-diagram authoritative values
│     ├── matching-data-provenance.md  (CANONICAL: confidence bands, IRIs, provenance)
│     └── agents.md                    (AGENTS.md — standing agent operating rules)
│
├── skills/                  Runtime agent procedures (deduped — backend copy only)
│     ├── graphrag-retrieval.md
│     ├── neptune-io.md
│     ├── rdf-serialisation.md
│     ├── rights-odrl.md
│     └── taxonomy-mapping.md
│
├── specs/                   Design specs (binding / intended)
│     ├── self-improving-agent.md
│     ├── matching-frontend.md
│     ├── hitl-two-way-chat.md
│     ├── auth-gate-strip-inject.md
│     ├── i5-lift-preconditions.md
│     └── ingestion-framework.md   (ETL §1–11 ONLY; diagram appendices dropped)
│
├── plans/                   Task-by-task implementation plans (mostly executed)
│     ├── phase0-foundations.md
│     ├── matching-dashboard.md
│     ├── matching-frontend-and-deploy.md   (front-end target superseded; C-1..C-8 retained)
│     ├── dense-arm-sdk-adoption.md         (v0.3)
│     ├── dense-arm-swap.md                 (v0.2; superseded_by dense-arm-sdk-adoption)
│     └── precedent-hydrator-workstream.md
│
├── deployment/              Reusable deploy runbooks (procedure, not snapshot)
│     ├── deploy.md                    (backend/scudo/DEPLOY.md)
│     ├── deploy-runbook-scudo-poc.md
│     └── demo-runbook.md
│
└── handovers/               Point-in-time, commit-pinned — read for history ONLY
      ├── aws-handoff.md
      ├── hitl-bands-2026-06-26.md
      ├── dashboard-repo-push.md     (MERGED from DASHBOARD_REPO_PUSH_HANDOFF + handoff/README)
      ├── code-review-fixes.md       (type: Handover — was Gap Analysis)
      ├── architecture-gap-analysis.md (type: Handover — was Gap Analysis)
      ├── smoke-fixes-round1.md      (type: Handover — was Smoke Test)
      ├── smoke-upload-flow-live.md  (type: Handover — was Smoke Test)
      └── redeploy-branding.md
```

### 4.1 Type vocabulary (8 values)

Collapse the 13+ scattered classifier types into 8 consistent values:

`Architecture` · `Decision Record` · `Reference` · `Skill` · `Spec` · `Plan` ·
`Runbook` · `Handover`

- **Gap Analysis** and **Smoke Test** fold into **Handover** — all three are dated,
  point-in-time status artifacts with the same staleness behavior and archive
  destination.
- **Agent Instructions** folds into **Skill** for `skills/`, and **Reference** for
  `reference/agents.md` (location disambiguates the role).

### 4.2 Curation policy (merged into staging frontmatter)

**Merge, never blindly prepend.** Several source docs (notably the 5 `SKILL.md`
files) **already begin with a YAML frontmatter block**. OKF parses only the *first*
frontmatter block, so prepending a second `---` block would push the original
frontmatter into the body and corrupt the concept. The staging step must therefore
**parse any existing frontmatter and overlay the curated keys on top** (existing keys
preserved unless intentionally overridden); only docs with no frontmatter get a fresh
block. Every concept ends with:

```yaml
---
type: <one of the 8>
title: <human-readable>
description: <one sentence — enough to decide whether to open it>
tags: [<topic>, ...]
timestamp: <ISO 8601>
staleness: current | historical | superseded
supersedes: /path/to/older.md        # when applicable
superseded_by: /path/to/newer.md     # when applicable
---
```

The synthesis flagged `staleness` + supersession links as **more important than
folder placement** — the bundle's biggest risk is that commit-pinned snapshots read
as authoritative. Descriptions are curated (the workflow already produced a
one-sentence description per doc); they are not left to the converter's weak
heuristic.

### 4.3 Dedup / merge / split decisions (verified)

- **Dedup:** the 5 root `*.SKILL.md` files were verified **byte-identical**
  (`diff -q`) to `backend/scudo/skills/*/SKILL.md`. Include each skill **once**
  (backend copy is canonical).
- **Merge:** `infra/DASHBOARD_REPO_PUSH_HANDOFF.md` and `infra/handoff/README.md`
  both cover pushing the same 7 dashboard commits to Egonex-AI/Understand-Anything
  via the git-bundle workaround. **Merge into one** `handovers/dashboard-repo-push.md`
  (keep the apply-bundle steps from `handoff/README.md`; fold in the build-ordering
  note from the other).
- **Split:** `docs/ingestion_framework_spec.md` is two documents stitched together —
  a standalone format-agnostic ETL spec (§1–11) plus SCUDO mapping-pipeline diagrams
  duplicated from the matching docs (Appendices A/B/C). Keep the **ETL spec §1–11**
  as `specs/ingestion-framework.md`; **drop the appendices** and replace with a link
  to `architecture/`.
- **Fold:** `backend/scudo_mapping_mcp/docs/README.md` and
  `backend/scudo_mapping_mcp/docs/architecture/README.md` are both directory-index
  documents — their navigation role is replaced by the bundle's generated `index.md`.
  But each carries unique, load-bearing prose that must be preserved: the
  `architecture/README.md` is the **supersession authority** (`diagram-1 → scudo-overview.mmd`,
  `diagram-2 → scudo-retrieval.mmd`) and the "if a diagram disagrees with prose, the
  diagram wins" rule + the `.mmd` source-of-truth location; the `docs/README.md` has
  the "quick orientation for a new reader" system summary and the I5 reviewer-queue
  framing. **Fold both into one new concept** `architecture/diagrams-and-sources.md`
  (type: Architecture) that names the canonical `.mmd` diagram set (which cannot live
  in an `.md` bundle), what each covers, the supersession mapping, and the orientation
  summary. This is the authoritative source for cross-link #3.

### 4.4 Priority cross-links to wire (8)

These are repointed during staging so they resolve within the bundle:

1. **`reference/matching-data-provenance.md` ← every band-citing doc** — canonical
   PASS≥0.85 / BORDERLINE 0.75–0.85 / FAIL<0.75 (from FLOOR 0.80 ±0.05). Wire from
   `architecture/overview.md`, `architecture/hooks.md`, `reference/agents.md`,
   `specs/i5-lift-preconditions.md`, both diagram docs, `architecture/arb-review-pack.md`,
   `specs/ingestion-framework.md`, `skills/taxonomy-mapping.md`. **Resolves the single
   most pervasive contradiction in the bundle.**
2. **`reference/agents.md` ↔ `specs/matching-frontend.md`** — the "ship ONE UI
   (dashboard, not `frontend/`)" decision (AGENTS = standing rule, spec = binding source).
3. **`architecture/diagram-main-flow.md` and `diagram-falkor-internals.md` →
   `architecture/diagrams-and-sources.md`** via `superseded_by` (the folded concept is
   the in-bundle supersession authority and names the canonical `.mmd` set those two
   were replaced by).
4. **`plans/dense-arm-sdk-adoption.md` (v0.3) → `plans/dense-arm-swap.md` (v0.2)** —
   supersession + carry-forward of the open critical-review findings.
5. **`specs/i5-lift-preconditions.md` → `reference/matching-data-provenance.md` +
   the HITL-bands handover** — gate definition needs band semantics + the shipped HITL UI.
6. **`specs/auth-gate-strip-inject.md` ↔ `handovers/code-review-fixes.md` (B1/B2) ↔
   deployment runbooks** — the open dev-auth security gap (accepted-risk @ `896b6eb`)
   threads through all three; wire so the action item stays traceable.
7. **`handovers/hitl-bands-2026-06-26.md` → `deployment/deploy-runbook-scudo-poc.md`**
   — surface the handover's correction that the live CodeBuild project is
   `scudo-poc-console-build`, not the runbook's stale `scudo-poc-build`.
8. **Skill chain:** `taxonomy-mapping → graphrag-retrieval (discovery) → neptune-io
   (authoritative confirmation) → rdf-serialisation (publish)`.

---

## 5. Build pipeline

A committed, reproducible script `docs/okf/build_bundle.sh` (so the bundle is
rebuildable, not a one-off artifact):

0. **Resolve `okf` binary** → `OKF_BIN="${OKF_BIN:-/Users/anthonylui/OpenKnowledgeFormat/.venv/bin/okf}"`;
   assert it exists and `"$OKF_BIN" --help` works (fail loudly with install guidance
   otherwise). The OKF toolkit lives in a **separate repo/venv**, so the path is
   overridable and version-checked, never hard-assumed.
1. **Stage** → populate `build/okf-src/` (gitignored): produce the 37 concepts in the
   7-folder layout (copies of the 39 source docs with the merge + fold applied);
   **merge** curated frontmatter (§4.2 — parse-and-overlay, never double-block); apply
   merge / split / fold (§4.3).
2. **Repoint cross-links** during staging so the 8 priority links (§4.4) resolve
   within the staged tree (convert only rewrites links resolving inside the source root).
   Links to non-concept targets (code, `.yaml`, `.mmd`) are rewritten to repo-relative
   or external form so they are not flagged as broken in-bundle links.
3. **Clean + Convert** → remove any stale `docs/okf/scudo/` first (convert does **not**
   clean its output dir — `convert_markdown_to_okf` only writes/overwrites the files it
   emits, leaving orphaned concepts behind to be re-indexed). Convert into a temp dir
   then swap, or `rm -rf docs/okf/scudo` immediately before:
   `"$OKF_BIN" convert build/okf-src --out docs/okf/scudo --default-type Document`.
   Generates `index.md`/`log.md`/`claude.md`, fills any frontmatter gaps, rewrites
   links to bundle-absolute.
4. **Validate** → `"$OKF_BIN" validate docs/okf/scudo` — **must pass (zero errors)**.
   This is true OKF v0.1 conformance (parseable frontmatter + non-empty `type` +
   structural `index.md`/`log.md` rules). **`--strict` is NOT the gate** — it promotes
   every *warning* (incl. broken internal links, which OKF SPEC §9 says consumers MUST
   tolerate) to a failure (`cli.py:52`). Instead, run `okf validate --strict` as an
   **advisory report** whose warnings feed the link-integrity loop (§6.3); drive them
   toward zero but do not hard-gate on them.
5. **Evals** → `okf evals run docs/okf/scudo` — the **4 automated evals must pass**
   (the command exits 0): **01** conformance, **02** index coverage, **06** claude.md
   coverage, **07** log presence (verified against `evals/runner.py:124-134` —
   `_run_automated` implements checks for exactly these four IDs). The remaining evals
   (03 frontmatter, 04 link sanity, 05 progressive disclosure, 08 RAG persistence) are
   **agent-graded** — spot-checked, not gated. (All four automated checks pass under
   the default `okf convert`, which writes `index.md`/`claude.md`/`log.md`.)
6. **Visualize** → `okf visualize docs/okf/scudo` → `viz.html`.

A short `docs/okf/README.md` documents how to rebuild (the OKF toolkit path + venv +
`build_bundle.sh`).

---

## 6. Verification discipline (loops, fan-out, Codex)

Per the operating discipline (independent verification, not self-critique), the
build is gated by separate checkers at three points. **Maker and verifier are never
the same context.**

### 6.1 Spec gate (this document)
- Self-review pass (placeholder / consistency / scope / ambiguity) — **applied**;
  caught and fixed two errors (eval count 5→4; concept count + two unplaced READMEs).
- **Codex review** of this spec — **done** (verdict APPROVE-WITH-FIXES). Codex
  independently confirmed deploy packaging, converter behavior, the 4-automated-eval
  count, and the 37-concept enumeration, and surfaced the load-bearing `--strict`
  validate error (now fixed in §5/§10), the blast-radius overclaim (now §2), and the
  prepend-vs-merge / clean-output / `OKF_BIN` hardening items (now §4.2/§5). All
  findings were hand-verified against source before acceptance.

### 6.2 Content-accuracy fan-out (after staging, before convert)
A **parallel agent fan-out** (one verifier per staged concept) checks that each
concept's curated `type` / `description` / `staleness` **actually matches the doc's
content**, and that supersession links point the right direction. This is a
**self-verifying-loop**: any concept that fails is **requeued** for frontmatter
correction, and the fan-out re-runs on the corrected set, **looping until clean**
(zero failures) or two consecutive dry rounds.

### 6.3 Link-integrity loop (after convert)
Run `okf validate --strict` (advisory) to enumerate broken-internal-link warnings. A
checker confirms the 8 priority cross-links (§4.4) resolve to existing bundle files and
that no *intended* in-bundle link dangles. Broken links are tolerated by OKF (SPEC §9),
so out-of-bundle targets (code, `.yaml`, `.mmd`) are repointed to repo-relative/external
form rather than treated as failures — but the 8 priority links are load-bearing.
Failures are fixed in staging and convert re-run, **looping until the priority set is
green and `--strict` warnings are minimized** (zero residual *in-bundle* dangling links).

### 6.4 Codex review gate (before commit)
**Codex reviews the generated bundle** — taxonomy coherence, frontmatter quality,
the merge/split correctness, and the staleness/supersession metadata — and the
**README diff**. Codex findings are triaged: load-bearing findings are fixed and
re-verified before commit. Per discipline: **any Codex refutation that would reverse
a load-bearing decision is hand-checked** before acting (verifiers can wrongly reject
true findings on technicalities).

### 6.5 Approvals (human gates)
- **Gate A — Spec approval:** user approves this spec (after Codex spec review).
- **Gate B — Pre-commit approval:** user approves the built bundle + README diff
  (after the Codex bundle review) before anything is committed.

---

## 7. README update (additive)

Two minimal, additive edits to the root `README.md`, matching its existing style
(the README is already the human index over the scattered docs):

- **`## Repo layout`** — add a `docs/okf/scudo/` entry: "navigable OKF knowledge
  bundle — start at `index.md`."
- **`## Architecture source of truth`** — add a line: the OKF bundle is the
  index-first mirror of the scattered docs; navigate via `index.md`, not grep.

No other README sections change. No content is removed.

---

## 8. Deliverables

1. `docs/okf/scudo/` — the OKF bundle (37 concepts + generated index/log/claude.md +
   `viz.html`), conformant and passing the 4 automated evals.
2. `docs/okf/build_bundle.sh` — reproducible build script.
3. `docs/okf/README.md` — how to rebuild.
4. Root `README.md` — two additive pointers (§7).
5. `.gitignore` — `build/okf-src/` entry.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Curated description/type drifts from doc content | Content-accuracy fan-out (§6.2), looped until clean |
| Cross-links dangle after reorg | Repoint during staging + link-integrity loop (§6.3) |
| Bundle bloats deploy artifact | Verified excluded by Dockerfile COPY + `.dockerignore` + scoped S3 sync (§2) |
| Original docs go stale vs bundle copy | Out of scope (auto-refresh deferred); `docs/okf/README.md` documents manual rebuild; bundle `log.md` records build provenance |
| Merge/split loses information | Codex bundle review (§6.4) explicitly checks merge/split correctness against originals |
| Staleness metadata wrong | Both the fan-out (§6.2) and Codex (§6.4) verify supersession direction |
| Double frontmatter block corrupts skill concepts (SKILL.md already has frontmatter) | Staging **merges** frontmatter (parse-and-overlay), never prepends (§4.2) |
| `okf convert` leaves stale concepts in output dir | Clean output (temp-swap or `rm -rf`) before convert (§5 step 3) |
| OKF CLI in separate venv missing/moved on rebuild | `OKF_BIN` override + existence/version assert in build script (§5 step 0) |
| `--strict` validate fails on tolerated broken links | Gate on plain `okf validate` (errors only); `--strict` is advisory (§5 step 4, §6.3) |

---

## 10. Success criteria

- `okf validate docs/okf/scudo` exits 0 (zero **errors** = OKF v0.1 conformance).
- `okf validate docs/okf/scudo --strict` warnings are reviewed and minimized (the 8
  priority links resolve; residual broken-link warnings to out-of-bundle targets are
  acceptable per OKF SPEC §9) — advisory, not a hard gate.
- `okf evals run docs/okf/scudo` — 4 automated evals (01, 02, 06, 07) PASS.
- All 8 priority cross-links resolve to existing bundle files.
- Content-accuracy fan-out reaches a clean round (zero type/description/staleness mismatches).
- Codex bundle review returns no unaddressed load-bearing findings.
- Deploy paths unchanged (no edits to Dockerfile, `.dockerignore`, buildspec, infra YAML).
- User approves at Gate A (spec) and Gate B (pre-commit).
