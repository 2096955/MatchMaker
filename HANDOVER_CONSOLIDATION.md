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

All 12 are **untracked** (never committed). Oldest first:

| Date | File | Lines | Status |
|---|---|---|---|
| 08-04 | [`JPMC_LOCAL_CHANGES.md`](JPMC_LOCAL_CHANGES.md) | 1391 | **stale** — React/Flask era, pre-Streamlit |
| 08-04 | [`JPMC_UPLOAD_AND_MATCH_REVIEW.md`](JPMC_UPLOAD_AND_MATCH_REVIEW.md) | 513 | review brief, largely delivered |
| 08-06 | [`JPMC_LOCAL_RUN_HANDOVER.md`](JPMC_LOCAL_RUN_HANDOVER.md) | 313 | **partly stale** — `start_local.py`/React path |
| 08-06 | [`JPMC_PORT_TYPE_IN.md`](JPMC_PORT_TYPE_IN.md) | 447 | `jpmc-port/` only — separate work stream |
| 08-06 | [`CITRIX_FOLLOWUP.md`](CITRIX_FOLLOWUP.md) | 149 | superseded |
| 08-07 | [`CITRIX_CHECK_FRONTEND.md`](CITRIX_CHECK_FRONTEND.md) | 163 | superseded |
| 08-07 | [`CITRIX_UPDATE_2.md`](CITRIX_UPDATE_2.md) | 210 | superseded |
| 08-07 | [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) | 130 | still valid (React-via-Flask fallback) |
| 08-07 | [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) | 113 | current |
| 08-07 | [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) | 209 | **current — the Streamlit source of truth** |
| 08-08 | [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) | 273 | internal; P0/P1/P2, self-critical |
| 08-12 | [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) | 592 | **current — the Aurora/Bedrock source of truth** |

**The two current documents are the last two.** Treat everything dated 08-06
or earlier as historical unless you verify a specific claim still holds.

### The contradiction that will bite JPMC

Nine documents tell the reader to run `start_local.py` and open the React UI
on **:3000**. On the Citrix desktop **that cannot work** — Node is blocked.
The correct instruction is `streamlit run streamlit_app.py` on **:8501**.

`README.md`, `CLAUDE.md` and [`AGENTS.md`](AGENTS.md) also still reference the
`start_local.py` path as the primary route. They are not wrong for a
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
`CONSOLE_DB_PASSWORD` raises before connecting (`backend/db.py:41-45`).

**Nothing AWS loads locally.** Importing the Flask app with the local env
loads **no** `aurora*` module and **does not import `boto3`**.

**The score is deterministic and LLM-free.** `_jaro_winkler` returns
`0.908333` repeatably; no boto3/Bedrock in its source. The model narrates
only. `agent.py:571` — "matcher runs regardless of what the LLM recommended".

**There is no Aurora matching store.** `get_store()` with
`STORE_BACKEND=aurora` raises `ValueError`. `RetrievalStore` has **16**
abstract methods. Nuance: `Settings.from_env()` *accepts* `aurora` — only
`SCUDO_PERSIST_TARGET` is allow-listed — so the failure surfaces at first
store use, not import.

**There is no Terraform in the repo.** Zero `.tf` files; all IaC is
CloudFormation. JPMC's "we can't do the Terraform" is an account-permissions
task, not a code task.

**`store/falkordb_store.py` must stay on disk** even though FalkorDB is
unused — `opus_dense.py:149` imports `_jaro_winkler` from it. The pip package
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
   `streamlit_app.py:139-155` derives `SCUDO_REGION` and the `eu.`/`us.`
   prefix together, and the preflight uses the same region as the run. Earlier
   drafts told JPMC to fix this. Do not.
2. **The Streamlit Approve/Reject correction UI already exists**
   (`streamlit_app.py:997-1079`), with a staleness guard and fail-loud error
   handling. An earlier draft called it missing.

Both were caught by re-reading the live file. **Line numbers in these docs
drift** — the Streamlit file grew from 872 to 1079 lines during the work.
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
5. **Reconcile `README.md` / `CLAUDE.md`** so the Citrix path is named
   alongside the laptop path, rather than the laptop path appearing canonical.

### The agent explainer should answer, in this order

1. What is an agent here? (a tool-using loop over 4 MCP tools, not a chatbot)
2. Who computes the score? (**the matcher, deterministically** — not the LLM)
3. What does the LLM add? (narration/reasoning trace; swap Opus→Haiku and the
   number does not move — say this out loud, it pre-empts the obvious question)
4. What is "memory"? (a human decision becomes a precedent; the next match
   reuses it; `precedents.jsonl` is readable on screen)
5. How do I correct it? (Approve/Reject in the sidebar; approve reuses, reject
   excludes and re-ranks)
6. What happens if Bedrock fails? (you still get a valid score, and the UI
   warns you — **without the warning this is the most dangerous state**)

---

## 5. Real gaps — do not paper over these

**The agent is not conversational.** `get_agent(provider).run(ref)` is a
generator over one product reference. There is no free-text entry point. The
client asked for "users engage with the Agents to intelligently query" — today
that means *watch a structured reasoning trace*, not *ask questions*. If they
want genuine Q&A that is new work; scope it explicitly.

**`override` is not exposed in Streamlit.** Approve and Reject exist; mapping
to a *different* node from that screen does not. The store and the Flask API
both support it (`store/base.py:78-119`).

**No Aurora-backed matching store.** ~16 methods of new code. Not needed for
a demo — `local_file` is the better demo anyway because the journal is
human-readable.

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

Repo state at handover: **42 modified, 44 untracked** files on `main`; HEAD
is `8c53dbc` (`docs: design for sterile client-demo fork`). The worktree is deliberately dirty — several unrelated work
streams are in flight (self-improvement gate, jpmc-port, costings). **Preserve
unrelated uncommitted changes**; edit shared files narrowly.

---

## 8. Related

- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — Aurora/Bedrock file list + verification basis
- [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) — the Streamlit build and its known issues
- [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) — internal, self-critical; useful for what went wrong
- [`AGENTS.md`](AGENTS.md) — learned preferences and workspace facts (kept current)
