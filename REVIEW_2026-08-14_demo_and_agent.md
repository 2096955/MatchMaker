# Review pack — Cognizant demo, agent chat, real Bedrock wiring

**Date:** 2026-08-14 · **Branch:** `main` · **Six commits, `a92b8d0` … `f569787`**

> ## ⚠ Read this first — corrected 2026-08-15 after three review passes
>
> This document shipped with a **false central claim**: that the LLM does not
> produce the score. On the path both launchers actually use, **it does**.
> Every affected section is corrected **in place** (marked `CORRECTED
> 2026-08-15`) rather than by an appendix, so no reader can hit the wrong
> sentence first — it was the one I told you to say to a client.
>
> The third pass re-measured every number against the working tree rather than
> trusting the second pass's text, and found that the corrections themselves had
> gone stale: the test counts, the smoke figure, and the §8a equivalence claim
> were all wrong by the time they were written. **Corrections rot at the same
> rate as the text they correct.**
>
> | Section | Status |
> |---|---|
> | §1 "the agent was decorative" | **WRONG — corrected**; same false claim, in the section read first |
> | §3 architecture note | **WRONG — corrected**; user-facing copy fixed in `fb61a00` |
> | §4.2 chat "same six tools" / "says it is not a model" | **Both wrong — corrected**; true of `bedrock` only, and 2 of 7 branches. Both were quoted from `chat.py`'s docstring, where they are **still live** |
> | §5.1 "score unchanged" | **Overstated — corrected**; concurrency can flip the band |
> | §6.1 sidebar as mitigation | **Was not one when written; now genuine** — `dense_arm_status()` reports effective state |
> | §6.4 "identical score" | **WRONG — corrected** |
> | §7 "Codex not installed" | **False — corrected** |
> | §7 test baseline | **WRONG twice — corrected**; export gave 4 failures, now **15** |
> | §5 defect #2, #4, #5 | **Listed as fixed, still open**; #5 made conditional, not removed |
> | §5 defect #7 | Fixed in `fb61a00`, verified |
> | §7 test leak | **RESOLVED `247905e`** — pinned to an incomplete env restore |
> | §8a concurrency | **Implemented but UNCOMMITTED**; its "byte-identical" claim is false and its counts are stale |
>
> **Every absolute test count in the original text is stale.** Measured on the
> current tree: mapping **617 passed** (doc said 569 / 585), full backend
> **1095 passed, 2 failed** (doc said 1047 / 1065), smoke **113/117** (doc said
> 117/117). The suite grew ~48 tests during the review. Quote a count with the
> invocation and tree that produced it, or do not quote it.
>
> **Still open and user-facing:** `streamlit_app.py:707` carries the false
> "deterministic either way" claim verbatim (§9 item 1), and the suite does not
> pass in the configuration both launchers ship (§9 item 2).
>
> **A fourth pass ran the §9 gate rather than only specifying it** (`rg
> deterministic` over `streamlit_app.py`, `chat.py`, `agent.py`,
> `run_cognizant.py` — 24 hits, each adjudicated in §9 item 1). It found **three
> more false claims** the three prior passes missed, all in comments:
> `chat.py:19`, `chat.py:10-12`, `streamlit_app.py:1237-1238`. Two of them are
> the very sentences this document quoted as its own evidence in §4.2 — the
> review read a module docstring as a specification. The gate as originally
> worded exempted "a comment" and so would have passed all three; it now reads
> **arm-conditional or accurate**. *Specifying a gate is not running it.*
>
> Everything else was hand-verified by the reviewers and holds: the §2
> diffstats, §6.2 (`strands_specialist.py` genuinely absent), §6.3 (single-host
> boundary), defects #1/#3/#6/#8/#9, the serve-flag `/app/` 404 finding,
> `chat.py` has zero tests, and `override` is not exposed.

Other work landed on `main` in parallel (the `scipy_sqlite` store, the
self-improvement gate). **This pack covers only the six commits listed below.**
Where that other work changed my assumptions mid-session, I have said so rather
than quietly rewriting history.

