# MCP Matching Engine — Review & Agent Plan

**Scope of this review.** Will the three-MCP matching pipeline work under the proposed
operating model:

1. **Scudo's existing schema treated as the initial golden dataset** (seed ground truth),
2. **CDAO as the broadened scope** the matcher checks against,
3. **Normalisation of multi-source vendor data done at the Ingestion MCP end**.

No code is changed by this review. It is a plan of what an agent must consider, in order,
with file-level pointers so nothing here rots independently of the code.

---

## Verdict in one paragraph

**Architecturally yes — the seams were built for exactly this configuration — but three of
those seams are configuration-without-implementation, two parallel schema worlds coexist
with incompatible identity minting, and two concrete defects would corrupt identity or
bypass normalisation if a golden dataset were loaded today.** The three-seam contract in
`config.py` (`SCUDO_VENDOR_ADAPTERS` / `SCUDO_TAXONOMY_LOADER` / `SCUDO_PERSIST_TARGET`)
names the three swap points this operating model needs, and the M6 bundle + precedent
graph is the right substrate for a golden dataset. The work is reconciliation and
implementation, not redesign.

---

## How the engine actually works (baseline for the review)

- **Cost ladder** (`matching.py`): scope gate → precedent reuse → hybrid retrieval
  (Jaro-Winkler "dense" + BM25 lexical + RRF + negative-precedent drop + rank-signal
  tilt, all inside the store seam) → Opus specialist on the borderline band only →
  three-band gate against `confidence_floor=0.80 ± 0.05`.
- **Trust gradient**: Ingestion MCP (`ingestion_mcp.py`, read-only frame server, scope
  layer 1) → Match & Verify MCP (`match_verify_mcp.py`, runs the matcher, signs an HMAC
  verdict, scope layer 2) → Persistence MCP (`persistence_mcp.py`, sole writer, verifies
  the seal, scope layer 3, enforces I5: agent-driven `AUTO_MAPPED` never writes to canon).
- **Golden-data substrate**: confirmed `MAPPED_TO` precedent edges
  (`store/base.py: upsert_precedent`), exported/imported as a versioned `MappingBundle`
  (`models.py: MappingBundle`, `persist.import_bundle`) with a taxonomy fingerprint and
  skip-don't-fake semantics on unknown nodes.

These mechanics are sound and well-defended (validations outside the model, specialist
anchored to the candidate window with fail-closed off-list handling, confidence capped at
`min(dense, specialist)`). The findings below are about feeding this engine the proposed
data, not about the engine's decision logic.

---

## Question 1 — Scudo's existing schema as the initial golden dataset

### What already works

- `persist.import_bundle` is the designed golden-load path: idempotent, scope-gated,
  records a `taxonomy_version` fingerprint, and **skips** patterns whose
  `mapped_node_iri` is absent rather than fabricating nodes
  (`models.py: BundleImportSummary`).
- `upsert_precedent` derives rank signals from edges (no stored counters), so replaying
  a golden import cannot drift counts.
- `BundleProvenance` fields are optional, so golden records lacking M8 audit hashes
  import cleanly.

### Finding G1 — **Identity fork: two incompatible IRI mints** (blocking)

The matching package mints `mds_iri()` from a fixed custom UUID seed with slug rule
`"S&P Global" → "sandpglobal"` (`scudo_mapping_mcp/models.py:22-27`). The vendor
catalogue MCP mints `product_iri()` from a `NAMESPACE_URL`-derived seed with enum slugs
`"spglobal"` (`vendor_catalogue_mcp/contract.py:29-35`). **The same (vendor, product)
yields two different IRIs.** Any golden dataset minted under Scudo's existing scheme will
fail to join the matcher's identity space — precedent reuse (rung 2) and rank signals
will silently never fire. An agent must pick one canonical mint (or build an explicit
translation table) **before** any golden load; this breaks I8 (IRI stability) otherwise.

### Finding G2 — **Two parallel schema worlds, no translator** (blocking)

