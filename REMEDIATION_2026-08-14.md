# Remediation plan — findings from the 2026-08-14 review audit

**Baseline:** HEAD **`fb61a00`** (see §0). Tests at time of writing: **1047
passed, 2 failed** — the two documented pre-existing `test_provenance.py`
failures. Nothing new is broken.

**Root cause behind most of this:** `config.py:379` defaults
`SCUDO_DENSE_BACKEND` to `jaro_winkler`, but **both shipped entry points
override it to `opus`** (`streamlit_app.py:86`, `run_cognizant.py:144`). Every
document, comment and UI string that says "the score is deterministic" was
written against the default and is false on the path we actually ship. Fixing
the prose without fixing that split just moves the lie around.

---

## 0. Already fixed since the review — verify, don't redo

Committed as **`fb61a00`** ("fix: stop claiming the score is model-free, and heal
the breaker") while this plan was being written. Both fixes are **verified
working** — do not re-implement them.

| Finding | Fix | Verified |
|---|---|---|
| Breaker never retries | `opus_dense.py` half-open probe: `_breaker_should_probe()` + `_BREAKER_COOLDOWN_S` (default 30s), lock-guarded so the thread pool sends exactly one probe | With cooldown 0.5s: in-cooldown retry served fallback (`calls=3`); after cooldown `score=0.97, calls=4, open=False` |
| Split-brain (`run_demo.py`) | Both children now derive store + path from one `_STORE`/`_DB` constant | `STORE_BACKEND` resolves identically for the Flask and Streamlit children |

Three client-facing score claims were also corrected to branch on the live arm:
`streamlit_app.py:1078-1105`, `chat.py:379-392`, and the chat **system prompt**
(now `chat.py:54`, conditional on the dense backend). Those are done.

**Action:** the commit is unprotected by tests (§1.2) — that gap is now the
first item, not the commit itself.

---

## 1. P0 — client-facing integrity

### 1.1 One "deterministic score" claim is still false

`fb61a00` fixed the system prompt. **`streamlit_app.py:571` survives verbatim**
(re-checked against the post-commit tree):

| Site | Current text | Problem |
|---|---|---|
| `streamlit_app.py:571` | *"The PASS/FAIL score stays deterministic either way; the model narrates and, on borderline cases, advises."* | Help text on the **agent selector**. "Either way" is precisely the false part: switching to `scripted` does not make the score deterministic — measured below, it still makes 14 LLM calls. |

**Fix:** branch on `env_dense_backend()` the way `chat.py:379` and
`streamlit_app.py:1087` already do.

**The sharpest form of this defect — the "off" switch doesn't switch it off.**
`SCUDO_DENSE_BACKEND` is a module-level `setdefault` independent of the agent
selector, so choosing the **offline `scripted`** agent — the one a user picks
precisely to avoid AWS — still routes every candidate through Bedrock.
Measured on the real `map_vendor_product` with only `_opus_invoke_score`
stubbed:

```
SCUDO_AGENT_BACKEND=scripted   (the offline narrator)
opus_dense LLM calls = 14
published: conf=0.99  band=pass  status=auto_mapped  target=FX Rates
```

A user who selects the offline agent believing they are running deterministically
is not, and nothing on screen says otherwise. Whatever wording lands in §1.1, the
**mechanism** needs addressing too: either bind the dense arm to the agent
selector, or state plainly in the sidebar that they are independent levers.

**Done when:** no unconditional "deterministic" score claim survives on a
user-reachable surface. Gate it — `rg` for `deterministic` across
`streamlit_app.py`, `chat.py`, `agent.py` and assert every hit is
**arm-conditional or accurate**. Do *not* exempt comments: `chat.py:19` is a
comment and false.

### 1.2 The new breaker code has no tests

`grep` for `breaker` across both test suites returns only `smoke.py`. The
half-open probe involves a module global, a lock, and a monotonic clock — the
three things that break silently.

**Fix:** add `backend/scudo_mapping_mcp/tests/test_opus_breaker.py` covering:
trips after `_BREAKER_THRESHOLD` failures; serves fallback while open; probes
exactly once after cooldown; a successful probe resets to closed; a failed
probe re-arms the cooldown rather than probing every call; **and** that
concurrent callers produce exactly one probe. Reset the globals in a fixture —
they persist across tests in one process.

---

## 2. P1 — correctness

### 2.1 Concurrency makes the published score timing-dependent

`retrieval_scoring.py:68` claims "the scorer is stateless". It is not — workers
share the breaker global. Reproduced with a 250 ms round trip and Bedrock
failing its first 3 calls:

```
SERIAL     : conf=0.84  band=pass
CONCURRENT : conf=0.77  band=borderline
```

Same inputs, same code; thread timing alone flips the band. A latched breaker
also yields one ranked list mixing LLM scores with Jaro scores — two different
scales sorted against each other.

**Fix (choose one, then state which in the comment):**
- **(a) Consistency:** decide the arm once per `score_candidates` call and score
  every candidate on it. A mixed list is the real defect; make the fallback
  all-or-nothing per match.
- **(b) Transparency:** keep mixing but record per-candidate provenance
  (`scored_by: opus|jaro`) and surface it, so a mixed-scale ranking is visible
  rather than silent.

(a) is the smaller change and removes the failure mode; (b) preserves current
behaviour and only makes it legible. **This needs a decision before work
starts** — it changes what a borderline score means.

Correct the `:68` comment either way.

### 2.2 Scripted-router defects #2 and #4 are half-fixed

- `chat.py:397` still uses bare substring matching — *"Do you support anodes
  and cathodes?"* routes to the catalogue branch (`nodes` ⊂ `anodes`). Every
  other branch uses the word-boundary `hit()` helper at `:319`. Use it here.
- `chat.py:411` emits `tool_call {"tool": "get_taxonomy_node", "args": {"all":
  True}}` while the code above calls `list_taxonomy_nodes()`. The named tool is
  never called and `{"all": True}` isn't even its signature. This is defect #4
  restated in another branch — the class docstring promises every answer comes
  from a real call.

**Fix:** route branch 3 through `hit()`; emit a `tool_call` naming the function
actually invoked.

**Done when:** a test asserts no `tool_call` payload names a tool absent from
`_strands_tools_for_mapping()`, and the `anodes` case routes correctly.

Both reproduce on the current file — the phantom event is emitted live:

```
AgentEvent(type='tool_call', payload={'tool': 'get_taxonomy_node', 'args': {'all': True}})
AgentEvent(type='tool_result', payload={'result': '{"count": 14}'})
```

### 2.4 The catalogue branch writes, and can crash, unguarded

`chat.py:444` still calls `seed_taxonomy()` — i.e. `store.replace_taxonomy()` —
when the catalogue is empty. Defect 5's fix made the write *conditional*, not
absent: a first chat question on a cold store still mutates the taxonomy.

Worse, an AST scan puts that call **outside every `try` block** in the file
(handlers span lines 138-145, 203-257, 379-382; the call is at 444). Verified by
parsing, not reading. `seed_taxonomy` raises `RuntimeError` on an empty seed, so
a bad seed config turns a chat question into an unhandled traceback out of
`send()`.

**Fix:** move seeding out of the chat read path (answer "catalogue is empty"
instead), or wrap it and degrade to a message. Prefer the former — a read path
should not write.

### 2.5 Five of seven scripted branches don't say they're scripted

The review claims the scripted responder "states in its own replies that it is
not a model". Measured across all seven branches — **2 of 7** do:

| Branch | Self-identifies |
|---|---|
| scoring, fallback | yes |
| many-to-one, catalogue, walkthrough, vendors, match | **no** |

Two of the three starter buttons (*"How do I start?"*, *"Can two vendors match
the same dataset?"*) land on un-disclaimed branches and answer in fluent
first-person prose that reads like a model.

**Severity is lowered by a real mitigation:** `streamlit_app.py:1428` renders a
persistent "Scripted responder" caption above the chat, so the UI does disclose
it. This is a **documentation** defect (§4) plus an optional polish item, not a
user-facing lie. Fix the doc claim; adding the disclaimer to the two starter
branches is cheap and worth doing.

### 2.3 `chat.py` still has zero tests

The review flagged this as the highest-value follow-up and it remains true —
it is the newest client-facing surface. The routing table is the natural unit
under test; 2.2's gates can live in the same file.

---

## 3. P2 — observability

### 3.1 The fallback is unloggable

`opus_dense.py` and `retrieval_scoring.py` contain **zero** logging statements
(measured: `grep -c` returns 0 for both). When the key dies, every candidate
silently degrades to Jaro-Winkler and nothing anywhere records it.

**Fix:** log at WARNING on first breaker trip, on each probe outcome, and on
per-call fallback (rate-limited — this runs per candidate).

### 3.2 The sidebar reports configured, not effective, state

`streamlit_app.py:653` renders `SCUDO_DENSE_BACKEND`, which is *always* `opus`
because `:86` sets it. With the breaker open the sidebar reads "Dense arm:
opus" while every candidate was scored by Jaro-Winkler. §6.1 of the review
offers this as the mitigation for silent fallback; as built it cannot be one.
The strip also shows two of the three levers — the agent backend is a separate
`selectbox` at `:564`.

**Fix:** report effective state (`opus (degraded — breaker open)`), sourced
from the breaker rather than the env var. Depends on 3.1.

---

## 4. Documentation — DONE 2026-08-15

`REVIEW_2026-08-14_demo_and_agent.md` is now **tracked and corrected in place**.
A concurrent session corrected §3, §5.1, §6.4 and §7 (commits `a558f52`,
`137d991`); a third pass added §1, §4.2, the §5 defect-status table, §6.1, §8a
and §9, and **re-measured every number** — which is where the interesting result
came from:

**The corrections had themselves gone stale before they were written.** Measured
on the working tree versus what the correction text claimed:

| Claim in the corrected text | Measured |
|---|---|
| mapping 569 / 585 passed | **617 passed** |
| backend 1047 / 1065 passed | **1095 passed**, 2 known failures |
| smoke **117/117** | **113/117** (4 pre-existing; clean `HEAD` gives the same) |
| export condition: 4 failures | **15 failures** |
| §8a fallback "byte-identical to `jaro_winkler`" | false — and `retrieval_scoring.py`'s own comment now says so |

The export-condition number is the one that matters: `247905e` fixed the env
*leak*, not the *sensitivity*. **15 tests fail when `SCUDO_DENSE_BACKEND=opus`
is set** — i.e. the suite does not pass in the configuration both launchers
ship. Same root cause as everything else in this plan.

Also corrected in the same pass:

| § | Claim | Correction |
|---|---|---|
| §4.2 | scripted "states in its own replies that it is not a model" | 2 of 7 branches do (§2.5) |
| §4.2 | chat has the "same six tools … so it cannot reach data the pipeline cannot" | true for `bedrock` only; the scripted path calls `get_store()`/`seed_taxonomy()` directly, and reaches a **write** the six-tool surface cannot express |
| §5 defect 5 | read-path write "fixed" | write is now *conditional*, not eliminated (§2.4) |
| §5 defect 2 | substring misrouting "fixed" | branch 3 was never converted (§2.2) |
| §5.1 | perf table read as one before/after for `f569787` | rows span three commits; the thread pool alone doesn't account for the full-run figure |

The §5.1 timing table is **unverifiable** without live Bedrock in `eu-west-2`
and the expired key — flag it as measured-once rather than restating it.

The `streamlit_app.py:128-160` comment block is worth preserving: it already
documents the four uncapped branches and correctly says the safety comes from
the **default**, not from a cap. It just needs a line noting that `:86`
overrides that default — as written, its own safety argument doesn't apply to
the app it lives in.

---

## Suggested order — revised 2026-08-15

Struck through = done since this plan was written.

1. ~~§1.2 — tests for the breaker~~ **DONE** (uncommitted): `test_opus_breaker.py`,
   17 cases.
2. ~~§2.1 — decide (a) vs (b)~~ **DONE** (uncommitted): option (b), all-or-nothing
   per match. Found two further defects while implementing.
3. ~~§3 — logging and effective-state reporting~~ **DONE**: `dense_arm_status()`
   plus an amber `(degraded)` sidebar and fallback logging.
4. ~~§4 — correct the review doc~~ **DONE** (see above).

5. ~~§1.1 — the four false "deterministic" claims~~ **DONE and COMMITTED**
   as `04d5864` on branch `fix/deterministic-score-claims` (off `main`
   `137d991`; **not pushed**). Detail below.

**Still open, in priority order:**

1. ~~**§1.1 — `streamlit_app.py:707`**~~ **DONE** — see the closure note at the
   end of this section. Original text kept for the record:
   (it moved from `:571`; match on the symbol,
   not the number). The false "deterministic either way" text survives verbatim
   on a user-reachable surface. Highest client risk, smallest change.

   **The gate has now been run** (24 hits across `streamlit_app.py`, `chat.py`,
   `agent.py`, `run_cognizant.py`). Four sites are false and need fixing —
   `streamlit_app.py:707` (the only user-facing one), plus three comments:

   - `chat.py:19` — *"the number stays deterministic and auditable"*
   - `chat.py:10-12` — *"the SAME six tools … so the chat cannot reach data the
     pipeline cannot"* (untrue for the scripted branch, which calls
     `get_store()`/`seed_taxonomy()` directly — §4.2)
   - `streamlit_app.py:1237-1238` — *"the score is Jaro-Winkler and the model
     only narrates"*

   The rest are correct: `chat.py:54/385/399-405/424` and
   `streamlit_app.py:1273-1288` are arm-conditional; `streamlit_app.py:139-160`
   and `run_cognizant.py:54` state the opus override plainly. `agent.py`'s six
   "deterministic matcher" hits mean the authoritative *component*, not the
   scoring method — leave them, but a note there is warranted since that phrase
   is what seeded the misreading. Full table in the review doc §9 item 1.

   Because three of the four are comments, the gate must be **"arm-conditional
   or accurate"** — a comment exemption passes all three.

   ---

   **CLOSED 2026-08-15 — commit `04d5864`** (`fix/deterministic-score-claims`,
   two files, +74/-19, **not pushed**). All four sites now branch on the live
   arm or state the opus override plainly. The correction blocks quote the old
   wording in order to refute it — a `grep` for the old strings still returns
   hits, and that is expected; read the surrounding block before treating a hit
   as a survival.

   Verified before committing: `py_compile` on both **staged blobs** (not the
   working tree — `streamlit_app.py` carries 209 unrelated uncommitted lines);
   both help-text branches exec'd with assertions; backend suite **1095 passed /
   2 failed**, the two known `test_provenance.py` failures, unchanged.

   **Gate re-run after the commit — 28 hits across the four files, every one
   adjudicated:**

   | File | Hits | Verdict |
   |---|---|---|
   | `streamlit_app.py` | 7 | 3 are the new arm-conditional help text; 2 the pre-existing arm-conditional `_dense_live` warning; 2 inside CORRECTED blocks quoting refuted text |
   | `chat.py` | 8 | 2 in the new CORRECTED blocks; 6 the pre-existing arm-conditional `_dense` scoring branch |
   | `agent.py` | 12 | all mean the authoritative **component** (`map_vendor_product`) vs the agent's recommendation — not a claim about how the number is computed. **Correct; do not "fix".** |
   | `run_cognizant.py` | 1 | arm-conditional ("Explicit `SCUDO_DENSE_BACKEND=jaro_winkler` gives the deterministic offline scorer") |

   **Two findings the re-run produced that were not in the original four:**

   - `run_cognizant.py`'s client-facing *WHAT IS REAL AND WHAT IS NOT* docstring
     used to say the score is *"deterministic Jaro-Winkler"* and *"The SCORE
     does not change either way"* — contradicted by its own `_ENV` block ~90
     lines below, which sets `SCUDO_DENSE_BACKEND=opus`. **Already fixed in the
     working tree** by the §8a stream; still false at `HEAD`. This lands only
     when item 3 lands.
   - Same for `streamlit_app.py`'s model-picker comment block (`:128-160`) —
     the working tree now says the picker can change *"candidate similarity,
     confidence, band and selected target"*; `HEAD` still carries the old text.

   So the count of false claims was **six, not four** — two of them fixed by
   another work stream that is not committed. **Landing the §8a stream (item 3)
   is now a correctness dependency, not just housekeeping.**

   `agent.py:1260` also cites a stale **86/86** smoke figure (real: **113/117**).
   Unrelated to this gate; noted so it is not lost.
2. **The suite does not pass as shipped** — 15 failures under
   `SCUDO_DENSE_BACKEND=opus`. New item, found by the third pass. Decide whether
   the suite pins the default or covers both arms.
3. **Commit or discard the §8a work** — 438 uncommitted lines across the scoring
   path plus three new test files. Nothing above is landed until this is.
4. §2.2 + §2.4 + §2.3 — router fixes, the read-path write, and the `chat.py`
   test file together (they touch the same branches). Still zero chat tests.