---

## 1. What was asked, and what it turned into

The brief moved three times, and each move invalidated something already
written. In order:

1. *"How much work to get this running at JPMC?"* → an Aurora/Bedrock file map.
2. *"Package it for a Cognizant machine — no Citrix port."* → one-click demo.
3. *"Everything wired with a real agent."* → Bedrock on by default, verified live.

The through-line: the system could always **match**, but nobody could **run
it** without insider knowledge.

> **CORRECTED 2026-08-15.** This paragraph originally ended *"and the agent was
> decorative — the score is deterministic, so an LLM that only narrates is easy
> to switch off and never notice."* That is the same false claim as §3, in the
> section a reader reaches first. The agent was never decorative on the shipped
> path: it produces the number.
>
> The sharpest measurement — **the "off" switch does not switch it off.**
> `SCUDO_DENSE_BACKEND` is a module-level `setdefault`, independent of the agent
> selector. Choosing the offline `scripted` agent — the one a user picks
> *precisely* to avoid AWS — still routes every candidate through Bedrock.
> Stubbing only `_opus_invoke_score` and running the real `map_vendor_product`:
>
> ```
> SCUDO_AGENT_BACKEND=scripted   (the offline narrator)
> opus_dense LLM calls = 14
> published: conf=0.99  band=pass  status=auto_mapped  target=FX Rates
> ```
>
> There are **three independent levers** — `SCUDO_AGENT_BACKEND` (narrator),
> `SCUDO_SPECIALIST_BACKEND` (borderline adjudication), `SCUDO_DENSE_BACKEND`
> (scoring) — and nothing on screen said so. The sidebar now reports all three,
> which is disclosure, not coupling: selecting `scripted` still leaves the LLM
> on the score.

---

## 2. Commits

| Commit | What | Size |
|---|---|---|
| `a92b8d0` | Aurora/Bedrock file map + handover triage | +872 −20 |
| `fd2a65e` | README: where the agent, tools and engine live | +55 −3 |
| `90da191` | One-click demo, agent chat, three upload points | +1815 −90 |
| `f0d7c12` | Chat: tell the agent which screen it is on | +70 −4 |
| `0a7806c` | Bedrock as the default across every surface | +109 −9 |
| `f569787` | Score opus dense candidates concurrently | +21 −6 |

---

## 3. The headline result — the agent genuinely works

Verified against **live Bedrock, eu-west-2**, with a real 12-hour key.

All three models answer `ConverseStream`: Opus 4.8 (2.3s), Sonnet 4.5 (1.5s),
Haiku 4.5 (0.7s).

A full agent run is a real three-tool reasoning loop, not narration over a
precomputed answer:

```
Tool #1: find_similar_products
Tool #2: get_taxonomy_node
Tool #3: map_vendor_product_tool
→ 0.85 PASS → Equity Prices
```

**Unprompted, it caught a data-quality signal**: it noticed `Q-CONTRACT-X`
"suggests a contract/rights artefact", checked whether the name and description
had been commingled or swapped, satisfied itself they had not, and said so
before deferring to the matcher. That is the moment worth showing a client —
it is the difference between a model writing prose and a model reasoning about
the data.

> ### ⚠ CORRECTED 2026-08-15 — this section was WRONG
>
> This document originally said: *"the LLM does **not** produce the score…
> switching Opus → Haiku changes the prose and the latency, not the number"*,
> and told you to say it first to a client. **That is false on the path both
> shipped launchers use.** Two independent reviews caught it; I reproduced it
> before accepting it.
>
> `config.py` defaults the dense arm to `jaro_winkler`, but
> `streamlit_app.py` and `run_cognizant.py` both override it to
> `SCUDO_DENSE_BACKEND=opus`. On that path an LLM re-scores every nominated
> candidate and **that float becomes the published confidence.** Stubbing only
> the network call and running the real `map_vendor_product`:
>
> | LLM returned | Published | Status |
> |---|---|---|
> | 0.93 | conf **0.93** | auto_mapped / pass |
> | 0.72 | conf **0.72** | needs_review / borderline |
>
> It also moves which node wins, and the sidebar model picker feeds
> `SCUDO_BEDROCK_MODEL_ID` into that scorer — so Opus → Haiku changes the
> number too.
>
> **What is actually true:** the *bands*, the validations and the publish gate
> are deterministic. The *similarity* is not, unless
> `SCUDO_DENSE_BACKEND=jaro_winkler`. Say that instead.
>
> Worse than a wrong review: the same false claim was being shown to **users**
> in three places (the Streamlit degraded-run warning, the scripted chat reply,
> and the chat system prompt). All three now branch on the live dense arm —
> fixed in `fb61a00`.