`backend/scudo/schemas.py` defines `MappingResult` with `Band` = high/medium/low,
`Evidence`, `BriefBundle`, `ontology_snapshot` pins. `backend/scudo_mapping_mcp/models.py`
defines a *different* `MappingResult` with band = pass/borderline/fail, `Validation`,
seal-oriented fields. "Treat Scudo's existing schema as golden" requires an explicit,
tested projection from the Scudo contracts (and whatever shape the existing Scudo
precedent data is in) into `MappingPattern` records. Today nothing translates between
the two packages — they share vocabulary, not types.

### Finding G3 — **Golden data tilts ordering; it does not raise automation** (expectation)

By design (I5, Section 10a caveat), precedents only:

- short-circuit **exact** `(vendor, product_id)` repeats (rung 2), and
- tilt candidate *ordering* for near-neighbours via the rank-signal boost, capped at
  +0.10 on the **sort key only** — `Candidate.similarity` stays the raw oracle score,
  so the 0.80 floor never sees the boost (`store/base.py: compute_rank_boost`).

So loading the golden dataset will **not** increase auto-map rates beyond exact replays.
If the business intent of the golden set is "more automation", the correct lever is
**calibration** (use the golden set as an offline eval corpus to validate/tune the floor
and band width — see Question 2), not the tilt. State this expectation up front.

### Finding G4 — Load-order and provenance discipline (procedural)

- Taxonomy **must** be loaded before the bundle import, or every golden pattern lands in
  `skipped_unknown_node`. Order: full CDAO taxonomy load → golden bundle import →
  assert `applied == total` (or explain every skip).
- Mark migrated records distinctly (e.g. `decided_by="scudo-golden-import"`,
  `decision="approve"`) so audit can distinguish migrated ground truth from native HITL.
- `mds_iri` lowercases vendor but **not** `product_id` — golden ingest must carry the
  vendor-native primary key verbatim (the `frames.py` M8 contract already pins this).

---

## Question 2 — CDAO as the broadened scope

### Finding C1 — **The taxonomy loader seam has no implementation** (blocking)

`SCUDO_TAXONOMY_LOADER=cdao` is validated in `config.py` but the only thing that ever
loads CDAO is the hard-coded 20-node demo list `_CDAO_SEED` (`ingest.py:29-51`),
re-seeded at MCP startup. There is no loader for the real CDAO ontology. The agent must
build one (presumably from the Neptune/RDF export the `backend/scudo` package targets),
emitting `TaxonomyNode` rows — and decide how `data_class` tags ride along (see C2).

### Finding C2 — **`data_class_match` is pass-by-default; broadened scope is unsafe until class tags land** (precondition, not follow-up)

The required validation passes whenever *either* side lacks a declared class
(`validations.py:131-146`), and no seed node carries one. At 20 nodes this is tolerable;
at full CDAO breadth, a 0.85 string-similarity label match **across asset classes**
auto-maps with all required validations green. The code comments call this "tightens
automatically once class tags land" — under broadened scope, treat class-tagged nodes
as a **precondition** of go-live, and add vendor-side class normalisation at ingest
(see Question 3, N3) so both sides of the truth table are actually populated.

### Finding C3 — **The dense arm is a string metric; the floor is uncalibrated** (calibration)

The "dense" arm is Jaro-Winkler over labels (admitted stand-in — `store/base.py`
Diagram-2 notes, README "What is NOT done"), and the 0.80/±0.05 thresholds have never
been validated against any golden set. At CDAO scale, label similarity will produce
confident-but-wrong candidates. The golden dataset from Question 1 is exactly the
missing calibration asset:

1. Build an offline harness: replay every golden `(vendor product → CDAO node)` pair
   through `find_similar_products` + the gate with precedent reuse disabled.
2. Measure precision@1, recall@N (does the true node make the top-8 specialist anchor
   window?), and auto-map false-positive rate as a function of threshold.
3. Tune `CONFIDENCE_FLOOR` / `BORDERLINE_HALF_WIDTH` from evidence; decide whether the
   real-embedding swap (M9 / `SCUDO_USE_OPUS_DENSE` path) is required before broadening.

Recall@8 matters specifically: the specialist is anchored to the surfaced candidate set
and fails closed on off-list picks (`matching.py:309-350`) — safe, but if the true node
rarely makes the window at CDAO breadth, every borderline case degrades to review queue
throughput.

### Finding C4 — Backend and versioning hazards (verify per environment)

