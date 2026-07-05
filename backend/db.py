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


def _connect(search_path: str) -> psycopg.Connection:
    """Open a psycopg connection scoped to ``search_path`` (dict rows, manual
    commit). ``connect_timeout`` avoids long request hangs when the DB is briefly
    unreachable; ``public`` is kept on the path for extensions/shared objects."""
    return psycopg.connect(
        host=os.environ.get("CONSOLE_DB_HOST", "localhost"),
        port=int(os.environ.get("CONSOLE_DB_PORT", "5432")),
        user=os.environ.get("CONSOLE_DB_USER", "scudo"),
        password=os.environ.get("CONSOLE_DB_PASSWORD", ""),
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
    """
    return _connect("console")


def get_ingestion_conn() -> psycopg.Connection:
    """Return a new psycopg connection to the *ingestion* schema (physical data
    tables). Used by the ingestion engine to bulk-insert data rows.
    """
    return _connect("ingestion")
