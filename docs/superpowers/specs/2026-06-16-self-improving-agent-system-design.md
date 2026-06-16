# Self-Improving Agent System — Design Spec

**Date:** 2026-06-16
**Status:** Approved (brainstorming) → pending spec review
**Author:** Claude Code (Opus 4.8) as ARB lead
**Source:** Codez "14-step roadmap" thread + follow-up corrections, adapted to a local, Opus-routed stack (no Fable 5 available).

---

## 1. Problem & intent

Build a *system that compounds*, not a faster chat tool. The model is stateless; the
environment around it must get sharper run-over-run. The article frames this as a
4-layer "compound stack" (primitives → orchestration → memory → self-improvement)
with one feedback loop: every output is graded by an independent verifier, distilled
into a rule, and written back to memory so tomorrow's run resumes instead of restarts.

This spec adapts that to **what is runnable today on this machine** with Opus 4.8 as the
top tier, and documents the cloud (CMA / Outcomes / Routines) upgrade path without
depending on it.

### Goals
- A **portable skill** `~/.claude/skills/self-improving-agent/` that any project can use.
- Every one of the 14 article steps lands as a **concrete, runnable artifact** (script,
  workflow, template, or reference) — not prose.
- A **closed compound loop**: scaffold → maker → independent verifier → distill → memory
  write-back → scheduled re-run.
- One **worked example** (generic CI-triage) that exercises every layer end-to-end.
- **Honest fidelity:** local equivalents that run now, plus documented cloud upgrade.

### Non-goals
- Self-*learning* (weight updates / RSI). Out of scope; no production model does this.
- Standing up a parallel project memory store. See §6 — MatchMaker uses the existing
  harness `MEMORY.md`.
- Re-implementing primitives the harness already provides (sub-agents, worktrees, cron,
  the Workflow tool, Playwright). We compose them.

---

## 2. Model routing ("Opus 4.8 where possible")

No Fable 5. The orchestrator role collapses onto Opus; the rest of the matrix stands.

| Role | Model | Effort | Rationale |
|---|---|---|---|
| Orchestrator / loop driver / synthesis | Opus 4.8 | medium–high | planning & routing; **not** max — thinking bills as output tokens |
| Heavy bounded subtask (architecture, gnarly debug) | Opus 4.8 | high | clean context window; deep thinking pays off here |
| Fan-out workers (scaffold, refactor, docs, tests) | Sonnet 4.6 | low–medium | high-volume, cheap |
| Grader / verifier sub-agents | Haiku 4.5 | low | independent context, adversarial, cheap |
| Classifier-blocked domains (cyber/bio/chem/distillation) | **surface to human** | — | no Fable→Opus fallback to lean on; never fail silently |

Key correction from the thread, encoded in `references/model-routing.md`: **do not** put max
reasoning on the orchestrator. Reserve extended thinking for delegated Opus phases where a
bounded hard subtask gets a fresh context window.

---

## 3. Architecture — skill layout mirrors the 4 layers

The skill *is* the system. Directory layout maps 1:1 to the compound stack.

```
~/.claude/skills/self-improving-agent/
├── SKILL.md                      # orchestrator playbook: when/how to run a compounding loop
├── references/
│   ├── compound-stack.md         # steps 01–03
│   ├── model-routing.md          # steps 04, 14
│   ├── memory-protocol.md        # steps 10–12
│   ├── verifier-pattern.md       # step 06
│   └── safety-boundary.md        # step 14
├── workflows/                    # invoked via Workflow({scriptPath}); each begins with `export const meta`
│   ├── goal-loop.js              # step 05
│   ├── adversarial-verify.js     # steps 06–07
│   ├── fan-out-synthesize.js     # step 07
│   └── loop-until-done.js        # step 07
├── scripts/
│   ├── new_loop.py               # step 11
│   ├── distill_lessons.py        # steps 10, 12
│   ├── vision_verify.py          # step 13
│   └── install_routine.py        # step 09
├── assets/
│   ├── STATE.template.md         # portable state file (projects WITHOUT harness memory)
│   ├── MEMORY.template.md        # harness-memory index seed
│   └── compounding-skill.template.md   # step 12
├── examples/ci-triage/
│   ├── ci-triage.skill.md
│   ├── eval/ci-triage-cases.jsonl
│   ├── STATE.md
│   └── run.md
└── cloud/
    ├── outcomes-rubric.example.md      # step 05 (CMA)
    └── routine.example.md              # step 09 (CMA)
```

