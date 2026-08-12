"""Database connection helpers for the Data Ingestion Framework console.

Aurora PostgreSQL access via psycopg (v3), consolidated onto the single Aurora
cluster:

- ``get_conn``            connects with ``search_path=console`` - the console's
                          metadata (providers, datasets, run logs, users/roles).
- ``get_ingestion_conn``  connects with ``search_path=ingestion`` - the
                          dynamically-created physical data tables.

Setting the schema via ``search_path`` at connect time means the routes' and
ingestion engine's unqualified table names (``tp_provider``, ``etl_run_log``,
per-dataset physical tables, ...) resolve without changing their SQL.
Connections return rows as plain dicts (``row_factory=dict_row``) and use
``autocommit=False`` so callers own transaction boundaries. Credentials come
from the ``CONSOLE_DB_*`` env vars, defaulting to a local dev PostgreSQL.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _connect(search_path: str) -> psycopg.Connection:
    """Open a psycopg connection scoped to ``search_path`` (dict rows, manual
    commit). ``connect_timeout`` avoids long request hangs when the DB is briefly
    unreachable; ``public`` is kept on the path for extensions/shared objects.

    A missing ``CONSOLE_DB_PASSWORD`` is only tolerated for local hosts (the
    dev default) - a non-local host without one is almost certainly a
    misconfigured deploy about to attempt an unauthenticated connection to a
    real Aurora endpoint, so fail before ever calling ``psycopg.connect``.
    """
    host = os.environ.get("CONSOLE_DB_HOST", "localhost")
    password = os.environ.get("CONSOLE_DB_PASSWORD", "")
    if host not in _LOCAL_HOSTS and not password:
        raise RuntimeError(
            f"CONSOLE_DB_PASSWORD is required when CONSOLE_DB_HOST is not local "
            f"(got host={host!r})"
        )
    return psycopg.connect(
        host=host,
        port=int(os.environ.get("CONSOLE_DB_PORT", "5432")),
        user=os.environ.get("CONSOLE_DB_USER", "scudo"),
        password=password,
        dbname=os.environ.get("CONSOLE_DB_NAME", "scudo_console"),
        connect_timeout=int(os.environ.get("CONSOLE_DB_CONNECT_TIMEOUT", "10")),
        options=f"-c search_path={search_path},public",
        row_factory=dict_row,
        autocommit=False,
    )


def get_conn() -> psycopg.Connection:
    """Return a new psycopg connection to the *console* metadata schema.

    Rows come back as dicts and autocommit is disabled — the caller commits or
    rolls back and closes the connection.

    JPMC-LOCAL: with ``CONSOLE_DB_BACKEND=sqlite`` this returns a file-backed
    SQLite stand-in instead, so Providers / Datasets / Admin / Ingestion work
    with no PostgreSQL and no Docker. Checked at CALL time, not import time,
    so nothing changes for a deployed run that never sets it. See
    ``db_sqlite_fallback``.
    """
    if _sqlite_enabled():
        from db_sqlite_fallback import connect as _sqlite_connect

        return _sqlite_connect()  # type: ignore[return-value]
    return _connect("console")


def get_ingestion_conn() -> psycopg.Connection:
    """Return a new psycopg connection to the *ingestion* schema (physical data
    tables). Used by the ingestion engine to bulk-insert data rows.

    JPMC-LOCAL: same SQLite stand-in as ``get_conn``. SQLite has no schemas, so
    both collapse onto one file — fine locally, where the two schemas never
    collide on a table name.
    """
    if _sqlite_enabled():
        from db_sqlite_fallback import connect as _sqlite_connect

        return _sqlite_connect()  # type: ignore[return-value]
    return _connect("ingestion")


def _sqlite_enabled() -> bool:
    """JPMC-LOCAL: is the no-Docker SQLite stand-in selected?

    Deliberately reads the environment on every call (not a module constant)
    so a test can flip it with monkeypatch and so import order cannot bake in
    the wrong answer.
    """
    return os.environ.get("CONSOLE_DB_BACKEND", "").strip().lower() == "sqlite"
