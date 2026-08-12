"""JPMC-LOCAL: zero-install SQLite stand-in for the Aurora/PostgreSQL console DB.

WHY THIS EXISTS
    Providers / Datasets / Admin / Ingestion are the only pages that need a
    database. Without one they return HTTP 500 and the pages look broken. The
    supported answer is ``docker compose up postgres``, but on a locked-down
    laptop Docker is often not available, and the demo should not depend on it.

    This module is a drop-in stand-in: a file-backed SQLite database that
    speaks enough of the psycopg surface for the four route modules to work
    UNCHANGED. No pip install (sqlite3 is in the standard library), no daemon,
    no container.

HOW IT IS WIRED
    ``db.py`` chooses at call time. Set:

        CONSOLE_DB_BACKEND=sqlite

    ...and ``get_conn`` / ``get_ingestion_conn`` return one of these instead of
    a psycopg connection. Unset (the default) nothing changes and real
    PostgreSQL is used exactly as before.

    The file lives at ``CONSOLE_DB_SQLITE_PATH`` (default
    ``backend/.local/console.sqlite3``), so data survives a restart.

WHAT IT TRANSLATES
    The route SQL is 154 ``%s`` placeholders across 74 ``execute()`` calls and
    is almost dialect-free. Only four things actually differ:

      * ``%s``          -> ``?``            (paramstyle)
      * ``SERIAL``      -> ``INTEGER``      (DDL only; SQLite rowid autoincrements)
      * ``TIMESTAMPTZ`` -> ``TEXT``
      * ``JSONB``       -> ``TEXT``

    ``RETURNING`` and ``ON CONFLICT ... DO UPDATE`` are NATIVE in SQLite 3.35+
    (this interpreter has 3.51), so they pass through untouched. Verified by
    execution before this file was written.

WHAT IT IS NOT
    Not a PostgreSQL emulator. Concurrent writers, schemas, PL/pgSQL triggers,
    and advanced types are out of scope — a trigger in ``init_db.sql`` is
    skipped, and ``search_path`` schemas collapse into one namespace because
    SQLite has no schemas. It is sized for ONE person running a demo on a
    laptop, which is exactly the stated need.

    If a route ever needs something this cannot do, the failure is a loud
    ``sqlite3.OperationalError`` naming the SQL — not silent wrong data.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

# ── SQL translation ────────────────────────────────────────────────────────

_DDL_TYPE_SUBS: tuple[tuple[str, str], ...] = (
    (r"\bBIGSERIAL\b", "INTEGER"),
    (r"\bSERIAL\b", "INTEGER"),
    (r"\bTIMESTAMPTZ\b", "TEXT"),
    (r"\bJSONB\b", "TEXT"),
    (r"\bNOW\(\)", "CURRENT_TIMESTAMP"),
)

# Schema-qualified names collapse: SQLite has no schemas, and the routes rely
# on search_path making these unqualified anyway.
_SCHEMA_PREFIX = re.compile(r"\b(console|ingestion)\.", re.IGNORECASE)


def translate_params(sql: str) -> str:
    """psycopg ``%s`` -> sqlite ``?``.

    ``%%`` is psycopg's escape for a literal percent; protect it first so a
    LIKE pattern such as ``'%%foo%%'`` is not mangled.
    """
    protected = sql.replace("%%", "\x00PCT\x00")
    return protected.replace("%s", "?").replace("\x00PCT\x00", "%")


def translate_ddl(sql: str) -> str:
    """Postgres DDL -> SQLite DDL. Used only by :func:`ensure_schema`."""
    out = _SCHEMA_PREFIX.sub("", sql)
    for pattern, repl in _DDL_TYPE_SUBS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def _strip_unsupported(ddl: str) -> str:
    """Remove constructs SQLite cannot parse at all.

    PL/pgSQL functions, the triggers that call them, ``CREATE SCHEMA``, and
    ``DROP TRIGGER ... ON table`` (SQLite's DROP TRIGGER takes no ON clause).
    Dropping the updated-at trigger is a real behavioural gap and is stated in
    the module docstring rather than hidden.
    """
    out = re.sub(r"--[^\n]*", "", ddl)
    out = re.sub(
        r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION.*?\$\$\s*LANGUAGE\s+\w+\s*;",
        "",
        out,
        flags=re.IGNORECASE | re.DOTALL,
    )
    out = re.sub(r"CREATE\s+TRIGGER.*?;", "", out, flags=re.IGNORECASE | re.DOTALL)
    out = re.sub(r"DROP\s+TRIGGER[^;]*;", "", out, flags=re.IGNORECASE)
    out = re.sub(r"CREATE\s+SCHEMA[^;]*;", "", out, flags=re.IGNORECASE)
    return out


# ── psycopg-shaped wrappers ────────────────────────────────────────────────


class _Cursor:
    """Cursor exposing the slice of psycopg's API the routes actually use.

    Rows are ``dict`` — matching ``row_factory=dict_row`` — so route code that
    does ``row["provider_nm"]`` works unchanged.
    """

    def __init__(self, cur: sqlite3.Cursor) -> None:
        self._cur = cur

    def execute(self, sql: str, params: Any = None) -> "_Cursor":
        self._cur.execute(translate_params(sql), _norm_params(params))
        return self

    def executemany(self, sql: str, seq: Any) -> "_Cursor":
        self._cur.executemany(translate_params(sql), [_norm_params(p) for p in seq])
        return self

    def fetchone(self) -> Optional[dict]:
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in self._cur.fetchall()]

    def fetchmany(self, size: int = 1) -> list[dict]:
        return [dict(r) for r in self._cur.fetchmany(size)]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self):  # noqa: ANN201 - mirrors sqlite3
        return self._cur.lastrowid

    @property
    def description(self):  # noqa: ANN201 - mirrors DB-API
        return self._cur.description

    def close(self) -> None:
        self._cur.close()

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __iter__(self):  # noqa: ANN204 - mirrors DB-API
        for row in self._cur:
            yield dict(row)


def _norm_params(params: Any) -> Any:
    """psycopg accepts tuple/list/dict/None; sqlite3 wants a sequence or dict."""
    if params is None:
        return ()
    if isinstance(params, (tuple, list, dict)):
        return params
    return (params,)


class SqliteConnection:
    """Connection exposing the psycopg surface the routes use.

    ``autocommit=False`` semantics: the caller commits or rolls back, same as
    the psycopg path, so route transaction handling is unchanged.
    """

    def __init__(self, path: Path) -> None:
        self._con = sqlite3.connect(str(path), isolation_level="DEFERRED")
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")

    def cursor(self, *args: object, **kwargs: object) -> _Cursor:
        return _Cursor(self._con.cursor())

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        self._con.commit()

    def rollback(self) -> None:
        self._con.rollback()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "SqliteConnection":
        return self

    def __exit__(self, exc_type: object, *rest: object) -> None:
        # psycopg's context manager commits on success, rolls back on error,
        # and does NOT close. Mirror that exactly.
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


# ── schema bootstrap ───────────────────────────────────────────────────────


def sqlite_path() -> Path:
    raw = os.environ.get("CONSOLE_DB_SQLITE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(__file__).resolve().parent / ".local" / "console.sqlite3").resolve()


def ensure_schema(path: Optional[Path] = None) -> int:
    """Create the console tables from ``init_db.sql`` if absent. Idempotent.

    Returns the number of DDL statements applied (0 when already initialised).
    """
    target = path or sqlite_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(target))
    try:
        existing = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "tp_provider" in existing:
            return 0

        ddl_file = Path(__file__).resolve().parent / "init_db.sql"
        if not ddl_file.is_file():
            raise RuntimeError(f"cannot bootstrap SQLite: {ddl_file} not found")

        statements = translate_ddl(_strip_unsupported(ddl_file.read_text("utf-8")))
        applied = 0
        for statement in statements.split(";"):
            text = statement.strip()
            if not text:
                continue
            try:
                con.execute(text)
                applied += 1
            except sqlite3.OperationalError as exc:
                # Loud, not silent: an un-translatable statement is reported
                # with the SQL so it can be fixed, but one bad statement does
                # not abort the rest of the bootstrap.
                print(f"[db-sqlite] skipped: {text.splitlines()[0][:70]} -> {exc}")
        con.commit()
        return applied
    finally:
        con.close()


def connect() -> SqliteConnection:
    """Open the local SQLite console DB, creating the schema on first use."""
    ensure_schema()
    return SqliteConnection(sqlite_path())


def enabled() -> bool:
    """True when ``CONSOLE_DB_BACKEND=sqlite``. Read at call time, not import."""
    return os.environ.get("CONSOLE_DB_BACKEND", "").strip().lower() == "sqlite"
