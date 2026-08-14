# Handover — consolidate the JPMC/Citrix handover docs

**Written:** 2026-08-12 · **Branch:** `main` · **For:** the next agent

Your job is **consolidation, not new features**. There are 12 overlapping
handover documents totalling ~4,500 lines, written across 8 days as the
target environment changed underneath them. JPMC is reading these to run the
system. Some of them now contradict each other.

Read this whole file before touching anything. The most important section is
§3 (what is actually true), because several documents assert things that were
true on the day they were written and are not true now.

---

## 1. Where the work stands

**The system works locally with nothing installed.** Verified by execution on
2026-08-12: no Docker, no PostgreSQL, no FalkorDB, no Neptune, no Bedrock, no
AWS credentials. Matching runs, the agent narrates, HITL corrections persist
across a process restart.

**JPMC's current surface is Streamlit + SQLite**, not the React console.
Citrix group policy blocks `esbuild.exe`, so Vite cannot start at all. That
single fact invalidates the run instructions in the older documents.

**Open client asks, in priority order:**
1. Bedrock access (they are doing this themselves — they already have Bedrock
   working in another VS Code project)
2. Aurora access — blocked on their platform team, **not on this code**
3. Understand the agent/memory architecture well enough to demo it

---

## 2. The consolidation problem — the actual inventory

