# Dense-arm SDK adoption — replan

| Field | Value |
|---|---|
| Status | Draft for ARB — replaces `dense-arm-swap.md` (v0.2, SUPERSEDED-PENDING-REPLAN) |
| Date | 2026-06-10 |
| Authoritative diagrams | `docs/architecture/scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd` |
| Owner | _[TBC]_ |
| Version | 0.3 (was 0.2 under the build-it-ourselves framing) |

## 1. Decision recap — what is being adopted, what is being dropped

### 1.1 What is being dropped (vs `dense-arm-swap.md` v0.2)

- **The build-it-ourselves wrapping of Bedrock Titan + FalkorDB `db.idx.vector.queryNodes` is DROPPED.** v0.2 proposed `embed(text) → vector` and `vector_knn(vec, k) → ranked nodes` as two thin primitives we own. ARB has declined that route.
- **A direct Bedrock Titan invocation as the dense arm is DROPPED for now.** Titan-embed-text-v2 was the chosen model; ARB has noted that (a) IAM grants for the model ARN, (b) eu-west-2 account-level Bedrock model access for `amazon.titan-embed-text-v2:0` in sandbox 954976331678, and (c) the SDK-preprocessing audit are **PARKED** and not closed in this workflow (see §6).
- **`dense-arm-swap.md` v0.2 is SUPERSEDED-PENDING-REPLAN.** That document is retained as historical context with a banner forward-pointer to this file. Its critical-review findings are carried forward under §6 / §8.

### 1.2 What is being adopted

- **The three architecture diagrams under `docs/architecture/` are the source-of-truth.** `scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd`. ARB has approved them. The retrieval diagram is labelled TARGET STATE for the SDK multi-path; everything inside the gate is the current contract.
- **For TODAY (WS-B, shipped):** the **dense arm is an Opus-4.8 prompt path**. The agent (Strands on Bedrock, Opus-4.8) scores semantic similarity through a structured prompt; that score becomes the "dense" arm input to `find_similar_products`. FalkorDB's `falkordb_store.find_similar_products` continues to do multi-path retrieval (BM25 lexical sidecar + structural pass: precedent boost, negative-precedent drop) over the working graph. The GraphRAG-SDK is **NOT integrated**. Bedrock Titan-embed is **NOT used**. There is no live `db.idx.vector.queryNodes` call.
- **The door is left open** to swap the Opus-prompt dense arm for the real GraphRAG-SDK vector path once the BLOCKING IAM / model-access / SDK-preprocessing items are resolved. The feature flag `SCUDO_DENSE_BACKEND` is the swap-point (see §7).

### 1.3 Why this shape

Pragmatism. The three-MCP trust gradient, the 0.80 confidence floor, the borderline-only specialist invocation, and the HMAC seal v=2 with band are all unchanged regardless of how the dense score is computed. Swapping the *source* of the dense score (LLM prompt vs Titan vector vs SDK vector) does not move the control envelope. We can ship and operate the demo path now, and exchange the dense source later without re-architecting.

## 2. Current state (WS-B, shipped)

### 2.1 Retrieval surface today

`scudo_mapping_mcp/store/falkordb_store.py::find_similar_products`:

- **Dense arm:** Opus-4.8 prompt path. The agent is asked, against each candidate's `label` (and where present `description`), to emit a similarity score in `[0, 1]`. That score is what populates `Candidate.similarity`. No Bedrock Titan invoke; no `CALL db.idx.vector.queryNodes`; no SDK import.
- **Lexical arm:** pure-Python BM25 over `TaxonomyNode.label` (`store/base.py` BM25 helper). Universe-scoped scan, not bounded by the dense arm — preserves recall of ticker / RIC / ISIN matches the Opus-prompt dense arm under-weights.
- **Fusion:** RRF (rank-only). `Candidate.similarity = raw dense score`. RRF + boost composes the **sort key**, never the similarity. This is the discipline the 0.80 floor is calibrated against.
- **Structural pass:** precedent boost (≤ +0.10 on rank only) recomputed from confirmed `MAPPED_TO` edges; negative-precedent drop applied as a pre-filter before scoring; distance check **deferred** (no anchor data).
- **GraphRAG-SDK:** NOT integrated. `falkordb_store.py` module banner currently still names "AWS GraphRAG Toolkit" as the production target — STALE; see §5 / §8.
- **Titan-embed:** NOT used. `amazon.titan-embed-text-v2:0` has zero call-sites in code today.