---

## 4. What was built

### 4.1 One-command demo

`run_demo.py` starts both servers; `run_cognizant.py` starts Flask with the
environment set **before** `app.py` is imported (that ordering is the whole
point — `config.py` reads env at import time).

| URL | |
|---|---|
| `:8501` | Streamlit — upload → match → reasoning → review → chat |
| `:5055/app/` | React console |
| `:5055/demo/` | Matching dashboard |

**Finding: no Node is needed.** `frontend/dist/` and `dashboard-dist/` are
vendored and Flask can serve them — they were merely gated behind two unset
flags (`SCUDO_SERVE_FRONTEND_DIST`, `SCUDO_SERVE_DASHBOARD_DIST`), which is why
`/app/` 404s under any other launcher. That is an unset variable, not a missing
build.

### 4.2 Agent chat (`backend/scudo_mapping_mcp/chat.py`)

Free-text chat over the **same six tools** as the mapping agent, and it does not
score. Two backends: `bedrock` (real tool loop) and `scripted` (keyword-routed,
real catalogue data).

The scripted fallback exists because a chat box that errors on a machine with
no AWS is a worse first impression than an honest one. **It is not evidence of
agent reasoning** and the UI says which one is live.

> ### ⚠ CORRECTED 2026-08-15 — two claims in this section were wrong
>
> **"same six tools … so it cannot reach data the pipeline cannot"** holds for
> the **`bedrock`** backend only. The scripted path does not go through the tool
> surface at all — it calls `get_store()` and `seed_taxonomy()` directly, and
> `seed_taxonomy()` is a **write** that the six-tool surface cannot even
> express. See defect #5 below.
>
> **"states in its own replies that it is not a model"** — measured across all
> seven scripted branches, **2 of 7** do:
>
> | Branch | Self-identifies |
> |---|---|
> | scoring, fallback | yes |
> | many-to-one, catalogue, walkthrough, vendors, match | **no** |
>
> Two of the three starter buttons (*"How do I start?"*, *"Can two vendors match
> the same dataset?"*) land on un-disclaimed branches and answer in fluent
> first-person prose that reads like a model.
>
> **Severity is genuinely lowered by a mitigation the original text did not
> credit:** `streamlit_app.py` renders a persistent "Scripted responder" caption
> above the chat, so the UI *does* disclose it. This is therefore a defect in
> **this document's** claim about the replies, not a user-facing lie. Adding the
> disclaimer to the two starter branches is cheap and still worth doing.
>
> **Both wrong claims came from `chat.py`'s own module docstring, and both are
> still there** (re-checked 2026-08-15): `:10-12` *"the SAME six tools … so the
> chat cannot reach data the pipeline cannot"*, and `:19` *"the number stays
> deterministic and auditable"* — the latter is the §1 defect again, untrue
> under `SCUDO_DENSE_BACKEND=opus`, which both launchers set. This document
> inherited them by reading the docstring as a specification. Note the gate in
> §9 item 1 exempts hits that are "a comment"; these are comments **and** false,
> so the gate must be "arm-conditional **or** accurate".

### 4.3 Three upload points

Contracts now **accumulate across vendors** — previously the second upload
erased the first from the picker, so the many-to-one story could not be shown
at all even though the backend had kept both frames. Catalogue **datasets** are
the new third point, so a client is not limited to the shipped 14-node fixture.

---

## 5. Defects found and fixed (all reproduced before fixing)

> **⚠ CORRECTED 2026-08-15 — the heading over-claims.** Three rows below were
> listed as fixed while still live at the time of writing. Per-row status:
>
> | # | Status as of 2026-08-15 |
> |---|---|
> | #2 | **Partially fixed.** Six of seven branches use the word-boundary `hit()` helper (`chat.py:325`); the **catalogue branch alone** still matches bare substrings — `k in low for k in ("catalogue", "catalog", "taxonomy", "nodes", "datasets")`. *"Do you support anodes and cathodes?"* routes to the catalogue (`nodes` ⊂ `anodes`). **Open.** |
> | #4 | **Still live in the catalogue branch.** Reproduced: `AgentEvent(type='tool_call', payload={'tool': 'get_taxonomy_node', 'args': {'all': True}})` is emitted while the code calls `list_taxonomy_nodes()`. The named tool is never called and `{"all": True}` is not even its signature. **Open.** |
> | #5 | **Made conditional, not eliminated.** `seed_taxonomy()` now runs only when the catalogue is empty — but a first chat question on a cold store still mutates the taxonomy. An AST scan (parsed, not read) puts that call **outside every `try` block** in the file, and `seed_taxonomy` raises `RuntimeError` on an empty seed — so a bad seed config turns a chat question into an unhandled traceback out of `send()`. |
> | #7 | Fixed in `fb61a00` — both `run_demo.py` children derive the store from one constant. Verified. |
>
> Rows #1, #3, #6, #8 and #9 were re-checked and hold as written.

| # | Defect | Why it mattered |
|---|---|---|
| 1 | Selecting **azure** returned the scripted responder while the UI captioned it "real tool-calling loop" — and with `SCUDO_AGENT_BACKEND=bedrock` set it ran **Bedrock** while claiming azure | The dropdown lied about what produced the answer |
| 2 | Scripted router matched bare substrings: "why can two vendors…" hit the scoring branch; **"what is Germany's role?"** hit many-to-one (*Germany* contains *many*) | Free-typed questions misrouted live |
| 3 | Bedrock history replayed every turn — **12 messages after 3 turns instead of 6** — and tool calls re-emitted for the whole transcript | Latent (fresh agent per rerun) but fires on any caching |
| 4 | A `tool_call` event was emitted for a tool that was **never called** | The docstring promised every answer came from a real call |
| 5 | Asking "what's in the catalogue?" **rewrote the taxonomy**, bumping the revision every time (1→2→3) | A read path performing a write |
| 6 | Contract picker keyed on `product_id` only while the run used the **sidebar's** vendor | With two vendors loaded → `frame_not_found` 404 that looks like a broken matcher |
| 7 | Three launchers disagreed on the store (`local_file` vs `scipy_sqlite`) | Decision approved in Streamlit **invisible** to the API — silent split-brain |
| 8 | Approve/Reject silently 403'd | A **third** dev-write gate (`SCUDO_AUTH_ALLOW_DEV_WRITES`) I had missed; reads worked, so it looked like the buttons were broken |
| 9 | Agent replied *"there's no file-upload step in what I can see"* | With an upload box a few centimetres above the chat |

### 5.1 Two performance defects, both measured

**The dense arm was not fail-soft.** `opus_dense_score` *raises* unless
`SCUDO_DENSE_FALLBACK` is set, so a bad key aborted the whole match with a
`RuntimeError` rather than degrading.

**And it was catastrophically slow.** It runs **per candidate**:

| | Before | After |
|---|---|---|
| Dense arm (live Opus) | 54.6s | **9.4s** (thread pool) |
| Full agent run | 197.2s | **47.6s** |
| Dead key, first match | 33.2s | **7.1s** (circuit breaker) |
| Dead key, later matches | 33.2s | **0.0s** |
| Score | 0.8500 | 0.8500 — *see caveat* |

**CORRECTED 2026-08-15:** "unchanged" overstates it. Those are two samples from
a scorer that is not deterministic under `opus`, so this is not a controlled
comparison. Worse, a reviewer showed the concurrency change can move the
published band: with a realistic 250 ms round trip and Bedrock failing its first
three calls, **serial gave 0.84/pass and concurrent gave 0.77/borderline** on
identical inputs. The `retrieval_scoring.py` comment claiming "the scorer is
stateless" is false — the thread-pool workers share the circuit-breaker global.

**UPDATE 2026-08-15:** addressed by the §8a work (option (b), all-or-nothing per
match); the false "stateless" comment is gone from the file. That work is
**uncommitted** — see §8a. Also note the perf table above spans three commits,
not one: it cannot be read as a before/after for `f569787` alone, and the
figures are unreproducible without live Bedrock in `eu-west-2` and the expired
key. **Treat them as measured-once, not as a benchmark.**

---

## 6. Decisions a reviewer should challenge

**6.1 Silent fallback.** Chosen deliberately (you picked it): if the 12h key
expires mid-demo, the deterministic path takes over and the demo keeps moving.
**The risk is a run that looks agentic but is not.** Mitigation is visibility —
the sidebar now reports all three levers. *Judge a run by the reasoning trace,
not by a number appearing.* A reviewer may reasonably prefer fail-loud.

> **Verified 2026-08-15 — the mitigation is now real, which it was not when this
> was written.** As originally built the sidebar rendered the *configured* env
> var, which is *always* `opus` because the launcher sets it: with the breaker
> open it read "Dense arm: opus" while every candidate had been scored by
> Jaro-Winkler. A status that cannot show degradation is not a mitigation. It
> now sources from `dense_arm_status()` in `opus_dense.py`, which reports
> `configured` / `effective` / `degraded` / `consecutive_failures`, and the
> sidebar renders `(degraded)` in amber. `opus_dense.py` also logs the fallback
> — it previously contained **zero** logging statements, so a dead key degraded
> every candidate with nothing recorded anywhere.

**6.2 The specialist is `local`, not `strands`.** `strands` sounds like the
agentic one; `strands_specialist.py` **was never built**, so it abstains on
every call. It would have looked wired while doing nothing.

**6.3 `scipy_sqlite` is single-host.** Durability is solved; **sharing is not**.
Several ECS tasks cannot write one SQLite file. AWS templates deliberately stay
on FalkorDB. A shared Aurora `RetrievalStore` remains real work.

**6.4 47.6s per agent run is still slow** for a live demo. The remainder is
Opus's own reasoning. The lever is Sonnet or Haiku in the sidebar — ~3× faster.
**CORRECTED 2026-08-15: not an "identical score".** The model picker sets the
id the dense scorer uses, so a different model produces a different similarity.
Faster *and* different, not faster and equivalent.

---

## 7. Verification — and its limits

**Executed:** live Bedrock in eu-west-2 (three models, real agent loop);
Streamlit driven through `AppTest` (load two vendors → run match → Approve →
chat), decision surviving into a **fresh process** as `approved` / rationale
`precedent`; console DB switch; no `aurora*` module or `boto3` imported on the
local path; `STORE_BACKEND=aurora` correctly raising.

**Not verified — please treat as open:**

- **No real browser render.** `AppTest` executes widgets but paints no DOM, so
  layout and CSS wrapping are unchecked. This sandbox reaps background
  processes, so I could not host a server for a human to click.
- **`chat.py` has zero tests.** The routing table and factory contract are
  unguarded. Highest-value follow-up. **Re-verified 2026-08-15: still true** —
  no test file in `backend/scudo_mapping_mcp/tests/` references chat. It is the
  newest client-facing surface and the one with three open defects (#2, #4, #5
  above). *(The breaker did get tests in the uncommitted §8a work —
  `test_opus_breaker.py`, 17 cases — so that adjacent gap is closed.)*
- **No live Bedrock through the Streamlit UI** — only through the package.
- **CORRECTED 2026-08-15 — "Codex CLI is not installed" was false.** It is at
  `/Users/anthonylui/bin/codex` (codex-cli 0.145.0), predating this document.
  What is true is that my four audit subagents died on a provider error, so no
  independent review ran *at the time I wrote this*. Two reviews have since run
  and are the reason for the corrections above.

- **CORRECTED 2026-08-15 — my test-baseline claim was wrong.** I wrote that the
  one mapping-suite failure "reproduces identically at HEAD with
  `SCUDO_DENSE_BACKEND=opus` exported". It does not — measured:

  | Run | Result |
  |---|---|
  | no export | 1 failed, 568 passed |
  | `SCUDO_DENSE_BACKEND=opus` | **4 failed, 565 passed** |

  The three extra failures are `test_input_completeness.py` (×2) and
  `test_phase_e_measurement.py`. **"Pre-existing, not a regression" still
  holds** — a reviewer built a clean worktree at `f569787` and got the same
  single failure — but the export is a *different* condition, not a
  reproduction. The same wrong line is in the `fb61a00` commit message; this
  is the correction of record.

  **RESOLVED 2026-08-15 (`247905e`).** The leak is pinned and fixed. Four
  tests in `test_scipy_sqlite_integration.py` `exec()` the prefix of
  `streamlit_app.py`, which writes ~13 env vars; their `finally` blocks
  restored only three, so `SCUDO_DENSE_BACKEND=opus` leaked for the rest of
  the session. Minimal reproduction is **two tests**, not the 31-file prefix
  delta-debugging assumed — and reversing their order passes, which is what
  proves leakage rather than a bad assertion. Fixed by snapshotting and
  restoring the whole environment. `scudo_mapping_mcp` was **569 passed,
  0 failed** at `247905e`; full backend suite **1047 passed** with the 2 known
  `test_provenance.py` failures.

  **SUPERSEDED 2026-08-15 — these counts are stale, and the export condition
  got worse, not better.** Re-measured on the current working tree (which
  carries the uncommitted §8a concurrency work):

  | Run | At `247905e` | Current tree |
  |---|---|---|
  | mapping, no export | 569 passed, 0 failed | **617 passed, 0 failed** |
  | mapping, `SCUDO_DENSE_BACKEND=opus` | 4 failed, 565 passed | **15 failed, 602 passed** |
  | full backend | 1047 passed, 2 failed | **1095 passed, 2 failed** |

  The suite grew by 48 tests, so every absolute count in this document ages
  fast — **quote a count with the invocation and the tree that produced it, or
  do not quote it.** The two backend failures are the known pre-existing
  `test_provenance.py` pair.

  The export-condition regression is the load-bearing part: `247905e` fixed the
  *leak* (tests no longer pollute each other) but not the *sensitivity* — 15
  tests now fail when `SCUDO_DENSE_BACKEND=opus` is set in the environment, up
  from 4. They span `test_scipy_sqlite_store.py` (6),
  `test_scipy_sqlite_scoring_parity.py` (3), `test_phase_e_measurement.py` (3),
  `test_input_completeness.py` (2) and `test_taxonomy_text_threading.py` (1).
  This is the same class of defect the review keeps hitting: the suite is
  written against the `config.py` default while both launchers ship the
  override. **Open — the suite does not pass in the configuration we ship.**
- **`override` is still not exposed** in Streamlit (Approve/Reject only).

---

## 8. Things I got wrong mid-session

Recording these because they are the honest risk profile of this work.

1. **"No Aurora/SQL matching store exists"** — true when written, false hours
   later when `scipy_sqlite` landed from the parallel work stream. Corrected in
   two documents.
2. **"The agent is not conversational"** — true, then made false by my own
   `chat.py`. Corrected in three documents.
3. **Reported servers as "up"** when they died the moment my shell call
   returned. That wasted two exchanges and produced a "Connection error" the
   user had to point out twice. The demo must be started from a human terminal.
4. **Wrote a credentials hint recommending `AWS_PROFILE`** — the JPMC path, not
   this project's. Corrected to the bearer token after verifying botocore
   consumes `AWS_BEARER_TOKEN_BEDROCK` natively.

**Line numbers in the handover docs drift** — several files are edited by more
than one session. Match on symbol names, not line numbers.

---

## 8a. The concurrency decision — IMPLEMENTED, NOT COMMITTED (2026-08-15)

> **Implemented: option (b), all-or-nothing per match.** BM25 still nominates
> at most 25 candidates and Opus still scores them concurrently on up to eight
> workers, so the latency win is kept. What changed is that the batch is now
> committed **atomically**: a complete Jaro-Winkler baseline is computed before
> any model call, and if *any* Opus call fails, every Opus result is discarded
> and the whole match uses that baseline. A candidate list therefore carries
> exactly one similarity scale.
>
> Two defects were found while implementing, both by testing rather than
> reading, and both are fixed:
>
> - **The first cut did not actually close the hole.** The batch still called
>   `opus_dense_score`, which makes its own per-candidate fallback decision, so
>   failing at the *network* seam still produced a mix — measured
>   `[1.0, 0.9333, 0.91, 0.91]` with `0.91` the model value. Scripting the
>   injected scorer hid it. The batch now calls a strict seam that raises.
> - **An abandoned probe pinned the breaker open.** A half-open probe that
>   never reported back (the fallback-disabled re-raise path, or a crashed
>   worker) blocked recovery for the life of the process. Now treated as
>   abandoned after a timeout.
>
> Ranking also follows the arm that *actually* scored — previously the fallback
> scores were sorted with the opus rule.
>
> **CORRECTED 2026-08-15 — "byte-identical to `SCUDO_DENSE_BACKEND=jaro_winkler`"
> is false, and the implementation's own comment now says so.**
> `retrieval_scoring.py` records: BM25 has already narrowed the pool to
> `_MAX_OPUS_NOMINEES` (25) *before* the fallback decision, so a node with weak
> lexical overlap but strong string similarity is present in the jaro arm and
> absent here — measured on a 40-node fixture. The contract delivered is **one
> scale per list**, not identity with a differently-nominated run. That is the
> correct and defensible claim; the equivalence claim was not.
>
> **CORRECTED 2026-08-15 — the verification counts are stale and the smoke
> figure was wrong.** Re-measured on the current tree:
>
> | Claimed | Measured |
> |---|---|
> | mapping 585 passed | **617 passed** |
> | backend 1065 passed | **1095 passed**, 2 known failures |
> | smoke **117/117** | **113/117** |
>
> The four smoke failures are `TRUST_ingestion_mcp_imports_no_writers`,
> `TRUST_match_verify_mcp_imports_no_writers`,
> `TRUST_persistence_mcp_imports_writers` and
> `DEFENSE_IN_DEPTH_scope_gate_called_at_all_three_layers`. **They are
> pre-existing, not caused by this work** — a clean worktree at `HEAD` gives the
> identical 113/117. But "117/117" was never true of this tree, and the runner
> is not at the repo root: it is
> `PYTHONPATH=backend python3 -m scudo_mapping_mcp.tests.smoke`. (Invocation
> matters: the `backend.`-prefixed module path gives 112/117, a *different*
> number for the same code.)
>
> No live-Bedrock latency was re-measured, so no new timing claim is made here.
>
> **Status: uncommitted.** This work is in the working tree, not in a commit —
> `opus_dense.py` (+304) and `retrieval_scoring.py` (+134) are dirty, alongside
> new `test_opus_breaker.py` (17 tests) and two other new test files. §8a should
> not be read as landed.

### Original finding (kept for the record)

**The finding.** Thread-pool workers share the circuit-breaker globals, so
which candidates get an LLM score and which fall back to Jaro-Winkler depends
on thread interleaving. With a 250 ms round trip and Bedrock failing its first
three calls, a reviewer measured **serial 0.84/pass vs concurrent
0.77/borderline** on identical inputs. The `retrieval_scoring.py:68` comment
saying "the scorer is stateless" is false.

This is worse than slow: **the published band is timing-dependent**, and the
band drives whether a mapping auto-publishes or goes to a human.

Two options were on the table:

**(a) Make the breaker thread-safe** — lock the counters, keep concurrency.
Removes the data race but **not** the nondeterminism: whether call #3 or #4
trips the breaker still depends on which worker returns first, so the
partial-fallback mix still varies run to run.

**(b) Make the fallback decision once per match, not per candidate** — decide
the arm up front (LLM available or not), then score all candidates with that
one arm.

**Recommendation: (b).** It is the only option that makes a match's score
reproducible, which is the property the whole audit story rests on — a
confidence you cannot reproduce is a confidence you cannot defend to a client
or a regulator. It also removes the mixed-arm results that (a) leaves behind,
where some candidates are LLM-scored and others are not, ranked against each
other as though they were comparable. **They are not comparable** — that is
arguably a bigger correctness problem than the timing itself, and neither
review has flagged it yet.

Cost: one failed probe per match instead of per candidate, so a dead key costs
slightly more than today's breaker. Worth it.

**Not implemented** — this is a design change to the scoring path and needs
your approval before I touch it.

---

## 9. Suggested review focus

**Revised 2026-08-15** — reordered by what is actually still open.

1. **`streamlit_app.py:707` still carries the false claim.** The agent-selector
   help text reads *"The PASS/FAIL score stays deterministic either way; the
   model narrates…"* — verbatim, on a user-reachable surface. "Either way" is
   precisely the false part: selecting `scripted` does not make the score
   deterministic (§1, 14 LLM calls). This is the last user-facing instance and
   the highest client risk. `chat.py` already branches on the live arm; do the
   same here. **Gate it:** `rg deterministic` across `streamlit_app.py`,
   `chat.py`, `agent.py` and assert every hit is **arm-conditional or accurate**
   — *not* "or a comment". `chat.py:19` is a comment and false ("the number
   stays deterministic and auditable"), as is `chat.py:10-12`'s six-tools
   containment claim (§4.2); a comment exemption would pass both.

   **I ran that gate. 24 hits across the four files; here is the adjudication,
   so this does not have to be redone:**

   | Verdict | Sites |
   |---|---|
   | **False — fix** | `streamlit_app.py:707` (user-facing help text); `chat.py:19`; `chat.py:10-12`; `streamlit_app.py:1237-1238` (*"the score is Jaro-Winkler and the model only narrates"* — a comment, and the exact claim §1 refutes) |
   | **Correct, arm-conditional** | `streamlit_app.py:1273-1288` (branches on `_dense_live`); `chat.py:54`, `:385`, `:399-405`, `:424` (all inside the `jaro_winkler` else-branch) |
   | **Correct as written** | `streamlit_app.py:139-160` (the CORRECTED block — says plainly the shipped launchers override to opus); `run_cognizant.py:54` (*"the matching score is REAL … with the shipped opus"*) |
   | **Different meaning — leave** | `agent.py` ×6 and `agent.py:1260`. "The deterministic matcher" there names `matching.map_vendor_product` as the authoritative *component* versus the agent's recommendation; it is not a claim about how the number is computed. Still worth a one-line note that the matcher's own dense arm may be an LLM, because the phrase is what seeded the misreading. |

   Note `agent.py:1260` cites a **86/86** smoke suite; the real figure is
   113/117 (§7). Unrelated to the gate, but it is in the same docstring.
2. **The suite fails in the configuration we ship** — 15 failures under
   `SCUDO_DENSE_BACKEND=opus` (§7). Decide whether the suite should pin the
   default or cover both arms; today it silently tests a path no launcher uses.
3. `chat.py` — still no tests, three open defects (#2, #4, #5), newest
   client-facing surface.
4. **Commit or discard the §8a work** — 438 uncommitted lines across the scoring
   path plus three new test files. It is not landed.
5. §6.1 — is silent fallback the right call for a client demo? (The visibility
   mitigation is now genuine; the fail-loud question stands.)
6. §5 defect 7 — the split-brain class of bug; are the three launchers now the
   only places that set the store?
7. §6.3 — confirm the single-host boundary is acceptable for the demo, and that
   nobody expects Aurora to hold matching precedents.
