"""Native SQLite schema and operation-local connection helpers."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

_MIGRATION_1 = (
    """CREATE TABLE store_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""",
    """CREATE TABLE taxonomy_nodes (
    iri TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    definition TEXT NOT NULL DEFAULT '',
    node_kind TEXT NOT NULL,
    parent_iri TEXT,
    business_concept TEXT,
    asset_class TEXT,
    super_asset_class TEXT,
    temporal_coverage TEXT,
    payload_json TEXT NOT NULL
)""",
    """CREATE TABLE taxonomy_edges (
    from_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE CASCADE,
    to_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE CASCADE,
    edge_kind TEXT NOT NULL,
    PRIMARY KEY (from_iri, to_iri, edge_kind)
)""",
    """CREATE TABLE vendor_products (
    vendor TEXT NOT NULL,
    product_id TEXT NOT NULL,
    iri TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL,
    vendor_signature TEXT NOT NULL,
    source_content_hash TEXT,
    source_file_audit_id TEXT,
    temporal_coverage TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vendor, product_id)
)""",
    """CREATE TABLE positive_precedents (
    vendor TEXT NOT NULL,
    product_id TEXT NOT NULL,
    node_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'override')),
    decided_by TEXT NOT NULL,
    decided_at_ms INTEGER NOT NULL,
    confidence REAL NOT NULL,
    provisional INTEGER NOT NULL CHECK (provisional IN (0, 1)),
    source_content_hash TEXT,
    source_file_audit_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (vendor, product_id),
    FOREIGN KEY (vendor, product_id)
        REFERENCES vendor_products(vendor, product_id) ON DELETE CASCADE
)""",
    """CREATE TABLE negative_precedents (
    vendor TEXT NOT NULL,
    product_id TEXT NOT NULL,
    node_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE CASCADE,
    decided_by TEXT NOT NULL,
    decided_at_ms INTEGER NOT NULL,
    confidence REAL NOT NULL,
    source_content_hash TEXT,
    source_file_audit_id TEXT,
    PRIMARY KEY (vendor, product_id, node_iri),
    FOREIGN KEY (vendor, product_id)
        REFERENCES vendor_products(vendor, product_id) ON DELETE CASCADE
)""",
    """CREATE TABLE conceptual_nodes (
    iri TEXT PRIMARY KEY,
    concept_iri TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    payload_json TEXT NOT NULL
)""",
    """CREATE TABLE conceptual_edges (
    from_iri TEXT NOT NULL REFERENCES conceptual_nodes(iri) ON DELETE CASCADE,
    to_iri TEXT NOT NULL REFERENCES conceptual_nodes(iri) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY (from_iri, to_iri, kind)
)""",
    """CREATE INDEX idx_taxonomy_edges_from
    ON taxonomy_edges(from_iri, edge_kind, to_iri)""",
    """CREATE INDEX idx_taxonomy_edges_to
    ON taxonomy_edges(to_iri, edge_kind, from_iri)""",
    """CREATE INDEX idx_vendor_signature
    ON vendor_products(vendor_signature)""",
    """CREATE INDEX idx_conceptual_nodes_concept
    ON conceptual_nodes(concept_iri, iri)""",
)

_MIGRATION_2 = (
    "ALTER TABLE taxonomy_nodes ADD COLUMN active INTEGER NOT NULL DEFAULT 1 "
    "CHECK (active IN (0, 1))",
    "ALTER TABLE taxonomy_nodes ADD COLUMN last_seen_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE positive_precedents RENAME TO positive_precedents_v1",
    """CREATE TABLE positive_precedents (
    vendor TEXT NOT NULL,
    product_id TEXT NOT NULL,
    node_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'override')),
    decided_by TEXT NOT NULL,
    decided_at_ms INTEGER NOT NULL,
    confidence REAL NOT NULL,
    provisional INTEGER NOT NULL CHECK (provisional IN (0, 1)),
    source_content_hash TEXT,
    source_file_audit_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (vendor, product_id),
    FOREIGN KEY (vendor, product_id)
        REFERENCES vendor_products(vendor, product_id) ON DELETE CASCADE
)""",
    """INSERT INTO positive_precedents
    SELECT vendor, product_id, node_iri, decision, decided_by, decided_at_ms,
           confidence, provisional, source_content_hash, source_file_audit_id,
           description
    FROM positive_precedents_v1""",
    "DROP TABLE positive_precedents_v1",
    "ALTER TABLE negative_precedents RENAME TO negative_precedents_v1",
    """CREATE TABLE negative_precedents (
    vendor TEXT NOT NULL,
    product_id TEXT NOT NULL,
    node_iri TEXT NOT NULL REFERENCES taxonomy_nodes(iri) ON DELETE RESTRICT,
    decided_by TEXT NOT NULL,
    decided_at_ms INTEGER NOT NULL,
    confidence REAL NOT NULL,
    source_content_hash TEXT,
    source_file_audit_id TEXT,
    PRIMARY KEY (vendor, product_id, node_iri),
    FOREIGN KEY (vendor, product_id)
        REFERENCES vendor_products(vendor, product_id) ON DELETE CASCADE
)""",
    """INSERT INTO negative_precedents
    SELECT vendor, product_id, node_iri, decided_by, decided_at_ms, confidence,
           source_content_hash, source_file_audit_id
    FROM negative_precedents_v1""",
    "DROP TABLE negative_precedents_v1",
    "CREATE INDEX idx_taxonomy_nodes_active ON taxonomy_nodes(active, iri)",
)

_SCHEMA_CHECKSUMS = {
    1: hashlib.sha256("\n;\n".join(_MIGRATION_1).encode("utf-8")).hexdigest(),
    2: hashlib.sha256(
        "\n;\n".join((*_MIGRATION_1, *_MIGRATION_2)).encode("utf-8")
    ).hexdigest(),
}
SCHEMA_CHECKSUM = _SCHEMA_CHECKSUMS[SCHEMA_VERSION]
_MIGRATIONS = {1: _MIGRATION_1, 2: _MIGRATION_2}

_REQUIRED_COLUMNS = {
    "store_metadata": {"key", "value"},
    "taxonomy_nodes": {
        "iri",
        "label",
        "definition",
        "node_kind",
        "parent_iri",
        "business_concept",
        "asset_class",
        "super_asset_class",
        "temporal_coverage",
        "payload_json",
        "active",
        "last_seen_revision",
    },
    "taxonomy_edges": {"from_iri", "to_iri", "edge_kind"},
    "vendor_products": {
        "vendor",
        "product_id",
        "iri",
        "name",
        "description",
        "raw_json",
        "vendor_signature",
        "source_content_hash",
        "source_file_audit_id",
        "temporal_coverage",
        "payload_json",
    },
    "positive_precedents": {
        "vendor",
        "product_id",
        "node_iri",
        "decision",
        "decided_by",
        "decided_at_ms",
        "confidence",
        "provisional",
        "source_content_hash",
        "source_file_audit_id",
        "description",
    },
    "negative_precedents": {
        "vendor",
        "product_id",
        "node_iri",
        "decided_by",
        "decided_at_ms",
        "confidence",
        "source_content_hash",
        "source_file_audit_id",
    },
    "conceptual_nodes": {"iri", "concept_iri", "kind", "label", "payload_json"},
    "conceptual_edges": {
        "from_iri",
        "to_iri",
        "kind",
        "label",
        "payload_json",
    },
}
_REQUIRED_INDEXES = {
    "idx_taxonomy_edges_from",
    "idx_taxonomy_edges_to",
    "idx_vendor_signature",
    "idx_conceptual_nodes_concept",
    "idx_taxonomy_nodes_active",
}
_INITIALIZE_LOCKS: dict[Path, threading.Lock] = {}
_INITIALIZE_LOCKS_GUARD = threading.Lock()
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_DELAY_SECONDS = 0.05


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _execute_with_lock_retry(
    conn: sqlite3.Connection,
    statement: str,
) -> sqlite3.Cursor:
    for attempt in range(_LOCK_RETRY_ATTEMPTS):
        try:
            return conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc) or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
    raise AssertionError("unreachable SQLite retry state")


def _initialize_lock(path: Path) -> threading.Lock:
    key = path.resolve()
    with _INITIALIZE_LOCKS_GUARD:
        return _INITIALIZE_LOCKS.setdefault(key, threading.Lock())


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing SQLite database symlink: {path}")


def _harden_owner_only(path: Path) -> None:
    if not path.exists():
        return
    _reject_symlink(path)
    current = stat.S_IMODE(path.stat().st_mode)
    owner_only = current & 0o600
    if owner_only != current:
        path.chmod(owner_only)


def _harden_sqlite_files(path: Path) -> None:
    _harden_owner_only(path)
    for suffix in ("-wal", "-shm"):
        _harden_owner_only(Path(f"{path}{suffix}"))


def connect(path: str | Path) -> sqlite3.Connection:
    """Open one configured connection; callers own and close it."""
    db_path = Path(path)
    _reject_symlink(db_path)
    conn = sqlite3.connect(
        str(db_path),
        timeout=5.0,
        isolation_level=None,
        check_same_thread=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    _execute_with_lock_retry(conn, "PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    _harden_sqlite_files(db_path)
    return conn


@contextmanager
def read_connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Yield and always close one configured read connection."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Run a short immediate transaction with rollback on failure."""
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def schema_is_valid(conn: sqlite3.Connection) -> bool:
    """Validate required tables, columns, indexes, and metadata."""
    objects = {
        (row["type"], row["name"])
        for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')"
        )
    }
    if any(("table", table) not in objects for table in _REQUIRED_COLUMNS):
        return False
    if any(("index", index) not in objects for index in _REQUIRED_INDEXES):
        return False
    for table, required in _REQUIRED_COLUMNS.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not required <= columns:
            return False
    metadata = dict(conn.execute("SELECT key, value FROM store_metadata"))
    if metadata.get("schema_version") != str(SCHEMA_VERSION):
        return False
    if metadata.get("schema_checksum") != SCHEMA_CHECKSUM:
        return False
    if "taxonomy_revision" not in metadata:
        return False
    if metadata.get("taxonomy_building_revision"):
        return False
    return True


