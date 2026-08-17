# Verified Review Notes

Last verified: 2026-07-16.

- 2026-08-13 Citrix runtime work, typed into the VDI checkout at
  `C:\Users\W628453\OneDrive - JPMorgan Chase\Documents\MATCHMAKER-SCUDO\MatchMaker 2\MatchMaker`:
  - Added `backend/scudo_mapping_mcp/store/sqlite_store.py` and
    `backend/scudo_mapping_mcp/tests/test_sqlite_store.py`; surgically updated
    `backend/scudo_mapping_mcp/config.py`,
    `backend/scudo_mapping_mcp/store/factory.py`, `streamlit_app.py`, and
    `backend/scudo_mapping_mcp/agent.py`.
  - SQLite persists and replays HITL precedents while taxonomy nodes remain the
    normal process-local fixture seed. `streamlit_app.py` sets
    `STORE_BACKEND=sqlite` and an absolute backend-local
    `SCUDO_SQLITE_PATH` before importing cached settings.
  - The older Citrix agent factory now accepts
    `get_agent(provider: Optional[str] = None)` and resolves
    `provider` before `SCUDO_AGENT_BACKEND`; Streamlit passes its existing
    provider selector into the factory. Re-verified Citrix locations:
    `backend/scudo_mapping_mcp/agent.py:769`, `:780`, and
    `streamlit_app.py:786`.
  - Verified in Citrix with Python 3.14: both edited modules compile; explicit
    construction returns `ScriptedMappingAgent BedrockMappingAgent` without
    invoking Bedrock; setting `SCUDO_AGENT_BACKEND=bedrock` and calling the
    legacy zero-argument factory returns `BedrockMappingAgent`; SQLite unittest
    reports `Ran 1 test ... OK`; the SQLite-configured factory reports
    `sqlite SQLiteStore True`; seeded deterministic matching reports `seed 14` then
    `auto_mapped Equity Prices 0.913`.
  - The already-running Streamlit page at `http://localhost:8501` reloaded and
    visibly reported 14 taxonomy nodes and store `sqlite`. A standalone shell
    check without `STORE_BACKEND=sqlite` correctly fell through to the
    unchanged FalkorDB default and failed because the FalkorDB package is not
    installed; the same check passed once run with the Streamlit environment.
    No Bedrock request, AWS deployment, CloudShell, or infrastructure change
    was made.

- 2026-08-13: `JPMC_DEPLOYMENT_AGENT_HANDOVER.md` is stored locally on this
  machine at `/Users/anthonylui/MatchMaker/MatchMaker/JPMC_DEPLOYMENT_AGENT_HANDOVER.md`;
  it is not a file inside the Citrix environment.

- 2026-07-21: Read the user-provided JPMC landscape scope. It defines the
  day-one shipping/proof surface as the listed files under `jpmc-port/`, plus
  `backend/scudo/scripts/ab_capone_arm.py` for the real Capone A/B arm. It
  explicitly excludes Capone trunk except that arm, the ingestion console,
  Azure, and offline SkillOpt promotion as a live Lambda path. The heading
  `Root / runners (11)` currently lists 9 paths.

- On 2026-07-18, completed the Capone (`426271381846`, `us-east-1`) Aurora
  PostgreSQL console cutover. CloudFormation stacks `scudo-poc-data`,
  `scudo-poc-foundation`, and `scudo-poc` reached their successful terminal
  states; the application stack rolled the console ECS service to task
  definition revision `scudo-poc-console:3`.
- Verified live: the Aurora writer endpoint, port `5432`, database `scudo`,
  all `SCUDO_AURORA_*` variables, and Secrets Manager injections for
  `CONSOLE_DB_USER` and `CONSOLE_DB_PASSWORD` are present in the task
  definition. The Aurora console schema has 9 tables, the `scudo` schema has
  8 tables, and neither `public` nor `ingestion` has tables.
