# Aurora agent memory + rights/contract model + zone-aware agent tool Implementation Plan

> **RETROSPECTIVE NOTE:** This plan documents Parts A-F of
> `docs/superpowers/specs/2026-07-07-aurora-memory-rights-model-zone-tool-design.md` as they
> were actually implemented and tested. The brainstorming/spec step was followed at the time;
> the follow-on `writing-plans` step was skipped for Parts A-E, which a later Codex review
> flagged as a gap (item 6 of that review). This document closes that gap — it is written
> after the fact, describing what was built (file paths, functions, tests), not a forward
> plan for unstarted work. Part F is genuinely new work from the same review and is
> documented the same way, retrospectively, since all 8 review items were closed together.
>
> **For agentic workers:** No execution needed — all tasks below are already complete and
> tested. If resuming any follow-on work referenced in "Out of scope" (see the spec), use
> superpowers:writing-plans to create a NEW forward-looking plan for that specific slice.

**Goal:** Give the Orchestrator/Lambda pipeline real Aurora-backed CONSULT/DISTILL memory
(precedents, promoted rules, a SkillOpt-Sleep-inspired matching skill document), model the
rights/contract "bottom half" of the CatalogueOntology, give mapping agents zone-aware system
context, and close 8 Codex-reviewed gaps in that work (import-boundary hygiene, a real
SkillOpt CLI adapter, a scheduler job, a live Aurora diagnostic, doc cleanup, this plan, a
rights-model status guard, and a file-grouping report).

**Architecture:** A single new `scudo.agent_memory` Aurora table (RDS Data API, fail-loud
writes / fail-open reads) backs precedents, rules, trajectories, and a versioned
current/best skill document. Rights/contract entities extend the existing
`ConceptualNodeKind`/`ConceptualEdgeKind` closed enums. A zone-aware `describe_system_context`
tool/prompt-injection gives Bedrock/Azure mapping agents domain-recognition context. The
offline SkillOpt-Sleep half lives entirely outside the Lambda (`scudo.skillopt_sleep_runner`,
`scudo.skill_gate`, `scudo.skillopt_adapter`, `scudo.scripts.*`), verified never transitively
imported by `lambda_handler.py`.

**Tech Stack:** Flask/Lambda handler (Python), RDS Data API via `boto3` (lazy-imported),
Pydantic models, `strands` (Bedrock) / Azure OpenAI shim agents, `requests`/CLI subprocess
for the SkillOpt-Sleep adapter, `pytest` throughout.

## Global Constraints

- Stay within `/Users/anthonylui/MatchMaker/MatchMaker`. Never touch Understand-Anything or
  Defra repos.
- No real AWS/network calls in any test — every Aurora/SkillOpt-Sleep-CLI/HTTP interaction is
  hermetic via dependency injection (fake `execute`/`which`/`runner`/`resolve`/`getter`).
- Offline modules (`skillopt_sleep_runner.py`, `skill_gate.py`, `skillopt_adapter.py`,
  `scripts/run_sleep_cycle_job.py`, `scripts/aurora_smoke.py`) must never be transitively
  imported by `lambda_handler.py` — enforced by a subprocess-based test per module, not just
  grepping import lines (a direct-import-line check is insufficient against transitive
  imports — this is exactly the bug F1 fixed).
- Do not guess/fabricate external facts (SkillOpt's API surface, `ContentDeliveryModel`'s
  unsourced values, RDS Data API error message formats) — verify from real sources
  (`gh api repos/microsoft/SkillOpt/contents/...` for SkillOpt, direct repo grep for
  `ContentDeliveryModel`) or build guards/adapters that fail clearly instead.
- TDD throughout: RED (watch it fail for the right reason) → GREEN → verify.

---

### Task A: Aurora agent memory — CONSULT/DISTILL for the Orchestrator pipeline

**Files:**
- Created: `backend/scudo/aurora_memory.py`, `backend/scudo/tests/test_aurora_memory.py`
- Modified: `backend/scudo/lambda_handler.py`, `backend/scudo/schemas.py`,
  `backend/scudo/aurora_store.py` (added the `scudo.agent_memory` table to `ensure_schema()`)

**Interfaces:**
- Produces: `consult_priors(*, vendor, vendor_product_ref) -> Priors` (fail-open),
  `record_verified_precedent(...)` (fail-loud), `consult_best_skill() -> Optional[dict]`
  (fail-open), `record_trajectory(...)` (fail-loud), `harvest_trajectories(limit=100) ->
  list[dict]` (fail-open), `promote_skill(*, skill_text, validation_score, version) -> bool`
  (fail-open read of current best, fail-loud write, gated by `skill_gate.should_promote`).

