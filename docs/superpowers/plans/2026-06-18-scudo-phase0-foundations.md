# SCUDO Phase 0 — Foundations & Safety: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the three safe, fully-grounded foundations from the SCUDO production roadmap — retire the dead Strands skeleton, close the export→S3→hydrate cycle, and make the orchestrator's Gate-2 thresholds config-driven — without changing any decision behavior.

**Architecture:** SCUDO maps vendor products → CDAO taxonomy nodes through a deterministic cost-ladder matcher (`scudo_mapping_mcp/matching.py`) plus a Bedrock-wired orchestrator/verifier (`scudo/orchestrator.py`). Phase 0 hardens the seams these already expose; it adds no new infra and flips no defaults. Bigger moves (single-conductor consolidation, OpenSearch/SciPy arms, Aurora-canonical, DQ, JAPI ingestion) are deferred to their own plans (see *Downstream Roadmap*).

**Tech Stack:** Python 3.12, Pydantic, boto3 (lazy), FalkorDB/Neptune/in-memory store seam, standalone smoke runners + pytest.

---

## Context — why this change

The gap analysis (`docs/superpowers/2026-06-16-scudo-architecture-gap-analysis.md`) found SCUDO's data plane real/demo-ready but production-incomplete. Two target diagrams ("Matching Engine design proposal", "Matching engine drill-down") sharpened the end state. A read-only recon of the actual code (5 Explore agents) then corrected several assumptions in the roadmap's draft "Phase 0":

- **The WS-D specialist-anchored invariant is ALREADY SHIPPED.** `matching.py:309-350` fails closed to `NEEDS_REVIEW` with `invariant_violation="specialist_off_list"` and caps confidence below floor when the specialist returns an off-list pick. It is covered by `TRUST_specialist_anchored_to_top_candidate` in `scudo_mapping_mcp/tests/smoke.py`. **No work — verify-only.**
- **`map_vendor_product` already accepts `specialist=`** (an `Optional[SpecialistScorer]`, `matching.py:130-135`), consulted only in the borderline band.
- **Confidence floor + borderline half-width are already env-overridable** via `Settings.from_env()` (`config.py:138-203`). The *orchestrator's* Gate-2 thresholds are NOT — they're module constants (`scudo/orchestrator.py:38-40`). That asymmetry is the real gap.
- **The S3 *write* path genuinely does not exist.** `bundle.export_bundle()` returns an in-memory object; `hydrate()` reads from S3; nothing writes. The cycle is open.
- **`scudo_strands_app.py` has zero references repo-wide** (verified by grep) — a 7×`NotImplementedError` blueprint safe to delete.
- **The "70-79 review" Gate-1 band from the new diagram does NOT exist** and does not map onto the current PASS/BORDERLINE/FAIL model; adding it needs a new band variant + a ~160-line refactor. **Deferred to P1 (recalibration)** — not P0.

Intended outcome: a clean, reversible foundation that the later phases build on, with every existing smoke gate still green.

---

## Files touched in Phase 0

