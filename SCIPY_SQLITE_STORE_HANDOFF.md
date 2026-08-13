# SCUDO SciPy + SQLite RetrievalStore handoff

**Written:** 2026-08-13  
**Repository:** MatchMaker (paths below are repo-relative)  
**Status:** implemented and verified locally; uncommitted; not deployed to AWS

## Result

`STORE_BACKEND=scipy_sqlite` is now a complete FalkorDB-free implementation of
the SCUDO `RetrievalStore` contract for local, Citrix, test, demo, and
single-host deployments.

SQLite persists the matching state. SciPy supplies immutable, revision-stamped,
typed sparse taxonomy indexes.

This backend is not approved as shared state across separate ECS tasks or
Lambda execution environments.

## Configuration

```bash
export STORE_BACKEND=scipy_sqlite
export SCUDO_PERSIST_TARGET=scipy_sqlite
export SCUDO_SCIPY_SQLITE_PATH=/absolute/durable/path/scudo_matching.sqlite3
```

If the path is unset or blank, local entrypoints use:

```text
backend/.local/scudo_matching.sqlite3
```

The console database remains separate:

```text
backend/.local/console.sqlite3
```

## What is implemented

- Complete sixteen-method `RetrievalStore`.
- Native SQLite schema and forward migrations.
- WAL, foreign keys, bounded busy retries, and synchronous durable writes.
- Owner-only database directory and files.
- Symlink refusal.
- Atomic taxonomy replacement and revisions.
- Immutable typed SciPy CSR indexes.
- Revision refresh across two store instances.
- Full taxonomy fields and typed relations.
- Vendor products and provenance.
- Positive, provisional, and negative precedents.
- One-current-positive constraint.
- Rank signals derived from confirmed precedents.
- Soft retirement preserving decision audit.
- Active-target checks inside decision transactions.
- Conceptual node and edge persistence.
- Bundle export/import compatibility.
- Jaro-Winkler/Opus + BM25 + RRF scoring parity.
- Bounded Opus nomination.
- Raw dense `Candidate.similarity`.
- Flask, MCP, Streamlit, and local-launcher integration.
- Fresh-database bootstrap and live readiness checks.

## Runtime files

- [SQLite schema and connection layer](backend/scudo_mapping_mcp/store/scipy_sqlite_schema.py)
- [Complete SQLite RetrievalStore](backend/scudo_mapping_mcp/store/scipy_sqlite_store.py)
- [Immutable taxonomy snapshots](backend/scudo_mapping_mcp/store/taxonomy_snapshot.py)
- [Revision-aware snapshot manager](backend/scudo_mapping_mcp/store/snapshot_manager.py)
- [Shared scoring implementation](backend/scudo_mapping_mcp/store/retrieval_scoring.py)
- [RetrievalStore contract](backend/scudo_mapping_mcp/store/base.py)
- [Store factory](backend/scudo_mapping_mcp/store/factory.py)
- [Store configuration](backend/scudo_mapping_mcp/config.py)
- [Taxonomy ingestion and atomic seed](backend/scudo_mapping_mcp/ingest.py)
- [Generic matcher bridge](backend/scudo/matcher_bridge.py)
- [Compatible retrieval-store seeder](backend/scudo/seed_falkordb.py)

## Application integration

- [Flask mapping routes and readiness](backend/routes/mapping.py)
- [Mapping MCP lifecycle](backend/scudo_mapping_mcp/mcp_server.py)
- [Match & Verify MCP lifecycle](backend/scudo_mapping_mcp/match_verify_mcp.py)
- [Persistence MCP lifecycle](backend/scudo_mapping_mcp/persistence_mcp.py)
- [Lambda bridge gating](backend/scudo/lambda_handler.py)
- [Primary local launcher](start_local.py)
- [Backend local launcher](backend/run_local.py)
- [Streamlit application](streamlit_app.py)
- [Starter shell](start_all.sh)

## Tests

- [Schema and migration tests](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_schema.py)
- [Complete store-contract tests](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_store.py)
- [Snapshot and concurrency tests](backend/scudo_mapping_mcp/tests/test_taxonomy_snapshot.py)
- [Independent scoring golden tests](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py)
- [Real lifecycle and Flask E2E](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_e2e.py)
- [Integration/configuration tests](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_integration.py)
- [Streamlit fresh-bootstrap test](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_integration.py)
  (`test_streamlit_fresh_database_bootstraps_in_subprocess`)
- [Readiness tests](backend/tests/test_readiness.py)
- [Matcher bridge tests](backend/scudo/tests/test_falkordb_bridge.py)
- [Seeder tests](backend/scudo/tests/test_seed_store.py)

## Design and plan

- [Approved design](docs/plans/2026-08-13-scipy-sqlite-retrieval-store-design.md)
- [Implementation plan](docs/plans/2026-08-13-scipy-sqlite-retrieval-store.md)