---

## 4. The 14 steps → artifact map (coverage contract)

No step is dropped. Each maps to a file that ships in the skill.

| # | Article step | Artifact |
|---|---|---|
| 01 | Mythos / days-long autonomy | `references/compound-stack.md` — maps "days-long" to local resumable Workflow + cron |
| 02 | self-improving ≠ self-learning | `references/compound-stack.md` |
| 03 | compound stack (4 layers) | `references/compound-stack.md` + the whole skill structure |
| 04 | model routing matrix | `references/model-routing.md` |
| 05 | /goal vs Outcomes | `workflows/goal-loop.js` (local) + `cloud/outcomes-rubric.example.md` |
| 06 | verifier > self-critique | `references/verifier-pattern.md` + `workflows/adversarial-verify.js` |
| 07 | dynamic workflows (fan-out, adversarial, loop-until-done) | `workflows/fan-out-synthesize.js`, `adversarial-verify.js`, `loop-until-done.js` |
| 08 | worktrees for parallel safety | `SKILL.md` guidance + `isolation: 'worktree'` in workflows + note in `verifier-pattern.md` |
| 09 | Routines (laptop-off) | `scripts/install_routine.py` (CronCreate) + `cloud/routine.example.md` |
| 10 | 5-stage memory progression | `references/memory-protocol.md` + `scripts/distill_lessons.py` |
| 11 | the state file | `assets/STATE.template.md` + `scripts/new_loop.py` (writes to MEMORY.md on this project) |
| 12 | skills that compound | `assets/compounding-skill.template.md` + `distill_lessons.py` writes back to the skill |
| 13 | vision self-verify | `scripts/vision_verify.py` (Playwright screenshot → vision compare) |
| 14 | Mythos safety boundary | `references/safety-boundary.md` + routing fallback in `model-routing.md` |

---

## 5. The closed loop (what makes it compound)

```
new_loop.py            seeds a project: eval/ dir, memory wiring, a goal statement
   │
goal-loop.js           maker (Sonnet/Opus) produces an artifact toward the goal
   │
adversarial-verify.js  N independent Haiku skeptics grade it (NO self-critique);
   │                   majority-refute kills the finding
distill_lessons.py     confirmed lessons → 5-stage Fail→Investigate→Verify→Distill→Consult
   │                   → verified facts/rules written to MEMORY.md + compounding skill sharpened
install_routine.py     schedules the loop laptop-off (cron); next run reads MEMORY.md FIRST
```

Vision tasks insert `vision_verify.py` into the grader step (screenshot vs goal/design tokens).

The loop's two non-negotiable habits (from steps 11–12), enforced in `SKILL.md`:
1. **Read at start** — every run opens by reading `MEMORY.md` + relevant compounding skill.
2. **Write before walking away** — every run ends by writing what was tried / passed / failed
   and any new *verified* rule. No write = next run restarts from zero.

---

## 6. Memory reconciliation (resolves a CLAUDE.md conflict)

The user's global `CLAUDE.md` states: *"One memory system. MEMORY.md is the single source of
truth — don't stand up a parallel store (STATE.md, etc.)."* The article's step 11 is STATE.md.

**Resolution:**
- For **this project (MatchMaker)** and any harness-memory project, the loop writes the
  article's STATE.md *sections* into the harness memory:
  - verified facts → `type: project` memory files
  - general rules / lessons → `type: feedback` or `project` files with **Why/How to apply**
  - open failures → `type: project` "open" files, deleted when resolved
  - last-session pointer → a `project` memory file
  - all indexed in `MEMORY.md`.