- **Delete:** `scudo_strands_app.py` (repo root) — dead skeleton.
- **Modify:** `backend/scudo_mapping_mcp/hydrate.py` — add `export_to_s3()` (the writer; reuses `_s3_client()` + `_bundle_s3_location()`).
- **Modify:** `backend/scudo_mapping_mcp/persistence_mcp.py` — add a new opt-in read-write MCP tool `persist.publish_bundle` (leaves the existing read-only `persist.export_bundle` untouched).
- **Modify:** `backend/scudo/orchestrator.py` — `__init__` gains keyword Gate-2 threshold params (defaulting to today's constants); `_gate_and_decide` reads instance attrs.
- **Test:** `backend/scudo_mapping_mcp/tests/smoke.py` (S3 round-trip case), `backend/scudo/tests/smoke.py` (config-override case).

Verified anchors: `matching.py:130-135,246-249,275-436`; `config.py:44,49,138-203`; `orchestrator.py:38-40,55-78,151-152,215-260,319`; `hydrate.py:46-60,89,92-125,161-247`; `bundle.py:113-185`; `persistence_mcp.py:315-330`.

---

## Task 1: Retire the dead `scudo_strands_app.py` skeleton

**Files:**
- Delete: `scudo_strands_app.py` (repo root)

This is a deletion of verified dead code, so the regression gate is the existing smoke suites, not a new test.

- [ ] **Step 1: Re-confirm zero references (do not trust the plan)**

Run:
```bash
cd /Users/anthonylui/MatchMaker/MatchMaker && grep -rn "scudo_strands_app" --include="*.py" . ; echo "exit=$?"
```
Expected: no matches (grep exit=1). If ANY match appears, STOP — the file is not dead; do not delete it, report the reference instead.

- [ ] **Step 2: Capture the current green baseline**

Run (from `backend/`):
```bash
cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo_mapping_mcp.tests.smoke && python -m scudo.tests.smoke && pytest scudo_mapping_mcp/tests/test_invariants.py -q
```
Expected: all three suites pass. Record the pass counts.

- [ ] **Step 3: Delete the skeleton**

Run:
```bash
cd /Users/anthonylui/MatchMaker/MatchMaker && git rm scudo_strands_app.py
```

- [ ] **Step 4: Re-run the suites — identical pass counts**

Run the same command as Step 2. Expected: identical pass counts (nothing imported it, so nothing changes).

- [ ] **Step 5: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker && git add -A && git commit -m "chore(scudo): retire dead scudo_strands_app.py skeleton (zero refs)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Close the export→S3→hydrate cycle (S3 write path)

**Files:**
- Modify: `backend/scudo_mapping_mcp/hydrate.py` (add `export_to_s3`)
- Modify: `backend/scudo_mapping_mcp/persistence_mcp.py` (add `persist.publish_bundle` tool)
- Test: `backend/scudo_mapping_mcp/tests/smoke.py`

The writer lives in `hydrate.py` (not `bundle.py`) because `bundle.py` is imported by `hydrate.py` — putting the writer in `bundle.py` would create a circular import. `hydrate.py` already owns `_s3_client()` (lines 56-60) and `_bundle_s3_location()` (lines 92-125), so the writer reuses them and writes to the exact location the reader reads.

- [ ] **Step 1: Write the failing test (S3 round-trip)**

Add to `backend/scudo_mapping_mcp/tests/smoke.py`, modeled on the existing `M6_round_trip_reproduces_confirmed_mappings_in_fresh_env` case (~line 596) and the `HYDRATE_happy_path_applies_bundle_patterns` case (~line 2041). Reuse `_fresh_store()`, `_swap_settings`/`_restore_settings`, and `hydrate._set_s3_client_for_test`:

```python
@case("M6_export_to_s3_round_trips_through_hydrate")
def _():
    import io
    import scudo_mapping_mcp.hydrate as hydrate_mod
    from scudo_mapping_mcp.bundle import export_bundle

    class _FakeS3:
        def __init__(self):
            self.store = {}
        def put_object(self, *, Bucket, Key, Body, **kw):
            self.store[(Bucket, Key)] = Body
            return {}
        def get_object(self, *, Bucket, Key):
            if (Bucket, Key) not in self.store:
                raise KeyError("NoSuchKey")
            return {"Body": io.BytesIO(self.store[(Bucket, Key)])}

    # Populate a store with one confirmed precedent, then export it.
    fake = _fresh_store()
    ref = VendorProductRef(vendor=IN_SCOPE_VENDOR, product_id="EQ-1",
                           name="Equity Prices")
    node = TaxonomyNode(iri=EQ_PRICES_IRI, label="Equity Prices")
    fake.upsert_precedent(ref=ref, node=node, decision="approve",
                          decided_by="tester", confidence=0.95,
                          provisional=False, decided_at_ms=0)

    s3 = _FakeS3()
    hydrate_mod._set_s3_client_for_test(s3)
    saved = _swap_settings(s3_bucket="test-bucket")
    try:
        bundle = export_bundle(source_env="mock-local",
                               created_at="2026-01-01T00:00:00Z")
        bucket, key = hydrate_mod.export_to_s3(bundle)
        assert (bucket, key) in s3.store, "export_to_s3 wrote nothing to S3"

        # Fresh store + hydrate from the same fake S3 replays the precedent.
        _fresh_store()
        hydrate_mod._set_s3_client_for_test(s3)
        result = hydrate_mod.hydrate(strict=True)
        assert result.applied == 1, f"expected 1 applied, got {result.applied}"
    finally:
        _restore_settings(saved)
        hydrate_mod._set_s3_client_for_test(None)
```

(If `upsert_precedent`'s kwargs differ in the FakeStore, copy the exact seeding call used by `M6_export_bundle_carries_confirmed_precedent_with_rank` at ~line 538.)

- [ ] **Step 2: Run it — verify it fails**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo_mapping_mcp.tests.smoke`
Expected: FAIL — `AttributeError: module 'scudo_mapping_mcp.hydrate' has no attribute 'export_to_s3'`.

- [ ] **Step 3: Implement `export_to_s3` in `hydrate.py`**

Add after `_bundle_s3_location()` (after line 125). It mirrors the reader's resolution exactly:

```python
def export_to_s3(bundle: MappingBundle, *, uri: Optional[str] = None) -> tuple[str, str]:
    """Persist the canonical bundle JSON to the S3 location ``hydrate()`` reads.

    Closes the export->hydrate cycle: ``bundle.export_bundle()`` builds the
    snapshot, this writes it to ``s3://<bucket>/<key>``, and the next boot's
    ``hydrate()`` replays it. Resolution matches ``_bundle_s3_location`` so a
    writer and reader pointed at the same env round-trip.

    Args:
        bundle: the MappingBundle to persist.
        uri: optional ``s3://bucket/key`` override; defaults to the canonical
            location resolved exactly like the reader.

    Returns:
        (bucket, key) actually written.
    """
    if uri:
        if not uri.startswith("s3://"):
            raise HydrationError(f"export_to_s3: uri must be s3://… got {uri!r}")
        bucket, _, key = uri[len("s3://"):].partition("/")
        if not bucket or not key:
            raise HydrationError(f"export_to_s3: malformed uri {uri!r}")
    else:
        bucket, key = _bundle_s3_location()

    body = bundle.model_dump_json().encode("utf-8")
    _s3_client().put_object(
        Bucket=bucket, Key=key, Body=body, ContentType="application/json",
    )
    _log.info("export_to_s3: wrote canonical bundle to s3://%s/%s (%d bytes)",
              bucket, key, len(body))
    return bucket, key
```

- [ ] **Step 4: Run it — verify it passes**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo_mapping_mcp.tests.smoke`
Expected: PASS, including the new case.

- [ ] **Step 5: Wire the opt-in MCP tool in `persistence_mcp.py`**

Leave the existing read-only `persist.export_bundle` (lines 315-330) untouched. Add a new read-write tool below it. `_RW` should mirror the annotation style of `_RO` already in the file (a read-write equivalent — if no `_RW` exists, declare `_RW = {"readOnlyHint": False}` near `_RO`):

```python
@mcp.tool(
    name="persist.publish_bundle",
    annotations={"title": "Export and publish the M6 bundle to canonical S3", **_RW},
)
async def publish_bundle_tool(params: BundleExportInput) -> str:
    """Build the confirmed-precedent bundle AND write it to the canonical S3
    key that hydrate() reads at boot. Read-write: the only write side of the
    export->hydrate cycle. Bucket/key resolve exactly like hydration."""
    bundle = export_bundle(
        source_env=params.source_env,
        created_at=params.created_at,
    )
    from .hydrate import export_to_s3
    bucket, key = export_to_s3(bundle)
    return json.dumps({"bucket": bucket, "key": key,
                       "patterns": len(bundle.patterns)})
```

- [ ] **Step 6: Smoke the package suite again + commit**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo_mapping_mcp.tests.smoke`
Expected: PASS.

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker && git add -A && git commit -m "feat(scudo): S3 export path closes the export->hydrate cycle

Adds hydrate.export_to_s3 (writer mirrors the reader's location) and an
opt-in persist.publish_bundle MCP tool. Read-only persist.export_bundle
is unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Make the orchestrator's Gate-2 thresholds config-driven (behavior-preserving)

**Files:**
- Modify: `backend/scudo/orchestrator.py:55-78` (`__init__`) and `:215-260` (`_gate_and_decide`)
- Test: `backend/scudo/tests/smoke.py`

The module constants `VERIFIER_AUTOPUBLISH=16`, `VERIFIER_RETRY_LO,HI=12,15`, `CONFIDENCE_FLOOR=0.80` (lines 38-40) stay as the **defaults** (so `__all__` at line 319 and `cloudshell_demo.py`'s local copies are unaffected). They become injectable per-instance. No values change → no behavior change at defaults.

- [ ] **Step 1: Write the failing test (threshold override changes the outcome)**

Add to `backend/scudo/tests/smoke.py`. Reuse `FakeAgent`, `make_bundle_assembler`, `_verifier_with_total`, the `InMemory*Queue` stubs, and the mapping responder + intake payload from the existing PUBLISHED-path scenario (copy that scenario's `mapping` responder and `orch.run({...})` payload verbatim — only the two changes below differ):

```python
def scenario_gate2_thresholds_are_instance_config(mcp_client):
    """Raising verifier_retry_hi to 17 turns a total-16 result — normally
    PUBLISHED — into a RETRY, proving Gate-2 thresholds are per-instance."""
    rubric = "v1"
    # mapping responder: copy from the existing PUBLISHED scenario (confidence
    # >= floor so the floor never trips); verifier total fixed at 16.
    mapping = FakeAgent(structured_responder=_publish_path_mapping_responder)
    verifier = FakeAgent(
        structured_responder=lambda cls, p: _verifier_with_total(16, rubric))
    orch = Orchestrator(
        mapping_specialist=mapping,
        rights_specialist=None,
        verifier=verifier,
        hitl_queue=InMemoryHitlQueue(),
        research_queue=InMemoryResearchQueue(),
        publish_sink=InMemoryPublishSink(),
        ontology_snapshot="ont-1",
        rubric_version=rubric,
        bundle_assembler=make_bundle_assembler(mcp_client),
        verifier_retry_hi=17,   # NEW knob: 16 now lands inside the retry band
    )
    obj = orch.run(_publish_path_intake_payload())
    assert obj.outcome is Outcome.RETRY, f"expected RETRY, got {obj.outcome}"
```

Register the scenario in this file's runner list the same way the existing scenarios are registered. (`_publish_path_mapping_responder` / `_publish_path_intake_payload` are stand-ins for the inline responder + payload of the current PUBLISHED scenario — lift them directly.)

- [ ] **Step 2: Run it — verify it fails**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo.tests.smoke`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'verifier_retry_hi'`.

- [ ] **Step 3: Add the threshold params to `__init__`**

In `Orchestrator.__init__` (after `bundle_assembler=None,` on line 66), add keyword params defaulting to the module constants, and store them (after line 78):

```python
        verifier_autopublish: int = VERIFIER_AUTOPUBLISH,
        verifier_retry_lo: int = VERIFIER_RETRY_LO,
        verifier_retry_hi: int = VERIFIER_RETRY_HI,
        confidence_floor: float = CONFIDENCE_FLOOR,
    ) -> None:
        ...
        self.verifier_autopublish = verifier_autopublish
        self.verifier_retry_lo = verifier_retry_lo
        self.verifier_retry_hi = verifier_retry_hi
        self.confidence_floor = confidence_floor
```

- [ ] **Step 4: Point `_gate_and_decide` at the instance attrs**

In `_gate_and_decide` (lines 227, 235), replace the module constants with instance attributes:

```python
        if total < self.verifier_retry_lo or conf < self.confidence_floor or result.requires_human_review:
            ...
        if self.verifier_retry_lo <= total <= self.verifier_retry_hi:
            ...
```

Leave the module-level constants (lines 38-40) and `__all__` (line 319) in place as the defaults.

- [ ] **Step 5: Run the new test + the full orchestrator suite**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker/backend && python -m scudo.tests.smoke`
Expected: PASS — the new scenario passes AND every existing scenario (which uses defaults) is unchanged.

- [ ] **Step 6: Commit**

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker && git add -A && git commit -m "refactor(scudo): orchestrator Gate-2 thresholds are per-instance config

VERIFIER_AUTOPUBLISH/RETRY_LO/RETRY_HI/CONFIDENCE_FLOOR become __init__
kwargs defaulting to the existing module constants. No behavior change at
defaults; enables P1 golden-set recalibration.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 0 verification (run all, expect green)

From `backend/`:
```bash
python -m scudo_mapping_mcp.tests.smoke        # cost ladder, bands, M6/hydrate, new S3 round-trip
python -m scudo.tests.smoke                     # orchestrator routing/gate, new threshold-config case
pytest scudo_mapping_mcp/tests/test_invariants.py -q   # Section-13 invariants
```
Note: `scudo.tests.smoke` reaches the catalogue MCP over stdio — it must be runnable in the env. The Flask API suite (`backend/tests/run_all_tests.py`) needs MySQL and is **not** touched by Phase 0.

Exit criteria: all three suites green; `scudo_strands_app.py` gone; an export round-trips through S3 into a fresh store via `hydrate`; orchestrator thresholds overridable with defaults unchanged.

---

## Phase 0.5 — Single-conductor consolidation (its own spec + plan)

The roadmap's "make `scudo/orchestrator.py` the single conductor" (have `_call_mapping` delegate to `matching.map_vendor_product` instead of an LLM-structured call) is a **production-path change** to the Bedrock-wired orchestrator and needs its own brainstorm→spec→plan. Open questions to resolve first:
- `BriefBundle` → `VendorProductRef` extraction (the orchestrator path and the matcher path use **different** `MappingResult` types — `scudo/schemas.py` vs `scudo_mapping_mcp/models.py`; an adapter is required).
- Wrap the Bedrock specialist as a `SpecialistScorer` (`matching.py:124-127`) so the cost ladder consults it only in the borderline band.
- Default stays on the current path; flip to deterministic delegation only **after** P1 golden-set recalibration (behind a flag, e.g. `SCUDO_ORCH_DETERMINISTIC_MAPPING`).
- Today `routes/mapping.py:323` already calls `map_vendor_product` directly and `agent.py:351` wraps it — those are the reference for the target shape.

---

## Downstream Roadmap (each phase gets its own spec + plan + ExitPlanMode)

**What STAYS (mostly all of it):** the cost-ladder matcher logic, the deterministic orchestrator + routing/gate/verifier, the HMAC-sealed 10-dim verifier, the Bedrock specialist (M9), the self-improving loop (precedent + `rank_signals_for` + bundle/`hydrate`), the reviewer UI / NGINX / FastAPI gateway, the iFusion publish seam, ingestion readers + ETL, the three-seam `config.py` contract.

**P1 — Matching engine to target.** OpenSearch (lexical + k-NN) + SciPy/NetworkX arms; Titan query-vector generation (S3 vector store, WS-E IAM); build-time taxonomy index; **add the Gate-1 "70-79 review" band** (new band variant; extract the ~160-line band logic in `matching.py:275-436` to a helper first) and **recalibrate** floor/bands against a golden set. ⚠️ Decision: OpenSearch+SciPy replace/augment the current JW+BM25+RRF on FalkorDB — confirm FalkorDB's role (cache tier vs retired).

**P2 — Canonical = Aurora + transactional outbox (revises M7).** Aurora source of truth (canonical mappings + CDAO + lineage); atomic publish on Gate-1≥auto / verifier-pass; outbox (EventBridge/SQS) projecting async to Neptune (RDF/ODRL) + OpenSearch; precedent-hydrator reframed as the outbox projector. ⚠️ Decision: Aurora-canonical vs the M7 docs' Neptune-canonical — adopt and update M7.

**P3 — DQ Framework (from Aurora).** Ingestion-time + mapping-time DQ metrics; optional `DATA_QUALITY` verifier dimension; `DQNotificationSink` → SNS/SES.

**P4 — Ingestion cutover.** Per-vendor JAPI clients (read-only, paginated) replacing the catalogue mock; single trust boundary (gateway + presigned-URL direct-to-S3 after auth/scan); `FRAME_SOURCE=s3` prod path.

**P5 — Fusion SPI V2 + iFusion.** Real SPI V2 client + `SpiV2PublishSink`; Fusion JSON envelope; inbound callback consumer + durable store. *(Gated on JPMC SPI V2 spec.)*

**P6 — Hardening, memory plane, observability.** WS-C JAPI client (pool + circuit breaker); AgentCore Memory plane formalizing the self-improving loop; Bedrock Evaluations (traces/tokens/context-rot); DynamoDB reviewer queue; seal hardening (I5 §4.3); EventBridge scheduler + nightly self-improving routine; stateless-Lambda A/B/C packaging; I5-lift shadow-mode (MRGR-gated).

**External blockers (flag, don't wait silently):** vendor JAPI/Glue contracts (P4); JPMC SPI V2 spec (P5); Bedrock Titan IAM + model access (P1); MRGR sign-off (P6).

---

## Self-review

- **Spec coverage:** Phase 0 implements the three roadmap P0 items that are real, code-only, and unblocked; the WS-D item is dropped (already shipped — verified); the Gate-1 band item is correctly relocated to P1 (it's not behavior-preserving); the single-conductor item is carved into its own plan (it changes the production path).
- **Placeholder scan:** test code is concrete; the two reused helpers (`_publish_path_mapping_responder`, `_publish_path_intake_payload`) are explicitly identified as lift-from-existing-scenario, not invent-new.
- **Type consistency:** `export_to_s3(bundle, *, uri=None) -> tuple[str,str]` used consistently; orchestrator attrs (`self.verifier_retry_lo/hi`, `self.confidence_floor`) match their `__init__` params and the constants they default from.
