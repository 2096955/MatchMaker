# ARB verification handoff (for stuck Claude session)

**When:** 2026-07-19  
**Why:** Terminal Claude hit tool serialization / write failures while adversarially verifying `ARB_REVIEW_jpmc-port.md`. Cursor finished the dogfood + claim checks.

## Capone trunk note (from terminal 5 — for reviewing agent)

**Read:** `docs/ARB_REVIEWER_NOTE_capone_aurora.md`.

Capone Aurora deploy does **not** match GitHub. Aurora work is uncommitted on a stale `848f104` base and was tangled with a streaming refactor (`mapping.py` / `test_ingest_stream_route.py`) that must remain **dropped** on rebase. That is Capone deploy hygiene — **not** a jpmc-port failure. jpmc-port A/B Capone arm uses local `backend/scudo` via `python -P`, not the Aurora ECS image.

## Fresh results (do not re-run unless you want to)

| Check | Result |
|-------|--------|
| `pytest tests/ -q` | **46 passed** in 2.65s (env `-u SCUDO_AGENT_MODE`; corrected from stale 42) — `/tmp/jpmc-port-pytest-clean.txt` |
| `run_e2e.py` | **published** + project `dispatched: 1` — `/tmp/jpmc-arb-e2e.txt` |
| A/B deterministic | **3/3** target+outcome agreement 1.0 — `/tmp/scudo-ab-arb-watch/ab_report.json` |
| `/health` | `ok`, `model=us.anthropic.claude-opus-4-8` |
| `/demo/` | HTTP **200** |
| Claim matrix | **22/22 PASS** — `docs/ARB_VERIFICATION_REPORT.json` |

## Code claims confirmed

Opus 4.8 pin · `max_tokens=128000` · Verifier tools · `agent_loop` wired in orchestrator · floor 0.80 · `learn_from_teaching` on decision · dashboard façade SSE · vendored `dashboard-dist` · A/B harness · Playwright screenshot present · teach→learn runtime precedent+notes.

## Honest gaps (see Correction set in ARB_REVIEW_jpmc-port.md)

- Live Bedrock IAM A/B not run  
- Golden set n=3  
- Prod Aurora/Neptune unset under `SCUDO_LOCAL`  
- Older Opus reports: `model` = requested id; shim echo not in-artifact (`stub_forbidden` was decorative)

## If you continue in Claude

1. Read `docs/ARB_VERIFICATION_REPORT.json` — treat as completed verification.  
2. Do **not** retry large inline JSON tool payloads; use small shell commands or Read.  
3. ARB pack remains `docs/ARB_REVIEW_jpmc-port.md`. Verdict: local evidence pack **stands**.
