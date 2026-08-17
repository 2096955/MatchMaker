# The Gauntlet Loop, parameterised

A build method for an agent starting a **new project from nothing**. It combines
three published ideas and one correction derived from measuring a real codebase
that was built without them.

You are assumed to know nothing about the target domain. Every domain-specific
value in this document is a `${PARAMETER}` you must fill by asking a human, or
by executing something — never by inference.

## Assumptions and portability

The *domain* is parameterised — that is what the title claims and it holds. The
*environment* is not, and nothing above said so. Stated plainly (2026-08-17),
the method as written presumes:

- **A POSIX shell.** The templates below invoke `bash`. Any runner that returns a
  process exit code works — `make`, `just`, `npm test`, a PowerShell script — but
  you must translate the literal commands yourself.
- **A version-control system with a cheap "what changed" query.** §1's
  measurements and §5's "re-run on the committed tree" rule use Git commands
  directly; the requirement is a committed-vs-working-tree distinction, not Git
  specifically.
- **A runnable check script and a place to put it.** `.finder/` is a convention,
  not a contract. Rename the directory if your repo has a house layout; keep the
  three artifacts and their roles.
- **A repo the agent can read and write.**

**SCUDO and MatchMaker references throughout are worked examples, not
requirements.** They are the evidence base — each one is a measured failure from
a real codebase, cited so the rule attached to it can be checked rather than
believed. Nothing about the method needs that codebase, that domain, or that
language. Keep the rules; substitute your own `${PARAMETER}` values.

**Citations name their repository.** Any path in this document that lives outside
MatchMaker/SCUDO is prefixed with its repo, because a reader who greps the wrong
tree and finds nothing will reasonably conclude the example was invented — which
is what happened to one such citation on review.

---

## 0. The three inputs

| Source | What it contributes |
|---|---|
| **Gauntlet Loop** (somethingbig.ai) | Lead decomposes → builders build → *independent* critics A/B the output against a reference bar → iterate. Core rule: **never let the builder grade itself.** |
| **Claude-of-Duty** (mshumer) | The evidence it works at scale (~55k LOC, 11 subsystems) and its own honest limit: *"The goal was to match a modern Call of Duty. It does not."* Lesson: **sequential single-owner passes beat parallel fan-out.** |
| **Wayfinder** (mattpocock), as modified | Planning-first. A persistent map of decisions + fog-of-war. Modified to emit a **small fixed artifact set** instead of one-per-decision: a `map`, a **binary answer key**, and the **runner** that executes the key (§3). The answer key replaces the reference product as the critic's source of truth. |

The modification is the important part, and the first draft of this document
overstated it. A vanilla Gauntlet Loop needs an external artifact to A/B
against — a Call of Duty frame, a competitor's website. For a novel application
there is no such artifact.

**The answer key manufactures executability, not externality.** A reference
product does two jobs: it *verifies* (does my output match?) and it *anchors*
(the target was not mine to invent). Executing the key buys back the first
only. A self-authored gate can run, exit 0, discriminate correctly under a
planted defect, and still assert a spec you made up. See §6.5 — that is not a
hypothetical, it is what the first cold run of this document produced.

---

## 1. The failure this method exists to prevent

Measured on a real enterprise codebase (SCUDO) built over ~9 months *without*
this method, by capable agents under human review. Scale: 83,387 LOC across
570 tracked files at commit `a0cc72d`, measured with
`git ls-files '*.py' '*.ts' '*.tsx' '*.js' '*.jsx' | xargs wc -l`.

| Symptom | Measurement |
|---|---|
| A false claim about the system's own behaviour | lived in 6 document surfaces for ~9 days |
| Review passes that specified a detection gate | 3 |
| Review passes that **ran** it | 0 |
| What running it took / found | one command / 3 more false claims |
| Two of those false claims | were docstrings the review had quoted **as its own evidence** |
| Suite under the config the launchers actually ship | 15 failed, 602 passed (clean: 617 passed) |
| A 24/24 "no brand on screen" gate | passed on a **planted** defect |
| A gate declaring itself "the ONE place brand tokens are defined" | **zero importers**; both gates that drifted still hand-roll private lists |
| A test docstringed "Mirrors the upstream z.enum" | reads a hard-coded copy; **0** files open the upstream schema |

