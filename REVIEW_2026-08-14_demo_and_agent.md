# Review pack — Cognizant demo, agent chat, real Bedrock wiring

**Date:** 2026-08-14 · **Branch:** `main` · **Six commits, `a92b8d0` … `f569787`**

> ## ⚠ Read this first — corrected 2026-08-15 after two independent reviews
>
> This document shipped with a **false central claim**: that the LLM does not
> produce the score. On the path both launchers actually use, **it does**.
> Every affected section is corrected **in place** (marked `CORRECTED
> 2026-08-15`) rather than by an appendix, so no reader can hit the wrong
> sentence first — it was the one I told you to say to a client.
>
> | Section | Status |
> |---|---|
> | §3 architecture note | **WRONG — corrected**; user-facing copy fixed in `fb61a00` |
> | §5.1 "score unchanged" | **Overstated — corrected**; concurrency can flip the band |
> | §6.4 "identical score" | **WRONG — corrected** |
> | §7 "Codex not installed" | **False — corrected** |
> | §7 test baseline | **WRONG — corrected**; export gives 4 failures, not 1 |
> | §5 defect #2, #4, #7 | **Listed as fixed but were still live**; #7 fixed `fb61a00`, #2/#4 open |
>
> Everything else was hand-verified by the reviewers and holds: the §2
> diffstats, §6.2 (`strands_specialist.py` genuinely absent), §6.3 (single-host
> boundary), defects #3 and #5, the serve-flag `/app/` 404 finding, `chat.py`
> has zero tests, and `override` is not exposed.

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
it** without insider knowledge, and the agent was decorative — the score is
deterministic, so an LLM that only narrates is easy to switch off and never
notice.

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

Free-text chat over the **same six tools** as the mapping agent, so it cannot
reach data the pipeline cannot, and it does not score. Two backends: `bedrock`
(real tool loop) and `scripted` (keyword-routed, real catalogue data, states in
its own replies that it is not a model).

The scripted fallback exists because a chat box that errors on a machine with
no AWS is a worse first impression than an honest one. **It is not evidence of
agent reasoning** and the UI says which one is live.

### 4.3 Three upload points

Contracts now **accumulate across vendors** — previously the second upload
erased the first from the picker, so the many-to-one story could not be shown
at all even though the backend had kept both frames. Catalogue **datasets** are
the new third point, so a client is not limited to the shipped 14-node fixture.

---

## 5. Defects found and fixed (all reproduced before fixing)

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
**Open, and the highest-priority remaining item.**

---

## 6. Decisions a reviewer should challenge

**6.1 Silent fallback.** Chosen deliberately (you picked it): if the 12h key
expires mid-demo, the deterministic path takes over and the demo keeps moving.
**The risk is a run that looks agentic but is not.** Mitigation is visibility —
the sidebar now reports all three levers. *Judge a run by the reasoning trace,
not by a number appearing.* A reviewer may reasonably prefer fail-loud.

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
  unguarded. Highest-value follow-up.
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
  is the correction of record. Full-suite baseline is **1047 passed, 2 known
  pre-existing `test_provenance.py` failures**.
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

## 8a. The concurrency decision (blocking two other items)

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

1. §6.1 — is silent fallback the right call for a client demo?
2. §5 defect 7 — the split-brain class of bug; are the three launchers now the
   only places that set the store?
3. `chat.py` — no tests, and it is the newest client-facing surface.
4. §6.3 — confirm the single-host boundary is acceptable for the demo, and that
   nobody expects Aurora to hold matching precedents.
