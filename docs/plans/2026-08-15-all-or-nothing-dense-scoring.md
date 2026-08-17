# All-or-Nothing Dense Scoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve concurrent Opus performance while guaranteeing that every candidate in one match is scored entirely by Opus or entirely by Jaro-Winkler.

**Architecture:** Keep BM25 nomination and the 25-candidate cap in `score_candidates()`, precompute a complete Jaro-Winkler baseline, and attempt the Opus nominees as one logical batch. Add a strict Opus seam plus a small breaker availability contract so any model failure causes the caller to discard the complete Opus batch instead of retaining mixed per-candidate fallback values.

**Tech Stack:** Python 3.11, pytest, `concurrent.futures.ThreadPoolExecutor`, existing SCUDO retrieval stores and circuit breaker.

---

### Task 1: Pin the all-or-nothing batch contract

**Files:**
- Modify: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`
- Test: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`

**Step 1: Write the failing successful-batch test**

Add a test that:

- sets `SCUDO_DENSE_BACKEND=opus`;
- installs a strict scorer returning distinct values for every nominee;
- calls both `MemoryStore.find_similar_products()` and
  `ScipySQLiteStore.find_similar_products()`;
- asserts every returned `Candidate.similarity` is from the strict scorer;
- asserts both stores return the same shape.

Name it:

```python
def test_opus_batch_uses_model_scores_only_when_every_nominee_succeeds(
    tmp_path, monkeypatch
):
    ...
```

**Step 2: Write the failing partial-failure regression test**

Use a barrier or deterministic call counter so several concurrent model calls
succeed while one raises. Precompute expected Jaro-Winkler results by running
the same fixture with `SCUDO_DENSE_BACKEND=jaro_winkler`.

Then run with `SCUDO_DENSE_BACKEND=opus` and assert:

```python
assert _shape(opus_attempt_result) == _shape(jaro_baseline)
assert all(score not in successful_model_scores for _, score in _shape(opus_attempt_result))
```

Name it:

```python
def test_one_opus_failure_discards_the_whole_model_batch(
    tmp_path, monkeypatch
):
    ...
```

The test must demonstrate that successful model values from the same concurrent
batch do not survive.

**Step 3: Run the two tests and verify RED**

Run from `backend/`:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py::test_opus_batch_uses_model_scores_only_when_every_nominee_succeeds \
  scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py::test_one_opus_failure_discards_the_whole_model_batch
```

Expected:

- the successful-batch test may pass against existing behavior;
- the partial-failure test must fail because current code retains a mixed Opus /
  Jaro-Winkler list.

Do not continue unless the second test fails for that exact reason.

**Step 4: Commit only if explicitly requested**

Do not commit automatically. The repository may contain unrelated user changes.

---

### Task 2: Add a strict Opus scoring seam

**Files:**
- Modify: `backend/scudo_mapping_mcp/opus_dense.py`
- Create: `backend/scudo_mapping_mcp/tests/test_opus_breaker.py`
- Test: `backend/scudo_mapping_mcp/tests/test_opus_breaker.py`

**Step 1: Write failing strict-seam tests**

Create focused tests for a public/internal batch-facing function such as:

```python
def opus_dense_score_strict(
    query_label: str,
    query_desc: str,
    candidate_label: str,
    candidate_desc: str,
) -> float:
    ...
```

Tests:

1. returns the clamped model score and does not use Jaro-Winkler;
2. propagates/wraps a model failure even when `SCUDO_DENSE_FALLBACK=1`;
3. does not independently make a per-candidate fallback decision.

Use monkeypatching only at `_opus_invoke_score`, the unavoidable network seam.

**Step 2: Run strict-seam tests and verify RED**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_opus_breaker.py
```

Expected: FAIL because the strict seam does not exist.

**Step 3: Implement the minimal strict seam**

In `opus_dense.py`:

- keep imports at module top;
- extract the direct model invocation/clamp into
  `opus_dense_score_strict(...)`;
- make it return the model score or raise `RuntimeError`;
- do not read `SCUDO_DENSE_FALLBACK` inside the strict seam;
- preserve `opus_dense_score()` behavior for existing specialist and legacy
  callers by wrapping the strict seam with the current breaker/fallback logic.

Illustrative structure:

