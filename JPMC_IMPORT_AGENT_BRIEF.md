# Brief for the JPMC import agent

**Purpose:** you are picking up the SCUDO handover work. This is the single
document to start from. It consolidates three work streams — the Aurora/Bedrock
file-list work, the doc-consolidation audit, and the matching-agent
self-improvement/promotion work — and tells you what is verified, what is not,
and what to do in what order.

**Every file named in this brief is a clickable link.** Links are
**repo-relative**, so they resolve for anyone who clones the repo, not just on
the machine that wrote this. Open them from the repo root.

> **One inconsistency you will hit immediately.**
> [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
> — written by a different agent — uses **absolute** links
> (`/Users/anthonylui/MatchMaker/MatchMaker/...`). Those resolve on the
> authoring machine only and **will 404 for JPMC after a clone**. The content
> is fine; strip the `/Users/anthonylui/MatchMaker/MatchMaker/` prefix to get a
> working path. I have not rewritten that file — it is another agent's
> deliverable and editing it is not mine to decide.

**The `:NNN` line numbers are the perishable part.** The file links stay valid;
the line numbers drift, sometimes within the hour (see
[§2](#2-do-not-re-introduce-these-corrected-mistakes)). Match on content before
you type anything.

**Repo state, measured 2026-08-12 (and already stale — re-measure):** `main` @
`51cff58`, **75** dirty paths (re-measured at the end of the session).
**Do not commit or deploy unless the user asks.**

This number moved while this brief was being written: HEAD advanced
`8c53dbc` → `a92b8d0` → `e3baa75` → `51cff58` and the dirty count fell from ~98
to ~76, because **other sessions were committing to this repo concurrently**.
Three of the documents referenced here
([`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md),
[`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md),
[`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md))
are now **tracked and committed**; the hyperlink pass over the first two is an
**uncommitted delta on top of those commits**. This brief itself is untracked.

Run these two before you trust anything above:

```bash
git rev-parse --short HEAD
git status --porcelain | wc -l
```

**Every number in this brief is a measurement with a date on it.** Line counts
and file counts drift daily in this tree — re-measure before you quote one.
The dirty-path count moves as you work, including from writing documents like
this one; treat it as an order of magnitude, not an assertion. `git rev-parse
HEAD` and `git status --porcelain | wc -l` are the two commands to re-run.

**Hard constraint that shapes everything:** the JPMC engineer **types every
change by hand** on a locked-down Citrix desktop. A wrong command, a stale line
number, or an instruction for something already applied costs them hours. Verify
before you assert; prefer running code over reading prose.

---

## 1. The two facts that override the rest of the documentation

### 1.1 JPMC's surface is Streamlit on :8501 — but the React path is not dead

Citrix group policy blocks `node_modules/@esbuild/win32-x64/esbuild.exe`, so
**Vite dev on :3000 cannot run.** Say exactly that. Do **not** say "the React
path cannot work" — that is wrong, and I had it wrong in the first draft.

A **prebuilt React bundle is served by Flask** at `/app/`, with no Node, no
Vite and no esbuild involved ([`CITRIX_NO_NODE.md:12`](CITRIX_NO_NODE.md), `:22`):

```bash
export SCUDO_SERVE_FRONTEND_DIST=1
python start_local.py           # then open http://localhost:5000/app/
```

`:5000` is the default ([`start_local.py:82`](start_local.py)). If that port is already taken —
macOS AirPlay and some corporate agents squat on it — set `PORT=5050` and open
`/app/` on that instead ([`start_local.py:19-23`](start_local.py)). The startup banner prints the
real port, so trust it over this document.

The routes are real: [`backend/app.py:121`](backend/app.py) gates them, `:128` serves
`index.html`, `:132` serves assets with an SPA fallback so a refresh on a deep
link still loads.

This matters because **Streamlit covers the matching path only.** Providers,
Datasets, Admin and Ingestion are not in the Streamlit app at all
([`CITRIX_STREAMLIT_HANDOVER.md:179`](CITRIX_STREAMLIT_HANDOVER.md)) — for those, `/app/` is the route.

**Open issue you should raise before relying on this.** `frontend/dist/` exists
in this worktree, correctly built with `--base=/app/` (verified: `index.html`
requests `/app/assets/index-CW6wtwMJ.js`), and `.gitignore:18-19` deliberately
un-ignores it so it *can* be tracked — but **`git ls-files frontend/dist`
returns 0 rows; it has never been committed** (`git status` shows
`?? frontend/dist/`, measured 2026-08-12). So anyone who receives this repo
through git gets no bundle, and cannot build one on a Node-blocked desktop.
[`CITRIX_NO_NODE.md:47`](CITRIX_NO_NODE.md) reads as though this was already done. Confirm what JPMC
actually holds before telling them to set `SCUDO_SERVE_FRONTEND_DIST=1`.
Committing the bundle needs the user's approval — it has not been given.

So there are two live surfaces, not one:

| Surface | Command | Covers |
|---|---|---|
| Streamlit :8501 | `streamlit run streamlit_app.py` | matching + HITL correction |
| React via Flask `/app/` | `SCUDO_SERVE_FRONTEND_DIST=1 python start_local.py` | Providers, Datasets, Admin, Ingestion |

**The contradiction that will burn the client:** of 26 root-level `.md` files,
16 reference [`start_local.py`](start_local.py) and 10 contain `:3000` (measured 2026-08-12).
But only **7 actually instruct the reader to open it** — the other three
*warn against* it ([`HANDOVER_CONSOLIDATION.md:60`](HANDOVER_CONSOLIDATION.md), this brief) or quote
[`start_local.py`](start_local.py)'s own print statements ([`JPMC_LOCAL_CHANGES.md`](JPMC_LOCAL_CHANGES.md)). Fix these
seven, not all ten:

[`README.md`](README.md), [`CLAUDE.md`](CLAUDE.md), [`AGENTS.md`](AGENTS.md), [`CITRIX_CHECK_FRONTEND.md`](CITRIX_CHECK_FRONTEND.md),
[`CITRIX_FOLLOWUP.md`](CITRIX_FOLLOWUP.md), [`JPMC_LOCAL_RUN_HANDOVER.md`](JPMC_LOCAL_RUN_HANDOVER.md),
[`JPMC_UPLOAD_AND_MATCH_REVIEW.md`](JPMC_UPLOAD_AND_MATCH_REVIEW.md).

`:3000` is the Vite dev server. That is the instruction that cannot work on
Citrix — not [`start_local.py`](start_local.py) itself, which is exactly what you need for
`/app/`.

**Current documents — five, not two.** An earlier draft of this brief said
"only two are current"; that is false and would have caused a working document
to be buried:

| Document | Status |
|---|---|
| [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) | current — how to run Streamlit at JPMC |
| [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) | current — the AWS switch-over (610 lines) |
| [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) | **still valid** — the React-via-Flask fallback |
| [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) | current — overlaps the handover; fold, don't supersede |
| [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md) | current — the **self-improvement/promotion** work stream (586 lines) |

That last one arrived 2026-08-12 from a separate agent and covers a **different
work stream** to everything else here: the offline skill-optimizer, protected
evaluation, promotion into Aurora agent memory, and signed post-promotion
monitoring. It already carries absolute clickable links to every file it
touched. Two things to know before you act on it:

- Its scope is the **deployed-Lambda / Aurora-agent-memory** half of the system
  — the half [§3](#3-aurora-and-bedrock--the-short-version) notes is **not
  reachable from Streamlit at all**. Nothing in it changes what JPMC sees on
  `:8501` today.
- It reports **`230 passed`, 0 failed** on its own 13-file subset. An earlier
  draft of this brief said "229 passed / 1 failed" with an outstanding stale
  `not yet active` expectation — that was quoting a superseded draft, and the
  test has since been updated to expect the stricter runtime rejection. **Do
  not go looking for that failure; it is fixed.** I re-ran its exact command
  (`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md:398-416`): 230 passed.
- **That subset is not a separate universe — it overlaps §6.** 10 of its 13
  files live in `backend/scudo/tests/` and contribute **200 of the 230**; they
  are a strict subset of the 468. Only the 3 `scudo_mapping_mcp/tests/` files
  (30 tests) sit outside. An earlier draft said "do not reconcile the two
  counts" — wrong, and it would stop you noticing a regression inside that
  subset. The one figure in that document I could **not** reproduce is its
  "broader backend context" of `915 passed`: the tree gives **916 passed, 2
  failed** for `PYTHONPATH=. pytest scudo/tests/ scudo_mapping_mcp/tests/ -q`
  from `backend/`. That document quotes no command for the 915.

Anything else dated **2026-08-06 or earlier is historical**.
[`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md) is the audit that established this; its inventory
table carries the per-file status, and I re-measured all 12 line counts in it
against the live tree — **12/12 exact**, re-checked after the last edit. Note
that document's table is itself now incomplete: it pre-dates
[`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
and does not list it.

### 1.2 The matcher scores deterministically — the LLM only narrates

Pre-empt this objection before it is raised. `SCUDO_DENSE_BACKEND` defaults to
`jaro_winkler` ([`backend/scudo_mapping_mcp/config.py:301`](backend/scudo_mapping_mcp/config.py)), so the confidence number is a deterministic
string-similarity computation. The agent explains the result; it does not
produce the score.

**One caveat you must state accurately.** I got this wrong twice — first
claiming the LLM could never inflate the score, then understating how open the
path is. Both were caught in review, the second by execution.

Under `SCUDO_DENSE_BACKEND=opus` the model score *becomes*
`Candidate.similarity` ([`backend/scudo_mapping_mcp/store/memory_store.py:118`](backend/scudo_mapping_mcp/store/memory_store.py), under a comment reading
"CRITICAL: Candidate.similarity is the RAW DENSE SCORE ... No rerank, no boost,
no fusion") and flows straight to `confidence` **uncapped on four branches**.
An earlier draft of this brief said three — it missed the hard-FAIL branch.

Re-derive the list with
`grep -n 'confidence = ' backend/scudo_mapping_mcp/matching.py` rather than
trusting the line numbers below. **That grep returns 7 lines, not 4**: the four
uncapped ones in the table, plus the three capped ones (`:406`, `:479`, `:512`)
discussed after it. All 7 are accounted for on this page; the table lists only
the uncapped subset, which is the part that matters for trust.

| Branch | Line | Status it yields | Specialist |
|---|---|---|---|
| Hard FAIL (required-validation failed) | [`matching.py:348`](backend/scudo_mapping_mcp/matching.py) | `NEEDS_REVIEW` | **never consulted** (`:342-344`) |
| PASS band | [`matching.py:362`](backend/scudo_mapping_mcp/matching.py) | **`AUTO_MAPPED`** | **never consulted** (`:358-359`) |
| Borderline, specialist absent/abstained | [`matching.py:444`](backend/scudo_mapping_mcp/matching.py) | **`AUTO_MAPPED`** on the Streamlit/agent path (see below) | n/a |
| FAIL band | [`matching.py:526`](backend/scudo_mapping_mcp/matching.py) | `NEEDS_REVIEW` | **never consulted** (`:521-523`) |

**Two rows auto-map, not one.** An earlier draft of this brief said "only the
PASS row auto-maps". That is **false on exactly the path JPMC uses**, and it is
the single most important correction in this document.

The borderline branch splits on the `borderline_requires_specialist` argument,
which **defaults to `False`**
([`matching.py:165`](backend/scudo_mapping_mcp/matching.py)):

```python
if borderline_requires_specialist:        # :445 — Flask REST path only
    status = MappingStatus.NEEDS_REVIEW   # :450  fail SAFE to human review
elif best.similarity >= floor:            # :456 — legacy/AGENT path
    status = MappingStatus.AUTO_MAPPED    # :458  auto-maps on the raw dense score
```

Only the Flask route
[`backend/routes/mapping.py:568-569`](backend/routes/mapping.py) passes
`borderline_requires_specialist=True`. Streamlit calls
`get_agent(provider).run(ref)` and never sets it — so on the Streamlit surface a
borderline match with no specialist **auto-maps** rather than routing to a human.
Combined with `SCUDO_DENSE_BACKEND=opus`, that is an LLM-supplied score
auto-mapping unreviewed.

**The window is real, not theoretical.** With the shipped defaults
(`floor=0.75`, `half=0.05`), `_gate_thresholds()` returns
`pass=0.80 / borderline=0.70`, so the borderline band is `[0.70, 0.80)` and the
`>= floor` sub-branch covers **`[0.75, 0.80)`** — a non-empty window in which a
score is simultaneously borderline *and* auto-mapped. Derived by calling
`_gate_thresholds(0.75, 0.05)` directly, and the default confirmed by
`inspect.signature(map_vendor_product)` → `False`.

The cap `min(best, specialist)` at [`backend/scudo_mapping_mcp/matching.py:479`](backend/scudo_mapping_mcp/matching.py) applies **only** when a
specialist is both configured *and* concurring. Of the two `min(best,
borderline_threshold - 0.01)` caps, only `:406` is the contract-violation
("INVARIANT VIOLATION") branch — `:512` is the ordinary
specialist-disagreement path, not a violation. An earlier draft called both
violations.

So a hallucinated 0.99 **auto-maps on the PASS band with no specialist
involvement at all** — no abstention required. Verified by execution: with a
store returning dense=0.99 and no specialist, `confidence` came out `0.99`,
status `AUTO_MAPPED`. The same structure exists in [`backend/scudo_mapping_mcp/store/falkordb_store.py:470-493`](backend/scudo_mapping_mcp/store/falkordb_store.py),
so this is not one store's quirk.

**And the cap is off by default anyway.** `specialist_from_env()`
([`backend/scudo_mapping_mcp/specialist.py:221-222`](backend/scudo_mapping_mcp/specialist.py)) returns `None` unless `SCUDO_SPECIALIST_BACKEND` is
set. Streamlit reaches the matcher via `get_agent(provider).run(ref)`
([`streamlit_app.py:815`](streamlit_app.py) → [`backend/scudo_mapping_mcp/agent.py:363`](backend/scudo_mapping_mcp/agent.py) → `specialist_from_env()` → `None`),
so **by default, on the Streamlit surface, the cap is never in play.**

Be precise about "never": if someone sets `SCUDO_SPECIALIST_BACKEND`,
`specialist_from_env()` returns a real specialist and the `:479` cap **does**
become active on Streamlit. What does *not* change is
`borderline_requires_specialist`, which [`agent.py:361-366`](backend/scudo_mapping_mcp/agent.py)
never passes (the whole `map_vendor_product(...)` call — it appears **0 times**
in that file) — so the borderline branch still auto-maps on that path regardless
of the specialist. Only the Flask route
[`backend/routes/mapping.py:568-569`](backend/routes/mapping.py) passes a real specialist with
`borderline_requires_specialist=True`.

The module says so itself — [`backend/scudo_mapping_mcp/opus_dense.py:46`](backend/scudo_mapping_mcp/opus_dense.py): *"Opus is NOT deterministic —
callers expecting reproducible scores must set
`SCUDO_DENSE_BACKEND=jaro_winkler`."*

So **"the LLM can only lower, never inflate" is false.** Leave the variable
unset and the deterministic claim holds.

**The claim was also live in the source, and has now been corrected.**
[`streamlit_app.py:128-151`](streamlit_app.py) used to carry it as a comment:
*"It can only lower the deterministic anchor, never inflate it (matching.py:479
caps via min(best, specialist))."* Correcting the documents while leaving that
comment would have meant the next reader re-learning the wrong thing from
source, so the comment was replaced (2026-08-12, with the user's approval) by
one that names all four uncapped branches, both auto-mapping branches, and the
`SCUDO_DENSE_BACKEND` default as the actual reason the deterministic claim
holds. **If you are typing this file in by hand, type the new comment too** —
it is the only correction in this work stream that touches application source.

---

## 2. Do not re-introduce these corrected mistakes

Earlier drafts — mine and the other agent's — claimed the opposite of each of
these. All are verified against the live tree.

| Claim you may find in older docs | Reality |
|---|---|
| "The React path cannot work on Citrix" | **False** — only Vite dev on `:3000` cannot. See §1.1. |
| "The Bedrock EU/US region defect needs fixing" | **Already fixed.** Set `AWS_REGION`; type nothing. |
| "You need to build an Approve/Reject correction UI" | **Already exists**, [`streamlit_app.py:1018`](streamlit_app.py). |
| "Codex/independent review is unavailable" | **False.** `codex-cli 0.145.0` is installed; two review rounds ran successfully. |
| "The LLM can only lower the score" | **False** — see §1.2. |
| "`unset CONSOLE_DB_BACKEND` switches you to Aurora" | **False** — two launchers put it back. See below. |
| "Only two handover docs are current" | **False** — five are. See §1.1. |

**On `unset`:** [`streamlit_app.py:82`](streamlit_app.py) calls
`os.environ.setdefault("CONSOLE_DB_BACKEND", "sqlite")` directly. In
[`start_local.py`](start_local.py) the value is declared at `:65` but the line that actually
defeats your `unset` is the loop at **`:77`** (`env.setdefault(key, value)`).
Cite `:77` when you explain the mechanism. The fix is
`export CONSOLE_DB_BACKEND=postgres`, not `unset`.

**Line numbers drift, and faster than you expect.**
[`streamlit_app.py`](streamlit_app.py) grew 872 → 1101 lines during this work
(the last +22 was the comment correction described above, applied at the very
end — which is exactly why every anchor into that file was re-derived after it).
Worse, **other Claude sessions edit this tree while you are writing about it**:
during the link pass,
[`backend/scudo_mapping_mcp/config.py`](backend/scudo_mapping_mcp/config.py)
and [`backend/scudo_mapping_mcp/agent.py`](backend/scudo_mapping_mcp/agent.py)
were modified by two other sessions (`f102dafb`, `b4cae645` in
`~/.claude/audit.jsonl`), moving eight anchors that had been verified an hour
earlier — `config.py:296` → `:301`, `agent.py:571` → `:572`, and so on.

**So never re-type a line number from a document.** Match on content:

```bash
grep -n 'SCUDO_DENSE_BACKEND", "jaro_winkler"' backend/scudo_mapping_mcp/config.py
```

Every anchor in this brief was re-derived that way on 2026-08-12 and is exact
as of then. **The file links will not rot; the `:NNN` suffixes will.**

---

## 3. Aurora and Bedrock — the short version

Full detail is in [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) (610 lines as of 2026-08-12,
47 `file:line` citations, all verified in range). Headlines:

- **There is no Terraform in this repo.** Not one `.tf` file — it is all
  CloudFormation. If JPMC is "struggling with Terraform", the honest answer is
  there is none to fix; Bedrock/Aurora access is an **account-permissions**
  task.
- **Bedrock needs zero code changes.** Dropdown + one bearer token. One
  caveat on "set `AWS_REGION` and type nothing": [`backend/scudo_mapping_mcp/opus_dense.py:64`](backend/scudo_mapping_mcp/opus_dense.py) hardcodes
  `DEFAULT_BEDROCK_MODEL_ID = "eu.anthropic.claude-opus-4-8"`, so
  `AWS_REGION=us-east-1` without `SCUDO_BEDROCK_MODEL_ID` leaves that path on
  an `eu.` profile ID. Only reachable under `SCUDO_DENSE_BACKEND=opus`, so it
  is not a default-path defect — but "type nothing" is only true for
  `eu-west-2`.
- **"Aurora" is three unrelated systems:** the console DB (`CONSOLE_DB_*`,
  psycopg), the matching store (`STORE_BACKEND` — **no Aurora implementation
  exists**, and writing one means 16 abstract methods on `RetrievalStore`,
  counted from the AST), and agent memory (`SCUDO_AURORA_*`, RDS Data API,
  **not reachable from Streamlit at all** — verified by importing the app and
  enumerating `sys.modules`). A working console DB tells you nothing about the
  other two.

  If someone tries `STORE_BACKEND=aurora`, **which error they get depends on
  how they launched.** Both are `ValueError`; neither says "write an Aurora
  store". Measured, not inferred:

  | How they launched | Error they see |
  |---|---|
  | Via [`start_local.py`](start_local.py) or [`streamlit_app.py`](streamlit_app.py) (**the normal case**) | `ValueError: Unknown STORE_BACKEND 'aurora'. Use 'falkordb' … 'local_file'` — the factory raise, [`store/factory.py:56`](backend/scudo_mapping_mcp/store/factory.py) |
  | Bare `python -c "import ..."` with nothing else set | `ValueError: SCUDO_PERSIST_TARGET='aurora' not in (…)` — the config raise, [`config.py:272`](backend/scudo_mapping_mcp/config.py) |

  The split is because both launchers preset `SCUDO_PERSIST_TARGET=local_file`
  ([`start_local.py:50-51`](start_local.py),
  [`streamlit_app.py:75-76`](streamlit_app.py)), which satisfies the config
  allow-list so execution reaches the factory. An earlier draft of this brief
  reported only the config error — true for a bare import, wrong for the way
  JPMC actually starts the app. `persist_target` defaults to `store_backend`
  ([`config.py:262-267`](backend/scudo_mapping_mcp/config.py)), which is why the
  bare case diverges at all.
- **`export` on every line.** A bare `NAME=value` is a shell variable; Python
  never sees it, and the app silently falls back to SQLite with no error.

**Two reproduced gaps in the SQLite fallback** (`AttributeError` both times, not
theoretical — I ran them):

1. `translate_params()` calls `.replace()` on the SQL, but [`backend/routes/datasets.py`](backend/routes/datasets.py)
   passes a `psycopg.sql.Composed` → breaks Datasets CREATE/ALTER.
2. `SqliteConnection` has no `transaction()` → breaks the Ingestion per-row
   SAVEPOINT.

Read paths are fine. Both work on Aurora. Note that [`backend/db.py:65-69`](backend/db.py)'s
docstring claims Ingestion works locally — it **over-claims**.

---

## 4. What to do, in order

1. **Fix the run instructions first** — the seven docs listed in §1.1, not all
   ten that merely mention `:3000`. Name the audience: Streamlit `:8501` and
   Flask `/app/` for the Citrix desktop, Vite `:3000` for a developer laptop.
   This is the fix that stops the client following an instruction that cannot
   work. Note [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md) are **our** internal instruction
   files, not client deliverables — correct them, but they are not part of the
   JPMC-facing set.
2. **Add a one-line `SUPERSEDED` banner to each genuinely stale doc.** Use the
   inventory at [`HANDOVER_CONSOLIDATION.md:39-52`](HANDOVER_CONSOLIDATION.md) to pick them. **Do not banner
   [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) or [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md)** — both are current.
3. **Fold [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) into [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md).** Two current
   documents that overlap is the worst combination.
4. **Write the agent explainer — this is the genuine unmet client need.**
   "They've not done agents like this before" and the document does not exist.
   It must open with §1.2 above. The other five questions it must answer are
   specified in [`HANDOVER_CONSOLIDATION.md:152-163`](HANDOVER_CONSOLIDATION.md).
5. **Move superseded files to `docs/history/` — do not delete.** These are
   client-facing and removal has not been approved.

`JPMC_AGENTS_EXPLAINED.md` and `docs/history/` do not exist yet — they are
step 4 and step 5 output, not missing dependencies.

---

## 5. How to work on this

- **Independent verification, not self-critique.** Do not grade your own
  output from the same context. Codex works — use it
  (`mcp__codex__codex` / `codex-reply`). Two rounds on the Aurora doc produced
  12 findings, **4 of which were on the fixes for the first 8**. Applying
  findings is not the end of the loop; re-review the fixes. This brief itself
  went through a Codex round that returned "do not hand this over unchanged"
  and 10 findings — §1.1 and §1.2's citations are what that round corrected.
- **Hand-verify any refutation that would reverse a load-bearing decision.**
  Reviewers are sometimes wrong on technicalities, and a reviewer without your
  environment cannot re-run your reproductions. Every finding cited here was
  checked at source, and every command in §6 was executed, before being
  written down.
- **`rtk` mangles measurements.** Its summariser rewrites output before your
  `grep` sees it — it once reported a route clean that had 89 brand tokens in
  it. If you are about to *act* on a number, use `rtk proxy` or read it in
  Python. It mangled measurements twice more during the verification of this
  brief — `ls *.md | wc -l` returned `1`. Also note `grep -c` counts **lines,
  not matches**, with or without `-o`; use `grep -o … | wc -l` for matches.
- **Watch for concurrent sessions.** Another Claude session overwrote a
  deliverable mid-work here. Attribute via `session_id` in
  `~/.claude/audit.jsonl`, then **re-apply your corrections onto their text** —
  do not revert.
- **Never claim "production ready" or "fully operational."** Say "task is
  complete and ready for review."
- **Don't bury a failing check.** File it; decide deliberately.

---

## 6. Verification commands

Every line below was run on 2026-08-12 and produced the stated result.

```bash
# JPMC's two surfaces
streamlit run streamlit_app.py                  # :8501, matching + HITL
SCUDO_SERVE_FRONTEND_DIST=1 python start_local.py   # :5000/app/, console pages

# Tests — use EITHER of these. They are NOT equivalent; see the table below.
cd backend && PYTHONPATH=. python3.11 -m pytest scudo/tests/ -q   # 468 passed, 2 failed
PYTHONPATH=backend python3.11 -m pytest backend/scudo/tests/ -q   # 468 passed, 2 failed

# Graph fixture regen — MUST run from backend/. The form in CLAUDE.md
# ("python -m backend.scudo.build_matching_graph" from repo root) FAILS with
# ModuleNotFoundError: No module named 'scudo_mapping_mcp'.
cd backend && python3.11 -m scudo.build_matching_graph

ruff check streamlit_app.py                     # clean
```

**Expect `468 passed, 2 failed` (470 collected)** — but only if you set
`PYTHONPATH`. An earlier draft of this brief said "422 passed, 2 failed" and
claimed the two invocations were equivalent. Both were wrong. Re-measured
2026-08-12 by running each form and reading the real summary line:

| Invocation | Result | Why |
|---|---|---|
| `cd backend && PYTHONPATH=. python3.11 -m pytest scudo/tests/ -q` | **468 passed, 2 failed** | correct |
| `PYTHONPATH=backend python3.11 -m pytest backend/scudo/tests/ -q` | **468 passed, 2 failed** | correct |
| `python3.11 -m pytest backend/scudo/tests/ -q` (**no `PYTHONPATH`**) | **467 passed, 3 failed** | one extra, spurious failure |

The third row's extra failure is
`test_improvement_loop_e2e.py::test_real_subprocess_evaluation_promotion_rollback_and_migration_lifecycle`.
It spawns a **real subprocess**, which inherits an environment without
`backend/` on the path and dies with `ModuleNotFoundError: No module named
'scudo'`. It is an artefact of the invocation, not a broken test — setting
`PYTHONPATH` makes it pass. **If you see 3 failures, you forgot `PYTHONPATH`;
you have not broken anything.**

**The 2 real failures are pre-existing**, not yours: both in
[`backend/scudo/tests/test_provenance.py`](backend/scudo/tests/test_provenance.py), documented in [`CLAUDE.md`](CLAUDE.md) as
unadjudicated. Do not "fix" them without adjudicating first.

**Bare `pytest` at the repo root does not "collect nothing" — it errors**, with
**11** collection errors: 7 in `backend/tests/test_ingest_*.py` and **4** in
`jpmc-port/tests/` (`test_improvement_loop_e2e`, `test_promotion_monitor`,
`test_shared_improvement_contract`, `test_taxonomy_graph_parity`). Exit code 2,
`Interrupted: 11 errors during collection`. An earlier draft said 8; re-counted
by parsing the output. Older docs describe this as a silent no-op; it is not.

**Regenerating the graph fixture dirties the worktree** with an `analyzedAt`
timestamp change only. That churn is expected ([`CLAUDE.md`](CLAUDE.md)); revert it with
`git checkout -- backend/scudo/fixtures/matching-graph.json` if you ran the
build only to test the command. Note it writes **two** files —
`matching-graph.json` and `fixtures/meta.json`. In the verification run
`meta.json` came back byte-identical so the single-file revert sufficed, but
check `git status` rather than assuming.

Further verified context lives in the project memory at
`~/.claude/projects/-Users-anthonylui-MatchMaker-MatchMaker/memory/` — read the
`MEMORY.md` **in that directory** first. It is **not** the repo-root
`MEMORY.md`, which is a different file ("Verified Review Notes"), contains none
of the entries below, and is deliberately **not committed** — so a clone has
neither. Both live outside what a JPMC clone gives you. Most relevant here: `aurora-bedrock-file-surface`,
`streamlit-hitl-applied-codex-review`, `sqlite-fallback-two-gaps`,
`concurrent-session-doc-overwrite`, `rtk-mangles-measurements`.
