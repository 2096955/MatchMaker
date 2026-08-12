# Final handover — SCUDO / JPMC documentation work stream

**Written:** 2026-08-12 · **Branch:** `main` @ `51cff58` · **Worktree:** 77 dirty paths (re-measure: `git status --porcelain | wc -l`)
**Status:** task is complete and ready for review. **Nothing was committed or deployed.**

This is the closing report for the session. It exists so the next agent can
pick the work up cold. Read [§1](#1-start-here) and stop there if you only need
to know where to begin.

**Every file named below is a clickable, repo-relative link.** They resolve
after a `git clone`, not just on the authoring machine.

---

## 1. Start here

Read these three, in this order. They are the deliverable.

| # | Document | Lines | What it is |
|---|---|---|---|
| 1 | [`JPMC_IMPORT_AGENT_BRIEF.md`](JPMC_IMPORT_AGENT_BRIEF.md) | 497 | **The entry point.** Consolidates all three work streams, tells you what is verified and what is not, and gives the ordered task list. |
| 2 | [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) | 610 | The Aurora/Bedrock file list — the direct answer to the client's question. |
| 3 | [`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md) | 292 | The doc-consolidation audit: which of the 12 handover docs are current, which are stale. |

A fourth, [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
(586 lines), was written by a **different agent** covering a **different work
stream** (offline skill-optimizer, protected evaluation, promotion into Aurora
agent memory). It is accurate for its scope but uses **absolute** links
(`/Users/anthonylui/...`) that will 404 after a clone. I did not rewrite it —
it is not my deliverable to edit. Strip the prefix to get a working path.

**The one thing to know if you read nothing else:** under
`SCUDO_DENSE_BACKEND=opus`, **two** branches auto-map an LLM-supplied score with
no human review — the PASS band and the borderline branch. See
[§3](#3-the-correction-that-matters-most).

---

## 2. What was actually done this session

The client asked for a file list for Aurora and Bedrock. The work that followed
was **verification and correction**, not new features. No application code was
changed.

| Work | Outcome |
|---|---|
| Hyperlink pass over four reports | **255 relative links**, 0 broken, 0 nested, 0 bad anchors — validated by script, re-validated after every edit |
| Line-anchor re-derivation | 10 anchors had drifted (concurrent sessions were editing `config.py`/`agent.py`); all 22 load-bearing anchors re-derived by **content match**, not line number |
| Codex adversarial review | 7 findings, **all 7 hand-verified against source**, all 7 applied |
| End-to-end runnability check | Every command in §6 executed; **4 of 7 claims were wrong**; all corrected |
| ABC-method count reconciliation | "~15" corrected to **16** (AST count) across all four documents |

### The corrections applied to the brief

Each was hand-verified before being applied — a verifier finding that reverses
a load-bearing claim gets checked by hand, never taken on trust.

1. **"Only the PASS row auto-maps" was FALSE.** Two branches auto-map. This is
   the most serious correction; see [§3](#3-the-correction-that-matters-most).
2. **`matching.py:512` was misclassified** as a contract-violation branch. Only
   `:406` is. `:512` is the ordinary specialist-disagreement path.
3. **Test counts were wrong: "422 passed, 2 failed" → `468 passed, 2 failed`.**
4. **The two pytest invocations are not equivalent.** The brief claimed they
   were. Without `PYTHONPATH` you get `467 passed, 3 failed`.
5. **Bare-`pytest` collection errors: 8 → 11.**
6. **The `STORE_BACKEND=aurora` error text depends on how you launched** — the
   brief reported only the bare-import error, which is not what JPMC will see.
7. **`SCUDO_SPECIALIST_BACKEND` re-qualified.** "The cap is never in play on
   Streamlit" is true *by default* only; setting that variable activates it.

### Cross-document contradictions found and fixed

A separate verifier read all four documents against each other. Eight real
contradictions, each re-measured against the current text before being applied:

| # | Contradiction | Resolution |
|---|---|---|
| 1 | Brief quoted the other agent's suite as **229/1** with an outstanding stale test | Re-ran its exact command: **230 passed, 0 failed**. The failure was already fixed. Corrected in the brief and here. |
| 2 | Brief said "**do not reconcile** the two test counts" | Wrong — 10 of its 13 files sit inside `backend/scudo/tests/` and contribute **200 of the 230**, a strict subset of the 468. Reconcile them; that is where a regression would hide. |
| 3 | [`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md) said "**the two current documents are the last two**" | Contradicted its own table and the brief's "five". Corrected in place, and the fifth document — which post-dates that table and was **absent from it** — is now named. |
| 4 | That same file said Citrix "**invalidates**" the `start_local.py` instructions | The **most dangerous** one: an agent acting on it would strip out `start_local.py` and destroy the only route to the console UI. Only **Vite dev on `:3000`** is blocked. Corrected. |
| 5 | That file asserted "**the score is deterministic and LLM-free**" unconditionally, under a heading saying to cite it over any document | The `SCUDO_DENSE_BACKEND` precondition appeared **nowhere** in that file. Added, with a pointer to [§3](#3-the-correction-that-matters-most). |
| 6 | [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) still said "**Codex is not installed**, independent review did not happen" | Stale **about itself** — two Codex rounds ran against that very file. `codex-cli 0.145.0` verified present. Corrected. |
| 7 | That file cited `requirements.txt:12-13` for `boto3`/`strands-agents` | A `scipy` line inserted at `:11` by the other work stream pushed both to **`:13-14`**. Corrected, with "match on package name, not line number". |
| 8 | It lists the two `aurora_*.py` files as "**do not edit**" while the other document actively edits them | Both true — different work streams. Qualified as read-only *for the Bedrock switch-over*, so the next agent does not read the other agent's commits as violations. |

### Line-number citations — a second, wider sweep

The earlier pass re-derived only the **load-bearing** anchors (the confidence
branches). A final sweep re-derived **every** `file:NNN` citation in all four
documents against current bytes — 64 of them. **60 were exact; 4 were not**, and
all four are now corrected. Each was hand-verified by AST or by printing the
cited lines before the edit was applied:

| Was | Now | Why it mattered |
|---|---|---|
| `agent.py:1224-1263` — "`get_agent(provider)`" | **`:1242-1281`** | The old range sits inside `AzureMappingAgent` (class spans 1055-1236). A hand-typer paging to 1224 sees Azure's matcher call and **no dispatch logic at all**. |
| `aurora_store.py:14-17` — "`boto3.client("rds-data")`" | **`:19-20`** | Line 14 is a `typing` import. The cited call is in `_rds_data()` at 19-20. |
| `aurora_store.py:45-47` — the three `SCUDO_AURORA_*` requirements | **`:50-52`** | 45 is blank, 46 is the `def`. The three `_require(...)` calls are at 50-52. |
| `agent.py:362-367` — the `map_vendor_product(...)` call | **`:361-366`** | Off by one at both ends: the reader saw the argument list without its opening call, plus one line of unrelated code. |

One more, not a line number: the brief pointed at the project-memory directory
and then linked `MEMORY.md` **repo-relatively**. Both files exist, so a
link-existence check passes — but they are *different files*, and none of the
five named entries are in the repo-root one. Now stated explicitly, with the
note that the directory is outside the repo and a JPMC clone will not have it.

This is the general lesson: **a link validator proves a path resolves, not that
it resolves to what the sentence promises.** Line numbers and same-name files
both slip past it.

Its remaining findings — that several documents mis-stated each other's line
counts — were **already fixed** before the verifier reported, and I re-measured
to confirm: all 12 inventory rows exact, all cross-document counts in sync. One
figure I could not reproduce is left standing and flagged rather than silently
corrected: the other agent's document reports `915 passed` for the broad
backend suite; the tree gives **916 passed, 2 failed**, and that document quotes
no command for its number. It is not mine to edit.

---

## 3. The correction that matters most

Under `SCUDO_DENSE_BACKEND=opus`, the LLM's score becomes `Candidate.similarity`
and reaches published `confidence` **uncapped on four branches**, two of which
**auto-map without human review**:

| Branch | Line | Status |
|---|---|---|
| PASS band | [`matching.py:362`](backend/scudo_mapping_mcp/matching.py) | **`AUTO_MAPPED`** |
| Borderline, no specialist | [`matching.py:444`](backend/scudo_mapping_mcp/matching.py) | **`AUTO_MAPPED`** on the Streamlit/agent path |
| Hard FAIL | [`matching.py:348`](backend/scudo_mapping_mcp/matching.py) | `NEEDS_REVIEW` |
| FAIL band | [`matching.py:526`](backend/scudo_mapping_mcp/matching.py) | `NEEDS_REVIEW` |

The borderline branch splits on `borderline_requires_specialist`, which
**defaults to `False`** ([`matching.py:165`](backend/scudo_mapping_mcp/matching.py)).
Only the Flask route [`backend/routes/mapping.py:568-569`](backend/routes/mapping.py)
passes `True`. Streamlit never does — so **on exactly the surface JPMC uses**, a
borderline match auto-maps on the raw dense score.

**The window is non-empty.** With shipped defaults (`floor=0.75`, `half=0.05`)
`_gate_thresholds()` gives `pass=0.80 / borderline=0.70`, so scores in
**`[0.75, 0.80)`** are borderline *and* auto-mapped. Derived by calling
`_gate_thresholds(0.75, 0.05)`; the `False` default confirmed via
`inspect.signature(map_vendor_product)`.

**Why this is not currently a live defect:** `SCUDO_DENSE_BACKEND` defaults to
`jaro_winkler` ([`config.py:301`](backend/scudo_mapping_mcp/config.py)). The
score is deterministic and the LLM only narrates. **State the default as the
reason it is safe — never claim a cap protects you**, because on these two
branches no cap is involved.

**Also corrected in shipped source (2026-08-12, with the user's approval):**
[`streamlit_app.py:128-151`](streamlit_app.py) carried a comment asserting the
LLM *"can only lower the deterministic anchor, never inflate it."* That is
false, and it read as a safety guarantee. Correcting the documents while
leaving the comment would have meant the next reader re-learning the error from
code, so the comment was rewritten to name all four uncapped branches, both
auto-mapping branches, the `borderline_requires_specialist=False` default, and
the `SCUDO_DENSE_BACKEND` default as the actual reason the deterministic claim
holds. **This is the only application-source change in this work stream** — the
file grew 1079 → 1101 lines, which is why every `streamlit_app.py:NNN` citation
in these documents was re-derived afterwards (see the sweep in
[§2](#2-what-was-actually-done-this-session)).

---

## 4. Verification basis — how to reproduce

```bash
# Test suite — set PYTHONPATH or you get a spurious third failure
cd backend && PYTHONPATH=. python3.11 -m pytest scudo/tests/ -q   # 468 passed, 2 failed
PYTHONPATH=backend python3.11 -m pytest backend/scudo/tests/ -q   # 468 passed, 2 failed

# Link validation across the three reports
python3.11 -c "
import re,os
for d in ['JPMC_IMPORT_AGENT_BRIEF.md','HANDOVER_CONSOLIDATION.md','JPMC_AURORA_BEDROCK_FILES.md']:
    for m in re.finditer(r'\[\`?([^\]\`]+)\`?\]\(([^)#]+)\)', open(d).read()):
        if not m.group(2).startswith('http') and not os.path.exists(m.group(2)):
            print('BROKEN', d, m.group(2))
print('done')"

# Repo state — re-measure, both numbers drift
git rev-parse --short HEAD && git status --porcelain | wc -l
```

**The 2 expected failures are pre-existing**, both in
[`backend/scudo/tests/test_provenance.py`](backend/scudo/tests/test_provenance.py),
documented in [`CLAUDE.md`](CLAUDE.md) as unadjudicated. Do not "fix" them
without adjudicating. **If you see 3 failures, you forgot `PYTHONPATH`** — the
extra one spawns a real subprocess that dies with `ModuleNotFoundError: No
module named 'scudo'`.

---

## 5. Open items needing your approval

Listed in the order I would raise them. Item 1 was **not** actioned — it needs a
decision that is not mine. Item 2 was approved and is done.

1. **`frontend/dist/` has never been committed.** `git ls-files frontend/dist`
   returns **0 rows** while `git status` shows `?? frontend/dist/`, even though
   `.gitignore:18-19` deliberately un-ignores it. A git recipient on a
   Node-blocked Citrix desktop therefore gets **no console UI at all** —
   Providers, Datasets, Admin and Ingestion are unreachable.
   [`CITRIX_NO_NODE.md:47`](CITRIX_NO_NODE.md) reads as though this was already
   done. **Confirm what JPMC actually holds before pointing them at `/app/`.**
2. ~~The false comment at `streamlit_app.py:128-132`~~ — **done.** The user
   approved it; the comment now spans
   [`:128-151`](streamlit_app.py) and states the four uncapped branches, the
   two auto-mapping ones, and the `SCUDO_DENSE_BACKEND` default. See
   [§3](#3-the-correction-that-matters-most).

---

## 6. Known limits of this work — read before trusting it

State these plainly rather than letting the next agent discover them.

- **Never verified:** a real Aurora connection or a live Bedrock invoke from the
  JPMC account. Both are unreachable from this machine. Everything about them is
  read from source and CloudFormation, not executed.
- **Line numbers drift, sometimes within the hour.** Other sessions were
  committing to this repo *while this work was in progress* — HEAD moved
  `8c53dbc` → `a92b8d0` → `e3baa75` → `51cff58`. **Match on content before you
  type anything.** The file links are stable; the `:NNN` suffixes are not. Every
  citation in these four documents was re-derived against current bytes at the
  end of the session (4 corrections, §2) — but that was **one measurement, on
  2026-08-12**. It does not make them permanently true.
- **Both verifier agents have now reported, and their surviving findings are
  applied** (see [§2](#2-what-was-actually-done-this-session)). The
  cross-report verifier found 8 real contradictions between the four documents;
  its line-count findings were themselves stale, which is the recurring
  pattern — **a long review grades the tree as of dispatch**, so re-measure
  every finding against the current text before acting on it. I did.
- **The test count in
  [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
  is `230 passed`, 0 failed** — I re-ran its exact 13-file command. An earlier
  version of this report said "229/1, a different suite, do not reconcile".
  Both halves were wrong: the failure is fixed, and 10 of the 13 files are
  **inside** `backend/scudo/tests/`, contributing 200 of the 230 — a strict
  subset of the 468. Reconcile them; the subset is where a regression would
  hide. Its "broader backend context" figure of `915` I could not reproduce:
  the tree gives **916 passed, 2 failed**.
- **The agent is not conversational.** `get_agent(provider).run(ref)` is a
  generator over one product reference; there is no free-text entry point. The
  client's ask — "users engage with the Agents to intelligently query" — today
  means *watch a structured reasoning trace*. Genuine Q&A is new work; scope it
  explicitly rather than implying it exists.

---

## 7. Hard constraints inherited — these shape everything

- **The JPMC engineer types every change by hand** on a locked-down Citrix
  desktop. A wrong command, a stale line number, or an instruction for something
  already applied costs them hours. This is why the session was spent verifying
  rather than writing.
- **No commits, no deploys, unless the user asks.** No approval was given.
- **Do not claim production readiness** or "fully operational".
- **Verify before asserting** — prefer running code to reading prose, and state
  the verification basis. Multiple claims in these documents were stale within a
  single session.

---

## 8. Related

- [`JPMC_IMPORT_AGENT_BRIEF.md`](JPMC_IMPORT_AGENT_BRIEF.md) — start here
- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — the AWS file list
- [`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md) — doc inventory and staleness
- [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md) — the other work stream (absolute links)
- [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) — how to run Streamlit at JPMC
- [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) — the React-via-Flask fallback
- [`CLAUDE.md`](CLAUDE.md) — internal agent instructions and contracts