Rows 1–6 are **reading** failures: execution prevents each one, and they
dominated in practice. That is why this method sits on the execution axis.

**Rows 7–9 are not, and they are the ones to take seriously.** Each of those
gates *executed*, had an exit code, and was green. They failed because the
thing being asserted was authored by the same hand as the build. Execution has
nothing to say about them. §6.5 makes this a first-class limit rather than a
footnote.

---

## 2. Phase A — Grill (human required, agent must not proceed alone)

Run the grilling interview. Target ~30–40 questions. You are extracting what no
amount of repo-reading or web search can produce.

Ask until you can fill every row:

```
${PROJECT_NAME}          ${WHO_IT_IS_FOR}        ${WHAT_DONE_MEANS}
${IN_SCOPE}              ${EXPLICITLY_OUT_OF_SCOPE}
${THRESHOLDS}            — every number, and WHO OWNS each one
${CLOSED_VOCABULARIES}   — enums whose members come from outside
${AUTHORITY_MAP}         — for each decision: who can actually approve it
${GROUND_TRUTH_SOURCE}   — where labelled examples come from, and WHO HOLDS THEM
${SHIPPED_CONFIG}        — the env/flags the product actually runs under
${WHAT_GOES_WRONG}       — failure modes once real people use it
```

**Four rules, each earned from a specific observed failure:**

1. **The agent never answers its own grilling question.** A grilling agent that
   answers itself has broken the method.
2. **Record the owner, not just the value.** "0.80/0.70" is worth little;
   "0.80/0.70, owned by ${NAME}, movable only by ${FORUM}" is a control. In
   SCUDO the calibration harness can compute a better threshold and is
   explicitly forbidden from applying it — that constraint is invisible in the
   number alone.
3. **Some decisions have no reachable owner. Mark them, don't clear them.**
   SCUDO's invariant I5 is governed under SR 11-7 where *"No single engineer or
   LOB head can authorise lift."* A fog-clearing agent that returns an answer
   there has manufactured false confidence from whoever was in the room. Emit
   `BLOCKED-EXTERNAL` and keep building around it.
4. **Ask "who holds the labelled data?" early.** If the answer is "the client"
   or "nobody yet", you have discovered the method's hard limit on day one
   rather than month nine. Say so out loud.

Fog-clearing moves — research, throwaway prototype, sign up for the real service
— are for decisions blocked on *knowledge*. They are not for decisions blocked
on *authority*.

### If no human is reachable — the method's most likely real failure

**You do not have Phase A. Emit `.finder/map.md` with every row marked
`AGENT_INVENTED`, and stop. Do not write an answer key, and do not write the
runner — a `check.sh` over a self-answered key is the exact artifact this
section exists to prevent.**

This is not an edge case; it is what happened on this document's first cold run.
The agent could not reach a human, self-answered all 34 grilling questions, and
proceeded. The resulting key asserted that reviewer outcomes were the closed set
`{ACCEPT, OVERRIDE, NO_FIT, DEFER}`. The real system is
`_DECISIONS = {"approve", "override", "reject"}`
(`backend/scudo_mapping_mcp/feedback.py:36`). That gate
executes, exits 0, survives a planted defect, satisfies every rule below — and
certifies an invention. Forever.

Agents are usually run without a human in the loop, so this failure is the
*default* path, not the unlucky one. A key built on a self-answered grill
certifies your own guesses at exit 0.

---

## 3. Phase B — Emit `.finder/`

**Three artifacts. No more:** the `map` (what was decided and who owns it), the
`answer-key` (what "correct" means, as commands), and the `runner` that executes
the key. This section said "two files" while four later places required
`bash .finder/check.sh` without anything ever telling you to create it — an
agent following the doc literally could not run its own answer key
(corrected 2026-08-17). The map and the key are prose-and-table documents; the
runner is the only one of the three that is executable, which is precisely why
it is not optional.

### `.finder/map.md`

