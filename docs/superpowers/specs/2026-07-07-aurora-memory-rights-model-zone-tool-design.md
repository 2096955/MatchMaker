# Aurora agent memory + rights/contract model + zone-aware agent tool

**Date:** 2026-07-07 · **Status:** approved (brainstormed, this session) → implementing

## Intent

Three previously-separate design threads, bundled into one vertical slice per explicit
direction: (A) give the Orchestrator pipeline real Aurora-backed memory instead of its
current mocked/in-memory behaviour, (B) model the "bottom half" of the CatalogueOntology
(rights/contract: Party/Contract/Policy/Duty/Permission/ContentDeliveryModel) that today
has no typed representation, (C) let the mapping agents see enough system/domain context to
recognise when a product needs bottom-half (rights/contract) treatment.

## A — Aurora agent memory (Orchestrator pipeline)

Ground truth verified this session: the Orchestrator's "precedent" is currently **fabricated**
(`_build_bundle_assembler` in `lambda_handler.py:207-218` invents a canned `PrecedentMapping`
only when the caller sets `has_precedent=true` — no real store read), and its publish/HITL/
research sinks are **all in-memory stubs** (`lambda_handler.py:494-496`,
`InMemoryHitlQueue`/`InMemoryResearchQueue`/`InMemoryPublishSink` — evaporate at the end of
the Lambda invocation). This design replaces both with real Aurora-backed behaviour.

**Data model** — new `scudo.agent_memory` table (in the `scudo` schema via
`aurora_store.py::ensure_schema`, NOT the legacy `public.scudo_agent_memory` bootstrap in
`projection_handler.py`, which stays untouched/deprecated since nothing ever used it):

Matches the sibling tables' actual style in `ensure_schema()` (`_ms bigint` epoch timestamps,
no NOT NULL constraints — all 7 existing `scudo.*` tables are permissive the same way), not
the dead legacy table's `TIMESTAMPTZ DEFAULT now()` style:

```sql
create table if not exists scudo.agent_memory (
  memory_key text primary key,   -- 'precedent:{vendor}:{vendor_product_ref}' | 'rule:{vendor}:{rule_id}'
  memory_type text,              -- 'precedent' | 'rule'
  updated_at_ms bigint,
  payload jsonb
)
```

**CONSULT** (live, synchronous, in-Lambda) — new `backend/scudo/aurora_memory.py::consult_priors(vendor, vendor_product_ref)`,
a plain SQL read (RDS Data API, same style as `aurora_store.py`), called from
`_build_bundle_assembler`'s `_assemble()` in place of the `has_precedent` flag. Returns the
real precedent (if any) + already-promoted rules for that vendor. No LLM involved.