All 12 were untracked when this audit was written. **Three are now TRACKED** —
[`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md),
this file, and
[`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
(committed by another session at `51cff58`). The other 11 remain untracked.
Oldest first:

| Date | File | Lines | Status |
|---|---|---|---|
| 08-04 | [`JPMC_LOCAL_CHANGES.md`](JPMC_LOCAL_CHANGES.md) | 1391 | **stale** — React/Flask era, pre-Streamlit |
| 08-04 | [`JPMC_UPLOAD_AND_MATCH_REVIEW.md`](JPMC_UPLOAD_AND_MATCH_REVIEW.md) | 513 | review brief, largely delivered |
| 08-06 | [`JPMC_LOCAL_RUN_HANDOVER.md`](JPMC_LOCAL_RUN_HANDOVER.md) | 313 | **partly stale** — [`start_local.py`](start_local.py)/React path |
| 08-06 | [`JPMC_PORT_TYPE_IN.md`](JPMC_PORT_TYPE_IN.md) | 447 | `jpmc-port/` only — separate work stream |
| 08-06 | [`CITRIX_FOLLOWUP.md`](CITRIX_FOLLOWUP.md) | 149 | superseded |
| 08-07 | [`CITRIX_CHECK_FRONTEND.md`](CITRIX_CHECK_FRONTEND.md) | 163 | superseded |
| 08-07 | [`CITRIX_UPDATE_2.md`](CITRIX_UPDATE_2.md) | 210 | superseded |
| 08-07 | [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) | 130 | still valid (React-via-Flask fallback) |
| 08-07 | [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) | 113 | current |
| 08-07 | [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) | 209 | **current — the Streamlit source of truth** |
| 08-08 | [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) | 273 | internal; P0/P1/P2, self-critical |
| 08-12 | [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) | 610 | **current — the Aurora/Bedrock source of truth** |

**CORRECTED 2026-08-12 — five documents are current, not two.** This line
previously read "the two current documents are the last two", which contradicts
its own table two rows above (`CITRIX_NO_NODE.md` "still valid",
`STREAMLIT_RUN.md` "current") and would have buried working documents. The
current set is `CITRIX_STREAMLIT_HANDOVER.md`, `JPMC_AURORA_BEDROCK_FILES.md`,
[`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md), [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md),
and [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
— the last of which post-dates this table and is **not listed in it at all**
(a separate agent's work stream: offline skill-optimizer, promotion into Aurora
agent memory). Treat everything dated 08-06 or earlier as historical unless you
verify a specific claim still holds.

### The contradiction that will bite JPMC

**10** root `.md` files mention **:3000** (7 actually instruct you to open it);
an earlier draft said nine. On the Citrix desktop **:3000 cannot work** — it is
the Vite dev server and Node is blocked.

**Do not read that as "`start_local.py` cannot work."** An earlier version of
this paragraph lumped the two together; that is wrong and would strip out the
only route to the console UI. [`start_local.py`](start_local.py) is
**required** for the Flask-served `/app/` fallback — see
[`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md). What cannot work is the Vite dev
server on `:3000`, not the launcher.

So JPMC has **two** working surfaces: `streamlit run streamlit_app.py` on
**:8501**, and `start_local.py` serving `/app/` on the Flask port. §7 below
lists Streamlit only — that is an omission, not a statement that `/app/` is
unavailable.

[`README.md`](README.md), [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md) also still reference the
[`start_local.py`](start_local.py) path as the primary route. They are not wrong for a
developer laptop; they are wrong for JPMC's desktop. Any consolidation must
make the audience explicit rather than deleting one or the other.

---

## 3. Ground truth — verified by execution, cite this over any document

I ran each of these. Re-run them if you doubt them; do not trust prose.

**The memory loop survives a restart.** Two separate processes,
`STORE_BACKEND=local_file`:

| | Result |
|---|---|
| Process 1, first match | 0.8839, `AUTO_MAPPED` |
| Human approves | 1 line in the journal |
| **Process 2, fresh** | 0.8839, **`APPROVED`**, rationale **`precedent`** |

**Console DB is an env-var switch, no code edit.** `CONSOLE_DB_BACKEND=sqlite`
→ SQLite; unset → psycopg/Aurora. A remote `CONSOLE_DB_HOST` with an empty
`CONSOLE_DB_PASSWORD` raises before connecting ([`backend/db.py:41-45`](backend/db.py)).

**Nothing AWS loads locally.** Importing the Flask app with the local env
loads **no** `aurora*` module and **does not import `boto3`**.

**The score is deterministic and LLM-free — while `SCUDO_DENSE_BACKEND` is
unset or `jaro_winkler`.** State that precondition; it is one env var wide.
`_jaro_winkler` returns `0.908333` repeatably; no boto3/Bedrock in its source.
The model narrates only. [`backend/scudo_mapping_mcp/agent.py:572`](backend/scudo_mapping_mcp/agent.py) — "matcher runs regardless of what the LLM recommended".

**The precondition is load-bearing.** Set `SCUDO_DENSE_BACKEND=opus` and the
model's score *becomes* `Candidate.similarity` and reaches published
`confidence` **uncapped on four branches, two of which auto-map with no human
review**. It can move the number **either way**, not just down. The default
([`backend/scudo_mapping_mcp/config.py:301`](backend/scudo_mapping_mcp/config.py))
is what makes the sentence above safe — **never claim a cap protects you**.
Full derivation: [`JPMC_IMPORT_AGENT_BRIEF.md`](JPMC_IMPORT_AGENT_BRIEF.md) §1.2.

**There is no Aurora matching store.** `get_store()` with
`STORE_BACKEND=aurora` raises `ValueError`. `RetrievalStore` has **16**
abstract methods. Nuance: `Settings.from_env()` *accepts* `aurora` — only
`SCUDO_PERSIST_TARGET` is allow-listed — so the failure surfaces at first
store use, not import.

**There is no Terraform in the repo.** Zero `.tf` files; all IaC is
CloudFormation. JPMC's "we can't do the Terraform" is an account-permissions
task, not a code task.

**[`backend/scudo_mapping_mcp/store/falkordb_store.py`](backend/scudo_mapping_mcp/store/falkordb_store.py) must stay on disk** even though FalkorDB is
unused — [`backend/scudo_mapping_mcp/opus_dense.py:149`](backend/scudo_mapping_mcp/opus_dense.py) imports `_jaro_winkler` from it. The pip package
is not needed. Deleting the file breaks scoring entirely.

**The React-via-Flask fallback works, but its bundle is not in git.** Added
2026-08-12. `backend/app.py:121,128,132` serve `frontend/dist/` at `/app/`
behind `SCUDO_SERVE_FRONTEND_DIST`, and the local bundle is correctly built
with `--base=/app/`. But `git ls-files frontend/dist` returns **0 rows** —
`git status` shows `?? frontend/dist/`, so despite `.gitignore:18-19`
un-ignoring it deliberately, it has never been committed. [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md)
reads as though it had been. A git recipient on a Node-blocked desktop
therefore gets no console UI at all. Establish what JPMC actually holds before
pointing them at `/app/`; committing the bundle needs the user's approval.

### Two claims I corrected mid-session — do not reintroduce them

1. **The Bedrock EU/US region-prefix defect is already fixed.**
   [`streamlit_app.py:158-174`](streamlit_app.py) derives `SCUDO_REGION` and the `eu.`/`us.`
   prefix together, and the preflight uses the same region as the run. Earlier
   drafts told JPMC to fix this. Do not.
2. **The Streamlit Approve/Reject correction UI already exists**
   ([`streamlit_app.py:1018-1101`](streamlit_app.py)), with a staleness guard and fail-loud error
   handling. An earlier draft called it missing.

Both were caught by re-reading the live file. **Line numbers in these docs
drift** — the Streamlit file grew from 872 to 1101 lines during the work.
Verify anchors before repeating them.

---

## 4. Recommended consolidation

**Do not delete anything without asking.** These are client-facing artefacts
and the user has not approved deletion. Propose, then act.

### Target: 4 documents, one per audience

| Keep | Audience | Built from |
|---|---|---|
| [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) | JPMC engineer — how to run it | itself + [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) |
| [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) | JPMC engineer — AWS switch-over | itself (current) |
| `JPMC_AGENTS_EXPLAINED.md` **(new)** | JPMC — what the agents/memory do | §3 above + §5 gaps |
| `docs/history/` | us | the 8 superseded files, moved not deleted |

### Suggested sequence

1. **Add a status banner to each superseded file** — one line at the top:
   `> SUPERSEDED 2026-08-12 — see CITRIX_STREAMLIT_HANDOVER.md`. This is the
   cheapest fix and immediately stops JPMC following stale instructions. Do
   this first, before any reorganisation.
2. **Fold [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) into [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md)** — they
   overlap heavily and both are current, which is the worst combination.
3. **Write the agent explainer.** This is the client's actual unmet need
   ("they've not done agents like this before"). It does not exist yet.
4. **Move superseded files to `docs/history/`** with a one-line index.
5. **Reconcile [`README.md`](README.md) / [`CLAUDE.md`](CLAUDE.md)** so the Citrix path is named
   alongside the laptop path, rather than the laptop path appearing canonical.

### The agent explainer should answer, in this order

1. What is an agent here? (a tool-using loop over six tools; since `chat.py`
   it also answers free-text questions — but it still does not score)
2. Who computes the score? (**the matcher, deterministically** — not the LLM)
3. What does the LLM add? (narration/reasoning trace; swap Opus→Haiku and the
   number does not move — say this out loud, it pre-empts the obvious question)
4. What is "memory"? (a human decision becomes a precedent; the next match
   reuses it — stored in `backend/.local/scudo_matching.sqlite3`, or as a
   readable `precedents.jsonl` under `STORE_BACKEND=local_file`)
5. How do I correct it? (Approve/Reject in the sidebar; approve reuses, reject
   excludes and re-ranks)
6. What happens if Bedrock fails? (you still get a valid score, and the UI
   warns you — **without the warning this is the most dangerous state**)

---

## 5. Real gaps — do not paper over these

**RESOLVED 2026-08-14 — the agent is now conversational.**
`backend/scudo_mapping_mcp/chat.py` adds free-text chat over the SAME six tools
the mapping agent uses, surfaced as Streamlit step 04. Two backends: `bedrock`
(real Claude, genuine tool-calling loop) and `scripted` (keyword-routed, real
catalogue data, no AWS — honest about not being a model). The mapping path
itself is unchanged: `get_agent(provider).run(ref)` is still a generator over
one product reference. **For a client demo of agent reasoning use `bedrock`;
the scripted responder is a no-AWS stand-in, not evidence.**

**`override` is not exposed in Streamlit.** Approve and Reject exist; mapping
to a *different* node from that screen does not. The store and the Flask API
both support it ([`backend/scudo_mapping_mcp/store/base.py:78-119`](backend/scudo_mapping_mcp/store/base.py)).

**No Aurora-backed matching store** — but durability is solved.
`STORE_BACKEND=scipy_sqlite` (default in all three launchers) implements the
full 16-method `RetrievalStore` over SQLite. What is still missing is
*sharing*: it is single-host only, so multiple ECS tasks/Lambdas cannot write
to it. That is what an Aurora store would add, and the AWS templates
deliberately stay on FalkorDB. Use `local_file` only when you want to *show*
the JSONL journal on screen.

**Agent memory (`SCUDO_AURORA_*`) is unreachable from Flask/Streamlit.** It
is a deployed-Lambda concern. Do not let anyone spend a day wiring
`SCUDO_AURORA_*` on the desktop expecting the UI to change.

**Never verified:** a real Aurora connection or a live Bedrock invoke from the
JPMC account. Both are unreachable from this machine.

---

## 6. Constraints inherited from the user — follow these

- **Every change JPMC makes is typed by hand.** Minimise edit surface. Prefer
  new files over scattered edits. Always cite exact `file:line`.
- **Comment out, never delete** MySQL/FalkorDB/Neptune call sites.
- **No commits or deploys unless explicitly asked.** Everything above is
  uncommitted on `main` by choice.
- **Do not claim production readiness** or "fully operational". Say "task is
  complete and ready for review".
- **Re-verify before asserting.** Two of my own claims were stale within one
  session. Prefer running code to reading prose, and state the verification
  basis.
- **Codex review — CORRECTED 2026-08-12.** This section previously said Codex
  was unavailable. It is **installed and working**: `codex-cli 0.145.0` at
  `/Users/anthonylui/bin/codex`, reachable via the `mcp__codex__codex` /
  `codex-reply` MCP tools. Two full review rounds ran against
  [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) in the session that wrote this correction
  (12 findings, all applied). The three parallel audit agents did fail on a
  provider error — that is a **subagent fan-out** problem, not a Codex one.
  So §3 of this document is still single-reviewer and should be re-reviewed,
  but the gate itself does not need re-establishing: just call Codex.

---

## 7. Verification commands you will want

```bash
# Streamlit (JPMC's actual surface)
streamlit run streamlit_app.py            # :8501

# Backend tests — cwd and PYTHONPATH both matter
cd backend && PYTHONPATH=. pytest scudo_mapping_mcp/tests/ -q

# The pytest wrapper can falsely report "No tests collected":
python3.11 -m pytest -vv                  # use this for real evidence

# Prove the memory loop across a restart (two processes, local_file store)
# see JPMC_AURORA_BEDROCK_FILES.md "Verification basis" for the exact script
```

Repo state, **re-measured 2026-08-12**: **34 modified, 43 untracked** (77) on
`main`; HEAD is `51cff58`. Both numbers drift within the hour — other sessions
were committing during this work (`8c53dbc` → `a92b8d0` → `e3baa75` →
`51cff58`), so re-run `git rev-parse --short HEAD && git status --porcelain |
wc -l` rather than trusting this line. The worktree is deliberately dirty — several unrelated work
streams are in flight (self-improvement gate, jpmc-port, costings). **Preserve
unrelated uncommitted changes**; edit shared files narrowly.

---

## 8. Related

- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — Aurora/Bedrock file list + verification basis
- [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) — the Streamlit build and its known issues
- [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) — internal, self-critical; useful for what went wrong
- [`AGENTS.md`](AGENTS.md) — learned preferences and workspace facts (kept current)
