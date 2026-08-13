# SciPy + SQLite Retrieval Store Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a complete single-host `STORE_BACKEND=scipy_sqlite` that replaces FalkorDB for local/Citrix use while preserving matching, HITL, bundle, conceptual-graph, and confidence semantics.

**Architecture:** Native SQLite persists the complete `RetrievalStore` state. Immutable revision-stamped SciPy snapshots accelerate typed taxonomy operations and are rebuilt after committed taxonomy changes. Candidate ranking reuses the existing deterministic scoring composition.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, SciPy sparse, NumPy, Pydantic 2, pytest.

---

### Task 1: Native schema and connection layer

**Files:**
- Create: `backend/scudo_mapping_mcp/store/scipy_sqlite_schema.py`
- Create: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_schema.py`

**Steps:**
1. Write failing tests for migration idempotency, WAL, foreign keys, schema checksum/version, one-positive precedent constraint, and separate DB paths.
2. Run the focused tests and confirm failure.
3. Implement operation-local connections, explicit transactions, forward migrations, and integrity checks.
4. Re-run focused tests.

### Task 2: Immutable full-taxonomy SciPy snapshots

**Files:**
- Create: `backend/scudo_mapping_mcp/store/taxonomy_snapshot.py`
- Create: `backend/scudo_mapping_mcp/store/snapshot_manager.py`
- Create: `backend/scudo_mapping_mcp/tests/test_taxonomy_snapshot.py`

**Steps:**
1. Write failing tests for deterministic indexing, typed CSR separation, immutable arrays/maps, large taxonomies, topology rejection, and revision publication.
2. Implement complete uncapped internal indexes separate from bounded agent output.
3. Implement single-flight rebuild and atomic immutable publication.
4. Test external revision detection using two managers.

### Task 3: Complete SQLite RetrievalStore

**Files:**
- Create: `backend/scudo_mapping_mcp/store/scipy_sqlite_store.py`
- Create: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_store.py`
- Modify: `backend/scudo_mapping_mcp/store/base.py`
- Modify: `backend/scudo_mapping_mcp/ingest.py`

**Steps:**
1. Write a contract test matrix for all sixteen methods.
2. Add atomic `replace_taxonomy` to the seam and startup seed path.
3. Implement taxonomy, vendor, precedent, rank, conceptual, and bundle surfaces.
4. Implement bounded deterministic neighbourhood traversal from one snapshot.
5. Verify restart persistence and two-instance revision refresh.

### Task 4: Shared scoring composition

**Files:**
- Create: `backend/scudo_mapping_mcp/store/retrieval_scoring.py`
- Modify: `backend/scudo_mapping_mcp/store/memory_store.py`
- Modify: `backend/scudo_mapping_mcp/store/scipy_sqlite_store.py`
- Test: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py`

**Steps:**
1. Pin current MemoryStore outputs on representative fixtures.
2. Extract the scoring composition without changing output.
3. Run the same fixtures through SQLite and MemoryStore.
4. Assert exact candidate ordering and raw similarity parity.

### Task 5: Factory, config, bridge, and startup integration

**Files:**
- Modify: `backend/scudo_mapping_mcp/config.py`
- Modify: `backend/scudo_mapping_mcp/store/factory.py`
- Modify: `backend/scudo/matcher_bridge.py`
- Modify: `backend/scudo/seed_falkordb.py`
- Modify: `backend/routes/mapping.py`
- Modify: `backend/scudo_mapping_mcp/mcp_server.py`
- Modify: `backend/scudo_mapping_mcp/match_verify_mcp.py`
- Modify: `backend/scudo_mapping_mcp/persistence_mcp.py`

**Steps:**
1. Add failing selection and lifecycle tests.
2. Add `scipy_sqlite` configuration and lazy construction.
3. Remove Falkor-only restrictions from generic matching paths.
4. Generalize seed diagnostics while retaining compatibility commands.
5. Gate readiness on a valid current snapshot.

### Task 6: Local/Citrix entrypoints

**Files:**
- Modify: `start_local.py`
- Modify: `backend/run_local.py`
- Modify: `streamlit_app.py`
- Modify: `README.md`
- Modify: `docs/LOCAL_RUN.md`
- Modify: `CITRIX_STREAMLIT_HANDOVER.md`

**Steps:**
1. Add startup tests proving environment is set before package import.
2. Prefer `scipy_sqlite`, then `local_file`, then `memory`.
3. Keep console and matching SQLite files separate.
4. Document the single-host boundary and restart behavior.

### Task 7: Real end-to-end lifecycle

**Files:**
- Create: `backend/scudo_mapping_mcp/tests/test_scipy_sqlite_e2e.py`

**Steps:**
1. Use one real temporary SQLite file.
2. Seed the illustrative taxonomy.
3. Ingest multiple vendor products.
4. Match and capture candidates/confidence.
5. Approve one target and reject another.
6. Close and recreate the store.
7. Prove exact precedent reuse and negative exclusion.
8. Exercise graph neighbourhood and conceptual graph.
9. Export a bundle, import into a second database, and compare.
10. Assert 0.80/0.70 band parity.

### Task 8: Independent review and complete verification

**Steps:**
1. Run schema, snapshot, store-contract, scoring, factory, bridge, and E2E suites.
2. Run existing matching-store invariants and smoke tests.
3. Run Streamlit/local startup smoke where dependencies permit.
4. Run lints, compileall, and `git diff --check`.
5. Run independent specification and code-quality reviews.
6. Resolve every actionable finding before completion.

## Explicitly out of scope

- Switching multi-service ECS or Lambda deployments to SQLite.
- Removing FalkorDB infrastructure from AWS templates.
- Implementing the later Aurora matching `RetrievalStore`.
- Feeding SciPy graph evidence into confidence.
- Adding a GNN.
