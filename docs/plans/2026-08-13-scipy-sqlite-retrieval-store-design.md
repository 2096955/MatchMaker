# SciPy + SQLite Retrieval Store Design

**Status:** Approved for implementation, 2026-08-13

## Goal

Provide a FalkorDB-free `STORE_BACKEND=scipy_sqlite` that implements the
complete SCUDO `RetrievalStore` contract for local, Citrix, demo, test, and
single-host deployments.

SQLite is the durable source of truth. SciPy is a disposable in-process sparse
index. The design does not present SQLite as safe shared state for independent
ECS tasks or Lambda execution environments.

## Invariants

- `Candidate.similarity` remains the raw dense score.
- BM25, RRF, precedent boosts, and graph evidence affect ordering or
  explanation only.
- Automatic publication still requires confidence `>= 0.80`.
- Vendor-derived identities and signatures lowercase the vendor.
- There is at most one current positive precedent per vendor product.
- Provisional precedents are excluded from reuse, rank signals, and bundle
  export.
- Writes are durable before they become visible to readers.
- Readers see one immutable taxonomy revision, never mixed database/index
  revisions.
- SQLite and the console SQLite fallback use separate files and schemas.

## Runtime architecture

```text
SQLite matching database
  ├── taxonomy nodes and typed hierarchy edges
  ├── vendor products
  ├── positive and negative precedents
  ├── conceptual nodes and edges
  └── schema and taxonomy revision metadata
          │
          ▼
immutable TaxonomySnapshot
  ├── sorted IRI/node maps
  ├── class/concept SciPy CSR indexes
  ├── property SciPy CSR indexes
  ├── canonical dense/BM25 documents
  └── derived parent/child/component data
```

Every graph-dependent read captures one immutable snapshot. Taxonomy commits
increment a durable revision and publish a completed replacement snapshot only
after SQLite commits.

## Store surface

`ScipySQLiteStore` implements all sixteen `RetrievalStore` methods:

- lifecycle: health and close;
- taxonomy and vendor-product writes;
- candidate retrieval;
- taxonomy node and neighbourhood reads;
- positive, provisional, and negative precedents;
- derived rank signals;
- confirmed-precedent bundle export;
- conceptual node, edge, and graph operations.

Candidate scoring reuses the existing Jaro-Winkler/Opus + BM25 + RRF
composition. The SciPy index does not introduce a new similarity score.

## SQLite behavior

The store uses native `sqlite3`, not the console PostgreSQL-to-SQLite adapter.

Connection settings:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA synchronous = FULL;
```

Writes use short `BEGIN IMMEDIATE` transactions. Connections are
operation-local and are not shared unsafely among request threads.

The schema uses forward-only migrations and includes:

- store metadata and taxonomy revision;
- taxonomy nodes, alternate labels, superclass and superproperty edges;
- vendor products;
- positive and negative precedents with provenance;
- conceptual nodes and edges.

Rank signals are derived from confirmed precedent rows rather than counters.

## Taxonomy revisions and snapshots

The store supports an atomic bulk taxonomy replacement operation. Startup
seeding uses one replacement transaction instead of exposing partially seeded
node-by-node revisions.

An immutable snapshot contains the complete taxonomy and is not subject to the
agent-facing analyzer's 100-node output limit. Agent-facing graph operations
remain bounded to their existing request/output limits.

Snapshot construction validates:

- unique IRIs;
- typed class/concept versus property relations;
- missing references;
- self-loops and cycles;
- deterministic edge and node ordering.

Invalid topology aborts the SQLite transaction and does not replace the current
snapshot.

## Concurrency and restart

- A process lock serializes migrations.
- SQLite serializes writers.
- A build lock makes snapshot rebuilds single-flight.
- Snapshot publication swaps one immutable reference.
- Separate processes on one host detect taxonomy changes through the durable
  revision number and rebuild.
- WAL recovery restores the last committed state after a crash.
- The database must live on reliable local storage, not NFS/EFS/SMB.

## Configuration

```text
STORE_BACKEND=scipy_sqlite
SCUDO_PERSIST_TARGET=scipy_sqlite
SCUDO_SCIPY_SQLITE_PATH=/durable/local/path/scudo_matching.sqlite3
```

The default local path is `backend/.local/scudo_matching.sqlite3`.

## Cutover boundary

Phase 1 switches local/Citrix/single-host entrypoints to `scipy_sqlite`.
Existing AWS multi-service deployments remain on FalkorDB until a shared
Aurora `RetrievalStore` is implemented and shadow parity is proven.

Phase 2 will use Aurora PostgreSQL as shared authoritative matching state,
S3 for immutable taxonomy snapshots, and SciPy as a per-process index. That is
a separate deployment project.

## End-to-end acceptance

The phase is accepted only when one real temporary SQLite database proves:

1. schema migration and taxonomy seed;
2. vendor ingestion;
3. deterministic candidate matching;
4. graph neighbourhood and advisory evidence;
5. approve, override, and reject behavior;
6. restart persistence and exact precedent reuse;
7. rank-signal and negative-precedent effects;
8. conceptual graph round-trip;
9. bundle export/import parity;
10. two store instances observing a committed taxonomy revision;
11. no change to the 0.80/0.70 confidence contract.

## Deployment limits

This backend is production-shaped but single-host. Do not point multiple ECS
tasks or Lambda instances at independent SQLite files and call them shared
state. Do not mount the current implementation on EFS without a separate,
validated locking and recovery design.
