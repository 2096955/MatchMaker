# All-or-Nothing Dense Scoring Design

**Date:** 2026-08-15

## Problem

The local retrieval stores score up to 25 BM25-nominated candidates concurrently
when `SCUDO_DENSE_BACKEND=opus`. Each worker currently calls
`opus_dense_score()`, which may independently fall back to Jaro-Winkler. Because
the workers share a process-global circuit breaker, one candidate list can mix
Opus and Jaro-Winkler scores. Thread timing can therefore change ranking,
confidence, and the published band.

The thread pool itself is necessary: serial model calls made one match too slow
for the demo. The fix must retain bounded concurrent scoring without comparing
scores from different arms in one match.

## Decision

Use one effective dense arm for every candidate in a `score_candidates()` call.

1. BM25 nominates at most 25 candidates, as today.
2. Compute a complete Jaro-Winkler baseline for all nominees locally.
3. If the breaker is open and not ready for a half-open probe, skip Opus and use
   the complete baseline.
4. Otherwise attempt the Opus batch concurrently.
5. If every Opus call succeeds, use the complete Opus batch.
6. If any Opus call fails, discard every Opus result and use the complete
   Jaro-Winkler baseline.

Jaro-Winkler is a fallback baseline, not an eligibility gate. BM25 remains the
only nominator so that Opus can recover semantically relevant candidates with
weak lexical similarity.

## Architecture

Batch consistency belongs in `store/retrieval_scoring.py`, where the complete
candidate set is visible. `opus_dense.py` will expose a strict scoring seam that
returns an Opus score or raises; it will not silently substitute a per-candidate
Jaro-Winkler value. The existing `opus_dense_score()` public behavior remains
available to specialist and legacy callers.

The breaker remains process-local and owns availability/recovery state.
Retrieval scoring consults it before launching a batch and records batch
success or failure through a small public contract instead of mutating breaker
globals directly.

## Failure and Recovery Semantics

- Configured `jaro_winkler`: unchanged deterministic scoring.
- Configured `opus`, breaker open during cooldown: zero model calls; full
  Jaro-Winkler batch.
- Configured `opus`, probe allowed: attempt a bounded concurrent Opus batch.
- Any model exception in a batch: full Jaro-Winkler result; no partial Opus
  scores survive.
- Fallback disabled: preserve fail-loud behavior rather than silently changing
  the configured contract.
- Successful complete Opus batch: reset the breaker and use all model scores.
- Failed batch: record breaker failure coherently and log one match-level
  fallback decision.

## Invariants

- A returned candidate list contains exactly one similarity scale.
- This is NOT equivalence with `SCUDO_DENSE_BACKEND=jaro_winkler`. BM25 has
  already narrowed the pool to 25 nominees before the fallback decision, so a
  node with weak lexical overlap but strong string similarity appears in the
  jaro arm and not in a fallback result. Measured on a 40-node fixture during
  review. That follows from nominating for the model arm and is not a defect —
  but the contract must not be stated as identity.
- The guarantee must hold on BOTH opus routes: `score_candidates()` and the
  `SCUDO_USE_OPUS_DENSE=1` `multi_path_retrieve` route, which bypasses
  `score_candidates()` entirely.
- BM25 nomination remains capped at 25.
- Opus calls remain bounded to eight concurrent workers.
- Negative precedents and candidate filters retain their current ordering.
- Confidence bands, validations, specialist behavior, and publish gates do not
  change.
- No evaluation cases, thresholds, confidence bands, or gates are modified.

## Verification

Tests must prove:

- a complete successful Opus batch uses only Opus scores;
- one failed candidate discards earlier/later Opus successes and uses only the
  precomputed Jaro-Winkler batch;
- an open breaker skips all model calls;
- failed and successful half-open recovery preserve one arm per match;
- candidate order and band do not vary with worker interleaving;
- the 25-nominee and eight-worker performance bounds remain;
- existing Jaro-Winkler behavior and store parity remain unchanged.