- **2026-07-20 DDL fix (worktree):** `backend/init_db.sql` now creates
  `console.<table>` directly (no `SET search_path`, no
  `ALTER TABLE public.* SET SCHEMA console`).
  `infra/bootstrap_console_schema_data_api.py` asserts that invariant and
  still wraps apply in one Data API transaction with rollback. Contested
  create-in-public-then-relocate ambiguity is moot. Decisive CloudShell check:
  `SELECT table_schema FROM information_schema.tables WHERE table_name='tp_provider';`
  → expect `console`.
- The console's legacy `users.password` values are not used by
  `auth.resolve_principal()` (which currently resolves a trusted upstream
  header or explicitly enabled dev identity), but `backend/init_db.sql` still
  seeds `admin` with a known cleartext password and the admin routes still
  store supplied passwords directly. Treat that separate auth/password redesign
  as a required hardening item before non-demo exposure; do not silently bundle
  it into Aurora cutover work.

- Safety rework after adversarial review:
  `backend/scudo/matching_self_improvement.py` now counts wrong positive
  auto-passes, uses the auto-pass denominator, excludes abstention cases from
  match-confidence calibration, gates abstention recall when abstention cases
  exist, treats all-abstain exact-match splits as 1.0 only alongside that
  recall gate, and normalizes both parts of golden identities case-insensitively.
- `backend/scudo/aurora_memory.py` now provides a no-write
  `preflight_skill_promotion()` used by dry-run and real promotion, reconciles
  artifact/pointer partial-write retries while ignoring only generated
  evaluation/approval timestamps, rejects conflicting immutable artifact
  versions, and allocates versions beyond stored immutable artifacts.
- `backend/scudo/skillopt_adapter.py` maps only explicit auto-passed
  published/auto-mapped decisions to SkillOpt success; needs-review and other
  outcomes are failures. `skillopt_sleep_runner.py` rejects scalar evaluator
  outputs, rejects cross-partition vendor/product identity overlap, and asks
  the store for the next immutable-artifact-safe version. The dry-run wrapper
  delegates both version allocation and structured preflight to the real
  store without writes.
- Verified after the rework:
  `cd backend && PYTHONPATH=. pytest scudo/tests/test_matching_self_improvement.py
  scudo/tests/test_aurora_memory.py scudo/tests/test_skillopt_adapter.py
  scudo/tests/test_skillopt_sleep_runner.py scudo/tests/test_run_sleep_cycle_job.py
  scudo/tests/test_evaluate_matching_golden.py -q` -> 82 passed.
  The wider `scudo/tests scudo_mapping_mcp/tests` suite -> 482 passed, 2 known
  failures in `scudo/tests/test_provenance.py` due to Marketing graph content.
  Independent review identified and verified fixes for approval-timestamp retry
  reconciliation and dry-run version allocation.

- Implemented the MatchMaker-native self-improvement foundation in
  `backend/scudo/matching_self_improvement.py`: versioned golden-set contracts,
  JSONL loading, train/holdout leakage checks, agent/engine result
  normalization, exact-match/abstention/false-auto-pass/calibration/Brier
  metrics, and named-approval promotion validation.
- Added report-only evaluator
  `backend/scudo/scripts/evaluate_matching_golden.py`.
- Hardened `backend/scudo/aurora_memory.py` so legacy scalar skill rows are
  quarantined; live skills require a passed holdout report, immutable artifact
  metadata, named approval, and versioned artifact storage before the live
  pointer is updated.
- Added structured agent and deterministic-engine trajectory evidence,
  including surface, input snapshot, decision snapshot, source provenance,
  matcher/version pins, and the strict
  `run_evaluated_sleep_cycle()` entry point.
- Focused verification:
  `cd backend && PYTHONPATH=. pytest scudo/tests/test_matching_self_improvement.py
  scudo/tests/test_aurora_memory.py
  scudo/tests/test_lambda_handler_memory_wiring.py
  scudo/tests/test_skillopt_sleep_runner.py -q`
  passed: 49 tests.
- Full matching verification:
  `cd backend && PYTHONPATH=. pytest scudo/tests scudo_mapping_mcp/tests -q
  --disable-warnings --maxfail=10`
  passed: 463 tests; 2 known failures remain in
  `scudo/tests/test_provenance.py` because generated graph output still contains
  forbidden Marketing content. These failures are unrelated to the
  self-improvement changes.
