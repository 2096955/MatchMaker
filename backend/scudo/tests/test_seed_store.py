from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading

from scudo.seed_falkordb import seed
from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore


def test_legacy_seed_command_creates_scipy_sqlite_taxonomy(monkeypatch, tmp_path):
    database = tmp_path / "matching.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "STORE_BACKEND": "scipy_sqlite",
            "SCUDO_PERSIST_TARGET": "scipy_sqlite",
            "SCUDO_SCIPY_SQLITE_PATH": str(database),
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "scudo.seed_falkordb"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert database.is_file()

    from scudo_mapping_mcp.store.scipy_sqlite_store import ScipySQLiteStore

    store = ScipySQLiteStore(database)
    try:
        assert (
            store.get_taxonomy_node("jpmorgan:data:cdao:domain:market-data") is not None
        )
    finally:
        store.close()


def test_seed_replaces_taxonomy_in_one_revision(tmp_path):
    database = tmp_path / "matching.sqlite3"
    store = ScipySQLiteStore(database)
    try:
        before = store._read_taxonomy_revision()
        count = seed(
            [
                {"iri": "node:1", "label": "One"},
                {"iri": "node:2", "label": "Two", "parent_iri": "node:1"},
            ],
            store,
        )
        assert count == 2
        assert store._read_taxonomy_revision() == before + 1
    finally:
        store.close()


def test_concurrent_reader_never_observes_partial_seed(tmp_path, monkeypatch):
    database = tmp_path / "matching.sqlite3"
    writer = ScipySQLiteStore(database)
    reader = ScipySQLiteStore(database)
    entered = threading.Event()
    release = threading.Event()
    original = writer._write_taxonomy_node
    calls = 0

    def blocked_write(conn, node):
        nonlocal calls
        original(conn, node)
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(writer, "_write_taxonomy_node", blocked_write)
    thread = threading.Thread(
        target=lambda: seed(
            [
                {"iri": "node:1", "label": "One"},
                {"iri": "node:2", "label": "Two", "parent_iri": "node:1"},
            ],
            writer,
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    with sqlite3.connect(database) as conn:
        during = conn.execute("SELECT COUNT(*) FROM taxonomy_nodes").fetchone()[0]
    release.set()
    thread.join(timeout=5)
    with sqlite3.connect(database) as conn:
        after = conn.execute("SELECT COUNT(*) FROM taxonomy_nodes").fetchone()[0]
    writer.close()
    reader.close()

    assert during == 0
    assert after == 2