## Documentation updated

- [Root README](README.md)
- [Local run guide](docs/LOCAL_RUN.md)
- [Citrix Streamlit handoff](CITRIX_STREAMLIT_HANDOVER.md)
- [JPMC deployment-agent handoff](JPMC_DEPLOYMENT_AGENT_HANDOVER.md)

## End-to-end behavior proven

The real E2E uses environment-selected production factory paths and blocks
network access. It proves:

1. fresh schema initialization;
2. atomic taxonomy seed;
3. vendor ingestion;
4. pass, borderline, and fail bands at `0.80/0.70`;
5. specialist invocation only for borderline;
6. approve, reject, and override;
7. negative filtering and rank signals;
8. process restart;
9. exact precedent reuse;
10. soft retirement without deleting decision audit;
11. reactivation restoring precedent reuse;
12. graph neighbourhood and score-neutral SciPy evidence;
13. full conceptual graph round-trip;
14. bundle export/import and idempotency;
15. two-instance revision refresh;
16. invalid-topology rollback;
17. file permissions;
18. Flask API readiness, ingest, similar, and map routes;
19. no FalkorDB package or network dependency.

## Fresh verification

From:

```bash
cd <repo-root>/backend
```

Complete mapping suite:

```bash
PYTHONPATH=. /opt/homebrew/bin/python3.11 -m pytest \
  scudo_mapping_mcp/tests/ -q
```

Result:

```text
569 passed
```

Focused lifecycle/readiness suite:

```bash
PYTHONPATH=. /opt/homebrew/bin/python3.11 -m pytest \
  scudo_mapping_mcp/tests/test_scipy_sqlite_e2e.py \
  scudo_mapping_mcp/tests/test_scipy_sqlite_schema.py \
  scudo_mapping_mcp/tests/test_scipy_sqlite_store.py \
  scudo_mapping_mcp/tests/test_taxonomy_snapshot.py \
  scudo_mapping_mcp/tests/test_scipy_sqlite_scoring_parity.py \
  scudo_mapping_mcp/tests/test_scipy_sqlite_integration.py \
  tests/test_readiness.py -q
```

Result:

```text
131 passed
```

`test_scipy_sqlite_integration.py` covers the `SCUDO_SCIPY_SQLITE_PATH`
plumbing a deployer actually sets, so it belongs in the focused suite. Without
it the same command reports `98 passed` — an earlier revision of this document
claimed `107` for that six-file form, which was wrong.

Whole-tree baseline, measured against a clean `HEAD` worktree with the same
command: **zero new failures**. The pre-existing failures are unrelated
(`test_provenance.py` Marketing ×2, plus `test_datasets.py` / `test_providers.py`
/ `tests/e2e/`, which need a live console DB and a browser). Seven
`tests/test_ingest_*.py` files fail to *collect* at `HEAD` too
(`ModuleNotFoundError: _ingest_helpers`).

Counts quoted elsewhere (`585`, `151`, `127`) came from separate reviewers with
unstated invocations and could not be reproduced here; prefer the commands
above, and always quote a count together with the command that produced it.

Compilation and diff whitespace validation passed. The remaining test warning is
an unrelated rdflib deprecation.

## Safe deployment scope

Safe:

- Streamlit on one Citrix desktop.
- Flask on one host/process group sharing one local disk.
- Local MCP services only when every process sees the same reliable local
  filesystem and SQLite locking semantics.
- Tests, demos, development, and single-host packaged applications.

Not safe:

- separate ECS task filesystems;
- Lambda `/tmp`;
- independent replicas with separate DB files;
- EFS/NFS/SMB without a separately validated SQLite locking/recovery design.

## AWS state

AWS templates were intentionally not switched. They continue to set
`STORE_BACKEND=falkordb`.

Lambda does not auto-enable SciPy/SQLite merely from `STORE_BACKEND`. An
explicit neutral retrieval flag and pre-seeded, healthy, nonempty store are
required. This prevents accidental use of task-local empty SQLite state.

The later all-service FalkorDB removal requires an Aurora-backed
`RetrievalStore`, S3 taxonomy snapshots, and shadow parity. That remains a
separate deployment phase.

## Data migration

Use the existing bundle export/import path for confirmed precedents. Negative
precedents and other mutable store state are not fully represented by the
canonical bundle and require a dedicated migration/export if moving from a
live FalkorDB deployment.

Do not delete FalkorDB until:

- a complete state export is defined;
- shadow candidate and decision parity passes;
- rollback is tested;
- the later shared Aurora store is deployed.

## Worktree warning

This repository still contains unrelated modified and untracked files. Diff and
stage the files linked above narrowly. Do not commit the entire worktree as one
feature.
