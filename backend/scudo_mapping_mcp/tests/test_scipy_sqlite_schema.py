from __future__ import annotations

import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore
from scudo_mapping_mcp.store.scipy_sqlite_schema import (
    SCHEMA_CHECKSUM,
    SCHEMA_VERSION,
    connect,
    initialize_database,
    schema_is_valid,
)


def test_migration_is_idempotent_and_configures_connections(tmp_path):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    initialize_database(path)

    with connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        metadata = dict(conn.execute("SELECT key, value FROM store_metadata"))

    assert metadata["schema_version"] == str(SCHEMA_VERSION)
    assert metadata["schema_checksum"] == SCHEMA_CHECKSUM


def test_database_files_are_owner_only_under_permissive_umask(tmp_path):
    parent = tmp_path / "private" / "matching"
    path = parent / "matching.sqlite3"
    previous_umask = os.umask(0o022)
    try:
        initialize_database(path)
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO store_metadata(key, value) VALUES ('permission', 'test')"
            )
            sidecars = [Path(f"{path}-wal"), Path(f"{path}-shm")]
            assert all(sidecar.exists() for sidecar in sidecars)
            assert all(
                stat.S_IMODE(sidecar.stat().st_mode) == 0o600 for sidecar in sidecars
            )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    initialize_database(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_database_permissions_are_hardened_on_restart(tmp_path):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    path.chmod(0o664)

    initialize_database(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_database_symlink_is_rejected_without_touching_target(tmp_path):
    target = tmp_path / "target.sqlite3"
    target.write_text("do not clobber", encoding="utf-8")
    path = tmp_path / "matching.sqlite3"
    path.symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink"):
        initialize_database(path)

    assert target.read_text(encoding="utf-8") == "do not clobber"


@pytest.mark.parametrize("constructor_count", [8, 20])
def test_concurrent_initial_store_constructors_are_unready_but_valid(
    tmp_path, constructor_count
):
    path = tmp_path / "new-matching.sqlite3"

    with ThreadPoolExecutor(max_workers=constructor_count) as pool:
        stores = list(
            pool.map(
                lambda _index: ScipySQLiteStore(path),
                range(constructor_count),
            )
        )

    assert len(stores) == constructor_count
    assert all(not store.health() for store in stores)
    with connect(path) as conn:
        assert schema_is_valid(conn)
    for store in stores:
        store.close()


def test_positive_precedent_is_unique_per_vendor_product(tmp_path):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO vendor_products "
            "(vendor, product_id, iri, name, description, raw_json, "
            "vendor_signature, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("lseg", "p1", "mds.lseg:p1", "P1", "", "{}", "lseg::p1", "{}"),
        )
        for iri in ("cdao:a", "cdao:b"):
            conn.execute(
                "INSERT INTO taxonomy_nodes "
                "(iri, label, node_kind, payload_json) VALUES (?, ?, ?, ?)",
                (iri, iri, "concept", "{}"),
            )
        conn.execute(
            "INSERT INTO positive_precedents "
            "(vendor, product_id, node_iri, decision, decided_by, decided_at_ms, "
            "confidence, provisional) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("lseg", "p1", "cdao:a", "approve", "u", 1, 0.9, 0),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO positive_precedents "
                "(vendor, product_id, node_iri, decision, decided_by, "
                "decided_at_ms, confidence, provisional) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("lseg", "p1", "cdao:b", "override", "u", 2, 0.8, 0),
            )


def test_matching_database_paths_are_independent(tmp_path):
    first = tmp_path / "one.sqlite3"
    second = tmp_path / "two.sqlite3"
    initialize_database(first)
    initialize_database(second)
    with connect(first) as conn:
        conn.execute("INSERT INTO store_metadata(key, value) VALUES ('marker', 'one')")
    with connect(second) as conn:
        assert (
            conn.execute(
                "SELECT value FROM store_metadata WHERE key='marker'"
            ).fetchone()
            is None
        )


def test_schema_checksum_mismatch_fails_closed(tmp_path):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            "UPDATE store_metadata SET value='tampered' WHERE key='schema_checksum'"
        )

    with pytest.raises(RuntimeError, match="checksum"):
        initialize_database(path)


def test_version_one_database_migrates_without_losing_precedents(tmp_path):
    from scudo_mapping_mcp.store import scipy_sqlite_schema as schema

    path = tmp_path / "matching.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in schema._MIGRATION_1:
            conn.execute(statement)
        conn.executemany(
            "INSERT INTO store_metadata(key, value) VALUES (?, ?)",
            (
                ("schema_version", "1"),
                ("schema_checksum", schema._SCHEMA_CHECKSUMS[1]),
                ("taxonomy_revision", "1"),
            ),
        )
        conn.execute(
            "INSERT INTO taxonomy_nodes "
            "(iri, label, node_kind, payload_json) VALUES (?, ?, ?, ?)",
            ("cdao:eq", "Equity Prices", "concept", "{}"),
        )
        conn.execute(
            "INSERT INTO vendor_products "
            "(vendor, product_id, iri, vendor_signature, payload_json, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("lseg", "p1", "mds.lseg:p1", "lseg::p1", "{}", "{}"),
        )
        conn.execute(
            "INSERT INTO positive_precedents "
            "(vendor, product_id, node_iri, decision, decided_by, decided_at_ms, "
            "confidence, provisional) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("lseg", "p1", "cdao:eq", "approve", "u", 1, 0.9, 0),
        )
        conn.execute(
            "INSERT INTO negative_precedents "
            "(vendor, product_id, node_iri, decided_by, decided_at_ms, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("lseg", "p1", "cdao:eq", "u", 2, 0.0),
        )

    initialize_database(path)

    with connect(path) as conn:
        metadata = dict(conn.execute("SELECT key, value FROM store_metadata"))
        node = conn.execute(
            "SELECT active, last_seen_revision FROM taxonomy_nodes WHERE iri='cdao:eq'"
        ).fetchone()
        positive = conn.execute("SELECT COUNT(*) FROM positive_precedents").fetchone()[
            0
        ]
        negative = conn.execute("SELECT COUNT(*) FROM negative_precedents").fetchone()[
            0
        ]
    assert metadata["schema_version"] == "2"
    assert metadata["schema_checksum"] == SCHEMA_CHECKSUM
    assert tuple(node) == (1, 0)
    assert (positive, negative) == (1, 1)


def test_migration_partial_ddl_rolls_back_atomically(tmp_path, monkeypatch):
    path = tmp_path / "matching.sqlite3"
    from scudo_mapping_mcp.store import scipy_sqlite_schema as schema

    monkeypatch.setattr(
        schema,
        "_MIGRATIONS",
        {1: ("CREATE TABLE partial_marker(value TEXT)", "INVALID SQL")},
    )
    with pytest.raises(sqlite3.DatabaseError):
        initialize_database(path)

    with connect(path) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "partial_marker" not in names
    assert "store_metadata" not in names


@pytest.mark.parametrize("metadata_key", ["schema_version", "schema_checksum"])
def test_missing_current_schema_metadata_fails_closed(tmp_path, metadata_key):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute("DELETE FROM store_metadata WHERE key=?", (metadata_key,))

    with pytest.raises(RuntimeError, match=metadata_key):
        initialize_database(path)


def test_schema_validation_detects_missing_required_object(tmp_path):
    path = tmp_path / "matching.sqlite3"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute("DROP INDEX idx_vendor_signature")
        assert not schema_is_valid(conn)
    with pytest.raises(RuntimeError, match="required schema"):
        initialize_database(path)