### 2.2 What the dense arm *is* in the Opus-4.8 path

A structured-prompt scoring call routed through the existing Strands Agent on Bedrock (`agent.py::BedrockMappingAgent`). For each candidate the agent returns a JSON-shaped `{iri, similarity}` constrained to `[0, 1]`. That value is the raw dense score; it lands on `Candidate.similarity` unchanged.

### 2.3 What the dense arm is NOT, today

- Not a vector embedding.
- Not a Bedrock Titan invocation.
- Not a GraphRAG-SDK call.
- Not an HNSW / cosine query against a FalkorDB vector index.

The retrieval diagram's `VEC` node (Titan via LiteLLM) is **TARGET**, not shipped. The diagram is annotated accordingly in the `architecture/README.md`.

## 3. Invariants preserved

These hold in WS-B today and MUST hold under any future dense-arm swap:

- **I-RAW-DENSE.** `Candidate.similarity` is the raw dense score. RRF, boost, and any rerank affect SORT ORDER only. Encoded at `falkordb_store.py:194-209, 265-282` and at `base.py:281-362`. Under SDK adoption, the SDK's cosine rerank must either pass through the raw dense score on `Candidate.similarity` or the floor + disagreement-cap must move outside the SDK boundary (Open Question §8, retrieval-Q1 / retrieval-Q2).
- **I-FLOOR-0.80.** The three-band gate floor is `0.80`, half-width `0.05`. `pass_threshold = floor + half = 0.85`; `borderline_threshold = floor - half = 0.75`. Defined at `config.py:44, 49, 123-124, 179-182`; read at `matching.py:243-246`. **Calibrated against the Jaro-Winkler distribution.** Opus-prompt scoring is a different distribution; calibration is mandatory before the floor can be trusted under WS-B (see §4).
- **I-BOOST-RANK-ONLY.** Precedent boost is `+0.02` per confirmed approval, capped at `+0.10`, applied to `rank_score` only — never to `similarity`. `base.py:232-253` derives the boost from `MAPPED_TO` edges; `falkordb_store.py:281` scales by `rrf_top`. Under SDK adoption the `*rrf_top` scaling requires re-derivation because cosine rerank outputs sit in a different order of magnitude (Open Question §8, retrieval-Q4).
- **I-SEALED-BAND.** HMAC seal v=2 carries `band ∈ {pass, borderline, fail}`. Signed in Match&Verify only; verified in Persistence only. `verdict.py:155, 160, 215-219`. Unchanged.
- **I-TRUST-GRADIENT.** Ingestion MCP read-only; Match&Verify MCP matcher + signer (no writes); Persistence MCP sole writer + I5 gate. Validated by import boundaries in `ingestion_mcp.py:41-51`, `match_verify_mcp.py:54-65`, `persistence_mcp.py:64-78`. Unchanged.
- **I-SPECIALIST-BORDERLINE-ONLY.** Specialist runs only on `band == borderline`; `confidence = min(dense, specialist)` on concur; disagreement caps `confidence = min(best, borderline_threshold - 0.01) = 0.74` to force `NEEDS_REVIEW`. `matching.py:243-246, 310, 318, 351`. Unchanged.

## 4. Calibration plan

The 0.80 floor and ±0.05 half-width were calibrated against the Jaro-Winkler character-similarity distribution. Opus-prompt scoring is a different distribution shape — likely more bimodal (the LLM tends toward confident `0.9+` for clear matches and confident `<0.3` for clear mismatches, with a thinner middle than character-similarity). Without recalibration the band routes the wrong volume to the specialist: too few cases at risk of auto-promoting weak matches, too many at the cost of borderline-specialist invocations.

### 4.1 Golden set