- [x] Real precedent read replaces the fabricated `has_precedent`-flag canned
  `PrecedentMapping` in `_build_bundle_assembler`.
- [x] `record_verified_precedent` called from `handler()` on `Outcome.PUBLISHED`, alongside
  the existing `InMemoryPublishSink` (unrelated, untouched).
- [x] Tests: real-precedent hit, fail-open on Aurora error, fail-open when env missing,
  fail-loud write with correct parameterised SQL. 21/21 passing (final count, including E1's
  later additions to this same file).
- [x] Ran: `rtk proxy python -m pytest backend/scudo/tests/test_aurora_memory.py -v` → all pass.

### Task B: Rights/contract conceptual model (v1, provisional)

**Files:**
- Modified: `backend/scudo_mapping_mcp/models.py`, `backend/scudo/build_matching_graph.py`
- Created: `backend/scudo_mapping_mcp/tests/test_rights_contract_model.py`

**Interfaces:**
- Produces: `ConceptualNodeKind.{PARTY,CONTRACT,POLICY,DUTY,PERMISSION}`,
  `ConceptualEdgeKind.{PARTY_ROLE,GRANTS,HAS_PERMISSION,HAS_DUTY}`,
  `ContentDeliveryModel(str, Enum)` (provisional, 3 of ~11 reported values confirmed from
  source).

- [x] 5 new node kinds + 4 new edge kinds added to the existing closed enums, dashboard
  `_CONCEPTUAL_NODE_TYPE`/`_CONCEPTUAL_EDGE_TYPE` mapping extended (party→entity,
  contract→document, policy→config, duty→step, permission→claim;
  party_role/grants→related, has_permission/has_duty→contains).
- [x] `ContentDeliveryModel` modeled with only the 3 values confirmed from source
  (`distributionService`, `redistributionService`, `displayService`) — deliberately
  incomplete, not guessed.