```markdown
# ${PROJECT_NAME} — decision map
Generated ${DATE} · Interview: ${N} questions · Status: ${SETTLED}/${TOTAL}

## Destination
${ONE_PARAGRAPH_WHAT_DONE_LOOKS_LIKE}

## Decisions
| ID | Decision | Value | Owner | Reasoning | Enforced by |
|----|----------|-------|-------|-----------|-------------|
| D-1 | ${DECISION} | ${VALUE} | ${OWNER} | ${WHY} | ${KEY_ID or "PROSE ONLY"} |

## Fog
| ID | Blocked question | Blocked on | Clearing move | Status |
|----|------------------|------------|---------------|--------|
| F-1 | ${Q} | ${DEP} | research/prototype/task/**BLOCKED-EXTERNAL** | ${S} |

## Out of scope
${EXPLICIT_EXCLUSIONS}
```

**The `Enforced by` column is the anti-rot device.** A decision with
`PROSE ONLY` is a wish. It is not a control and must never be counted as
coverage.

**One row per decision is not always enough — check before you collapse.** Ask
of every value in Phase A: *is this stored or computed, from what, and how many
places declare it?* SCUDO has **two different constants both named
`CONFIDENCE_FLOOR`**: `backend/scudo_mapping_mcp/config.py:49` is `0.75` (band
centre) and `backend/scudo/orchestrator.py:41` is `0.80` (auto-publish gate).
Same name, different module,
different semantic, different value, **both correct**. And the bands are
*conditionally* derived: `pass_threshold()` returns the `PASS_CUT` constant when
floor and half-width are the module defaults, and only computes
`round(floor + half, 2)` for an **overridden** window. So the same name is a
stored constant on one path and a computed value on another — asking "stored or
computed?" has two answers in one function, and a gate that exercises only the
default path never reaches the arithmetic at all. Meanwhile the floor is
declared across multiple sites — `config.py`, `backend/Dockerfile`, three
`infra/scudo-dev-deploy.yaml` container definitions, a `CONFIDENCE_FLOOR` env
var read at `config.py:369`, and orchestrator.py's separate constant — including
a container that inherits it by declaring nothing at all. A gate parsing one
literal config file certifies one site and is silent about the rest; *absence*
of a declaration is invisible to a drift grep.

**And the reason for that rounding is a measurement, not a deduction** — a
distinction worth its own note (2026-08-17). It is tempting to write "floats are
inexact, so round". Measured with `python3`:

```
0.75 + 0.05 -> 0.8                    (exact — the DEFAULT window)
0.75 - 0.05 -> 0.7                    (exact)
0.80 + 0.05 -> 0.8500000000000001     (NOT exact — an OVERRIDDEN window)
0.85 - 0.05 -> 0.7999999999999999     (NOT exact)
```

The default config is not at risk; the *neighbouring* windows are, which is
exactly why the rounding guards the override path. A related false claim was
manufactured in this codebase when the floor moved 0.80 → 0.75 and the numbers
in a docstring were search-and-replaced without re-running them: the original
statement was true of 0.80, the rewrite was false of 0.75. **Numbers that look
like configuration can be measurements.** Substituting a parameter into a
recorded measurement does not carry the measurement with it — re-run it, or the
`${PARAMETER}` discipline this document opens with silently produces fiction.

Why this column exists: in SCUDO a *binding* decision record still reads
`PASS ≥ 0.85, BORDERLINE 0.75–0.85` a month after the bands moved to 0.80/0.70.
The correction lives in a different file. **A map is prose and prose rots.**
Only the executable half survives contact with time.

### `.finder/answer-key.md`

Every item is a **command with an exit code**. This is the whole method.

```markdown
# ${PROJECT_NAME} — answer key
Run: `bash .finder/check.sh` · Exit 0 = all pass
Oracle split: ${N_EXTERNAL} EXTERNAL · ${N_MEASURED} MEASURED · ${N_AUTHORED} AUTHORED

| ID | Asserts | Command | Oracle | Pass |
|----|---------|---------|--------|------|
| K-1 | ${PROPERTY} | `${COMMAND}` | ${ORACLE} | exit 0 |
```

**Eight construction rules, numbered from zero, each traceable to a measured
failure:**

