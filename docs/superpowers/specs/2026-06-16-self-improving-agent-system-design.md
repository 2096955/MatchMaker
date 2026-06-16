# Self-Improving Orchestrator — Design Spec

**Date:** 2026-06-16 · **Status:** approved (design) → pending spec review

## Intent

Give the SCUDO orchestrator **self-improving memory** so it gets better at mapping
vendor products → CDAO nodes the more it runs — across **new vendors** (NEW_MAPPING) and
**old vendors** (EXTEND / RECONCILE). The model stays fixed; the environment around it
compounds: every verified mapping leaves a precedent, every recurring defect becomes a
rule, and the next run consults those priors instead of re-deriving them.

The principles below are model-agnostic. They run today on Opus 4.8 (no Fable 5), against
the in-memory store (`STORE_BACKEND=memory`), and document the live (FalkorDB/Neptune/CMA)
upgrade path.

## What already exists vs. the gap

SCUDO already has most of the substrate — the layer plugs into it, never beside it:

| Already there | Where |
|---|---|
| Independent, sealed verifier (not self-critique) | `verdict.py` HMAC seal; Match & Verify MCP; 10-dim rubric |
| Precedent memory from HITL decisions | `feedback.py` → `store.upsert_precedent` (approve/override/reject) |
| Compounding rank signal consulted on later runs | `store.rank_signals_for(vendor_signature)` → `find_similar_products` |
| Resume-don't-restart | `hydrate.py` replays the canonical bundle at boot |
| Deterministic goal gate | `0.80` floor + bounded retry (12–15) + HITL escalation |

**The gap (what this builds):**
1. Learn from the orchestrator's **own verified** outcomes, not only HITL decisions.
2. **Distill general rules** — per-vendor quirks, per-subtree mapping rules, recurring
   rubric defects — above the level of single precedent edges.
3. **Consult** those rules as priors *before* mapping (today only the rank signal is read).
4. **Verified-facts-only** promotion: an independent grader must confirm a candidate rule
   before it enters durable memory (honors the "one memory system / verified facts" rule).
5. **Scheduled compounding**: a nightly routine re-runs the eval suite and distills.

## Model routing ("Opus where possible")

| Role | Model | Effort |
|---|---|---|
| Orchestrator / synthesis | Opus 4.8 | medium–high (not max — thinking bills as output) |
| Heavy bounded subtask (reconcile, gnarly map) | Opus 4.8 | high |
| Fan-out workers (scaffold, eval rows, docs) | Sonnet 4.6 | low–med |
| Grader / rule-refuter sub-agents | Haiku 4.5 | low |
| Classifier-blocked domain | surface to human (no Fable→Opus fallback to lean on) | — |

## Deliverable: portable skill + SCUDO integration

```
~/.claude/skills/self-improving-agent/
├── SKILL.md                  # when/how to run a compounding loop; read-at-start / write-before-exit
├── references/
│   ├── compound-stack.md     # 4 layers, self-improving≠self-learning (principles, no lore)
│   ├── model-routing.md      # the table above + classifier fallback
│   ├── memory-protocol.md    # 5-stage Fail→Investigate→Verify→Distill→Consult, mapped to SCUDO stores
│   └── verifier-pattern.md   # independent verifier > self-critique; applied to rule promotion
├── workflows/                # invoked via Workflow({scriptPath}); each opens with `export const meta`
│   ├── goal-loop.js          # maker → independent grader → iterate until pass
│   ├── adversarial-verify.js # N Haiku skeptics refute a candidate rule; majority-refute kills it
│   ├── fan-out-synthesize.js # split → parallel Sonnet → Opus synthesize
│   └── loop-until-done.js    # loop-until-dry (K empty rounds = stop)
├── scripts/
│   ├── distill_lessons.py    # run-log → candidate rules → (refuted?) → write skill + MEMORY.md + bundle
│   ├── install_routine.py    # CronCreate nightly compounding job
│   └── vision_verify.py      # Playwright screenshot vs goal (for MappingDemo UI checks; optional)
├── assets/
│   ├── MEMORY.template.md            # harness-memory index seed
│   ├── STATE.template.md             # portable fallback ONLY (projects without harness memory)
│   └── compounding-skill.template.md # a skill that accrues failure-modes/anti-patterns
├── examples/scudo-mapping/   # the worked example — runs against STORE_BACKEND=memory
│   ├── run.md                # end-to-end: consult → map → verify → distill → re-consult
│   ├── eval/mapping-cases.jsonl       # vendor products + expected CDAO nodes (new + old vendors)
│   └── mapping_loop.py       # drives one loop iteration over the in-memory store seams
└── cloud/
    ├── outcomes-rubric.example.md     # CMA Outcomes mirror of the publish gate
    └── routine.example.md             # CMA Routine (schedule / GitHub / API triggers)
```

## The closed loop (concrete to vendor mapping)

```
CONSULT   read priors: store.rank_signals_for(vendor_signature) + distilled rules
          (per-vendor / per-subtree) from taxonomy-mapping skill + MEMORY.md
   │
MAP       mapping specialist (Sonnet/Opus) → MappingResult (candidate CDAO node + confidence)
   │
VERIFY    independent verifier → 10-dim rubric; publish gate (0.80 floor / retry / HITL)
   │      verdict sealed (verdict.py). NO self-critique.
DISTILL   on a VERIFIED outcome (auto-pass OR HITL approve/override):
   │        a) store.upsert_precedent(...)            # existing edge + rank signal
   │        b) propose a general rule → adversarial-verify.js (Haiku refuters)
   │           → only if it survives, write to taxonomy-mapping SKILL + MEMORY.md + canonical bundle
COMPOUND  next run's CONSULT now sees the new precedent AND the new rule
   │
ROUTINE   nightly: re-run eval/mapping-cases.jsonl; newly-failing → investigate+distill;
          newly-passing → promote the rule; update skill + MEMORY.md; post digest
```