- [x] Tests: node/edge kinds exist with correct values, dashboard-enum mapping for each,
  `ContentDeliveryModel` has exactly the 3 confirmed values,
  `conceptual_iri()` works unchanged for the new kinds. Ran:
  `rtk proxy python -m pytest backend/scudo_mapping_mcp/tests/test_rights_contract_model.py -v`
  → 5/5 pass (includes F7's later `_CONTENT_DELIVERY_MODEL_SOURCES` citation-guard test).

### Task C: Zone-aware agent tool (streaming-demo pipeline)

**Files:**
- Modified: `backend/scudo_mapping_mcp/agent.py`
- Created: `backend/scudo_mapping_mcp/tests/test_zone_context_tool.py`

**Interfaces:**
- Produces: `_system_context_text() -> str` (shared text), a `@tool describe_system_context()`
  on `BedrockMappingAgent` (real Strands tool-calling loop), the same text pre-injected into
  `AzureMappingAgent`'s one-shot prompt.

- [x] Entity lists in `_system_context_text()` derived programmatically
  (`list(ConceptualNodeKind)[:13]`/`[13:]`) rather than hand-typed, after an adversarial
  review caught the hand-typed version silently dropping 4 of 13 top-half kinds.
- [x] Tests: Bedrock tool list includes the new tool and returns the expected text; Azure's
  constructed prompt contains the system-context text; exhaustive-listing regression test.
  Ran: `rtk proxy python -m pytest backend/scudo_mapping_mcp/tests/test_zone_context_tool.py -v`
  → all pass.

### Task D: SkillOpt-inspired matching skill memory (delivery to the Orchestrator pipeline)

**Files:**
- Modified: `backend/scudo/schemas.py` (`BriefBundle.skill_hint`), `backend/scudo/prompts.py`
  (`mapping_prompt` surfaces `skill_hint`), `backend/scudo/lambda_handler.py`
  (`_build_bundle_assembler` populates it, `_record_precedent_if_published` also calls
  `record_trajectory`)
- Created: `backend/scudo/skillopt_sleep_runner.py` (original `should_promote` +
  documented-stub version — since split further in F1/F2),
  `backend/scudo/tests/test_skill_hint_prompt.py`,
  `backend/scudo/tests/test_lambda_handler_memory_wiring.py`

**Interfaces:**
- Produces (original): `should_promote(candidate_score, current_best_score) -> bool` (moved
  to `scudo.skill_gate` in F1 — see Task F1 below).

- [x] Corrected mid-design: Part C's Bedrock-tool-vs-Azure-prompt asymmetric delivery pattern
  does NOT apply here — re-verified `orchestrator.py:193,202` shows BOTH Bedrock and Azure
  specialists are single-shot `structured_output(Model, prompt)` calls with no tool loop, so
  `skill_hint` uses ONE shared delivery mechanism (prompt injection via `BriefBundle`), not
  two.
- [x] Tests: skill read miss/hit/fail-open, prompt injection present/absent, trajectory
  recording, gate logic (no-prior-best/strict-improvement/tie/regression). Ran:
  `rtk proxy python -m pytest backend/scudo/tests/test_skill_hint_prompt.py backend/scudo/tests/test_lambda_handler_memory_wiring.py -v`
  → all pass.

### Task E: Gap closure round 2 (promote_skill, run_sleep_cycle, fixture churn, ContentDeliveryModel guard)

**Files:** `backend/scudo/aurora_memory.py`, `backend/scudo/skillopt_sleep_runner.py`,
`backend/scudo/tests/conftest.py`, `backend/scudo/tests/test_dashboard_enum_vocabulary.py`,
`backend/scudo/tests/test_provenance.py`, `backend/scudo_mapping_mcp/tests/test_rights_contract_model.py`

- [x] E1: `promote_skill()`/`harvest_trajectories()` added to `aurora_memory.py` (see Task A).
- [x] E2: `run_sleep_cycle()`/`default_held_out_split()`/lazy optimizer+evaluator added to
  `skillopt_sleep_runner.py`.
- [x] E3: fixture churn fixed via a shared `built_matching_graph` pytest fixture in
  `conftest.py` monkeypatching `_OUT`/`_META_OUT` to `tmp_path`.
- [x] E4: `_CONTENT_DELIVERY_MODEL_SOURCES` citation-map guard added.
- [x] Ran the combined suite: 61/61 passing at the time (excluding 2 pre-existing, unrelated
  `test_provenance.py` failures, documented separately).

### Task F1: Fix the Lambda/offline import boundary

**Files:**
- Created: `backend/scudo/skill_gate.py`, `backend/scudo/tests/test_skill_gate.py`
- Modified: `backend/scudo/aurora_memory.py`, `backend/scudo/skillopt_sleep_runner.py`,
  `backend/scudo/tests/test_skillopt_sleep_runner.py`

**Interfaces:**
- Consumes: nothing (foundational).
- Produces: `scudo.skill_gate.should_promote(candidate_score, current_best_score) -> bool` —
  the ONLY export of a deliberately neutral module (no imports beyond `__future__`/`typing`,
  enforced by an AST-parsing test).

- [x] Step 1: RED — `test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded` (a
  subprocess-based check, not a grep) confirmed failing: importing `scudo.lambda_handler`
  loaded `scudo.skillopt_sleep_runner` transitively via `aurora_memory.py`.
- [x] Step 2: created `skill_gate.py`, moved `should_promote` there (with its 4 existing unit
  tests, moved to the new `test_skill_gate.py`), updated `aurora_memory.py`'s import.
- [x] Step 3: GREEN — 5/5 in `test_skill_gate.py`, and the transitive-import test now passes.
  Full regression run: `rtk proxy python -m pytest backend/scudo/tests/test_skillopt_sleep_runner.py backend/scudo/tests/test_skill_gate.py backend/scudo/tests/test_aurora_memory.py backend/scudo/tests/test_lambda_handler_memory_wiring.py backend/scudo/tests/test_skill_hint_prompt.py -v`
  → 47/47 passing.
- [x] Ruff clean on all touched files.

### Task F2: Wire real SkillOpt as far as safely possible

**Files:**
- Created: `backend/scudo/skillopt_adapter.py`, `backend/scudo/tests/test_skillopt_adapter.py`

**Interfaces:**
- Consumes: nothing (standalone adapter).
- Produces: `find_skillopt_sleep_binary(*, which=None) -> Optional[str]`,
  `trajectory_to_task_record(trajectory: dict) -> dict`,
  `write_tasks_file(trajectories, *, project, target_skill_path, path) -> str`,
  `run_skillopt_sleep_dry_run(*, tasks_file_path, target_skill_path, project, which=None,
  runner=None, timeout=300.0) -> dict`, `SkillOptSleepUnavailableError(RuntimeError)`.

- [x] Verified ground truth directly from `github.com/microsoft/SkillOpt` via `gh api` (CLI
  entry point in `pyproject.toml`, argparse flags in `skillopt_sleep/__main__.py`,
  `TaskRecord` fields in `skillopt_sleep/types.py`, tasks-file shape in
  `skillopt_sleep/tasks_file.py`, JSON report shape in `_report_payload`) before writing any
  code — going further than the earlier README-only verification.
- [x] TDD: 9/9 tests, hermetic (`which`/`runner` injectable, no real subprocess spawned).
- [x] Documented, not implemented (verified as a genuine gap, not guessed around): reading a
  candidate skill doc's text back out of `staging_dir` (file-naming convention inside it
  unverified), and whether SCUDO's trajectories are a meaningfully scorable signal for
  SkillOpt-Sleep's judge/replay machinery (a domain-fit question).