0. **Every item names its oracle** — the thing that decides what "correct" means
   — as one of:
   - `EXTERNAL:<path or URL outside this repo>` — the gate *opens* an authority
     it does not control.
   - `MEASURED:<how the value was derived by running something>`.
   - `AUTHORED:<the builder wrote this down>`.

   Report the split, not just the pass rate. **A key that is 100% AUTHORED-oracle
   is a self-consistency suite no matter how many items execute**, and must be
   handed to the human described that way. This rule exists because the
   unqualified "every item executes" rule below *rewards the most circular gate
   shape*: a check reading only files the builder controls is the easiest to
   write and scores full marks.

1. **Every item executes.** If you cannot write it as a command, it goes in a
   `## Unverifiable` section and is *excluded* from the pass count. Never let
   prose sit in the checklist looking like coverage.

2. **Run each item in the turn you write it.** A gate written into a plan is
   unexecuted work, not a control. It *reads* as coverage — precisely phrased,
   present in the list — so later readers treat the area as handled. Three SCUDO
   review passes proved this; the cost of running was one command.

3. **Write the gate against the defect, not the convenient proxy.** SCUDO's gate
   exempted hits that were "a comment". Three of the four defects **were**
   comments — the gate as written would have passed every one it most needed to
   catch. *Whenever an exemption clause is easier to check than the property you
   actually care about, assume the defects are hiding inside the exemption.*

4. **Run under `${SHIPPED_CONFIG}`, not the default.** SCUDO's suite: 617 pass
   clean, 15 fail under the config both launchers set. A critic running "the
   tests" gets a green light on an environment nobody uses.

5. **Negative-test every gate.** Plant the defect the gate exists to catch and
   confirm it fails. A SCUDO brand gate passed 24/24 on a planted defect. An
   unfalsified gate is decoration.

6. **Grep gates need per-hit adjudication, not a count.** In SCUDO, six uses of
   "the deterministic matcher" were *correct* (naming a component) and would
   have been "fixed" by a count-based gate.

7. **A gate that mirrors an external authority must READ it, not a copy.**
   SCUDO's `backend/scudo/tests/test_dashboard_enum_vocabulary.py` (in **this**
   repo, MatchMaker) says *"Mirrors NodeType / EdgeType z.enum in
   understand-anything-plugin/packages/core"* and then hard-codes 21 node types.
   The authority it names is a **different** repo —
   `understand-anything-plugin/packages/dashboard`'s sibling
   `packages/core/src/schema.ts`. Measured from the MatchMaker root:
   `grep -rln --include='*.py' 'schema\.ts' backend/` returns **0**. No backend
   test ever opens the upstream schema. They agree today — which is exactly what
   makes it dangerous: if upstream adds a member, the gate stays green and the
   regression it was written to prevent returns. It asserts *"the builder emits
   the types I wrote down"*, not *"the types the dashboard accepts."*

   Same shape, worse — and note the repo, because this one is **not** in
   MatchMaker: in the **client-demo** repo, `scripts/brand-tokens.mjs` declares
   itself *"the ONE place brand tokens are defined"* and exports
   `ALL_BRAND_TOKENS`. Run from that repo's root,
   `grep -rn ALL_BRAND_TOKENS . | grep -v node_modules` returns exactly one hit
   — the declaration at line 81. **The aggregate list has zero importers.** Its
   *individual* exports (`CODENAME`, `CLIENT_TOKENS`, …) are imported widely, so
   the file is not dead; it is the one export that exists to be the single
   source of truth for *the whole token set* that nothing consumes. The two
   gates that scan for drift — `scripts/check-build-output.mjs` and
   `scripts/check-brand-drift.mjs` — hand-roll their own lists instead;
   `check-build-output.mjs` even documents the consequence in a comment
   (*"This list is the gate's whole blind spot"*), and JAPI and AIA shipped
   while it exited 0. Every gate executes. Every gate passes.
   (Repo attribution added 2026-08-17 after a reviewer grepped only MatchMaker
   and concluded the example was fabricated.)

   Also assert the artifact that **ships**, not the one that's convenient:
   28 sanitiser unit tests passed while the built bundle shipped 10 brand
   tokens, because minification strips comments but preserves regex literals.
   All 28 executed. All 28 read *source*, and source is not what ships.

### `.finder/check.sh` — the runner

