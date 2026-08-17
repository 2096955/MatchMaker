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
- **Frames are authoritative; inline text is gated** (2026-08-06). `_frame` /
  `_resolve_frame` exist in THREE files that must stay in agreement —
  `routes/mapping.py`, `scudo_mapping_mcp/match_verify_mcp.py`,
  `scudo_mapping_mcp/mcp_server.py`. All three ignore caller-supplied
  `name`/`description` unless `SCUDO_MV_ALLOW_INLINE_FRAME` is set, and refuse
  (404 / `frame_not_found` / `FrameRefusal`) instead of fabricating
  `name=product_id`. If you fix one, fix all three — that duplication has bitten
  twice.
- **Publish gate is deterministic, not advisory** (2026-08-06).
  `_pre_verify_defects` output is only pasted into the verifier LLM's *prompt*
  and enforces NOTHING. Two checks were promoted to hard `PublishGateError`
  raises in `_gate_and_decide`: the `vendor_product_iri` echo, and
  `proposed_target_iri` candidate membership (fail-closed on an empty candidate
  list). Both were proven to publish bad data before promotion. **Adding a check
  to `_pre_verify_defects` does not enforce it** — put it in `_gate_and_decide`.
- **All vendor-derived keys lowercase the vendor**: `models.mds_iri`,
  `verdict.input_hash`, and `store/base.vendor_signature`. They must fork or
  converge together — `vendor_signature` alone used not to, so `'LSEG'` and
  `'lseg'` shared an IRI but split rank signals silently. Gate:
  `tests/test_vendor_signature_casing.py`.

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

- **Local run (START HERE — no Docker, no Postgres, no FalkorDB, no Neptune, no Bedrock):**
  ```bash
  PORT=5055 VITE_API_PROXY=http://localhost:5055 python start_local.py
  ```
  Open the **UI on :3000**, not the backend port. `start_local.py` sets the
  environment *before* importing `app.py` — that ordering is the whole point:
  `start_all.sh` runs `python3 app.py` directly, so the auth gate 401s every
  `/api/*` call and only the shell renders ("only one page opens"). macOS
  AirPlay squats on :5000, hence the `PORT` override.

  Providers / Datasets / Admin / Ingestion are the only DB-backed pages. They
  now work with **no database installed** via `CONSOLE_DB_BACKEND=sqlite`
  (already set in `start_local.py`) → `backend/db_sqlite_fallback.py`, a
  file-backed SQLite stand-in at `backend/.local/console.sqlite3`. Unset the var
  and the psycopg/PostgreSQL path is unchanged. Full detail + known limits:
  `JPMC_LOCAL_RUN_HANDOVER.md`.

  Verified state (measured, not inferred): MySQL is **already gone** (zero
  imports); FalkorDB and Neptune are **lazy-imported and unused** under
  `STORE_BACKEND=local_file`; all `boto3` imports are lazy so Bedrock is
  additive.

  **The score is NOT deterministic on the shipped path.** `config.py:306`
  defaults `SCUDO_DENSE_BACKEND` to `jaro_winkler`, but both launchers
  **`setdefault` it to `opus`** — `streamlit_app.py:88`
  `os.environ.setdefault("SCUDO_DENSE_BACKEND", "opus")` and
  `run_cognizant.py:150` `"SCUDO_DENSE_BACKEND": "opus"` applied through the
  `os.environ.setdefault(_k, _v)` loop at `run_cognizant.py:158`. `setdefault`
  is **not** an override: an explicit `SCUDO_DENSE_BACKEND=jaro_winkler` already
  in the environment SURVIVES both launchers. What is true is that the *default*
  shipped path — nobody sets anything — runs the LLM dense arm.

  On that default path the Opus float IS the published
  `Candidate.similarity`/confidence (`opus_dense.py:17`), and `opus_dense.py:45-48`
  states INVARIANT II: *"Identical inputs return the same Jaro-Winkler fallback
  score (the legacy stand-in is fully deterministic). Opus is NOT deterministic —
  callers expecting reproducible scores must set `SCUDO_DENSE_BACKEND=jaro_winkler`."*
  The LLM does not merely narrate; it can change the confidence, the band and
  the selected target. That conclusion stands.

  Both launchers also `setdefault` **`SCUDO_DENSE_FALLBACK=1`**
  (`streamlit_app.py:92`, `run_cognizant.py:154`), which is required to
  accompany the opus arm — without it a Bedrock failure raises
  `DenseScoringUnavailableError`. With it, the batch path is **all-or-nothing**:
  a single failed model call discards the whole model batch and re-scores every
  nominee in that match with Jaro-Winkler, logging a WARNING but never mixing
  the two scales in one ranking
  (`store/retrieval_scoring.py` `score_candidates()`, and the parallel
  guarantee in `opus_dense.py` `make_opus_dense_scorer()` for the
  `SCUDO_USE_OPUS_DENSE` route). Consequence for reading a run: a run configured
  for Opus **can silently publish Jaro-Winkler scores instead**, so "the demo was
  on opus" does not tell you which arm produced a given number — check the
  effective dense-arm indicator / the WARNING, not the config.

  A fourth lever, `SCUDO_USE_OPUS_DENSE=1`, branches *before* the
  `SCUDO_DENSE_BACKEND` check — `if env_use_opus_dense():` at
  `store/scipy_sqlite_store.py:553` and `store/falkordb_store.py:427` (both
  verified 2026-08-17), returning `multi_path_retrieve` early and bypassing
  `score_candidates()` entirely — so setting `jaro_winkler` alone does not
  guarantee determinism; `SCUDO_USE_OPUS_DENSE` must also be unset. Line numbers
  here are as at the time of writing; search the symbols. Corrected 2026-08-16,
  precision-corrected 2026-08-17 (the earlier text said "override" where the code
  is `setdefault`, and omitted the fallback lever); see
  [[opus-dense-is-the-score]] and [[fourth-dense-lever-use-opus-dense]].