```python
def opus_dense_score_strict(...):
    try:
        return _clamp01(_opus_invoke_score(...))
    except Exception as exc:
        raise RuntimeError(f"opus dense scoring failed: {exc}") from exc
```

Avoid duplicating the model request construction.

**Step 4: Run strict-seam tests and verify GREEN**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_opus_breaker.py
```

Expected: all new strict-seam tests PASS.

---

### Task 3: Expose coherent breaker batch decisions

**Files:**
- Modify: `backend/scudo_mapping_mcp/opus_dense.py`
- Modify: `backend/scudo_mapping_mcp/tests/test_opus_breaker.py`
- Test: `backend/scudo_mapping_mcp/tests/test_opus_breaker.py`

**Step 1: Write failing breaker contract tests**

Test a small batch-facing contract rather than importing globals from
`retrieval_scoring.py`. The contract should support:

```python
decision = begin_dense_batch()
if decision.attempt_opus:
    ...
    record_dense_batch_success(decision)
else:
    ...
record_dense_batch_failure(decision)
```

Required tests:

- closed breaker permits an Opus batch;
- open breaker inside cooldown refuses an Opus batch;
- open breaker after cooldown permits exactly one half-open batch;
- concurrent callers receive exactly one probe permission;
- successful permitted batch resets failures;
- failed permitted batch keeps/reopens degradation;
- fallback disabled does not silently refuse an Opus attempt.

Reset module globals in an autouse fixture using a whole state snapshot.

**Step 2: Run breaker tests and verify RED**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_opus_breaker.py
```

Expected: new batch-decision tests FAIL because the API does not exist.

**Step 3: Implement the minimal batch contract**

In `opus_dense.py`:

- introduce an immutable decision value, for example a frozen dataclass or
  `NamedTuple`;
- serialize state mutations with the existing lock or a dedicated breaker
  state lock;
- ensure one half-open batch can claim the probe slot;
- record a batch failure once, not once per failed candidate;
- preserve `dense_arm_status()` compatibility;
- log match-level trip, probe success, and probe failure without exposing
  credentials or product data.

Do not change thresholds or cooldown defaults.

**Step 4: Run breaker tests and verify GREEN**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_opus_breaker.py
```

Expected: all breaker state-transition and concurrency tests PASS.

---

### Task 4: Implement all-or-nothing retrieval scoring

**Files:**
- Modify: `backend/scudo_mapping_mcp/store/retrieval_scoring.py`
- Modify: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`
- Test: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`

**Step 1: Precompute the complete fallback baseline**

After BM25 nomination and before any model calls, compute:

```python
jaro_scores = {
    node.iri: _jaro_winkler(query_text, taxonomy_dense_text(node))
    for node in eligible
}
```

Do not filter nominees based on these values before the Opus attempt.

**Step 2: Attempt Opus as one logical batch**

When configured for Opus:

1. obtain the breaker batch decision;
2. if refused, set `dense_scores = jaro_scores`;
3. if permitted, submit strict scorer calls with at most eight workers;
4. collect all results;
5. on complete success, record batch success and use all Opus scores;
6. on any exception, record one batch failure, discard every model result, and:
   - use `jaro_scores` when fallback is enabled;
   - re-raise when fallback is disabled.

Prefer submitting futures and consuming their results explicitly so outstanding
work can be cancelled after failure. Executor shutdown may wait for already
running calls; correctness is more important than pretending cancellation can
stop network requests already in flight.

**Step 3: Correct the stale stateless comment**

Replace the claim that the scorer is stateless with the actual invariant:

```python
# Calls run concurrently for latency, but the batch is committed atomically:
# either every returned similarity is model-scored or all use the precomputed
# Jaro-Winkler baseline.
```

Also keep the existing note that order is keyed by IRI.

**Step 4: Run the batch contract tests and verify GREEN**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py \
  scudo_mapping_mcp/tests/test_opus_breaker.py
```

Expected: all tests PASS, including the previously red partial-failure test.

---

### Task 5: Prove timing-independent ranking and preserve performance bounds

**Files:**
- Modify: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`
- Test: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`

**Step 1: Add an interleaving regression test**

Use events/barriers to force two different completion orders for the same
fixture:

- run A: failures complete before successful calls;
- run B: successful calls complete before failures.

Assert:

```python
assert _shape(run_a) == _shape(run_b) == _shape(jaro_baseline)
```

If practical at this layer, pass the result into `map_vendor_product()` and
assert the same confidence and band. Otherwise assert candidate ordering and raw
similarity here and add a focused matcher-level test using the returned shape.

**Step 2: Add/retain performance-bound assertions**

Pin:

- no more than 25 strict model calls;
- executor worker count remains `min(8, len(eligible))`;
- an already-open breaker makes zero strict model calls;
- Jaro-Winkler configured mode makes zero strict model calls.

Do not assert wall-clock timings in CI.

**Step 3: Run focused tests**

Run:

```bash
PYTHONPATH=. python3.11 -m pytest -vv \
  scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py \
  scudo_mapping_mcp/tests/test_opus_breaker.py \
  scudo_mapping_mcp/tests/test_taxonomy_text_threading.py
```

Expected: all focused tests PASS.

---

### Task 6: Align operator-facing documentation

**Files:**
- Modify: `REVIEW_2026-08-14_demo_and_agent.md`
- Modify: `REMEDIATION_2026-08-14.md`
- Modify if still stale: `backend/scudo_mapping_mcp/opus_dense.py`
- Modify if still stale: `backend/scudo_mapping_mcp/store/retrieval_scoring.py`

**Step 1: Update §8a status**

Record the implemented contract:

- BM25 nominates;
- Jaro-Winkler baseline is precomputed;
- Opus remains concurrent;
- any Opus failure selects Jaro-Winkler for the complete match;
- no candidate list mixes score scales.

Do not claim exact latency without a new live Bedrock measurement.

**Step 2: Correct fallback wording**

Replace per-candidate fallback wording on this path with per-match/batch
fallback. Preserve any text describing `opus_dense_score()` behavior for legacy
callers if that remains true.

**Step 3: Run documentation/code searches**

Run:

```bash
rg -n "stateless|per candidate|mixed|all-or-nothing|fallback" \
  backend/scudo_mapping_mcp/opus_dense.py \
  backend/scudo_mapping_mcp/store/retrieval_scoring.py \
  REVIEW_2026-08-14_demo_and_agent.md \
  REMEDIATION_2026-08-14.md
```

Expected: every surviving claim accurately distinguishes the batch retrieval
path from legacy per-candidate callers.

---

### Task 7: Full verification

**Files:**
- Verify: `backend/scudo_mapping_mcp/`
- Verify: `backend/scudo/`

**Step 1: Check IDE diagnostics**

Run the IDE linter/diagnostic check for:

- `backend/scudo_mapping_mcp/opus_dense.py`
- `backend/scudo_mapping_mcp/store/retrieval_scoring.py`
- new and modified test files

Expected: no newly introduced diagnostics.

**Step 2: Run the mapping suite**

From `backend/`:

```bash
PYTHONPATH=. python3.11 -m pytest -q scudo_mapping_mcp/tests
```

Expected baseline: **569 passed, 0 failed**, plus the new tests added by this
plan.

**Step 3: Run the canonical smoke suite**

From `backend/`:

```bash
PYTHONPATH=. python3.11 -m scudo_mapping_mcp.tests.smoke
```

Expected: **117/117 pass** unless the suite has intentionally grown.

**Step 4: Run the full backend suite**

From `backend/`:

```bash
PYTHONPATH=. python3.11 -m pytest -q scudo/tests scudo_mapping_mcp/tests
```

Expected baseline before new tests: **1047 passed, 2 known provenance
failures**. The only accepted failures are the existing Marketing assertions in
`scudo/tests/test_provenance.py`; any other failure blocks completion.

**Step 5: Inspect the final diff**

Run:

```bash
git diff --check
git diff -- \
  backend/scudo_mapping_mcp/opus_dense.py \
  backend/scudo_mapping_mcp/store/retrieval_scoring.py \
  backend/scudo_mapping_mcp/tests/test_opus_breaker.py \
  backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py \
  REVIEW_2026-08-14_demo_and_agent.md \
  REMEDIATION_2026-08-14.md
```

Confirm:

- no unrelated dirty-worktree files were changed;
- no thresholds, confidence bands, publish gates, or evaluation fixtures moved;
- every production behavior change is covered by a test that was observed
  failing before implementation.

Do not commit unless explicitly requested.