- `python -m scudo.scripts.evaluate_matching_golden --help`, Python compilation,
  and `git diff --check` passed.
- Corrected evaluator accounting so a positive case that abstains cannot count
  as an exact target match; added a regression test.

- Worktree state at review start: modified `backend/routes/mapping.py` contains an
  SSE heartbeat implementation; untracked `tour-fix-step2.jpeg` exists. Do not
  revert or overwrite either without explicit instruction.
- `cd backend && PYTHONPATH=. pytest tests/test_ingest_stream_route.py -q`
  passed: 3 tests.
- `cd backend && PYTHONPATH=. pytest scudo/tests scudo_mapping_mcp/tests -q
  --disable-warnings --maxfail=10` produced 343 passes and 2 failures. Both
  failures are `scudo/tests/test_provenance.py`: generated matching-graph output
  contains `marketing_dataset`, despite tests and project guidance forbidding
  Marketing content.
- `backend/routes/mapping.py`'s uncommitted `_sse_queue_frames()` emitted
  `: ping\n\n` in a direct queue-timeout smoke check. Existing endpoint tests do
  not assert heartbeat output.
- Deployment auth is not safe for external or customer-data exposure:
  `infra/scudo-poc-app.yaml` defaults an internet-facing ALB ingress CIDR to
  `0.0.0.0/0`; `backend/auth.py` accepts any non-empty
  `X-Authenticated-User`; `infra/scudo-poc-frontend.yaml` lacks a configured
  strip-and-inject identity boundary.
- The claimed sole Persistence-MCP/Aurora decision flow is not fully enforced:
  the Flask decision route calls `feedback.apply_decision()` directly, which
  writes a precedent to the retrieval store; the reviewer queue in
  `persistence_mcp.py` is a module-global list; Aurora schemas differ between
  `aurora_store.py` and `projection_handler.py`.
- Console DB deployment inputs are inconsistent: `backend/db.py` expects
  `CONSOLE_DB_*` PostgreSQL configuration, while `infra/scudo-poc-app.yaml`
  supplies `my_sql_*` values for a MySQL endpoint on port 3306.
- Matching is not yet production semantic retrieval: default dense backend is
  Jaro-Winkler, and `NeptuneStore.find_similar_products()` returns all taxonomy
  nodes at zero similarity. No versioned production golden set is present.
- The self-improvement job is not deployed and its default optimizer/evaluator
  raise `NotImplementedError`; recorded trajectories lack complete
  decision/version/evaluation evidence. Do not allow automatic promotion.
- No first-class persistence model was found for invoices, subscriptions,
  licences/entitlements, usage, allocations, or procurement workflows. The
  current product is a governed vendor-to-taxonomy matcher, not yet a
  market-data-spend optimisation platform.
- Compared `ReflexioAI/reflexio` at HEAD `966689e` (2026-07-15) with
  `kayba-ai/recursive-improve` at HEAD `9cf4b85` (2026-04-01). Reflexio is the
  stronger reference for reviewer-correction memory, scoped playbooks,
  retrieval, approval/versioning, and learning-impact evaluation. Recursive
  Improve is the stronger reference for offline trace capture, domain metrics,
  benchmark comparison, and isolated keep/revert experiments. Neither should
  be adopted wholesale.
- Verified architecture rule: newly accumulated precedents, playbooks, prompts,
  retrieval weights, or matcher variants must not influence live matching merely
  because they were recorded. Keep them quarantined until provenance and access
  controls, a versioned golden/holdout evaluation including adversarial cases,
  named approval, and immutable rollbackable promotion are complete. Keep
  Aurora/MatchMaker as the single persistence authority; use Reflexio patterns
  for the online learning workflow and Recursive Improve patterns for the
  offline optimizer/evaluator.