- The Neptune `find_similar_products` is a placeholder returning `similarity=0.0`
  (README). Broadened scope on `STORE_BACKEND=neptune` silently routes everything to
  `NEEDS_REVIEW`. Verify retrieval parity per backend before cutover.
- `taxonomy_version` is a hash of the loaded node set; broadening CDAO changes it and
  affects bundle import diagnostics. Define a re-baseline procedure (export → load new
  taxonomy → re-import → reconcile skips).
- The HMAC seal binds node IRI, status, confidence, band — **not** the taxonomy
  snapshot. `backend/scudo`'s `BriefBundle.ontology_snapshot` shows the intended pin;
  consider carrying a taxonomy version into the sealed payload (v=3) so a verdict is
  traceable to the CDAO snapshot it was scored against.
- The deferred structural **distance check** (`matching.py:35-37`, "pending anchor")
  becomes implementable the moment golden precedents are loaded — they are the anchor
  the comment is waiting for.

---

## Question 3 — Normalisation at the Ingestion MCP end

### Finding N1 — **Normalisation does not currently happen at the Ingestion MCP** (placement gap)

Today there are three normalisation surfaces, none of them the Ingestion MCP itself:

| Surface | What it does | Consumed by matcher? |
|---|---|---|
| `ingest.ingest_bytes` (`ingest.py:91`) | Generic column-alias heuristic (`_COL_ALIASES`), called from the **Flask upload route** (`routes/mapping.py:595`) | Yes (mock mode) |
| Upstream S3 pipeline (M8 contract, `frames.py:15-34`) | Must write already-normalised `VendorProductRef` JSON; the MCP only **reads** | Yes (s3 mode) |
| `vendor_catalogue_mcp` (`contract.py: NormalisedProduct`) | Rich per-vendor contract: provenance, snapshot, identifiers, asset_class, schema drift | **No** — parallel world |

The Ingestion MCP docstring claims "adding Bloomberg means a new adapter, not a new
server", and `settings.vendor_adapters` is parsed and smoke-tested — but **no code
consumes it**; there is no adapter registry or dispatch. The intent and the seam
placement are right (tier 1 is where untrusted data should be canonicalised, behind the
layer-1 scope gate, with the write boundary already enforced by AST smoke gates). The
adapter layer itself must be built. So: *yes, normalisation can be done at the ingest
MCP end — it just isn't, yet.*

### Finding N2 — **`row-{i+1}` product_id synthesis violates the identity contract** (defect)