- **N = 50 hand-mapped vendor products** drawn from the JPMC catalogue against the CDAO taxonomy.
- **Coverage targets:** ~20 clear matches (expected `band == pass`), ~15 clear non-matches (expected `band == fail` or out-of-scope), ~15 ambiguous cases (expected `band == borderline`, intended specialist invocations).
- **Provenance:** mappings come from the reviewer queue's APPROVED / REJECTED decisions where available; otherwise hand-curated by the SCUDO team with two-reviewer concurrence.
- **Storage:** `tests/golden/dense_arm_calibration_v1.jsonl` (path TBC; not yet in repo).

### 4.2 Baseline metrics (Opus-prompt dense arm, current WS-B)

Run the golden set through `find_similar_products` end-to-end and emit:

- **Score distribution histogram** of `Candidate.similarity` for the top-1 candidate per probe, split by ground-truth band.
- **Per-band precision / recall** at the current floor + half (pass-precision, borderline-correctness, fail-precision).
- **Rank-correlation against Jaro-Winkler** (run both arms on the same probes; Spearman ρ. ρ > 0.5 means the floor recalibration is a tuning shift; ρ ≤ 0.5 means the band semantics shift too and the half-width may need to widen).
- **Specialist-invocation rate**: fraction of probes routed to borderline. If significantly above the Jaro-Winkler baseline (~6-8% today), the half-width needs to narrow or the floor needs to lift.

### 4.3 Recalibration rule

- If the **pass-precision** at floor=0.80 is below the I5-target (TBC, expected ≥ 0.95), **lift the floor** to the value where pass-precision crosses the target. Cap the lift at 0.85; above that, the gate is too restrictive for the demo and a different scoring source is needed.
- If the **specialist-invocation rate** exceeds the cost-target (TBC, expected ≤ 10% of in-scope probes), **narrow the half-width** until the borderline band intersects the target rate.
- If the score distribution is **bimodal with a sparse middle** (Opus-prompt typical), consider widening the half-width to absorb the sparse mid-zone into the borderline band rather than letting it slip into pass / fail by noise.
- **Re-run calibration whenever the dense source changes** (Opus → Titan-embed → SDK). The floor recalibrate is mandatory, not optional, on any swap. This is `i5-lift-preconditions.md §4.2` and is unchanged.

### 4.4 What calibration does NOT do

- It does not register the Opus-4.8 dense-arm scoring path with MRGR — that is `i5-lift-preconditions.md §4.10` and is a separate gate.
- It does not lift I5 — that is `i5-lift-preconditions.md §4` in aggregate.
- It does not validate the SDK / Titan path — only the WS-B Opus-prompt path. The Titan / SDK path requires its own golden-set run inside its own change.

## 5. Path-to-prod — when we adopt a real vector store + GraphRAG-SDK

The Opus-prompt dense arm is the demo / sandbox shape. The production target is the diagram-as-drawn: GraphRAG-SDK vector + fulltext + cypher + relationship-expansion over a Titan-embedded FalkorDB index. The path to get there has six required deliverables:

### 5.1 IAM grant for Titan model ARN

The Match&Verify task role today has `bedrock:InvokeModel` for Opus 4.8 only. Production needs an additional statement scoped to the Titan model ARN:

```
arn:aws:bedrock:eu-west-2::foundation-model/amazon.titan-embed-text-v2:0
```

(written verbatim so policy review can grep it). Resource-level scoping — not `Resource: "*"` — and conditioned on the M&V task role principal only. Persistence and Ingestion roles MUST NOT receive this grant; the trust gradient demands the embed surface live only in M&V.

### 5.2 Bedrock model access enabled in eu-west-2 / sandbox 954976331678

Account-level Bedrock console toggle: Model access → Amazon Titan Text Embeddings V2 → Enable for eu-west-2. This is a separate enablement from IAM; both are required. Confirmed enablement evidence (screenshot or `aws bedrock list-foundation-models --region eu-west-2 --by-output-modality EMBEDDING` showing the model as accessible) must land in the same PR as the SDK adoption.

### 5.3 SDK preprocessing audit

GraphRAG-SDK runs its own preprocessing pipeline (text normalisation, chunking, stopwording). For the raw-dense-as-similarity invariant (I-RAW-DENSE) to hold under SDK adoption we need to know:

- Does the SDK apply preprocessing to **both** the query and the indexed labels (symmetric), or only to indexed labels (asymmetric)?
- Does the SDK expose the raw dense (cosine) score per candidate **before** its rerank, or only the reranked-and-normalised score?
- Does the SDK accept a **custom reranker callable** so we can preserve the raw-dense pass-through?