- Rewrote the root `README.md` as a business-first guide: purpose and outcomes,
  business flow, decision controls, reviewer experience, controlled
  self-improvement, current status, explicit delivery gates, quick start,
  technical architecture, API/runbook references, and glossary. Preserved
  important limits around deterministic authority, Aurora ownership, demo-only
  endpoints, uncalibrated retrieval, incomplete Neptune deployment, identity,
  reviewer queue persistence, and the two known provenance failures.
- Corrected README architecture-table paths against the working tree. Verified
  all local README links and representative code paths exist; `git diff --check`
  passes and the README is 451 lines. No code tests were rerun because the final
  changes after the prior implementation verification are documentation-only.
- Pushed the selected README and self-improvement change set as commit
  `1f52f87` (`feat(scudo): add governed matching self-improvement`) to
  `origin/scudo-phase0-foundations`. Deliberately left `MEMORY.md`, unrelated
  superpowers specs, and `tour-fix-step2.jpeg` untracked and out of the commit.
- Merged `scudo-phase0-foundations` into `main` with merge commit `c1b8993`
  (`Merge branch 'scudo-phase0-foundations' into main`) and pushed it to
  `origin/main`. Verified the remote `main` ref points to `c1b8993`; only the
  previously excluded untracked files remain in the worktree.
- Independently re-reviewed the adversarial safety-gate report after the merge.
  Verified the false-auto-pass metric gap in
  `backend/scudo/matching_self_improvement.py`: with 19 correct and 1 wrong
  confident auto-publish across 20 positive cases, the default policy reports
  `passed=True` because the false-auto-pass denominator has no abstention cases.
  Verified the calibration inversion too: a correct abstention at confidence
  `0.05` produces Brier `0.9025` and fails the default `0.10` threshold.
- Verified `cd backend && PYTHONPATH=. pytest scudo/tests/ -q
  --disable-warnings --maxfail=10` currently reports 227 passed and 2 known
  provenance failures. The earlier 463 figure referred to the broader
  combined `scudo/tests` plus `scudo_mapping_mcp/tests` command, so the scope
  should have been stated explicitly.
- Assessment: the adversarial review is substantially correct. Additional
  verified gaps include the artifact-write/live-pointer retry wedge, the
  legacy `run_sleep_cycle_job` path bypassing the structured report/approval
  contract, trajectory mining that labels needs-review engine outcomes as
  success in `skillopt_adapter.py`, and missing train/holdout identity checks.
  No code changes were made during this review turn; self-improvement should
  remain unapproved until these issues are fixed and metric semantics are
  pinned by tests.
- Follow-up verification after `278b2b1`:
  - Confirmed two new correctness regressions. `EvaluationMetrics.auto_pass_cases`
    is required, so an artifact serialized before that field existed fails
    parsing and is quarantined by `consult_best_skill()`. This can make a valid
    incumbent invisible to `validate_promotion()`. Also, an all-abstain
    holdout passes the default policy because `exact_match_rate` is forced to
    `1.0` when no positive cases exist and `GoldenSet` requires only one
    holdout case. Direct smoke checks reproduced both behaviours.
  - Confirmed the metric tests do not independently pin the abstention-recall
    denominator or non-zero match-confidence calibration/Brier values. The
    existing strict-cycle test is a happy path only; it does not exercise
    `run_evaluated_sleep_cycle()`'s `EvaluationReport` type guard or early
    returns. A legacy pointer with no immutable artifact history can still
    make `next_skill_version()` return `1`.
  - Qualification: `test_lambda_handler_memory_wiring.py` already asserts
    agent input/decision snapshot plumbing. Its lack of change in `278b2b1`
    is not itself proof of a current defect without a more specific missing
    contract.
  - Verified scope on 2026-07-16:
    `cd backend && PYTHONPATH=. pytest scudo/tests -q --disable-warnings --maxfail=10`
    -> `246 passed, 2 failed`; both failures remain the known Marketing
    provenance tests. The earlier `482 passed` count was from the wider
    `scudo/tests scudo_mapping_mcp/tests` collection.
