# Citrix update #2 — changes since `CITRIX_FOLLOWUP.md`

Two parts. **Part A** is a code-hygiene task list — mechanical, low risk, do it
in order. **Part B** is what changed in the repo since the last note, so you
know what you are syncing against.

Nothing here changes behaviour. Every item in Part A is comment text only.

---

# PART A — cut the over-talk (work list)

## Why

Comments in this repo narrate *how a defect was found* — "an adversarial
verifier proved…", "a completeness critic measured…" — rather than what the
code does or why the rule exists. That is session provenance. A JPMC engineer
needs the rule; the provenance belongs in the handover docs.

**The test for each edit:** does the sentence tell the reader something that
changes what they would *do*? Keep it. Does it tell them how it was
discovered? Cut it.

**Keep the reasoning, cut the attribution.** These comments encode real
invariants that are expensive to rediscover — the point is to shorten them, not
delete them. `JPMC-LOCAL` markers stay as they are; they are useful.

## A1 — Remove reviewer attribution (21 sites, 10 files)

Delete the attribution clause; keep the surrounding technical explanation.

| File | Lines |
|---|---|
| `backend/scudo_mapping_mcp/tests/smoke.py` | 1857, 1964, 2014, 2217, 2256, 2274 |
| `backend/scudo_mapping_mcp/tests/test_temporal_validation.py` | 263, 386, 460 |
| `backend/scudo_mapping_mcp/tests/test_band_config_parity.py` | 106, 399 |
| `backend/scudo_mapping_mcp/tests/test_record_decision_auth.py` | 359, 392 |
| `backend/scudo_mapping_mcp/validations.py` | 189, 369 |
| `backend/scudo_mapping_mcp/persistence_mcp.py` | 147, 182 |
| `backend/scudo_mapping_mcp/mcp_server.py` | 136 |
| `backend/scudo_mapping_mcp/tests/test_trust_transitive_write_gate.py` | 482 |
| `backend/tests/test_flask_frame_gate.py` | 21 |
| `backend/scudo/orchestrator.py` | 291 |

Find with:

```bash
grep -rn "adversarial verifier\|completeness critic\|Found by an\|external reviewer" \
  --include="*.py" backend/
```

Worked example — `backend/scudo/orchestrator.py:288-291`:

```python
# BEFORE
# into the verifier's PROMPT (_call_verifier) and nothing enforces it — a
# verifier that scores well and ignores the injected text publishes anyway. A
# completeness critic proved exactly that, publishing a forked IRI end-to-end.
# So the real control has to live here, in the unconditional gate.

# AFTER
# into the verifier's PROMPT (_call_verifier) and nothing enforces it — a
# verifier that scores well and ignores the injected text publishes anyway.
# The real control has to live here, in the unconditional gate.
```

The invariant survives. Only "who noticed" goes.

## A2 — Trim historical narration in production files

"This used to be X" is useful **only** when someone might reintroduce X.
Otherwise it describes a state no JPMC reader has seen.

Keep (the old form is a live hazard someone could restore):

- `backend/scudo_mapping_mcp/store/base.py:267` — `vendor_signature` not
  lower-casing the vendor. Restoring it silently splits rank signals.
- `backend/scudo_mapping_mcp/config.py:221` — the `falkordb` default.

Trim to one line (the history is no longer load-bearing):

- `backend/scudo/lambda_handler.py:206-215` — ~10 lines quoting the old inline
  IRI mint. Reduce to: *"Canonical mint. Do not inline a second one — the IRI
  is the VendorProduct MERGE key."*
- `backend/scudo/lambda_handler.py:288` — "the fabricated canned mapping this
  used to invent…". Cut to what the code does now.
- `backend/scudo_mapping_mcp/mcp_server.py:130-143` — the "THIS IS THE THIRD
  COPY" block. Keep one line naming the other two files (they must stay in
  agreement); drop the before/after transcript.

## A3 — The density outliers

Measured comment-to-code ratio, worst first. Read these for *redundant*
comments — ones restating the line below them — not for a target number.

| File | Ratio | Note |
|---|---|---|
| `backend/run_local.py` | 66.7% | 14 comments / 42 lines. Superseded by `start_local.py`; consider reducing to a pointer. |
| `backend/scudo_mapping_mcp/models.py` | 44.5% | 195 comments. Field-level rationale; mostly legitimate — skim, do not bulk-cut. |
| `backend/scudo_mapping_mcp/store/factory.py` | 44.4% | The lazy-import explanation is load-bearing. Compress, keep. |
| `backend/scudo_mapping_mcp/store/local_file_store.py` | 38.4% | |
| `backend/scudo_mapping_mcp/config.py` | 32.4% | |
| `backend/scudo_mapping_mcp/matching.py` | 30.9% | |