Asymmetric preprocessing shifts the score distribution at the seam — recalibration is mandatory, not optional. The audit must produce a written finding ("symmetric / asymmetric"; "raw exposed / rerank-only"; "custom reranker supported / not") before the SDK can be wired.

### 5.4 Shadow-property reindex protocol

If the SDK exposes index lifecycle (rebuild vector / fulltext on schema change), it MUST be wired as shadow-property reindex — build alongside under a new property name, atomic alias swap, then drop the old — not in-place rebuild. In-place floods the reviewer queue with transient `NEEDS_REVIEW` during rebuild. This is `dense-arm-swap.md` v0.2 Theme 7 carried forward.

### 5.5 Per-arm observability

Before the flag flip (§7), the following metrics MUST be wired and emitting to CloudWatch:

- Per-arm latency: VEC, FT, CY, REL, RR (rerank), BOOST, DROP.
- Per-arm hit count and per-arm contribution to surviving candidates.
- Score distribution histogram (raw dense, post-RRF / post-rerank, post-boost).
- Rerank-vs-raw-dense gap (sanity check for the I-RAW-DENSE invariant).
- Boost-scaling diagnostic (if `*rrf_top` is replaced — emit the replacement scaling factor).
- Precedent-drop count per probe.
- Fail-closed rate (EmbeddingError / SDK retrieval error → `NEEDS_REVIEW band=fail`).

This is `dense-arm-swap.md` v0.2 Theme 6 carried forward and `i5-lift-preconditions.md §4.5` partial closure.

### 5.6 Golden-set recalibration

Same shape as §4 above but against the live Titan + SDK distribution. Must land **in the same change** as the SDK adoption — not a follow-up. This is `i5-lift-preconditions.md §4.2` and is the gate that controls I5 lift.

## 6. Blocking items PARKED

These were identified as BLOCKING in `arb-review-pack.md §5.1` and `dense-arm-swap.md` v0.2 Theme 3. The user has explicitly **PARKED** them at this workflow boundary. They are NOT resolved here; they remain open as preconditions for the path-to-prod swap (§5).

| Item | Owner | Status | Rationale for parking |
|---|---|---|---|
| IAM policy add — `bedrock:InvokeModel` on Titan model ARN for M&V task role | _[TBC]_ | PARKED | The Opus-prompt dense arm in WS-B does not require Titan IAM. Adding the grant prematurely widens the trust surface for a path we are not using. Re-table in the SDK-adoption PR. |
| Bedrock model access enablement — Titan-embed-text-v2 in eu-west-2 / 954976331678 | _[TBC]_ | PARKED | Account-level toggle; takes minutes once decided but unblocks nothing in WS-B. Defer to the SDK-adoption PR. |
| SDK preprocessing audit — symmetric vs asymmetric; raw-dense exposure; custom reranker support | _[TBC]_ | PARKED | The audit cannot be done without standing the SDK up against a real graph. Parked until the SDK adoption is sequenced. |
| GraphRAG-SDK version pin — commit hash on a provenance property | _[TBC]_ | PARKED | The SDK is not integrated; pinning a hash for a non-imported dependency is premature. Re-table in the SDK-adoption PR. (Theme 8 of `dense-arm-swap.md` v0.2.) |
| Floor recalibration against a Titan + SDK distribution | _[TBC]_ | PARKED | Cannot run until SDK is integrated. Tracked under `i5-lift-preconditions.md §4.2`. |

The WS-B Opus-prompt path **does** require its own floor recalibration (§4), and that calibration is **not parked** — it is in scope and is the gate that lets the demo run with credible band semantics.

## 7. Rollback — feature flag SCUDO_DENSE_BACKEND

A single flag controls **both** retrieval-side and write-side behaviour, atomically. This is `dense-arm-swap.md` v0.2 Theme 4 carried forward (Theme 4 said the flag must gate the WRITE side too; it does).