- Follow-up implementation pending commit:
  - `EvaluationMetrics.auto_pass_cases` now defaults to `0`, preserving
    readability of earlier serialized artifacts and their incumbent
    no-regression protection.
  - `GoldenSet` now requires at least one positive holdout case. Abstention-only
    evaluation remains allowed for the adversarial split when that invariant is
    met, but cannot be used as sole promotion evidence.
  - `next_skill_version()` now considers both immutable artifact rows and the
    historic `skill:matching:best` pointer, including quarantined legacy
    pointers, to avoid version regression.
  - Added regression tests for the compatibility path, holdout composition,
    abstention-recall denominator, non-zero positive-case calibration/Brier
    semantics, strict-runner scalar rejection and early returns, and legacy
    pointer versioning. Updated README language for the positive-holdout rule
    and test scope.
  - Verification: focused README command -> `98 passed`; broader
    `scudo/tests scudo_mapping_mcp/tests` -> `491 passed, 2` known Marketing
    provenance failures; compileall and `git diff --check` passed. A separate
    council/gemini delegate produced no extractable review response.

# Agent process: copy MatchMaker runtime into MATCHMAKER-SCUDO via Citrix

## Job (and only this job)

Get the **files and code** from the laptop source into the Citrix VS Code workspace.

| Role | Path |
|------|------|
| Source (read on Mac / agent host) | `/Users/anthonylui/MatchMaker/MatchMaker/` |
| Target (Citrix VS Code) | `MATCHMAKER-SCUDO` (open in the Citrix desktop) |

No AWS deploy. No CloudShell. No Jira. No infra. No “scaffold”, no inventories theatre, no reduced subset. Runtime application files only.

Do **not** overwrite these unless the user explicitly says so:

- `backend/routes/mapping.py` (local SSE / uncommitted work)
- `tour-fix-step2.jpeg`

---

## How Citrix actually works (read before any click)

Citrix is a remote desktop stream. Your Browser / Computer Use tools drive that
stream. They do **not** share a filesystem with the Mac.

| Reality | What you must do |
|---------|------------------|
| No shared disk | You cannot `cp`, `rsync`, drag-drop folders, or “upload the repo”. |
| No paste | Clipboard and paste into Citrix are unreliable / forbidden here. **Type.** |
| No shortcuts | Do not use Cmd/Ctrl+V, Cmd/Ctrl+A via automation as a transfer method. User said **click and type**. |
| One keystroke at a time | Fast bursts drop or garble characters (e.g. text becomes `vaA1`). Throttle typing. |
| Click coordinates drift | Citrix scaling offsets Explorer clicks. Wrong click = typing into the wrong pane (e.g. into `FileU` instead of a New File name field). |
| Prove focus every time | Before typing a path or file body: confirm Explorer selection + editor **breadcrumb** shows the exact folder/file. |
| Large files are slow | Say so. Do not invent a workaround (zip, curl, git clone on the VDI, terminal paste of whole files) unless the user allows it. |

If you are struggling: stop, say Citrix only accepts throttled remote keystrokes, ask how to proceed. Do not silently switch to a shortcut.

---

## Citrix engagement loop (every file)

1. **Calibrate once per session**  
   Click a known Explorer folder. Confirm selection highlight. Adjust if off-target.

2. **Diff first — never recreate blindly**  
   On source Mac, list what should exist. In Citrix Explorer, list what already exists.  
   **Only create missing files / missing content.** The user may already have done `db.py` and most of the tree. Recreating existing work is a failure.

3. **Create a missing file**  
   - Click the **correct parent folder** in Explorer (verify name).  
   - Click that folder’s **New File** control (not the title bar, not the wrong pane).  
   - Type the **filename only**. Verify breadcrumb / tab name.  
   - Then type the file body from the source, throttled.  
   - After a chunk: re-read start, middle (defs), and end against source.  
   - If garbled: clear via menu (**Selection → Select All** then delete — do not assume Edit→Select All exists), retype slower.  
   - Save via **File → Save** (click menus), not a shortcut.

4. **Empty / accidental files**  
   If you created junk (`FileU`, etc.): rename/repurpose only when it is the right empty module (e.g. `__init__.py`), otherwise delete via UI. Do not leave orphans.

