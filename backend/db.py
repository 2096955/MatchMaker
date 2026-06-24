"""Database connection helpers for the Data Ingestion Framework.

Provides two connection factories:
- ``get_conn``: connects to the ``metadata`` database (providers, datasets, run logs).
- ``get_ingestion_conn``: connects to the ``ingestion`` database (physical data tables).

Credentials are read from environment variables ``my_sql_user`` and
``my_sql_password``, falling back to ``root`` / empty string for local dev.
All connections use ``DictCursor`` so rows are returned as plain dicts and
``autocommit=False`` so callers control transaction boundaries explicitly.
"""

import os
import pymysql
from pymysql.cursors import DictCursor

# Base connection config targeting the metadata schema.
DB_CONFIG = {
    # Host/port come from env so the same image runs locally (localhost) and on
    # Fargate (Aurora endpoint via my_sql_host). connect_timeout avoids 30s
    # request hangs when the DB is briefly unreachable.
    "host": os.environ.get("my_sql_host", "localhost"),
    "port": int(os.environ.get("my_sql_port", "3306")),
    "user": os.environ.get("my_sql_user", "root"),
    "password": os.environ.get("my_sql_password", ""),
    "database": "metadata",
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
    "autocommit": False,
    "connect_timeout": int(os.environ.get("my_sql_connect_timeout", "10")),
}


def get_conn():
    """Return a new pymysql connection to the *metadata* database.

    Returns:
        pymysql.connections.Connection: Open connection with DictCursor and
            autocommit disabled.  The caller is responsible for committing or
            rolling back and for closing the connection.
    """
    return pymysql.connect(**DB_CONFIG)


# Ingestion config re-uses all base settings but targets the ingestion schema.
_INGESTION_CONFIG = {**DB_CONFIG, "database": "ingestion"}


def get_ingestion_conn():
    """Return a new pymysql connection to the *ingestion* database.

    Returns:
        pymysql.connections.Connection: Open connection with DictCursor and
            autocommit disabled.  Used exclusively by the ingestion engine to
            bulk-insert data rows into provider-specific tables.
    """
    return pymysql.connect(**_INGESTION_CONFIG)