**Write this in the same turn as the key.** It is the artifact every later phase
invokes; a key without a runner is prose again.

Its whole contract:

- It executes **the `Command` column of `.finder/answer-key.md`, one item at a
  time**, in the environment `${SHIPPED_CONFIG}` describes.
- It prints, per item, the `ID`, the command, and that command's **real output** —
  not a summary. The critic is required to paste this; give it something worth
  pasting.
- It **exits 0 only if every item exited 0**, and non-zero otherwise. Do not let
  a pipeline swallow a failure — `set -o pipefail`, and check the status of each
  item explicitly rather than trusting the script's last command.
- It **counts only key items**, never anything in `## Unverifiable` (rule 1).
- It prints the oracle split from the key's header (rule 0) alongside the pass
  count, so the split cannot be dropped by the reader who only skims the last
  line.

Two failure modes worth pre-empting, both measured in §1: a runner that stops at
the first failure hides how many gates are broken (run all, aggregate at the
end), and a runner sourcing a convenient `.env` rather than `${SHIPPED_CONFIG}`
reproduces exactly the 617-pass/15-fail split that made SCUDO's suite look green.

The runner is itself un-negative-tested until you break a gate on purpose and
confirm `check.sh` exits non-zero. Do that once, at creation.

---

## 4. Phase C — Rewrite the Gauntlet prompt

Replace the reference-product line:

```
Build ${PROJECT_NAME} per .finder/map.md.

CRITIC INSTRUCTIONS — the source of truth is .finder/answer-key.md.
  1. RUN `bash .finder/check.sh` under ${SHIPPED_CONFIG}. Paste real output.
  2. You may only cite an item as passing if you executed it this turn.
  3. Docstrings, comments and design docs are CLAIMS TO VERIFY, never evidence.
  4. Before reporting a gate as passing, plant its defect and confirm it fails.
  5. Report failures with the command and its output. Never summarise as "green".
  6. If an item is Unverifiable, say so — do not count it toward the score.

You did not write this code. Your job is to find where it does not meet the key.
```

**The independence problem, stated honestly.** "Sub-agents check their own work
against the checklist" weakens the Gauntlet's founding rule. A `.finder` folder
is a document the same system produced; a Call of Duty frame is not.

Every gate has two halves: a **probe** (the part that runs) and an **oracle**
(the encoded definition of correct). Execution disciplines the probe. The oracle
is still prose the builder wrote — it has merely been compiled. The judgement did
not disappear; it moved from review-time to authoring-time, where nobody looks at
it. That relocation can make things *worse*: a green exit code carries more
authority than a prose checklist item, so it suppresses inquiry harder.

Mitigations, in descending order of actual strength:

1. **A second, independently-authored artifact to cross-reference.** The
   strongest mitigation here, because it attacks the *oracle* rather than the
   probe: it replaces a definition of correct that the builder wrote with one
   they did not. This — not execution — is what broke the
   24/24 brand gate: a reviewer holding a separately-derived token list.
   Independence comes from artifact multiplicity. Its executable form is an
   `EXTERNAL:` oracle (rule 0): a gate that *opens* the upstream authority
   instead of copying it. Had `test_dashboard_enum_vocabulary.py` read
   `schema.ts` rather than transcribing it, no planted defect would have been
   needed to find the drift — the gate could not have drifted.
2. **Planted defects.** The strongest thing you can do to a *single* gate, and
   the one that catches a probe wired to always pass. Plant the defect the gate
   exists to catch; if it stays green, the gate is decoration.

   **But it does not validate the oracle** — corrected 2026-08-17; this entry
   read "the only mitigation that tests the oracle", which contradicts this
   section's own thesis. The defect you plant is *drawn from
   your model of what wrong looks like*. If the oracle encodes the wrong target,
   a defect selected from that same wrong model is caught exactly as designed and
   the gate goes green with more authority than before. §6.5's `K-3` is the
   worked case: it asserted `{ACCEPT, OVERRIDE, NO_FIT, DEFER}`, it *was*
   negative-testable, and it was flatly wrong about the domain. Planting tests
   whether the probe discriminates; nothing you can plant tests whether the
   definition of correct came from outside you.