**Do not chase a percentage.** `models.py` at 44.5% is largely field
documentation that earns its place. The signal to act on is narration, not
density.

## A4 — Verify

After each file:

```bash
python3 -m pytest backend/scudo/tests/ -q --ignore=backend/scudo/tests/test_local_file_store.py
python3 -m pytest backend/scudo_mapping_mcp/tests/ -q
cd backend && python3 -m scudo_mapping_mcp.tests.smoke && python3 -m scudo.tests.smoke
```

Expected, unchanged by any of this:

```
backend/scudo/tests          318 passed / 2 failed
backend/scudo_mapping_mcp    422 passed
mapping smoke                117/117
offline smoke                SCUDO SMOKE OK
```

The 2 failures are `test_provenance.py` — pre-existing, verified on a clean
HEAD worktree, **leave them failing**.

If a count moves, you cut something that was not a comment. Revert that file
and redo it.

---

# PART B — what changed since the last note

## B1 — FalkorDB stops being asked for

The cause was the **default**, not the store branches. `STORE_BACKEND`
defaulted to `"falkordb"`, so any entry point setting no environment opened a
connection to :6379 — and `start_all.sh` set no environment at all.

- `backend/scudo_mapping_mcp/config.py` — default is now `local_file`. Safe
  because every deployed path sets the variable explicitly (verified:
  `Dockerfile`, `scudo-dev-deploy.yaml` ×4, `scudo-poc-app.yaml`,
  `template.yaml`).
- `start_all.sh` — now delegates to `start_local.py` instead of running
  `app.py` bare, which is what caused both the 401s and the FalkorDB demand.

With the environment fully stripped, `get_store()` returns `LocalFileStore`.

## B2 — The publish gate now enforces

`_pre_verify_defects` output was only concatenated into the verifier model's
**prompt**. Nothing acted on it. A specialist could propose a node never
offered and it published — measured, `outcome: PUBLISHED`.

Two checks moved into `_gate_and_decide` as hard `PublishGateError` raises:
`vendor_product_iri` must echo the minted value, and `proposed_target_iri`
must be one of the offered candidates (fail-closed on an empty list).

**Rule worth carrying:** adding a check to `_pre_verify_defects` does not
enforce it.

## B3 — Agents are told the rules they are judged by

`vendor_product_iri` appeared **zero times** in the specialist's prompt while
the gate hard-rejects a mismatch on that field. The ten rubric dimensions had
**no definitions anywhere**, so the verifier invented them per call — and its
`total_score` drives a publish/retry/HITL gate.

Both fixed in `backend/scudo/prompts.py`: a HARD REQUIREMENTS block with the
IRI interpolated inline, and `_RUBRIC` defined once and rendered for both
models. `rubric_text()` raises if a dimension has no definition.

## B4 — Frontend

- Typed refusals were losing their useful half: all 16 call sites read
  `.error` and dropped `.detail`, so users saw a bare `frame_not_found`
  instead of "ingest it first". Fixed with one axios interceptor.
- The agent reasoning trace was arriving but rendered as raw JSON truncated at
  120 chars. Now rendered as a readable sequence (`data-testid="agent-reasoning"`).

See `CITRIX_CHECK_FRONTEND.md` for what to click.

## B5 — Docs

- `README.md` — Quick start rewritten around `start_local.py`; five appendices
  added covering rationale (local run, deterministic gates, agent rubric,
  refusals, verification).
- `CLAUDE.md` / `AGENTS.md` — corrected stale guidance that pointed agents at
  `run_local.py` (sets no auth env → 401s) and claimed Docker Postgres was
  required.

---

## Still open, deliberately

- **Temporal matching is built but unreachable.** The comparator and
  `SCUDO_TEMPORAL_VALIDATION` flag exist and are tested, but the DCAT loader
  drops `temporal_coverage` before it reaches a `TaxonomyNode` and
  `matching.py` never passes `node_temporal_coverage`. On a live run the check
  passes by default.
- **The Lambda HITL approve path bypasses the publish gate** — it writes
  `mapping_result` from the request body straight to the catalogue. A
  malformed IRI can reach the projection table by a route the auto-publish
  path rejects.
- **Security hardening is parked** by decision (in-house demo, not a hardening
  exercise).