- `assets/STATE.template.md` ships **only as a portable fallback** for projects that do not
  use the harness memory system. It is never created inside MatchMaker.

This honors the single-source-of-truth rule while preserving the article's 5-stage discipline.

---

## 7. Build approach — dogfood via Workflow

The system is built by running the pattern it teaches (the chosen "dogfood" method).

- One dynamic **Workflow** (`pipeline()`), ~10 component groups (references, workflows,
  scripts, assets, example, cloud, SKILL.md).
- Each group: **drafted by a Sonnet worker** → **adversarially verified by a Haiku grader**
  against explicit acceptance criteria → **Opus synthesizes/integrates** and fixes gaps.
- Worktree isolation **not** needed: writers target disjoint files. (Documented as the
  escape hatch if that ever changes.)
- Workflow runtime constraints the implementer must respect: each `.js` begins with a pure-
  literal `export const meta`; no `Date.now()`/`Math.random()`/argless `new Date()`; plain
  JS (no TS); `agent()/parallel()/pipeline()` only.

### Acceptance criteria (graders check these)
1. **Coverage:** all 14 steps have their mapped artifact present (§4 table).
2. **Workflows valid:** each `.js` begins with a valid `meta` literal and parses under Node.
3. **Scripts run:** each `.py` responds to `--help`; `distill_lessons.py`, `vision_verify.py`,
   `install_routine.py` support `--dry-run`.
4. **Example runs:** `examples/ci-triage` completes one loop iteration (maker → verify →
   distill to a local STATE.md) without network.
5. **Routing honored:** orchestrator/heavy = Opus, workers = Sonnet, graders = Haiku,
   classifier-blocked = surface-to-human, documented in `model-routing.md`.
6. **Memory rule honored:** nothing creates a competing STATE.md in a harness-memory project.
7. **Honesty:** every known limitation (no live CMA, Playwright bot-blocks, classifier
   fallback gap) is stated in-skill, not hidden.

### Post-build smoke test (run by orchestrator, not graders)
- `node --check` (or equivalent parse) on each workflow `.js`.
- `python <script> --help` / `--dry-run` on each script.
- Execute one `ci-triage` loop iteration; confirm a lesson is written to the example STATE.md.

---

## 8. Local ↔ cloud fidelity

| Primitive | Local (ships, runnable now) | Cloud (documented upgrade) |
|---|---|---|
| Goal loop (step 05) | `goal-loop.js` via Workflow tool | CMA **Outcomes** rubric (`cloud/outcomes-rubric.example.md`) |
| Dynamic workflows (07) | Workflow tool `agent/parallel/pipeline` | same, hosted in CMA |
| Routines (09) | `install_routine.py` → CronCreate | CMA **Routine** triggers: schedule/API/GitHub (`cloud/routine.example.md`) |
| Days-long autonomy (01) | resumable Workflow + cron heartbeat | CMA sandbox sessions |
| Vision verify (13) | `vision_verify.py` (Playwright) | same |

---

## 9. Risks & honest limitations
- **No live CMA / Routines / Outcomes** on this machine — cloud artifacts are config
  examples, validated by shape, not executed.
- **Classifier fallback gap:** the article relies on Fable→Opus auto-fallback. With Opus as
  top tier there is nothing below to fall to for blocked domains, so the system **surfaces to
  a human** rather than pretending to handle it.
- **Vision verify** needs Playwright + a renderable target; the script degrades to a clear
  "no target" message rather than failing opaquely.
- **Dogfood cost:** the build fans out many sub-agents. Acceptable under the session's
  ultracode directive; flagged so it is a choice, not a surprise.

---

## 10. Out of scope / YAGNI
- No MatchMaker-specific eval cases in v1 (worked example is generic CI-triage).
- No GUI / dashboard for the loop.
- No multi-project memory federation.