3. **A critic that never wrote the code.** Fresh context, no build history.
4. **Adversarial framing.** Instruct the critic to *refute*, defaulting to
   "refuted" under uncertainty.
5. **Execution.** Necessary, and it eliminates the entire §1 rows-1–6 failure
   class. But it disciplines the probe only.

An exit code is not a judgement about the artifact — it is the mechanical replay
of a judgement the builder froze at authoring time. Execution guarantees the
answer is **current**. It guarantees nothing about whether it is **right**.

Where the key is prose and the critic is a sibling agent, you have **no**
independent bar. Say so rather than reporting a score.

---

## 5. Phase D — Build

- **Sequential single-owner passes over parallel fan-out** (Claude-of-Duty's
  measured finding). Parallel is for *independent verification*, not building.
- Re-run `check.sh` every pass. **Track the oracle split, not the pass count**
  (corrected 2026-08-17 — this line called a rising pass-count "the only
  progress signal that means anything", which rewards exactly the pathology §4
  exists to name: authoring more gates you control is the cheapest way to make
  that number go up). The honest signals are:
  - the count of `EXTERNAL` and `MEASURED` oracles **rising**, and
  - the `AUTHORED` share of the key **falling**.

  A pass count is still worth watching — it is how you see a regression — but it
  measures the probe. Read it *next to* the split, never instead of it. A key
  that went from 12/12 to 30/30 while staying 100% AUTHORED has grown, not
  progressed.
- **Re-run the whole key after landing fixes.** Re-running a SCUDO gate on the
  committed tree found 6 defects where the working-tree run found 4. "Fixed in
  the working tree" is not fixed.
- **A fix can carry the bug's own shape.** A 21-agent adversarial pass on a
  correction commit found two defects *in the fix itself*.
- When the pull to skip the key and "just build" appears — that is the edge of
  the map. Return to Phase A.

---

## 6. What this method still cannot do

State these to the human at kickoff. They do not become false by being ignored.

1. **It cannot invent ground truth.** Grilling extracts thresholds because a
   human holds thresholds in their head. Nobody holds 500 labelled mappings in
   their head. If `${GROUND_TRUTH_SOURCE}` is client-held, the key can check
   *shape, policy and invariants* but never *correctness*. This is the one place
   an existing reference product genuinely wins: the Call of Duty frame **is**
   the labelled data, free.

2. **It cannot resolve authority.** `BLOCKED-EXTERNAL` decisions stay blocked.
   Manufacturing an answer from the available human is worse than the block.

3. **Cross-surface invariants need a key item that opens BOTH surfaces** — this
   is a mandate, not an apology. SCUDO's "byte-identical fixture" contract is
   violated right now, and the two surfaces are in **different repos**:
   MatchMaker's `backend/scudo/fixtures/matching-graph.json` and
   understand-anything-plugin's `packages/dashboard/public/matching-graph.json`.
   Both files are 49,406 bytes, MD5 `e562e339…` vs `e20c5bdf…`. No test in
   either repo references the other, so nothing can see it. Worse, `CLAUDE.md` has since normalised the violation — *"`analyzedAt`
   timestamp churn is expected"* — so the doc now licenses the drift the contract
   forbids. **An invariant spanning two repos cannot live in either repo's
   suite.** Nearly every expensive defect in SCUDO has this shape: two or three
   places that must agree, and one silently didn't.

4. **A self-authored key can be wrong in the same direction as the build.** If
   the grilling misunderstood the domain, the key encodes the misunderstanding
   and the loop converges confidently on the wrong thing. Only a human who knows
   the domain can catch this — schedule that review explicitly.

5. **A gate can be executable and still wrong about the domain.** This is
   distinct from §6.4 and worse, because it survives every check in §3. Measured:
   the cold run's `K-3` asserted reviewer outcomes were
   `{ACCEPT, OVERRIDE, NO_FIT, DEFER}`; the real system is
   `{approve, override, reject}`
   (`backend/scudo_mapping_mcp/feedback.py:36`). Real command, clean exit,
   negative-testable, §3's construction rules satisfied, flatly wrong. (A count
   stood here — "six rules" — while §3 lists eight; replaced with the reference
   so the two cannot drift apart again, 2026-08-17.)

   **The mitigation is not another gate.** It is a *named* domain reviewer, with
   a date, reviewing `.finder/map.md` itself — not the code — before Phase D. If
   you cannot name that person, record it as `BLOCKED-EXTERNAL` and tell the
   human the key is unanchored.