- **Alternate local backend:** `backend/run_local.py`, Flask on :5001,
  `STORE_BACKEND=memory` (set env **before** import). Prefer `start_local.py`
  above; this one sets no auth env, so `/api/*` will 401. Local loop gotchas:
  `vendor_signature`, `decision=` — see project memory `scudo-local-loop-run`.
- **Dashboard dev:** `pnpm dev` in the dashboard package, Vite on :5173 (tokened URL printed at startup). Build: `pnpm build:matching` (tsc -b + `vite.config.matching.ts`).
- **Vendored dist rebuild:** `bash infra/build_dashboard_dist.sh` (syncs fixture → builds → copies into `dashboard-dist/`).
- **Tests:** backend `pytest backend/scudo/tests/` (bare `pytest` at root collects nothing); dashboard `pnpm vitest run`. Standalone smoke runners: `smoke.py` (mapping 111-gate, no deps), orchestrator smoke needs `strands`.
- **Graph fixture regen:** `python -m backend.scudo.build_matching_graph` (run from repo root).

### Env flags added 2026-08-06 (all default OFF / safe)

| Flag | Default | Effect when set |
|---|---|---|
| `CONSOLE_DB_BACKEND=sqlite` | unset → PostgreSQL | DB pages work with no Docker |
| `SCUDO_MV_ALLOW_INLINE_FRAME` | off → frame wins | honour caller-supplied name/description |
| `SCUDO_TEMPORAL_VALIDATION` | off | enable the `temporal_compatible` validation |
| `SCUDO_PERSIST_WRITE_TOKEN` | unset → **writes refused** | shared secret for the persistence MCP write tools |
| `SCUDO_PERSIST_ALLOW_DEV_WRITES` | off | local dev bypass for the above |

Two gotchas worth knowing before you debug something:

- `SCUDO_VERDICT_ALLOW_DEV` selects the dev HMAC **signing key only**. It used
  to also disable the canonical-write gate, which meant the README's own
  local-run recipe silently opened it. Deliberately decoupled — do not re-couple.
- The persistence MCP write tools (`record_decision`, `import_bundle`,
  `publish_bundle`) **fail closed**: with no `SCUDO_PERSIST_WRITE_TOKEN` set they
  refuse every write. That is intentional. `commit_mapping` is exempt because
  the HMAC verdict seal is the stronger control on that path. The Flask console
  route is a SEPARATE ingress (`feedback.apply_decision` direct) and is
  unaffected.

## Conventions

- OKF docs bundle at `docs/okf/scudo/` has its own navigation protocol — read `index.md` first, don't glob-scan; regenerate with `okf index docs/okf/scudo` after doc changes.
- Say "task is complete and ready for review" — never claim production readiness or "fully operational" without the user's explicit approval.
- Don't commit or deploy unless asked. `dashboard-dist/` is vendored build output but IS tracked — rebuild it via the script, never hand-edit.
- Session memory (verified facts, gotchas) lives in the Claude auto-memory for this project; read it before large tasks, update it after.
