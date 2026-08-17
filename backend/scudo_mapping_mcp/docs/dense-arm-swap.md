# Dense-arm swap — Build plan

> **SUPERSEDED-PENDING-REPLAN — 2026-06-10**
>
> This plan was written on the assumption that we would **BUILD the dense-arm primitives ourselves** — wrapping Bedrock Titan invoke as `embed(text) → vector` and FalkorDB `CALL db.idx.vector.queryNodes` as `vector_knn(vec, k) → ranked nodes` — explicitly choosing **not** to adopt the FalkorDB GraphRAG-SDK (see §2 "Why we're not using the SDK" below).
>
> **That decision has been reversed.** We are **adopting the FalkorDB GraphRAG-SDK**, which provides vector + fulltext + cypher + relationship expansion + cosine rerank out of the box. The SCUDO-specific structural pass (precedent boost, negative-precedent drop, distance check) plugs into the SDK as swappable strategies. The trust gradient and the I5 gate are unchanged. The canonical retrieval diagram is now `docs/architecture/scudo-retrieval.mmd`.
>
> **Critical-review findings from v0.2 that STILL APPLY to SDK adoption (carry forward verbatim):**
>
> - **Theme 1 — Fail-closed contract is prose, not code.** `matcher.py:208` still has no `try/except` for retrieval errors. Routing `IndexVersionMismatchError` / `EmbeddingError` / SDK retrieval errors to `NEEDS_REVIEW` band=`fail` must be implemented in code, not asserted in docs. **STILL APPLIES.**
> - **Theme 3 — IAM / Bedrock surface unspecified.** The Match&Verify task role still needs `bedrock:InvokeModel` for Titan (now invoked **via the SDK's LiteLLM layer**) AND the AWS console model-access toggle must be enabled for Titan in eu-west-2. **STILL APPLIES.**
> - **Theme 5 — Score normalisation `[-1, 1]` vs `[0, 1]`.** The SDK's cosine-rerank output range must be empirically verified before it is connected to the 0.80 floor gate; if the SDK emits raw cosine in `[-1, 1]` we must normalise to `[0, 1]` at the seam. **STILL APPLIES.**
> - **Theme 6 — Rollback model asymmetry.** If a feature flag wraps SDK adoption, the flag flip must gate the **WRITE** side too — not just the read path — or rollback will leave inconsistent state in Neptune. **STILL APPLIES if a flag wraps SDK adoption.**
> - **Theme 7 — Reindex floods review queue.** If the SDK exposes reindex semantics (rebuild vector index / fulltext index on schema change), they must be implemented as **shadow-property** reindex (build alongside, atomic swap) — not in-place — to avoid flooding the reviewer queue with transient `NEEDS_REVIEW` during rebuild. **STILL APPLIES.**
> - **Theme 8 — Observability.** Embed-latency, retrieve-latency, throttle counts, and fail-closed rate metrics still need to be wired (now at the SDK boundary rather than at our own primitives). **STILL APPLIES.**
> - **Theme 9 — Persistence dependency.** Reviewer queue is still in-memory (DynamoDB queue provisioned but not wired). **STILL APPLIES, separate workstream.**
> - **Theme 10 — Preprocessing policy version derivation.** The SDK's preprocessing is internal to the SDK; we therefore need to **pin the SDK version to a hash** and surface that hash on the same provenance property where we previously planned to record our own preprocessing-policy version. **STILL APPLIES with shifted location.**
>
> **Replacement plan (now drafted):** [`dense-arm-sdk-adoption.md`](dense-arm-sdk-adoption.md) is the live plan. It records the WS-B Opus-4.8-prompt dense arm as the shipped state, leaves the door open for a real vector / SDK swap behind the `SCUDO_DENSE_BACKEND` feature flag, and parks the BLOCKING IAM / Bedrock-model-access / SDK-preprocessing items (with owners TBC) until the path-to-prod swap. All carried-forward findings above are tracked there in §3 (invariants), §5 (path-to-prod), §6 (parked blockers), and §8 (open questions, verbatim).
>
> **Numbers below are POINT-IN-TIME (2026-06-10). Do not "correct" them.**
> This document was written when `config.CONFIDENCE_FLOOR` really was `0.80`
> (that value held from the initial commit until `eb48d67`, 2026-07-04,
> "feat(matcher): move confidence bands to 0.80/0.70"). Every "0.80 floor"
> below is therefore an accurate historical record, NOT a mislabel.
>
> As of 2026-07-04 the live values are: `CONFIDENCE_FLOOR = 0.75` (the band
> CENTRE, `config.py:49`) with `BORDERLINE_HALF_WIDTH = 0.05`, giving
> `PASS_CUT = 0.80` / `FAIL_CUT = 0.70`. Note BORDERLINE (0.70-0.80) can
> still auto-map, so a present-day safety argument must cite FAIL_CUT 0.70,
> not the 0.80 PASS cut. Separately, `backend/scudo/orchestrator.py:41` has
> its own unrelated `CONFIDENCE_FLOOR = 0.80` (Runtime-A auto-approve publish
> gate) — the two constants are independent and are not to be reconciled.
>
> Content below is retained as historical context only. Do not edit.
>
> ---

| Field | Value |
|---|---|
| Status | Draft for review — v0.2 incorporates critical review (21 findings) |
| Goal | Replace the Jaro-Winkler dense-arm stand-in with real semantic embeddings (Bedrock Titan v2) backed by FalkorDB's native vector index. Lift the core principles from FalkorDB's GraphRAG-SDK without adopting the SDK itself. |
| Scope | Code change only. Does **NOT** lift I5, does **NOT** recalibrate the 0.80 floor against real data, does **NOT** register the embedding model with MRGR. Those are downstream consequences with their own gates ([i5-lift-preconditions.md §4.2](i5-lift-preconditions.md)). |
| Estimated size | ~200 LoC (up from ~150 — observability + matcher catch site + shadow-property reindex). 4 PR-sized chunks. |
| Owner | _[TBC]_ |
| Version | 0.2 |

## 1. What we're building, in one sentence

Two primitives wired through the existing seam: **`embed(text) → vector`** (Bedrock Titan invoke) and **`vector_knn(vec, k) → ranked nodes`** (FalkorDB `CALL db.idx.vector.queryNodes`). Everything else in `find_similar_products` — BM25 sidecar, RRF, structural pass, candidate filter — stays exactly as today.

## 2. Why we're not using the SDK

Decided in the prior conversation. Short form: the SDK is a docs → KG → QA pipeline that owns retrieve-plus-generate as one step and imposes structural conventions around your schema. We have the graph already (CDAO), we already do hybrid retrieval, and we need retrieve and gate to be **separate deterministic steps** that the LLM cannot reach (I3/I4/I5/I6). The SDK's value would be ~10% of its surface; the other 90% would fight our trust gradient. The two primitives we *do* need are well-documented FalkorDB + Bedrock APIs and cost ~150 lines.

## 3. In scope / out of scope

### In scope (this plan)

- `embeddings.py` — Bedrock Titan invoke surface + preprocessing function
- `matching.py` — **NEW: try/except at Rung 3 boundary** routing `IndexVersionMismatchError` / `EmbeddingError` to `NEEDS_REVIEW` band=`fail`
- `store/falkordb_store.py` — dense arm swap + vector index seed + shadow-property reindex
- `store/base.py` — seam additions for embedding model identity
- `ingest.py` — embed-on-seed for taxonomy nodes (batched)
- `config.py` — embedding model + dimension + version + flag settings
- `scudo_mapping_mcp/scripts/reindex_taxonomy.py` — two-phase shadow-property reindex
- Smoke gates covering the new contract (~15 unit + ~5 matcher-side)
- Observability — metrics on embed latency, throttles, fail-closed rate
- Doc updates — eight files listing the Jaro-Winkler stand-in:
  - `matching.py:L202` (module docstring)
  - `store/base.py:L19, L271-275`
  - `store/falkordb_store.py:L8-21, L188-199, L224-233`
  - `docs/diagram-2-falkor-internals.md:L48-61`
  - `docs/README.md:L8`
  - `docs/i5-lift-preconditions.md:L78, L84`

### Explicitly out of scope (own follow-ups)

- **Floor + band recalibration against a golden set.** The 0.80 floor and ±0.05 bands are calibrated for Jaro-Winkler. Embeddings change the score distribution. This swap MUST be followed by a calibration event before I5 can lift. Tracked under [i5-lift-preconditions.md §4.2](i5-lift-preconditions.md).
- **MRGR registration of the embedding model.** Model card, independent validation, drift suite. Tracked under [i5-lift-preconditions.md §4.10](i5-lift-preconditions.md).
- **Vendor-product embedding cache.** Every `find_similar_products` call burns one Bedrock invoke. At programme scale this is fine (~fractions of a cent per query) but a per-vendor-product LRU is worth it later. Not blocking.
- **FakeStore embeddings.** FakeStore stays at `set_score` direct-similarity semantics so existing smoke tests don't change. Real-FalkorDB integration tests are the discipline for the new contract.
- **I5 lift.** Separate gate, separate document.

## 4. Architecture changes — what moves where

### 4.1 New module: `embeddings.py` (~55 LoC)

Adds: explicit `preprocess()` (no-op for Titan v2 — pass raw text), `normalize=True` body, adaptive retry, module-singleton client (avoid per-call boto3.client overhead).

```python
"""
Bedrock Titan embedding surface.

Single seam: embed(text: str) -> list[float]. Used at TWO points:
  - Taxonomy seed time (one embed per node, persisted on the node)
  - Query time inside find_similar_products (one embed per vendor product)

Versioning: every embedding written to FalkorDB carries the model_id and
embedding_dim it was produced under. The reindex script detects drift and
re-embeds; the store fails-closed on a dimension mismatch at query time.

PREPROCESSING POLICY: Titan v2 handles raw text well. We deliberately do
NOTHING (no lowercase, no punctuation strip) so the embedded distribution
matches Titan's training. The preprocessing function is a documented seam
so any future change is captured by the version-derivation hash.
"""
from __future__ import annotations

import json

import boto3
from botocore.config import Config

from .config import settings


class EmbeddingError(RuntimeError):
    """Raised on Bedrock failure or dim mismatch. Matcher catches at Rung 3."""


def preprocess(text: str) -> str:
    """No-op for Titan v2 — pass raw text. Source code is hashed into
    settings.embedding_version so any edit forces a reindex."""
    return text or ""


# Module-singleton client with adaptive retry + bounded timeout.
# Per-call boto3.client() carries config-load overhead we don't want
# in the hot path (every find_similar_products call goes through here).
_CLIENT = boto3.client(
    "bedrock-runtime",
    region_name=settings.bedrock_region,
    config=Config(
        retries={"mode": "adaptive", "max_attempts": 5},
        read_timeout=10,
        connect_timeout=5,
    ),
)


def embed(text: str) -> list[float]:
    """Bedrock Titan invoke. Returns the embedding as a list of floats.

    Sets normalize=True in the Titan request body so cosine similarity
    scores from FalkorDB land in [0, 1] — see falkordb_store.find_similar_products
    score-clamp comment for why this matters to the matcher's floor gate.

    Raises EmbeddingError on Bedrock failure (4xx/5xx, throttle exhaustion)
    or dim mismatch. The matcher catches at Rung 3 and routes the case to
    NEEDS_REVIEW with band='fail' (see matching.py change in §4.8).
    """
    body = json.dumps({
        "inputText": preprocess(text),
        "normalize": True,
        "dimensions": settings.embedding_dim,
    })
    try:
        response = _CLIENT.invoke_model(
            modelId=settings.embedding_model_id,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
    except Exception as e:
        raise EmbeddingError(f"Bedrock invoke failed: {type(e).__name__}: {e}") from e
    payload = json.loads(response["body"].read())
    vec = payload["embedding"]
    if len(vec) != settings.embedding_dim:
        raise EmbeddingError(
            f"Titan returned dim={len(vec)}, settings pin dim={settings.embedding_dim}"
        )
    return vec
```

### 4.2 `config.py` — pin model + dim + version + flag

```python
# Settings dataclass additions:
embedding_model_id: str       # "amazon.titan-embed-text-v2:0"
embedding_model_arn: str      # DERIVED — "arn:aws:bedrock:{region}::foundation-model/{model_id}"
                              # — written verbatim so IAM policies can reference it.
embedding_dim: int            # pinned per model — 1024 for Titan v2
embedding_version: str        # DERIVED hash. Forces correctness: any change to
                              # the inputs invalidates the index without an engineer
                              # remembering to bump.
                              # Computed at module load as:
                              #   sha256(
                              #     model_id +
                              #     str(dim) +
                              #     inspect.getsource(embeddings.preprocess)
                              #   ).hexdigest()[:12]
                              # — pulling the preprocess source means an edit to
                              # the function automatically bumps the version.
bedrock_region: str           # "eu-west-2" for dev/demo
use_vector_dense: bool        # SCUDO_USE_VECTOR_DENSE — feature flag for the
                              # dense arm. False today (Jaro-Winkler);
                              # PR 4 flips default to True per-environment.
```

`from_env()` reads `SCUDO_EMBEDDING_MODEL_ID`, `SCUDO_EMBEDDING_DIM`, `BEDROCK_REGION`, `SCUDO_USE_VECTOR_DENSE`. `embedding_model_arn` and `embedding_version` are derived; never set from env so they can't drift.

### 4.3 `store/falkordb_store.py` — dense arm swap (~50 LoC)

This is the load-bearing one. Five things change vs v0.1:

1. **BM25 keeps its full-universe scan** — the vector kNN does NOT bound the BM25 set. Without this, BM25's whole point (ticker / RIC / ISIN recovery for items embeddings rank low) is silently lost. Vector kNN top-K and BM25 top-K are UNION'd before RRF.
2. **Score clamp `max(0.0, float(score))`** — cosine on un-normalised vectors is `[-1, 1]`, and the matcher's floor gate (`matching.py:264, 272, 285, 296, 351`) compares against `0.80` expecting `[0, 1]`. Normalisation in §4.1's Titan body should make this redundant, but the clamp is defense-in-depth at the seam.
3. **Dynamic over-fetch budget** instead of `*3` constant. Negative precedents and candidate_filter drops compound; rejects concentrate at the top of cosine ranking because reviewers reject what the system surfaced. Constant multiplier breaks for high-volume vendors.
4. **Whole-query fail on any version mismatch** — was the v0.1 design; reaffirmed. Per-node skip was the wrong gate name in v0.1 §6.1.
5. **`if not settings.use_vector_dense: ... return jaro_winkler_path(...)`** branch at the top — feature flag honoured.

```python
def find_similar_products(self, ref, max_results=10, min_similarity=0.0,
                          *, candidate_filter=None):
    if not settings.use_vector_dense:
        return self._jaro_winkler_path(ref, max_results, min_similarity,
                                       candidate_filter=candidate_filter)

    rejected = set(self.get_negative_precedents(ref.vendor, ref.product_id))
    query_text = f"{ref.name} {ref.description}".strip() or ref.product_id

    # Dynamic budget: clamp_results + len(rejected) + candidate_filter headroom.
    # Static *3 breaks for vendors with many rejects (a single high-mapped vendor
    # may have 25+ reviewer-rejected nodes, concentrated at the top of cosine
    # ranking — by construction, since rejects are nodes the system surfaced).
    cf_headroom = 5 if candidate_filter is not None else 0
    k_vec = self.clamp_results(max_results) + len(rejected) + cf_headroom

    query_vec = embed(query_text)  # raises EmbeddingError; matcher catches

    # Vector kNN — bounded set
    hits = self._ro(
        "CALL db.idx.vector.queryNodes('TaxonomyNode', 'embedding', $k, $vec) "
        "YIELD node, score "
        "RETURN node.iri, node.label, node.parent_iri, "
        "       node.embedding_version, score",
        {"k": k_vec, "vec": query_vec},
    )

    # Full-universe scan for BM25 — preserves recall of ticker / RIC / ISIN
    # matches that embeddings rank low. Without this, BM25 only sees the
    # cosine top-K and the lexical-recovery promise is silently broken.
    universe = self._ro(
        "MATCH (t:TaxonomyNode) RETURN t.iri, t.label, t.parent_iri"
    )

    labels: dict[str, str] = {}
    parents: dict[str, Optional[str]] = {}
    dense_scores: dict[str, float] = {}

    # Build labels from the FULL universe so BM25 has the universe to score.
    for iri, label, parent in universe:
        if iri in rejected:
            continue
        labels[iri] = label or ""
        parents[iri] = parent or None

    # Score dense arm from the vector hits ONLY — and check version coherence.
    version_mismatches: list[str] = []
    for iri, label, parent, embedding_version, score in hits:
        if iri in rejected:
            continue
        if embedding_version != settings.embedding_version:
            version_mismatches.append(iri)
            continue
        if iri not in labels:
            # Shouldn't happen — vector hit but absent from universe scan —
            # but be defensive.
            labels[iri] = label or ""
            parents[iri] = parent or None
        # Defense-in-depth: clamp negative cosine to 0. Titan's normalize=True
        # should make this unreachable, but matching.py's 0.80 floor gate
        # cannot tolerate negative values.
        dense_scores[iri] = max(0.0, float(score))

    # Whole-query fail on ANY version mismatch — mixing score distributions
    # from two embedding models is the I5-style "never auto-promote on a
    # corrupted dense arm" violation.
    if version_mismatches:
        raise IndexVersionMismatchError(
            f"{len(version_mismatches)} nodes on stale embedding_version "
            f"(current={settings.embedding_version}); "
            f"run scudo_mapping_mcp.scripts.reindex_taxonomy"
        )

    # BM25 + RRF + structural pass continues as today (universe-scoped BM25).
    # ... [unchanged from current implementation, only the dense arm changed]
```

`_jaro_winkler` and `_jaro_winkler_path(...)` (a refactor of today's full method body) are kept — the flag-False path lives here. Don't delete.

### 4.4 `store/falkordb_store.py` — seed the vector index (~15 LoC)

New method, called once at install time:

```python
def seed_vector_index(self) -> None:
    """Idempotent. Creates the HNSW vector index on TaxonomyNode.embedding
    if it doesn't exist. Safe to call on every container start."""
    self._g.query(
        "CALL db.idx.vector.createNodeIndex("
        "  'TaxonomyNode', 'embedding', $dim, 'cosine'"
        ")",
        {"dim": settings.embedding_dim},
    )
```

**Batching at seed time.** `upsert_taxonomy_node` is called per node by `ingest.seed_taxonomy`. With K taxonomy nodes that's K sequential Bedrock invocations — minutes on a large CDAO. `ingest.py` (§4.5) must batch the embed calls with bounded concurrency (recommend a `concurrent.futures.ThreadPoolExecutor` with max_workers=8) and call the store only with `(node, vec)` tuples already in hand. Spec'd here as a constraint on §4.5, not inside `upsert_taxonomy_node`, so per-node upserts from the reviewer path stay simple and don't pay the threadpool overhead.

`upsert_taxonomy_node` writes the embedding alongside label/parent:

```python
def upsert_taxonomy_node(self, node: TaxonomyNode) -> None:
    vec = embed(f"{node.label}")    # M5+: include description if present
    self._g.query(
        "MERGE (t:TaxonomyNode {iri:$iri}) "
        "SET t.label=$label, t.parent_iri=$parent, "
        "    t.embedding=$vec, "
        "    t.embedding_model=$model, "
        "    t.embedding_version=$ver",
        {
            "iri": node.iri, "label": node.label,
            "parent": node.parent_iri or "",
            "vec": vec,
            "model": settings.embedding_model_id,
            "ver": settings.embedding_version,
        },
    )
    if node.parent_iri:
        self._g.query(
            "MATCH (p:TaxonomyNode {iri:$p}),(c:TaxonomyNode {iri:$c}) "
            "MERGE (p)-[:HAS_CHILD]->(c)",
            {"p": node.parent_iri, "c": node.iri},
        )
```

### 4.5 `ingest.py` — call `seed_vector_index` and BATCH the embeds

Two changes:

1. Call `store.seed_vector_index()` once at the top.
2. Compute embeddings via a bounded thread pool (max_workers=8) before calling `upsert_taxonomy_node`, so K sequential Bedrock invokes become roughly K/8 batches. Per-node upsert from non-seed paths (reviewer queue, future single-node additions) continues to embed inline because the cost there is one invocation, not K.

```python
from concurrent.futures import ThreadPoolExecutor

def seed_taxonomy() -> int:
    store = get_store()
    store.seed_vector_index()
    nodes = load_cdao_taxonomy_nodes()
    with ThreadPoolExecutor(max_workers=8) as pool:
        vectors = list(pool.map(lambda n: embed(n.label), nodes))
    for node, vec in zip(nodes, vectors):
        store.upsert_taxonomy_node_with_embedding(node, vec)
    return len(nodes)
```

Note the new seam method `upsert_taxonomy_node_with_embedding(node, vec)` that skips the inline embed — used only by the batched seed path. The existing `upsert_taxonomy_node` (no vec arg) embeds inline for non-batch callers.

### 4.6 `scudo_mapping_mcp/scripts/reindex_taxonomy.py` — ~30 LoC

Location: new directory `scudo_mapping_mcp/scripts/` (does not exist today). Add `__init__.py`. Lives inside the package so imports work via `python -m scudo_mapping_mcp.scripts.reindex_taxonomy`.

Stand-alone script. Workflow:

1. Read `embedding_version` and `embedding_model_id` from `settings`.
2. Query all `TaxonomyNode` whose stored `embedding_version` differs from settings.
3. For each such node: re-embed via `embed(label)`, write back via `upsert_taxonomy_node`.
4. Print a summary: scanned / re-embedded / failed.

Run on:
- After any change to `SCUDO_EMBEDDING_VERSION`
- After any change to `SCUDO_EMBEDDING_MODEL_ID`
- After a taxonomy version bump
- On container start if `SCUDO_REINDEX_ON_BOOT=1`

Idempotent: nodes already at the current `embedding_version` are skipped.

### 4.7 `store/base.py` — declare seam additions

`RetrievalStore` ABC gains `seed_vector_index(self) -> None`. `FakeStore` implements as no-op (it never had a vector index). `NeptuneStore` raises `NotImplementedError` for now (Neptune side is stubbed; called out in plan §7).

## 5. PR-sized sequencing

Each is independently testable and reversible.

### PR 1 — config + embeddings module (no behaviour change)

- `config.py` settings additions
- `embeddings.py` new module
- New smoke gates:
  - EMBED_returns_dim_matches_settings_pin
  - EMBED_raises_on_dim_mismatch
  - EMBED_round_trips_same_text_to_same_vector_deterministically (mock Bedrock)

No store code changes, no behaviour change in the matcher.

### PR 2 — falkordb_store vector seam (behind a flag)

- `seed_vector_index` method
- `upsert_taxonomy_node` writes embeddings
- `find_similar_products` checks `settings.use_vector_dense` flag:
  - True → use vector k-NN
  - False → use Jaro-Winkler (today's path)
- Default flag: **False**. Lands code without changing default behaviour.
- New smoke gates:
  - VECTOR_index_is_idempotent_on_repeat_seed
  - VECTOR_upsert_writes_embedding_property_and_version
  - VECTOR_query_returns_hits_with_score_matching_cosine
  - VECTOR_version_mismatched_node_is_skipped (fail-closed)
  - VECTOR_overfetch_3x_compensates_for_post_filter_drops

### PR 3 — reindex script + boot policy

- `scripts/reindex_taxonomy.py`
- Document `SCUDO_REINDEX_ON_BOOT` env var
- Smoke gate:
  - REINDEX_skips_nodes_at_current_version (idempotency)
  - REINDEX_rewrites_nodes_at_drift_version
  - REINDEX_summary_reports_scanned_reindexed_failed_counts

### PR 4 — flip the flag

- `settings.use_vector_dense = True` as default
- Deploy to sandbox dev; sanity check on live FalkorDB
- Sets up the §4.2 recalibration event

## 6. Test strategy

### 6.1 Unit gates (no Bedrock calls)

- `embeddings.embed` mocked with a deterministic hash-based fake (text → fixed-dim vector via stable hash). **The hash-fake has no semantic relationship to similarity**: it can prove plumbing (dimension, round-trip, version-handling, fail-closed semantics) but CANNOT prove "similar text → similar vector". Anything testing the actual semantic property MUST be an integration test against real Titan (§6.2).
- FakeStore stays at `set_score` semantics — existing 86 smoke gates do not change.
- New gates total: ~10. Land in `tests/smoke.py` under `# DENSE ARM SWAP` block.

### 6.2 Integration gates (real Bedrock + real FalkorDB — NEW for SCUDO)

This is the gap [i5-lift-preconditions.md §4.4](i5-lift-preconditions.md) calls out. Adds:

- `tests/integration/test_dense_arm.py` (new dir): seed a small taxonomy, embed a query via real Titan, hit real FalkorDB, assert non-zero similarity scores; smoke that the same query returns the same node twice (cosine determinism).
- Gated by `SCUDO_INTEGRATION=1` so it doesn't run in normal CI but is callable in the sandbox.

### 6.3 Calibration sanity (separate)

Not part of this PR — but the script that runs after the swap:

- Pull N labelled mappings from the reviewer queue / precedent set.
- For each, run the **old** matcher and the **new** matcher.
- Report distribution of `(old_similarity, new_similarity)` and the rank-correlation between them.
- This is the prerequisite to recalibrating the floor.

## 7. Risk surface

| Risk | Mitigation | Residual |
|---|---|---|
| Embedding model API change (Titan v2 → v3) | `embedding_version` bump triggers reindex | None at protocol layer; recalibration burden remains |
| Dimension mismatch silently corrupts the index | Embed function raises on dim mismatch; store query filters version-mismatched hits | None |
| Bedrock outage during seed | Seed is idempotent; reindex script can be re-run | None |
| Bedrock outage during query | `find_similar_products` raises `EmbeddingError` — caller (matcher) routes the case to the reviewer queue, **never** auto-persists. I5 already provides this safety net | None for dev; for I5-lifted future, Path A's verifier fail-closed (§4.12 of I5 doc) covers it |
| Per-query Bedrock cost at scale | Vendor-product LRU (deferred follow-up) | Cost line until cache lands; quantify at scale-out |
| Calibration drift — the 0.80 floor is now wrong | Calibration is the explicit follow-up; PR 4 flag-flip is the trigger event | Until §4.2 closes, do not move toward I5 lift |
| FalkorDB vector index unavailability (older version) | Pinned FalkorDB image version in `ingestion/infra/scudo-dev-foundation.yaml` already supports `db.idx.vector.*` | None |
| Bands tuned for Jaro-Winkler are wrong for cosine — the borderline window is too wide or too narrow against the new score distribution | Calibration follow-up (§4.2 of I5 doc). Until then, the band may route fewer (or more) cases to the specialist than it should — wasting cost or missing reviews | Live until calibration closes |
| VendorProduct nodes in the graph carry no embeddings — re-querying same product re-invokes Bedrock | Per-query cache (deferred follow-up). Plan §10 q5 raises it | Cost line only |
| Empty taxonomy → seed_vector_index errors | Idempotent CREATE INDEX is safe on empty graph; embed-on-upsert is per-node so empty seed is a no-op | None |
| Neptune backend (stubbed today) doesn't gain this | `NeptuneStore.seed_vector_index` raises `NotImplementedError`; the matcher route never reaches Neptune in dev | Persists as a known gap; tracked separately |

## 8. Deploy plan

1. Land PR 1 + PR 2 + PR 3. Default flag stays False.
2. In sandbox, set `SCUDO_USE_VECTOR_DENSE=true` on the Match&Verify task def via a one-off update.
3. Run `scripts/reindex_taxonomy.py` once against the live FalkorDB.
4. Run the integration test against live Bedrock + FalkorDB.
5. Run the calibration sanity script — emit the old-vs-new similarity distribution to S3.
6. If distribution looks sane (rank correlation > 0.5 on labelled samples, no catastrophic re-rankings), land PR 4 (flip default).
7. **Do not progress toward I5 lift until [i5-lift-preconditions.md §4.2](i5-lift-preconditions.md) is closed.**

Rollback at any step is one env var flip back to `false` + redeploy.

## 9. What this triggers downstream

- **Calibration event ([i5-lift-preconditions.md §4.2](i5-lift-preconditions.md)).** Floor + band widths must be re-derived against a golden set. This is the most expensive downstream consequence and the one that gates I5 lift.
- **MRGR registration ([§4.10](i5-lift-preconditions.md)).** Bedrock Titan v2 becomes a registered model in the inventory. Model card, independent validation, drift suite, vendor-version pin.
- **Cost line.** Per-query Bedrock invoke. Titan v2 is $0.00002 / 1000 input tokens; a ~30-token vendor description costs ~$6×10⁻⁷ per query. At 1000 queries/day → ~$0.60/year. The cost is not material at any reasonable SCUDO volume; flagging only so it appears on the cost dashboard alongside the specialist LLM line and doesn't surprise anyone at audit.
- **Falkor / Neptune parity gap.** Neptune side stays stubbed; if the Neptune cutover happens before this, the swap has to land there separately. Capture in the Neptune store followup.

## 10. Open questions for review

1. Embed at seed time on `label` only, or `label + description` (when present)? Latter is more discriminating but the CDAO seed has sparse descriptions today.
2. Cosine similarity is the obvious default — but should we try euclidean / dot-product? Titan is trained for cosine; pinning is correct, but worth flagging that we don't experiment-test.
3. Over-fetch multiplier (3x) is a guess. Worth a smoke gate or live measurement.
4. `SCUDO_REINDEX_ON_BOOT=1` is convenient but lengthens cold-start. Default off, on for sandbox / smoke.
5. Should the embedding go on the **VendorProduct** node too, at upsert time? Today vendor products live in the graph (for rank-signal derivation), but their embedding isn't used because we re-embed at query time. Caching it on the node would let a re-query skip Bedrock — at the cost of a stale-on-vendor-update problem.
6. Tokeniser / preprocessing policy. Titan handles raw text fine; do we lowercase / strip punctuation before embedding? The BM25 tokeniser already does; consistency matters here.
7. Should the embedding's `embedding_version` be derived from a hash of `(model_id, dim, preprocessing_policy)` instead of a manual tag? Less foot-gun, more correct.

## 11. Document control

- v0.1 — initial draft for review.