`ingest.py:78` synthesises `row-{i+1}` when no primary-key column is found. This
directly contradicts the M8 contract in `frames.py:28-30` ("the upstream pipeline
rejects rows where the primary-key field is null — it does NOT synthesise"). Synthesised
ids are row-order dependent: a re-upload re-forks every IRI, silently orphaning
precedents and rank signals. Under golden-dataset operation this is a data-corruption
vector. Adapters must reject-to-quarantine instead.

### Finding N3 — **The inline-frame path bypasses ingestion normalisation, and the seal doesn't catch it** (defect/risk)

`matchverify.verify_mapping` accepts inline `name`/`description` "to avoid a frame
lookup" (`match_verify_mcp.py:135-155`); the HMAC seal binds only
`(vendor::product_id, node, status, confidence, band, ts)` (`verdict.py` "WHAT'S
SEALED"). An agent can therefore feed arbitrary unnormalised (or adversarial) text into
the matcher for a real product_id, and the resulting sealed verdict is
indistinguishable from one scored against the ingested frame. I5 limits the blast
radius (it still can't write to canon), but it poisons the reviewer queue with
plausible-looking verdicts. If "normalisation is guaranteed at ingest" is to be a real
invariant, either bind a frame content hash (`source_content_hash`) into the sealed
payload, or restrict the inline path to dev.

### Finding N4 — Fields the matcher needs are buried or missing (schema work)

- `data_class` lives only in the untyped `raw` dict and is grepped out by alias
  (`validations.py:72-80`). Lift it (plus identifier sets — ISIN/RIC/ticker, which the
  BM25 arm already implicitly rewards) to first-class `VendorProductRef` fields with a
  controlled vocabulary mapped to CDAO classes. This is the vendor-side half of C2.
- `VendorProductRef` carries no vendor schema version / snapshot;
  `NormalisedProduct.Provenance` (source_snapshot, version) and `CatalogueSchema` drift
  detection already model this. Converge the two contracts — either extend
  `VendorProductRef` or have the Ingestion MCP serve a
  `NormalisedProduct → VendorProductRef` projection — rather than maintaining both.
- `FieldRule` transforms (`identity|trim|lower|upper`) are emitted as metadata on every
  result but nothing ever *applies* them. Decide: descriptive (audit-only, document it)
  or executable (adapters apply them at ingest). Today's half-state invites drift.
- The layer-1 scope gate checks vendor membership only; the ODRL entitlement lookup is
  a placeholder (`frames.py:234-238`). Broadened CDAO scope likely implies per-product /
  per-licence entitlements that a vendor-level allow-list cannot express.

---

## The plan — what an agent should do, in order

### Phase 0 — Decisions (cheap, unblock everything else)
1. **Canonical IRI mint**: `models.mds_iri` vs `vendor_catalogue_mcp.product_iri` —
   pick one, or commit to a translation table (G1).
2. **Canonical schema contract**: which package's types are authoritative, and what the
   Scudo-golden → `MappingPattern` projection looks like (G2).
3. **Where adapters live**: confirm the Ingestion MCP / upstream-pipeline split — who
   normalises in s3 mode, and whether `vendor_catalogue_mcp` is folded in or retired (N1).
4. **State the automation expectation**: golden data ⇒ exact-replay reuse + ordering
   tilt + calibration corpus; not lifted auto-map rates (G3).

### Phase 1 — Identity & schema reconciliation
5. Implement the chosen mint/translator with round-trip tests across both packages.
6. Write and test the Scudo-golden → `MappingBundle` exporter (provenance:
   `decided_by="scudo-golden-import"`).

### Phase 2 — Golden dataset load
7. Build the real CDAO `taxonomy_loader` (replacing the `_CDAO_SEED` demo) **with
   data_class tags** (C1, C2).
8. Load taxonomy, then import the golden bundle; assert `applied == total` and account
   for every skip (G4).

### Phase 3 — Calibration (uses the golden set)
9. Offline eval harness: precision@1, recall@8, auto-map FPR vs threshold (C3).
10. Tune `CONFIDENCE_FLOOR` / `BORDERLINE_HALF_WIDTH` from evidence; decide whether the
    dense-arm swap (M9 / Opus-dense) is a precondition for broadened scope.

### Phase 4 — Broadened CDAO scope
11. Gate go-live on class-tagged nodes so `data_class_match` actually bites (C2).
12. Implement the deferred structural distance check using golden precedents as the
    anchor (C4).
13. Verify retrieval parity on the production backend (Neptune placeholder) and define
    the taxonomy re-baseline procedure; consider sealing the taxonomy version (C4).

### Phase 5 — Ingest normalisation hardening
14. Build the adapter registry actually keyed by `settings.vendor_adapters`; per-vendor
    adapters replace the generic `_COL_ALIASES` heuristic (N1).
15. Remove `row-{i+1}` synthesis — reject-to-quarantine on missing primary key (N2).
16. Lift `data_class` + identifiers to first-class frame fields; add schema-version
    provenance; resolve the `FieldRule` descriptive-vs-executable question (N4).
17. Close the inline-frame bypass: bind frame `source_content_hash` into the sealed
    payload or restrict inline input to dev (N3).

### Invariants to preserve throughout (do not relitigate)
- **I3** scope is deterministic, fail-closed; the LLM never votes on entitlement.
- **I5** agent-driven `AUTO_MAPPED` never writes to canon; rank tilt never reaches the floor.
- **I6** validations stay outside the model.
- **I8** IRI stability — every phase-1/5 change must be replay-safe for existing edges.
- The AST-enforced write boundary (`TRUST_*` smoke gates): Ingestion and Match & Verify
  import no writers; Persistence remains the only writer.

### What does NOT need to change
- The cost-ladder decision logic, the three-band gate, the specialist anchoring and
  fail-closed off-list handling, the min-cap confidence discipline, the HMAC
  seal/verify protocol (modulo the v=3 payload additions above), the store seam's
  bounded-retrieval clamps, and the bundle format. These are the parts of the engine
  this review found sound.