**DISTILL — precedents** (live, synchronous, in-Lambda) — CORRECTED from the earlier draft:
`PublishSink.publish(self, *, named_graph, triples)` (`stubs.py`) only carries serialised RDF
triples, not vendor/confidence — the wrong hook to shoehorn precedent-writing into (would
require parsing triples back out, fragile). The real hook is `lambda_handler.py:503`, right
after `obj = orch.run(...)` returns a `MappingObject` (`outcome`, `mapping_result`,
`bundle_ref` already populated — verified by reading `orchestrator.py::_object()`). New
`aurora_memory.py::record_verified_precedent(vendor, vendor_product_ref, mapping_result)`,
called from `handler()` when `obj.outcome == Outcome.PUBLISHED`. `orchestrator.py` itself is
untouched — it stays narrowly protocol-based per its own stated design ("the LLM is in the
judgement path, never the routing or publish path"); `InMemoryPublishSink()` keeps doing
exactly what it does today (RDF-triple sink), unrelated to this. **Fails loud** — a lost
precedent write defeats the entire point, matching this session's `db.py` Aurora fail-fast
precedent.

**DISTILL — rules** (offline, nightly — explicitly out of scope for this slice, documented/
stubbed only) — a scheduled routine reusing `~/.claude/skills/self-improving-agent`'s existing
`distill_lessons.py` + `adversarial-verify.js` wholesale. Not buildable synchronously inside a
Lambda (the Workflow tool / subagent refuters only exist in an interactive session). This
slice adds a `# TODO(nightly-rule-distillation): ...` stub and the read-side of the contract
(CONSULT already reads `memory_type='rule'` rows) so wiring the routine later is additive.

**Error handling** — CONSULT read failure → fail-open (log + proceed with no priors, matching
`hydrate.py`'s "cold start is a WARN"). Precedent DISTILL write failure → fail loud.

## B — Rights/contract conceptual model (v1, PROVISIONAL)

The "top half" (Field→FieldGroup→DataDictionary→Distribution/DataService/DeliveryChannel→
Dataset/ProductPackage/DataTaxonomy) is already modelled 1:1 onto the CatalogueOntology
transcript in `scudo_mapping_mcp/models.py`'s `ConceptualNodeKind`/`ConceptualEdgeKind`. The
"bottom half" (Party, Contract, Policy, Duty, Permission, `ContentDeliveryModel`) has no
typed representation — only `rights_odrl.py`, a narrow untyped ODRL 2.2 evaluator with no
data model for parties/contracts/duties.

**Explicitly provisional**: only 3 of the reported ~11 `ContentDeliveryModel` values are
confirmed from source (`distributionService`, `redistributionService`, `displayService`).
This slice models only those 3, clearly commented as incomplete — inventing the remaining
~8 would bake wrong data into the codebase. The 5 new entity kinds and their edges ARE
grounded in the public, stable ODRL 2.2 spec structure (Policy contains Permission contains
Duty; Party is assigner/assignee of a Policy) which `rights_odrl.py` already partially
implements — not guessed.

New `ConceptualNodeKind` entries: `PARTY`, `CONTRACT`, `POLICY`, `DUTY`, `PERMISSION`.
New `ContentDeliveryModel(str, Enum)`: `DISTRIBUTION_SERVICE`, `REDISTRIBUTION_SERVICE`,
`DISPLAY_SERVICE` (provisional, incomplete — see comment).
New `ConceptualEdgeKind` entries: `PARTY_ROLE` (Party→Policy), `GRANTS` (Contract→Policy),
`HAS_PERMISSION` (Policy→Permission), `HAS_DUTY` (Permission→Duty).

Dashboard closed-vocabulary mapping (`backend/scudo/build_matching_graph.py`'s
`_CONCEPTUAL_NODE_TYPE`/`_CONCEPTUAL_EDGE_TYPE`, the same table fixed earlier this session
for the top half): `party→entity`, `contract→document`, `policy→config`, `duty→step`,
`permission→claim`; `party_role→related`, `grants→related`, `has_permission→contains`,
`has_duty→contains`.

Out of scope for this slice: persistence/projection wiring beyond the model + dashboard
mapping, and rewiring `rights_odrl.py`'s runtime evaluator onto the new typed model (a
separate, larger follow-on).

## C — Zone-aware agent tool

New `_system_context_text()` in `scudo_mapping_mcp/agent.py`: a short static description of
the 5-zone architecture plus guidance distinguishing catalogue/DCAT concepts (the top half)
from rights/contract concepts (the new bottom half from B), so an agent can recognise which
domain a field/term belongs to.

Delivery is asymmetric because the two backends have different shapes (verified this
session): `BedrockMappingAgent` runs a real Strands tool-calling loop, so it gets a new
`@tool describe_system_context()` returning the text, callable mid-reasoning.
`AzureMappingAgent` does one candidate lookup + one plain chat completion (no tool loop), so
it gets the same text pre-injected into its one-shot prompt. Same underlying text, two
delivery mechanisms.

## Testing

All three parts: hermetic, no real AWS/network. A: mirrors `test_db_connect.py`'s mocked
RDS Data API client pattern — CONSULT real-precedent/fail-open, DISTILL upsert/fail-loud. B:
extends `test_dashboard_enum_vocabulary.py` with the 5 new node kinds + 4 new edge kinds,
proving none fall back to the default "entity"/"related" mapping. C: Bedrock tool list
includes the new tool and returns the expected text; Azure's constructed prompt contains the
system-context text.

## D — SkillOpt-inspired matching skill memory (added 2026-07-07, post-review)

Ground truth verified before designing (not assumed): `microsoft/SkillOpt` is a real,
published project (PyPI `skillopt`, MIT, arXiv:2605.23904). Confirmed via its README, not
guessed. Its actual mechanism: **the skill document is the trainable state of a frozen
agent** — a separate optimizer model turns scored rollouts into bounded add/delete/replace
edits on a single skill doc (rollout → reflect → aggregate → select → update → evaluate); a
candidate edit is accepted only when it strictly improves a **held-out validation score**.
The deployed artifact is a compact `best_skill.md` (~300–2,000 tokens) that runs against the
unchanged target model with **zero inference-time model calls** — all optimization is
offline. `SkillOpt-Sleep` (v0.2.0) is the nightly variant: harvest → mine → replay →
consolidate, still behind the same held-out validation gate.

**Verified constraint**: the `skillopt` PyPI package is NOT installed and NOT vendored in
this repo (`pip show skillopt` → not found; not in any `requirements*.txt`). Per explicit
instruction, the live Lambda must not depend on it. This is not an awkward workaround —
SkillOpt's own deployment model already separates "trainable, offline, model-in-the-loop"
from "deployed artifact, plain text, zero inference-time calls," so a live path that only
ever reads a plain-text skill doc (never imports `skillopt` itself) is faithful to the
project's own architecture, not a simplification of it.

**Data model** — two new `memory_type='skill_doc'` rows in the SAME `scudo.agent_memory`
table from Part A (no new table): `memory_key='skill:matching:current'` (latest candidate,
may be unvalidated) and `memory_key='skill:matching:best'` (validated, deployment-ready —
the only one live agents ever read). Payload: `{skill_text, version, validation_score,
promoted_at}`.

**CONSULT (live, read-only)** — new `aurora_memory.consult_best_skill() -> Optional[dict]`,
same fail-open contract as `consult_priors` (Aurora unreachable/misconfigured/malformed →
`None`, never raises, never blocks a mapping request).

**Delivery — CORRECTED from an earlier draft that copied Part C's pattern without
re-verifying against the right pipeline.** Part D must target the SAME pipeline Part A
instruments (Orchestrator/Lambda), not Part C's pipeline (the streaming demo console) —
otherwise the loop wouldn't close: the agents that would receive the skill hint must be the
same ones whose verified outcomes are recorded as trajectories. Verified by reading
`orchestrator.py:193,202`: `mapping_prompt(bundle)` builds ONE prompt string, then
`self._structured_call(self.mapping, MappingResult, prompt)` calls the specialist —
BedrockMappingAgent/AzureMappingAgent's asymmetric tool-loop-vs-prompt distinction (Part C)
does NOT apply here: the Orchestrator's `mapping_specialist` is a bare Strands `Agent` with
NO `tools=` argument (`lambda_handler.py::_build_bedrock_agents`) and Azure's is
`AzureOpenAIShim` (`_build_azure_agents`) — both single-shot `structured_output(Model,
prompt)` calls, neither with a tool-calling loop. So BOTH get the SAME delivery: prompt
injection, via ONE shared field, not two mechanisms.

Concretely: new `skill_hint: Optional[str] = None` field on `BriefBundle` (`schemas.py`),
populated in `_build_bundle_assembler`'s `_assemble()` via `aurora_memory.consult_best_skill()`
— the exact same place (and pattern) `precedent` is already populated from
`consult_priors()`. `mapping_prompt(bundle)` (`.prompts`) surfaces `bundle.skill_hint` as a
clearly-labelled, prominent section when present (not merely relying on the bundle's already-
embedded `model_dump_json` dump, which would bury it as one JSON field among many —
SkillOpt's own premise is that the skill doc is a standalone instructional text, not
incidental data). `orchestrator.py` itself remains completely untouched, same as Part A.

**DISTILL — trajectory recording** — new `aurora_memory.record_trajectory(...)`, called
alongside `record_verified_precedent` from the same `_record_precedent_if_published` hook in
`lambda_handler.py` on a `Outcome.PUBLISHED` result. `memory_key='trajectory:{bundle_ref}'`,
`memory_type='trajectory'`, payload captures what an offline harvest step needs (vendor,
vendor_product_ref, target_iri, confidence, rationale, decided_at). Same fail-loud contract
as the precedent write (a lost trajectory silently starves the offline loop).

**Offline/nightly runner — documented stub, outside the Lambda entirely**: new
`backend/scudo/skillopt_sleep_runner.py`, never imported by `lambda_handler.py` (verified by
grep as part of testing). Documents the intended harvest (read `memory_type='trajectory'`
rows) → mine/replay/consolidate (real `skillopt-sleep` invocation — explicitly NOT
implemented here, since the package isn't vendored; a `# TODO(skillopt-integration)` marks
exactly where it plugs in) → **validation gate**, implemented as one small, genuinely
testable pure function: `should_promote(candidate_score, current_best_score) -> bool` —
`True` only if there's no current best yet, or the candidate strictly improves on it. This
mirrors Part A's already-deferred nightly-rule-distillation pattern; the gate logic is real
and tested even though the optimizer-model rollout loop itself is a documented stub.

**Testing** — hermetic, no real AWS/network, no `skillopt` import required anywhere in
tests: skill read miss (no `best` key) → `None`; hit → real text returned; Aurora error →
fail-open; `_assemble()` populates `skill_hint` from Aurora (mirrors the existing
`precedent`-from-`consult_priors` test); `mapping_prompt(bundle)` surfaces
`bundle.skill_hint` prominently when present, omits gracefully when `None` (pure-function
test, no Aurora/monkeypatching needed); `record_trajectory` issues the right parameterised
insert and is fail-loud; `should_promote` gate logic (no-prior-best / strict-improvement /
non-improvement / tie cases).

## E — Gap closure (added 2026-07-07, second pass)

Closes four gaps named in the prior report.

**E1 — `aurora_memory.promote_skill()`.** Encapsulates the whole gated-promotion operation
in one function (not a raw write): reads the current best via `consult_best_skill()`
(reused, fail-open — a read error is treated as "no current best," never raises), calls
`should_promote(candidate_score, current_best_score)`, and — only if it returns `True` —
performs the upsert to `skill:matching:best` (fail-loud, same unguarded-`_execute` pattern as
`record_verified_precedent`). Returns `bool` (whether it actually promoted), so callers don't
need to duplicate the gate check. Also adds `harvest_trajectories(limit=100)` (fail-open read
of all `memory_type='trajectory'` rows, newest first) — the HARVEST step's data source.

**E2 — `skillopt_sleep_runner.py` becomes a real, injectable offline orchestrator.**
`run_sleep_cycle(*, store, optimizer=None, evaluator=None, held_out_ratio=0.2)`: HARVEST via
`store.harvest_trajectories()` → deterministic `default_held_out_split()` (last
`ceil(n*ratio)` by harvest order, NOT random — reproducible in tests, no `random`/time-seeded
non-determinism) → MINE via `optimizer(train, current_skill_text)` → EVALUATE via
`evaluator(candidate_text, held_out)` → GATE+PROMOTE via `store.promote_skill(...)` (which
internally re-checks `should_promote`, so the runner doesn't duplicate that logic). `store`,
`optimizer`, `evaluator` are all injectable — tests pass fakes, no real Aurora/network/
`skillopt` import ever touched in tests. Default `optimizer` (only used when the caller
supplies none) is `_lazy_skillopt_optimizer`: imports the real `skillopt` package lazily
INSIDE the function body (never at module load, never triggered by anything
`lambda_handler.py` reaches — verified by the existing
`test_runner_module_is_never_imported_by_lambda_handler` test), and raises a clear
`RuntimeError` if the package isn't installed. Its actual "mine a candidate" call into
`skillopt`'s Python API is NOT implemented (still a `# TODO(skillopt-integration)` —
verified this session that only the CLI/README-level description of SkillOpt is known, not
its internal Python API surface; guessing that API's exact calls would be the same kind of
fabrication this whole gap-closure pass exists to avoid). This is honestly the one piece
still not "real" — everything else in the offline orchestrator (harvest, split, gate,
promote, injection seams) is.

**E3 — Fixture churn.** Root cause confirmed: `build_matching_graph.py::main()` hardcodes
writing to the module-level `_OUT`/`_META_OUT` constants (the tracked
`backend/scudo/fixtures/*.json`), and `build_knowledge_graph()` stamps a live
`datetime.now()` into `analyzedAt` — every test run through `main()` regenerates the tracked
fixture with a fresh timestamp. Fix is test-side only (production behavior of `main()` is
correct and untouched — a real dashboard sync SHOULD stamp a real build time):
`test_dashboard_enum_vocabulary.py`'s `_run_main_and_load()` now takes `tmp_path`/
`monkeypatch` pytest fixtures and monkeypatches `_OUT`/`_META_OUT` to a scratch path before
calling `main()`, so the tracked fixture is never written by any test.

**E4 — `ContentDeliveryModel` real-value search.** Searched the whole repo (`.md`/`.py`/
`.json`/`.txt`) and the Codex attachment directory the earlier pasted transcript came from —
confirmed no source for the remaining ~8 reported values exists anywhere accessible. Per
explicit instruction, not filling them in from guesswork. Instead: a new
`_CONTENT_DELIVERY_MODEL_SOURCES` citation map (test-side) requires every enum member to have
a documented source string, and fails loudly if a member is ever added without one — this
doesn't complete the enum, but makes it structurally impossible to silently guess a 4th value
in later without deliberately adding (and thus surfacing for review) a citation for it.

**tour-fix-step2.jpeg** — confirmed still present, untouched, unrelated to any part of this
work (pre-existing debris from an earlier session's dashboard-tour verification).

## F — Codex review gap closure (round 3, added 2026-07-08)

Closes eight items from a Codex review of the E1-E4 gap-closure round.

**F1 — Lambda/offline import-boundary fix.** Confirmed via a subprocess check
(`import scudo.lambda_handler; 'scudo.skillopt_sleep_runner' in sys.modules` → `True`
before the fix) that `aurora_memory.py` importing `should_promote` directly from
`skillopt_sleep_runner.py` meant the live Lambda TRANSITIVELY loaded the offline runner
module, contradicting "never imported by lambda_handler.py." Fixed: `should_promote` now
lives in a new, deliberately tiny, neutral module `backend/scudo/skill_gate.py` (only
`from __future__`/`typing` imports allowed — enforced by
`test_skill_gate.py::test_skill_gate_module_stays_neutral`, which AST-parses the file
rather than trusting a comment). `aurora_memory.py` imports from `.skill_gate` instead.
`skillopt_sleep_runner.py` no longer defines `should_promote` at all (nothing in it called
it directly — only `aurora_memory.promote_skill()` did). New
`test_lambda_handler_import_leaves_skillopt_sleep_runner_unloaded` proves the fix via a real
subprocess import (not just grepping import lines, which the ORIGINAL
`test_runner_module_is_never_imported_by_lambda_handler` test already did and was
insufficient against transitive imports).

**F2 — Real SkillOpt wiring, as far as safely possible.** Re-verified microsoft/SkillOpt
directly from source this round (`gh api repos/microsoft/SkillOpt/contents/...`), going
further than the earlier README-level pass: confirmed a REAL installed CLI entry point
(`pyproject.toml`: `skillopt-sleep = "skillopt_sleep.__main__:main"`), its exact
subcommands/flags (`skillopt_sleep/__main__.py`: `run`/`dry-run`/`status`/`adopt`/`harvest`/
`schedule`/`unschedule`, `--tasks-file`, `--target-skill-path`, `--project`, `--backend
mock|claude|codex|copilot`, `--json`), its `TaskRecord` field schema
(`skillopt_sleep/types.py`), its tasks-file payload shape
(`skillopt_sleep/tasks_file.py::make_tasks_payload`), and its `dry-run --json` report shape
(`_report_payload`). New `backend/scudo/skillopt_adapter.py` (never imported by
`lambda_handler.py`, same transitive-import test pattern as F1) provides:
`find_skillopt_sleep_binary()` (checks PATH, injectable), `trajectory_to_task_record()` +
`write_tasks_file()` (map a SCUDO trajectory onto the VERIFIED TaskRecord/tasks-file schema —
every field's derivation is documented inline, not fabricated), and
`run_skillopt_sleep_dry_run()` (shells out to the real CLI with verified flags, parses the
verified JSON report shape, raises `SkillOptSleepUnavailableError` — a clear, typed error —
when the binary isn't installed). Honest remaining gap, clearly documented in the module's
own docstring rather than glossed over: whether SCUDO's vendor-mapping trajectories are a
MEANINGFULLY SCORABLE signal for SkillOpt-Sleep's replay/judge machinery
(`skillopt_sleep/replay.py`, `judges.py`, which appear built around coding-session
transcripts scored against a reference/rubric) is a domain-fit question, not a technical
unknown — SCUDO's trajectories carry `reference_kind="none"`. The Python API surface
(`import skillopt_sleep` as a library) remains unverified; only the CLI and its two JSON
schemas were confirmed.

**F3 — Scheduler/job wrapper.** New `backend/scudo/scripts/run_sleep_cycle_job.py`
(following the exact pattern of the existing `scripts/cleanup_stale_cdao.py`: dry-run by
default, `--apply` to actually write, lazy imports of heavy/live deps inside `main()`, pure
helper functions tested directly rather than `main()` itself). Validates the 3 required
Aurora env vars (`SCUDO_AURORA_CLUSTER_ARN`/`SCUDO_AURORA_SECRET_ARN`/
`SCUDO_AURORA_DATABASE_NAME`, the same ones `aurora_store._execute` already requires) before
doing anything. Calls `run_sleep_cycle(store=..., held_out_ratio=...)` against the REAL
`aurora_memory` module (which already satisfies the `store` protocol directly — a module's
functions are attribute-accessible exactly like an object's methods, no wrapper class
needed for the real path). In dry-run mode, wraps the real store so reads pass through
unchanged (already fail-open) but `promote_skill` reports the `skill_gate.should_promote`
decision without ever writing. Actual EventBridge/cron infra deployment is explicitly out of
scope (see below) — the module docstring documents the intended invocation
(`python -m scudo.scripts.run_sleep_cycle_job --apply` on whatever schedule/host runs it).

**F4 — Live Aurora smoke/diagnostic.** New `backend/scudo/scripts/aurora_smoke.py`. Same env
validation as F3; SKIPS clearly (exit 77, the conventional Automake/CI "test skipped" code —
distinct from both 0/success and 1/failure) when Aurora env vars are absent, since that's the
expected state for local/CI runs, not a diagnostic failure. Read-only by default (`SELECT 1
FROM scudo.agent_memory LIMIT 1`); the only write path (`--write-smoke-test`, off by default)
round-trips ONE throwaway row (insert → read back → delete) and leaves no residue. Does NOT
attempt to classify IAM-denied vs. schema-missing errors by matching specific error-message
substrings — no real RDS Data API error was ever captured this session to verify such
patterns against, so the raw exception is surfaced as-is for a human to read, rather than a
fabricated classification.

**F5 — Stale docs/comments.** `skillopt_sleep_runner.py`'s module docstring no longer says
`promote_skill`/`run_sleep_cycle` are "not built" (they were, in the E1/E2 round, but the
docstring hadn't been updated since) — rewritten to describe the actual current STATUS: real
injectable orchestrator, only the default optimizer/evaluator's actual skillopt-API call
remains a documented gap, with `scudo.skillopt_adapter` now the verified alternative. This
Part F section (and the missing plan doc, F6) are the rest of that cleanup.

**F6 — Missing Superpowers plan doc.** Added
`docs/superpowers/plans/2026-07-07-aurora-memory-rights-model-zone-tool-design-plan.md` —
the brainstorming skill's process was followed for the design (this spec), but the
follow-on `writing-plans` step was skipped for Parts A-E at the time; this plan doc is
written after the fact, describing the tasks as they were actually implemented and tested,
so the spec/plan/implementation trio is complete for this design doc (matching the E2E
work's spec+plan pairing).

**F7 — Rights-model status, still not guessed.** Re-confirmed (again) that no source exists
anywhere accessible for the remaining ~8 reported `ContentDeliveryModel` values — the E4
citation-map guard from the prior round already enforces this structurally. No new enum
values added. See `test_rights_contract_model.py`'s `_CONTENT_DELIVERY_MODEL_SOURCES` map.

**F8 — File-grouping report.** Delivered in the final report for this round, not in this
doc — grouping every changed/untracked file into SkillOpt/Aurora (this work),
E2E/frontend-URL-ingest (the prior round's work), and unrelated
(`tour-fix-step2.jpeg`, confirmed still present/untouched).

## Out of scope (this slice)

- Nightly rule-distillation routine itself (stub/TODO only, per A).
- The `skillopt` Python API surface itself — per F2, the CLI is now verified and adapted as
  far as safely possible; the library API (`import skillopt_sleep`) remains unverified.
- Reading a candidate skill doc's actual TEXT back out of `run_skillopt_sleep_dry_run`'s
  `staging_dir` (per F2 — the exact staged-file naming convention inside that directory was
  not verified this session; only the report JSON's own fields were confirmed).
- Whether SCUDO's vendor-mapping trajectories are a scorable signal for SkillOpt-Sleep's
  judge/replay machinery (per F2 — a domain-fit question, not a technical one).
- Actual EventBridge/cron infrastructure deployment for the scheduler (per F3 — the job
  script and its documented invocation exist; wiring real AWS scheduling infra is out of
  scope, consistent with the rest of this repo's Aurora work).
- Rewiring `rights_odrl.py`'s evaluator onto the new typed model.
- Any change to the Flask/streaming pipeline (`scudo_mapping_mcp.store`'s `RetrievalStore`
  family) — this slice is scoped to the Lambda/Orchestrator pipeline only.
- Filling in the remaining ~8 `ContentDeliveryModel` values (per E4/F7 — no source found;
  guarded, not guessed).