def initialize_database(path: str | Path) -> None:
    """Create or forward-migrate a matching database."""
    db_path = Path(path)
    _reject_symlink(db_path)
    parent_created = not db_path.parent.exists()
    db_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if parent_created:
        db_path.parent.chmod(0o700)
    with _initialize_lock(db_path):
        _initialize_database_locked(db_path)
    _harden_sqlite_files(db_path)


def _initialize_database_locked(db_path: Path) -> None:
    """Migrate under the process lock and SQLite's cross-process lock."""

    conn = connect(db_path)
    try:
        _execute_with_lock_retry(conn, "BEGIN EXCLUSIVE")
        objects = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not objects:
            current = 0
        elif "store_metadata" not in objects:
            raise RuntimeError("missing schema metadata table")
        else:
            metadata = dict(conn.execute("SELECT key, value FROM store_metadata"))
            if "schema_version" not in metadata:
                raise RuntimeError("missing schema_version metadata")
            if "schema_checksum" not in metadata:
                raise RuntimeError("missing schema_checksum metadata")
            current = int(metadata["schema_version"])
            expected_checksum = _SCHEMA_CHECKSUMS.get(current)
            if expected_checksum and metadata["schema_checksum"] != expected_checksum:
                raise RuntimeError("database schema checksum does not match this build")
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than supported "
                f"schema {SCHEMA_VERSION}"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in _MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO store_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("schema_version", str(version)),
            )
            conn.execute(
                "INSERT INTO store_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("schema_checksum", _SCHEMA_CHECKSUMS[version]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO store_metadata(key, value) "
                "VALUES ('taxonomy_revision', '0')"
            )
        if not schema_is_valid(conn):
            raise RuntimeError("required schema objects or metadata are invalid")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