| Value | Dense arm source | I5 gate behaviour |
|---|---|---|
| `opus_prompt` (default, WS-B) | Opus-4.8 structured-prompt scoring through the Strands Agent | Persistence `commit_mapping` accepts `sealed_status` routing; `sealed_band == fail` for any auto-mapped seal is refused / down-routed to the reviewer queue. Opus-prompt-derived seals are tagged `dense_source=opus_prompt` on the trajectory archive. |
| `titan_sdk` (target, parked) | GraphRAG-SDK multi-path over a Titan-embedded FalkorDB index | Same gate semantics. Additionally: refuse any `commit_mapping` payload whose `dense_source != settings.scudo_dense_backend` — this catches in-flight seals from the prior backend after a flag flip. |

### 7.1 Flag flip protocol

- Read in M&V at `find_similar_products` entry: if `settings.scudo_dense_backend == "opus_prompt"`, route through the Opus-prompt path; if `"titan_sdk"`, route through the SDK path.
- Read in Persistence at `commit_mapping` entry: refuse seals whose `dense_source` field does not match the current setting.
- Both reads happen at request time; no cached snapshot. Flip is atomic at the next request, no service restart required.
- Rollback is one env var flip back to `opus_prompt` and one ECS task-def update. Mean time to rollback: minutes.

### 7.2 What the flag does NOT do

- It does not toggle calibration. Each backend has its own calibrated floor; switching backend without running the matching calibration is operator error.
- It does not toggle MRGR registration. Each scoring path must be registered (Opus-4.8 as a registered model already, Titan-v2 to be registered when adopted) under `i5-lift-preconditions.md §4.10`.
- It does not lift I5. The I5 gate is the band-aware Persistence routing, which is unchanged by the flag value.

## 8. Open questions — GraphRAG-SDK (kept verbatim from `arb-review-pack.md §6`, PARKED)

The following questions are the GraphRAG-SDK / retrieval-surface Open Questions from `arb-review-pack.md §6` ("Diagram-vs-code reconciliation" → scudo-retrieval entries). They are reproduced **verbatim** so future readers cannot mistake a paraphrase for the original wording. All are **PARKED** until §5 / §6 unblock.

- **[scudo-retrieval] Does GraphRAG-SDK expose the raw dense (cosine) score per candidate before its rerank, or only reranked-and-normalised scores?** This decides whether `Candidate.similarity` can keep its raw-dense, floor-anchored contract or whether the floor + disagreement-cap must move outside the SDK boundary. **PARKED.**

- **[scudo-retrieval] Does the SDK accept a custom reranker callable (the "swappable" claim on the diagram), or are we forced to either accept its cosine rerank or post-rerank ourselves outside the SDK?** **PARKED.**

- **[scudo-retrieval] Does the SDK do its own text preprocessing (stopwording, chunking, normalisation) before computing embeddings, and if so does it apply that to the QUERY as well as to indexed labels?** If asymmetric, the 0.80 floor's calibration distribution shifts and a recalibration is mandatory — not optional. **PARKED.**

- **[scudo-retrieval] Should the in-file production-swap commentary (`falkordb_store.py:4-37` and `base.py:18-23`) be updated in the same change as the diagram lands, so the next reviewer doesn't trip on the "AWS GraphRAG Toolkit, vector-only" framing?** And should `dense-arm-swap.md` be marked SUPERSEDED with a forward pointer? (Yes, the latter is done in this workflow.) **PARKED for the in-file commentary; banner-supersession resolved.**

- **[scudo-retrieval] Is the `*rrf_top` scaling of the boost (`falkordb_store.py:281`) still correct under cosine rerank?** Cosine scores sit in `[-1, 1]` (or `[0, 1]` post-normalisation), an entirely different order of magnitude from RRF's ~0.016 top contribution — the scaling factor will need re-derivation. **PARKED.**

## 9. Document control

- **Workflow:** WS-E, dense-arm-sdk-adoption.md draft.
- **Run date:** 2026-06-10.
- **Supersedes:** `docs/dense-arm-swap.md` v0.2 (build-it-ourselves plan).
- **Linked authority:** `docs/architecture/arb-review-pack.md` (ARB tabling pack); `docs/architecture/scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd` (approved diagrams).
- **Linked preconditions:** `docs/i5-lift-preconditions.md` §4.1 / §4.2 / §4.4 / §4.5 / §4.10.
- **Forward references:** none — this is the current plan.

**End of document.**