5. **After each file**  
   Report: path created/updated, and that breadcrumb matched. Then next missing file.

---

## What to transfer

Runtime application hierarchy from `/Users/anthonylui/MatchMaker/MatchMaker/` into `MATCHMAKER-SCUDO`.

Include: `backend/` app code, runtime scripts the app needs, fixtures the app needs to run.

Exclude: agent scratch, caches (`.pytest_cache`, `__pycache__`, `.git` if they forbid it), logs, local tooling config, generated churn unless required to run.

Read source on the Mac with normal tools. Enter content into Citrix **only** by click + type as above.

Example gap that already bit a prior agent:

- Source: `/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/authoritative/`  
  needs `__init__.py`, `client.py`, `mcp.py`, `mock.py`  
- Target had none (or only accidental `FileU`) — create those four by typing, not by recreating `db.py`.

---

## Mistakes that already angered the user (do not repeat)

1. Overplaying AWS / mega-ticket / deploy when they asked for **files into MATCHMAKER-SCUDO**.  
2. Inventory / Citrix-control theatre instead of transferring files.  
3. Using shortcuts or bulk methods after “TYPE” / “click and type” / “NO SHORTCUTS”.  
4. Clicking the top chrome / wrong UI instead of Explorer New File + editor.  
5. Recreating `db.py` (and other files the user already typed).  
6. Typing at full speed so Citrix drops characters, then saving garbage.  
7. Assuming Explorer clicks landed without checking breadcrumb.

---

## Done when

Target `MATCHMAKER-SCUDO` has the missing runtime files from  
`/Users/anthonylui/MatchMaker/MatchMaker/`, entered by click-and-type, with no
overwrite of user-owned files, and you can name what is still missing if anything.

Infra comes later. Do not start it.

---

## MCP checklist recovery (2026-08-01)

- External deliverable workspace: `/Users/anthonylui/MCPDBAgents`.
  The original Claude PID `77732` was gone; a later resumed Claude session
  briefly rewrote `mcp-checklist-work/rows/09-interop.jsonl`, so final source
  content was rechecked immediately before assembly.
- Independently reviewed and corrected MCP checklist rows in
  `rows/03-client.jsonl`, `06-authz.jsonl`, `08-performance.jsonl`, and
  `09-interop.jsonl`. Key corrections preserve optional/SHOULD behavior as
  advisory, fix authorization-server and server/client scope, and keep the
  dual-era stdio fallback condition as a conditional MUST.
- Final verification passed:
  `validate_rows.py` -> 291 rows, 0 findings;
  `scan_overclaim.py` -> 0 contradiction/deadline/log-gate findings (only
  pre-existing heuristic candidates);
  `inspect_workbook.py` -> 4 sheets, 335 data rows, 15 checklist columns,
  0 findings; `unzip -t` -> clean.
- Baseline SHA remains exact:
  `RAG_Testing_Checklist_Clean.xlsx` ->
  `0c7bcdd392c73d933a6b015028c02ad391c89e4114a433803d0959d86740a2fa`.
  Final deliverables were rebuilt and visually rendered with LibreOffice:
  `RAG_Testing_Checklist_MCP.xlsx`, `RAG_Testing_Checklist_MCP.csv`,
  `MCP_Checklist_Gap_Matrix.csv`, and `MCP_Checklist_Research_Notes.md`.

## Claude Shim Routing (2026-08-01)

- `/Users/anthonylui/.codex/shim-router/router.py` resolves the model for
  every request from its payload and then chooses eligible upstreams. An Opus
  5 request stays on an available native Opus 5 route; if none is warm, its
  configured compatibility fallback is Fable 5, not Sonnet 5.
- Router upstream definitions and `SHIM_ROUTER_ORDER` are loaded into
  `RouterState` at process startup. There is no reload or `SIGHUP` handler, so
  router-side environment or configuration changes require a router restart to
  take effect. An in-session Claude `/model` change does not require a router
  restart because the router honors the model carried by each new request.