- [x] Ruff clean.

### Task F3: Scheduler/job wrapper for the sleep cycle

**Files:**
- Created: `backend/scudo/scripts/run_sleep_cycle_job.py`,
  `backend/scudo/tests/test_run_sleep_cycle_job.py`

**Interfaces:**
- Consumes: `scudo.aurora_memory` (as the real `store`), `scudo.skillopt_sleep_runner.run_sleep_cycle`,
  `scudo.skill_gate.should_promote` (via the dry-run wrapper).
- Produces: `main(argv=None) -> int` CLI entrypoint (0 success / 2 env-missing / 3
  run-sleep-cycle error), `_env_validation_errors() -> list[str]`,
  `_make_dry_run_store(real_store) -> object`, `_parse_args(argv) -> argparse.Namespace`.

- [x] Followed the exact pattern of the existing `scripts/cleanup_stale_cdao.py`: dry-run by
  default, `--apply` to write, lazy imports inside `main()`, pure helpers tested directly.
- [x] TDD: 11/11 tests (env validation, dry-run-store read-passthrough/write-blocking,
  transitive-import boundary).
- [x] Manually smoke-checked the CLI's env-missing path: exit code 2, clear message.
- [x] Ruff clean.

### Task F4: Live Aurora smoke/diagnostic

**Files:**
- Created: `backend/scudo/scripts/aurora_smoke.py`, `backend/scudo/tests/test_aurora_smoke.py`

**Interfaces:**
- Produces: `main(argv=None) -> int` (0 success / 1 check-failed / 77 skipped-no-env),
  `check_agent_memory_table(*, execute=None) -> tuple[bool, str]`,
  `run_write_read_delete_smoke_test(*, execute=None) -> tuple[bool, str]`,
  `SKIPPED_EXIT_CODE = 77`.

- [x] TDD: 9/9 tests (env validation, read-only check success/failure, write/read/delete
  round-trip success/failure with call-order assertion, transitive-import boundary).
- [x] Manually smoke-checked the CLI's skip path (no Aurora env vars in this environment):
  exit code 77, clear "expected state, not a failure" message.
- [x] Ruff clean.

### Task F5: Clean stale docs/comments

**Files:** `backend/scudo/skillopt_sleep_runner.py`,
`docs/superpowers/specs/2026-07-07-aurora-memory-rights-model-zone-tool-design.md`

- [x] Rewrote `skillopt_sleep_runner.py`'s module docstring: removed "not yet built"/"stub
  with nothing real to promote" language (stale since the E1/E2 round actually built
  `promote_skill`/`run_sleep_cycle`), replaced with an accurate STATUS section.
- [x] Added spec Part F documenting all 8 review items and their resolutions, and updated
  "Out of scope" to reflect what F2 newly covers vs. what's still genuinely unverified.

### Task F6: This plan document

- [x] Written retrospectively, covering Parts A-F as actually implemented (this file).

### Task F7: Rights-model status, without guessing

**Files:** `backend/scudo_mapping_mcp/tests/test_rights_contract_model.py` (already extended
in the E4 round — re-confirmed, not re-guessed, in this round)

- [x] Re-searched the repo for any source of the remaining ~8 `ContentDeliveryModel` values —
  none found, consistent with E4's original finding. No new values added.
- [x] `_CONTENT_DELIVERY_MODEL_SOURCES` citation-map guard (from E4) re-verified still passing
  and still structurally prevents a silently-guessed 4th value.

### Task F8: File-grouping report

- [x] Delivered in the final chat report for this round (not a repo file) — every
  changed/untracked file grouped into SkillOpt/Aurora (this round + the earlier A-E work),
  E2E/frontend-URL-ingest (a separate, prior round on this branch), and unrelated
  (`tour-fix-step2.jpeg`).

---

## Plan self-review

**Spec coverage:** Every part of the spec (A, B, C, D, E, F1-F8) has a corresponding task
above with file paths and test commands. No spec section lacks a task.

**Placeholder scan:** no "TBD"/"TODO: fill in later" in this document — every task describes
what was actually built, since this is retrospective.

**Type/name consistency:** `should_promote`'s home (`scudo.skill_gate`, not
`scudo.skillopt_sleep_runner`) is consistent across Tasks D, E, and F1 above — Task D's
original description is intentionally marked "moved to scudo.skill_gate in F1" rather than
silently rewritten, so the history is traceable. `run_sleep_cycle`'s `store` parameter name
and protocol (`harvest_trajectories`/`consult_best_skill`/`promote_skill`) match exactly
across Tasks A, E, and F3.