Two non-negotiable habits enforced in `SKILL.md`: **read priors at start**, **write verified
lessons before exit**. No write = next run restarts from zero.

## Memory integration (resolves the CLAUDE.md "one memory system" rule)

No parallel STATE.md in this repo. The article's STATE.md sections map onto what SCUDO and
the harness already own:

| Article section | Lands in |
|---|---|
| verified facts (precedents) | `store.upsert_precedent` → FalkorDB/memory store + canonical bundle (survives via `hydrate`) |
| general rules / lessons | `taxonomy-mapping` skill (procedural, cross-project) + harness `MEMORY.md` (project facts) |
| open failures | harness `MEMORY.md` `type: project` files, deleted when resolved |
| last-session pointer | `MEMORY.md` project file |

`STATE.template.md` ships only as a portable fallback for projects with no harness memory.

## Build approach — dogfood via Workflow

Build the system by running the pattern it teaches. One dynamic Workflow, `pipeline()` over
~10 component groups: each **drafted by a Sonnet worker** → **adversarially verified by a
Haiku grader** against the acceptance criteria → **Opus integrates** and fixes gaps. No
worktree isolation needed (writers target disjoint files; documented as the escape hatch).
Workflow runtime rules the implementer must respect: each `.js` opens with a pure-literal
`export const meta`; no `Date.now()`/`Math.random()`/argless `new Date()`; plain JS.

### Acceptance criteria (graders check)
1. **Coverage:** every principle below has its mapped artifact present.
2. **Workflows parse:** each `.js` has a valid `meta` literal and passes `node --check`.
3. **Scripts run:** each `.py` answers `--help`; `distill_lessons.py` + `install_routine.py`
   + `vision_verify.py` support `--dry-run`.
4. **Example runs:** `examples/scudo-mapping/mapping_loop.py` completes one CONSULT→MAP→
   VERIFY→DISTILL iteration against `STORE_BACKEND=memory`, no network, and a re-CONSULT
   shows the new precedent/rule is visible.
5. **Routing honored:** Opus orchestrator/heavy, Sonnet workers, Haiku graders, classifier
   → surface-to-human, documented in `model-routing.md`.
6. **Memory rule honored:** nothing creates a competing STATE.md in this repo; writes go to
   the store + `MEMORY.md` + skill.
7. **Verified-facts-only:** no rule reaches durable memory without surviving the refuter pass.
8. **Honesty:** every limitation (no live CMA/Neptune, Playwright needs a target, classifier
   fallback gap) is stated in-skill.

### Post-build smoke test
`node --check` each workflow; `--help`/`--dry-run` each script; run one `mapping_loop.py`
iteration and confirm the re-consult sees the written precedent.

## Principle → artifact (coverage contract, no step dropped)

| # | Principle | Artifact / where |
|---|---|---|
| 1 | long-horizon autonomy = resume not restart | `hydrate.py` (existing) + resumable Workflow + cron |
| 2 | self-improving ≠ self-learning | `references/compound-stack.md` |
| 3 | compound stack (4 layers) | skill structure + `compound-stack.md` |
| 4 | model routing | `references/model-routing.md` |
| 5 | goal loop w/ independent grader | `workflows/goal-loop.js` + publish gate; `cloud/outcomes-rubric.example.md` |
| 6 | verifier > self-critique | `verdict.py` (existing) + `references/verifier-pattern.md` + `adversarial-verify.js` |
| 7 | dynamic workflows | `fan-out-synthesize.js`, `adversarial-verify.js`, `loop-until-done.js` |
| 8 | worktrees for parallel safety | `SKILL.md` + `isolation:'worktree'` in workflows |
| 9 | routines (laptop-off) | `scripts/install_routine.py` + `cloud/routine.example.md` |
| 10 | 5-stage memory | `references/memory-protocol.md` + `scripts/distill_lessons.py` |
| 11 | the state file | store precedents + canonical bundle + `MEMORY.md` (no parallel STATE.md) |
| 12 | skills that compound | `taxonomy-mapping` skill accrual + `assets/compounding-skill.template.md` |
| 13 | vision self-verify | `scripts/vision_verify.py` (MappingDemo UI; optional) |
| 14 | safety boundary | classifier → surface-to-human; in `model-routing.md` |

## Risks & limits (stated, not hidden)
- No live CMA / Neptune / Bedrock here — cloud artifacts are validated by shape; the loop runs
  against the in-memory store only.
- Classifier-blocked domains have no Fable→Opus fallback under Opus-top-tier → surface to human.
- Rule distillation is only as good as the refuter pass; a wrong rule in memory pollutes every
  later run, so the refuter defaults to "reject if uncertain."
- `vision_verify.py` needs a renderable target; degrades to a clear "no target" message.

## Out of scope (v1)
- Rewriting SCUDO backend modules — v1 wraps the existing seams; live wiring is a follow-on.
- Multi-project memory federation; loop dashboard/GUI.
