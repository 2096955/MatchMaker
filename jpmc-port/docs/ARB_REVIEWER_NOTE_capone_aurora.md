# Note for reviewing agent — Capone Aurora vs GitHub (2026-07-19)

**Source:** Capone-side Claude session (terminal 5) — *not* a jpmc-port defect.  
**Why this is in the JPMC-port ARB pack:** reviewers must not treat Capone `backend/scudo` / Aurora deploy state as “what GitHub says,” and must not confuse Capone trunk hygiene with `jpmc-port/` evidence.

---

## What the Capone session established

| Fact | Implication for review |
|------|------------------------|
| **Capone Aurora deploy does not match GitHub** | Do not cite “deployed Capone” as proof of current `backend/scudo` HEAD behaviour |
| **Aurora work is uncommitted** on a **stale worktree base** (`848f104`) | Local Capone Aurora changes are tangled and not the remote trunk |
| That worktree also tangled in a **stale streaming refactor** (`backend/routes/mapping.py` + `backend/tests/test_ingest_stream_route.py`) | Deliberately **dropped** from the rebase/deploy path — do **not** “helpfully” re-add it as if it were lost |
| Broad suite gate after rebase: re-run on the **new** base; if more than the **known 2** `test_provenance.py` failures, **stop and report** — do not push | Capone known-red: Marketing / incoherent provenance greps |
| That session had **no AWS creds** and would **not commit** | Deploy/rebase is for a deploying agent with access, not for ARB paper claims |

**Recap (terminal 5):** Goal was make Capone Aurora deploy match GitHub. Confirmed it doesn’t. Handoff is rebase-and-push **dropping** the stale streaming refactor. Next step is a deploying agent — **not** an ARB claim that Capone Aurora already matches the repo.

---

## How this interacts with `jpmc-port` review

1. **jpmc-port A/B Capone arm** imports local `backend/scudo` via `python -P` — that is **source-tree Capone**, not the Aurora/ECS deploy at `848f104`.
2. **Do not** downgrade jpmc-port live Opus / deterministic evidence because Capone’s **deployed** Aurora is behind GitHub.
3. **Do** flag as an open Capone trunk risk: production Aurora ≠ GitHub until the rebase-and-push handoff is executed and verified.
4. Console/Aurora known gap remains: `CONSOLE_DB_*` PostgreSQL vs `scudo-poc-app.yaml` MySQL/3306 (see AGENTS.md / ARB honest gaps).
5. **Console DDL (2026-07-20):** `backend/init_db.sql` is now fully schema-qualified (`console.<table>`). Bootstrap no longer does `public`→`console` relocate. Deploying agent: after bootstrap, `SELECT table_schema FROM information_schema.tables WHERE table_name='tp_provider';` must return `console`.

---

## Reviewer checklist (Capone trunk only)

- [ ] Treat Capone Aurora deploy as **stale relative to GitHub** until a deploying agent confirms otherwise  
- [ ] Do **not** restore `mapping.py` / `test_ingest_stream_route.py` streaming refactor as part of Aurora alignment  
- [ ] After any Capone rebase onto a fresh base: full suite; tolerate only the known provenance pair; else stop  
- [ ] Keep Capone deploy remediation **out of** jpmc-port “ready for ARB” verdict wording  

---

## Pointers

- This pack’s main doc: `ARB_REVIEW_jpmc-port.md`  
- Live Opus / Cursor smoke reports: `OPUS_SMOKE_REPORT.json`, `OPUS_AB_REPORT.json`, `CURSOR_SMOKE_REPORT.json`  
- Capone known provenance red: `backend/scudo/tests/test_provenance.py` (Marketing / incoherent branch)
