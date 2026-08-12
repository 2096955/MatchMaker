"""JPMC-LOCAL: the no-Docker SQLite stand-in for the console DB.

Pins the two properties that matter:
  1. With ``CONSOLE_DB_BACKEND=sqlite`` the DB-backed routes work with no
     PostgreSQL and no Docker.
  2. WITHOUT it, nothing changes — the psycopg path is untouched, so a
     deployed run cannot accidentally land on SQLite.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import db_sqlite_fallback as fallback  # noqa: E402


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CONSOLE_DB_SQLITE_PATH", str(tmp_path / "console.sqlite3"))
    return tmp_path / "console.sqlite3"


# ── selection is opt-in and read at call time ──────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_BACKEND", raising=False)
    assert fallback.enabled() is False
    assert db._sqlite_enabled() is False


def test_enabled_only_by_the_explicit_value(monkeypatch):
    for value, expected in [
        ("sqlite", True),
        ("SQLite", True),
        ("postgres", False),
        ("", False),
        ("1", False),
    ]:
        monkeypatch.setenv("CONSOLE_DB_BACKEND", value)
        assert db._sqlite_enabled() is expected, value


def test_selection_is_read_at_call_time_not_import(monkeypatch):
    """Import order must not bake in the answer — a test (or start_local.py)
    setting the var after import must still take effect."""
    monkeypatch.delenv("CONSOLE_DB_BACKEND", raising=False)
    assert db._sqlite_enabled() is False
    monkeypatch.setenv("CONSOLE_DB_BACKEND", "sqlite")
    assert db._sqlite_enabled() is True


# ── SQL translation ────────────────────────────────────────────────────────


def test_placeholders_translate():
    assert fallback.translate_params("SELECT * FROM t WHERE a=%s") == (
        "SELECT * FROM t WHERE a=?"
    )


def test_literal_percent_is_not_mangled():
    """``%%`` is psycopg's escaped percent. A LIKE pattern must survive."""
    out = fallback.translate_params("SELECT * FROM t WHERE n LIKE '%%foo%%'")
    assert out == "SELECT * FROM t WHERE n LIKE '%foo%'"
    assert "?" not in out


def test_ddl_types_translate():
    out = fallback.translate_ddl(
        "CREATE TABLE console.x (id SERIAL, big BIGSERIAL, t TIMESTAMPTZ, j JSONB)"
    )
    assert "SERIAL" not in out.upper()
    assert "TIMESTAMPTZ" not in out.upper()
    assert "JSONB" not in out.upper()
    assert "console." not in out


# ── schema bootstrap + round trip ──────────────────────────────────────────


def test_schema_bootstraps_the_console_tables(sqlite_db):
    applied = fallback.ensure_schema()
    assert applied > 0, "no DDL applied — init_db.sql not found or all skipped"

    con = fallback.connect()
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        con.close()
    names = {r["name"] for r in rows}
    for expected in ("tp_provider", "tp_dataset", "etl_run_log", "users", "roles"):
        assert expected in names, f"{expected} missing from {sorted(names)}"


def test_bootstrap_is_idempotent(sqlite_db):
    assert fallback.ensure_schema() > 0
    assert fallback.ensure_schema() == 0, "second bootstrap re-ran DDL"


def test_insert_returning_and_read_back(sqlite_db):
    """``RETURNING`` and dict rows are what the routes depend on.

    Note ``provider_sid`` (the SERIAL PRIMARY KEY), NOT ``provider_id``:
    init_db.sql:34-35 makes ``provider_sid`` the surrogate auto-increment key
    and ``provider_id`` a plain ``INT`` business key the ROUTE assigns. An
    earlier version of this test asserted on ``provider_id`` and failed with
    None — the test was wrong, not the translation.
    """
    con = fallback.connect()
    try:
        cur = con.execute(
            "INSERT INTO tp_provider (provider_name) VALUES (%s) RETURNING provider_sid",
            ("LSEG",),
        )
        new = cur.fetchone()
        assert isinstance(new, dict), "rows must be dicts (psycopg dict_row parity)"
        assert new["provider_sid"], "SERIAL PRIMARY KEY did not auto-increment"

        got = con.execute(
            "SELECT provider_name FROM tp_provider WHERE provider_sid = %s",
            (new["provider_sid"],),
        ).fetchone()
        assert got["provider_name"] == "LSEG"
        con.commit()
    finally:
        con.close()


def test_serial_primary_key_autoincrements_across_rows(sqlite_db):
    """SERIAL -> INTEGER is only safe because SQLite auto-assigns the rowid
    alias for an INTEGER PRIMARY KEY. Pin it: a non-PK SERIAL would NOT
    auto-increment, so if a future column relies on that this fails loudly."""
    con = fallback.connect()
    try:
        first = con.execute(
            "INSERT INTO tp_provider (provider_name) VALUES (%s) RETURNING provider_sid",
            ("A",),
        ).fetchone()
        second = con.execute(
            "INSERT INTO tp_provider (provider_name) VALUES (%s) RETURNING provider_sid",
            ("B",),
        ).fetchone()
        assert second["provider_sid"] > first["provider_sid"]
        con.commit()
    finally:
        con.close()


def test_data_survives_reconnect(sqlite_db):
    con = fallback.connect()
    try:
        con.execute("INSERT INTO tp_provider (provider_name) VALUES (%s)", ("ICE",))
        con.commit()
    finally:
        con.close()

    con2 = fallback.connect()
    try:
        rows = con2.execute(
            "SELECT provider_name FROM tp_provider WHERE provider_name=%s", ("ICE",)
        ).fetchall()
    finally:
        con2.close()
    assert len(rows) == 1, "file-backed DB did not persist across connections"


def test_rollback_discards(sqlite_db):
    con = fallback.connect()
    try:
        con.execute("INSERT INTO tp_provider (provider_name) VALUES (%s)", ("GONE",))
        con.rollback()
        rows = con.execute(
            "SELECT 1 FROM tp_provider WHERE provider_name=%s", ("GONE",)
        ).fetchall()
        assert rows == [], "rollback did not discard the insert"
    finally:
        con.close()


def test_context_manager_commits_like_psycopg(sqlite_db):
    """psycopg's ``with conn:`` commits on success and does NOT close."""
    con = fallback.connect()
    try:
        with con:
            con.execute("INSERT INTO tp_provider (provider_name) VALUES (%s)", ("CM",))
        rows = con.execute(
            "SELECT 1 FROM tp_provider WHERE provider_name=%s", ("CM",)
        ).fetchall()
        assert len(rows) == 1
    finally:
        con.close()


# ── end to end through the real Flask routes ───────────────────────────────


def test_providers_route_works_with_no_postgres(sqlite_db, monkeypatch):
    """The point of the whole exercise: 200 instead of 500, routes unchanged."""
    monkeypatch.setenv("SCUDO_AUTH_ALLOW_DEV", "1")
    monkeypatch.setenv("SCUDO_AUTH_DEV_PRINCIPAL", "local@dev")
    monkeypatch.setenv("STORE_BACKEND", "local_file")

    from app import app

    client = app.test_client()

    created = client.post("/api/providers", json={"provider_name": "LSEG"})
    assert created.status_code == 201, created.get_data()

    listed = client.get("/api/providers")
    assert listed.status_code == 200, listed.get_data()
    assert any(p["provider_name"] == "LSEG" for p in listed.get_json())