6. **It cannot choose its own target.** Execution never selects the artifact it
   reads; the builder does, and the builder's blind spot selects with it. A
   browser check that scanned each page right after load reported 17/18 green
   while branded IRIs rendered ~25 s into an agent run. Enumerate *surfaces*,
   not files or pages.

---

## 7. Kickoff checklist

```
[ ] Grilled a human. ${N} questions. Agent answered none of them.
[ ]   — if NO human was reachable: stopped after map.md, wrote no key (§2)
[ ] .finder/map.md — every decision has an owner and an `Enforced by` value
[ ] Asked of each value: stored or computed? from what? how many declaration sites?
[ ] Decisions marked PROSE ONLY are counted as risks, not coverage
[ ] BLOCKED-EXTERNAL items listed and communicated
[ ] .finder/answer-key.md — every item is a command with an exit code
[ ] .finder/check.sh WRITTEN — runs every key item, aggregates, exits 0 iff all pass
[ ] Every key item's oracle labelled EXTERNAL / MEASURED / AUTHORED
[ ] Reported the AUTHORED-oracle percentage to the human, not just the pass rate
[ ] Tracking EXTERNAL+MEASURED rising / AUTHORED share falling — not raw pass count
[ ] Each gate reads the artifact that SHIPS (source ≠ bundle; load-time ≠ runtime)
[ ] Cross-surface invariants have a key item opening BOTH surfaces
[ ] Every cited path names its repo when it lives outside this one
[ ] Ran check.sh. Pasted real output. Not "should pass".
[ ] Ran it under ${SHIPPED_CONFIG}, not the default
[ ] Broke one gate on purpose; confirmed check.sh itself exits non-zero
[ ] Planted a defect per gate; confirmed each fails (probe only — not oracle proof)
[ ] Unverifiable section exists and is excluded from the score
[ ] Named the domain reviewer (person + date) who will read map.md before Phase D
[ ] Told the human what the key cannot check (§6)
```

---

## 8. Provenance

Phases A–C are the published methods. §1's numbers, and every rule with a "why"
attached, were measured on SCUDO at commit `a0cc72d` on 2026-08-16 — a codebase
built *without* this method, which is what makes its failure modes evidence
rather than speculation.

**What the cold test showed, and its contamination.** This document was run cold
on 2026-08-16: agents given only the playbook and a client brief, no access to
SCUDO, then compared against the real repo. Result — the cold plan reached
roughly 10–15% of the system by substance, but ~60% of the *invariant catalogue*
and under 5% of its *content*. Against three named real defects its key caught
one, partially. It also produced two false positives on correct code.

Two caveats that must travel with those numbers:

- **The cold run was contaminated.** The agent's context contained `CLAUDE.md`,
  which states the band drift, the PROSE-ONLY lesson and the nondeterministic
  scorer *verbatim*. Three of six headline confirmations were plausibly
  restatements rather than derivations. The genuinely independent hits reduce to
  about two — naming SR 11-7 as the threshold authority, and the
  compute-but-never-apply split. **Treat this document as unvalidated until an
  agent with no SCUDO exposure runs it.**
- **§1 supports execution-based discipline generically, not this method
  specifically.** No row in that table is evidence for a map file, a fog table,
  or the three-artifact layout. And SCUDO eventually built the right gates anyway,
  without any playbook — just slowly and expensively.

The single-sentence version:

> **A checklist item is a control only to the extent that it executes.**

That sentence is close to a truism. The non-obvious mechanism under it is not:
**a precisely-worded *unexecuted* gate reads as coverage and therefore suppresses
later checking — so specifying a gate and not running it is worse than
specifying nothing.**

And the parts of this document that are *not* about execution — recording the
owner rather than the value, `BLOCKED-EXTERNAL`, `PROSE ONLY`, the oracle split,
the Unverifiable scoreboard — are the parts a competent engineer would not have
done anyway. They are the differentiated content, not scaffolding for the
sentence.
