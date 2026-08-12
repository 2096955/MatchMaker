#!/usr/bin/env python3
"""Bootstrap empty SCUDO Aurora PostgreSQL schemas through the RDS Data API.

The console DDL contains a PL/pgSQL function, so it cannot be split on every
semicolon. This tool handles PostgreSQL quotes, dollar-quoted bodies, and SQL
comments before issuing one statement per RDS Data API request. It refuses to
run the console schema file if either console-owned schema already has tables:
``backend/init_db.sql`` intentionally drops the versioned ``tp_*`` tables.

Console objects MUST be schema-qualified (``console.<name>``). This tool never
creates unqualified tables in ``public`` and never runs
``ALTER TABLE public.* SET SCHEMA console`` — that relocate dance was ambiguous
under RDS Data API transaction/session semantics.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import boto3


SCUDO_SCHEMA_DDL = (
    "create schema if not exists scudo",
    """
    create table if not exists scudo.audit_events (
      item_id text,
      event_type text,
      created_at_ms bigint,
      payload jsonb
    )
    """,
    """
    create table if not exists scudo.mapping_decisions (
      ticket text primary key,
      status text,
      created_at_ms bigint,
      payload jsonb
    )
    """,
    """
    create table if not exists scudo.publish_outbox (
      event_id text primary key,
      detail_type text,
      dispatched boolean default false,
      created_at_ms bigint,
      detail jsonb
    )
    """,
    """
    create table if not exists scudo.lineage_facts (
      source_bucket text,
      source_key text,
      content_hash text,
      payload jsonb
    )
    """,
    """
    create table if not exists scudo.catalogue_products (
      iri text primary key,
      payload jsonb
    )
    """,
    """
    create table if not exists scudo.cdao_taxonomy (
      iri text primary key,
      payload jsonb
    )
    """,
    """
    create table if not exists scudo.etl_jobs (
      job_id text primary key,
      status text,
      updated_at_ms bigint,
      fields jsonb
    )
    """,
    """
    create table if not exists scudo.agent_memory (
      memory_key text primary key,
      memory_type text,
      updated_at_ms bigint,
      payload jsonb
    )
    """,
)

CONSOLE_TABLES = (
    "tp_provider",
    "tp_dataset",
    "tp_dataset_col",
    "roles",
    "users",
    "user_roles",
    "role_privileges",
    "etl_run_log",
    "tp_dataset_col_transforms",
)

_RELOCATE_RE = re.compile(
    r"alter\s+table\s+public\.\w+\s+set\s+schema\s+console",
    re.IGNORECASE,
)
_SEARCH_PATH_RE = re.compile(r"set\s+search_path\b", re.IGNORECASE)
_CREATE_TABLE_PREFIX_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:only\s+)?",
    re.IGNORECASE,
)
_QUALIFIED_SCHEMA_PREFIXES = ("console.", "ingestion.", "scudo.")


def split_sql(sql: str) -> list[str]:
    """Split PostgreSQL statements without breaking quoted/dollar-quoted SQL."""
    statements: list[str] = []
    buf: list[str] = []
    state = "normal"
    dollar_tag = ""
    index = 0

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "-" and next_char == "-":
                buf.extend(("--",))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                buf.extend(("/*",))
                index += 2
                state = "block_comment"
                continue
            if char == "'":
                buf.append(char)
                index += 1
                state = "single_quote"
                continue
            if char == '"':
                buf.append(char)
                index += 1
                state = "double_quote"
                continue
            if char == "$":
                match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
                if match:
                    dollar_tag = match.group(0)
                    buf.append(dollar_tag)
                    index += len(dollar_tag)
                    state = "dollar_quote"
                    continue
            if char == ";":
                statement = "".join(buf).strip()
                if statement:
                    statements.append(statement)
                buf = []
                index += 1
                continue
            buf.append(char)
            index += 1
            continue

        if state == "line_comment":
            buf.append(char)
            index += 1
            if char == "\n":
                state = "normal"
            continue

        if state == "block_comment":
            buf.append(char)
            index += 1
            if char == "*" and next_char == "/":
                buf.append(next_char)
                index += 1
                state = "normal"
            continue

        if state == "single_quote":
            buf.append(char)
            index += 1
            if char == "'" and next_char == "'":
                buf.append(next_char)
                index += 1
            elif char == "'":
                state = "normal"
            continue

        if state == "double_quote":
            buf.append(char)
            index += 1
            if char == '"' and next_char == '"':
                buf.append(next_char)
                index += 1
            elif char == '"':
                state = "normal"
            continue

        if state == "dollar_quote":
            if sql.startswith(dollar_tag, index):
                buf.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
            else:
                buf.append(char)
                index += 1
            continue

        raise AssertionError(f"unknown SQL parser state: {state}")

    tail = "".join(buf).strip()
    if tail and re.sub(r"(--[^\n]*|/\*.*?\*/)", "", tail, flags=re.DOTALL).strip():
        statements.append(tail)
    return statements


def _has_unqualified_create_table(sql: str) -> bool:
    """True if any CREATE TABLE target lacks console./ingestion./scudo. prefix."""
    for match in _CREATE_TABLE_PREFIX_RE.finditer(sql):
        rest = sql[match.end() :].lstrip()
        lower = rest.lower()
        if any(lower.startswith(prefix) for prefix in _QUALIFIED_SCHEMA_PREFIXES):
            continue
        return True
    return False


def assert_console_ddl_is_schema_qualified(statements: Iterable[str]) -> None:
    """Fail loud if DDL relies on search_path or public→console relocation."""
    for statement in statements:
        # Comments may mention the forbidden patterns; only enforce on executable SQL.
        stripped = re.sub(r"--[^\n]*", "", statement)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        if _RELOCATE_RE.search(stripped):
            raise ValueError(
                "console DDL must not relocate public.* → console; "
                "schema-qualify CREATE TABLE console.<name> instead"
            )
        if _SEARCH_PATH_RE.search(stripped):
            raise ValueError(
                "console DDL must not SET search_path; "
                "schema-qualify every object (RDS Data API session semantics)"
            )
        if _has_unqualified_create_table(stripped):
            raise ValueError(
                "console DDL contains an unqualified CREATE TABLE; "
                "use CREATE TABLE console.<name> (or ingestion./scudo.)"
            )


def _console_table_count(
    client, *, cluster_arn: str, secret_arn: str, database: str
) -> int:
    response = client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=(
            "select count(*) from information_schema.tables "
            "where table_schema in ('console', 'ingestion')"
        ),
    )
    return int(response["records"][0][0]["longValue"])


def _public_console_table_count(
    client, *, cluster_arn: str, secret_arn: str, database: str
) -> int:
    names = ", ".join(f"'{name}'" for name in CONSOLE_TABLES)
    response = client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=(
            "select count(*) from information_schema.tables "
            "where table_schema = 'public' and table_name in (" + names + ")"
        ),
    )
    return int(response["records"][0][0]["longValue"])


def _execute(
    client,
    statements: Iterable[str],
    *,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    label: str,
    transaction_id: str | None = None,
) -> None:
    for number, statement in enumerate(statements, start=1):
        print(f"Applying {label} statement {number}")
        request = dict(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=database,
            sql=statement,
        )
        if transaction_id is not None:
            request["transactionId"] = transaction_id
        client.execute_statement(**request)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--cluster-arn", required=True)
    parser.add_argument("--secret-arn", required=True)
    parser.add_argument(
        "--database",
        required=True,
        help="Aurora PostgreSQL database that owns the console and scudo schemas",
    )
    parser.add_argument(
        "--sql-file",
        type=Path,
        default=Path("backend/init_db.sql"),
        help="PostgreSQL console schema file to apply",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute DDL. Without this flag the tool only prints the plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    console_statements = split_sql(args.sql_file.read_text(encoding="utf-8"))
    assert_console_ddl_is_schema_qualified(console_statements)
    print(
        f"Prepared {len(SCUDO_SCHEMA_DDL)} scudo statements and "
        f"{len(console_statements)} console/ingestion statements."
    )
    if not args.apply:
        print(
            "Dry run only. Re-run with --apply after confirming the database is empty."
        )
        return 0

    client = boto3.client("rds-data", region_name=args.region)
    table_count = _console_table_count(
        client,
        cluster_arn=args.cluster_arn,
        secret_arn=args.secret_arn,
        database=args.database,
    )
    if table_count:
        raise RuntimeError(
            "Refusing to apply backend/init_db.sql: console or ingestion already "
            f"contains {table_count} table(s), and the file drops tp_* tables."
        )
    public_table_count = _public_console_table_count(
        client,
        cluster_arn=args.cluster_arn,
        secret_arn=args.secret_arn,
        database=args.database,
    )
    if public_table_count:
        raise RuntimeError(
            "Refusing to apply backend/init_db.sql: public already contains "
            f"{public_table_count} console-owned table(s)."
        )

    transaction_id = client.begin_transaction(
        resourceArn=args.cluster_arn,
        secretArn=args.secret_arn,
        database=args.database,
    )["transactionId"]
    try:
        _execute(
            client,
            SCUDO_SCHEMA_DDL,
            cluster_arn=args.cluster_arn,
            secret_arn=args.secret_arn,
            database=args.database,
            label="scudo",
            transaction_id=transaction_id,
        )
        _execute(
            client,
            console_statements,
            cluster_arn=args.cluster_arn,
            secret_arn=args.secret_arn,
            database=args.database,
            label="console",
            transaction_id=transaction_id,
        )
        client.commit_transaction(
            resourceArn=args.cluster_arn,
            secretArn=args.secret_arn,
            transactionId=transaction_id,
        )
    except Exception:
        try:
            client.rollback_transaction(
                resourceArn=args.cluster_arn,
                secretArn=args.secret_arn,
                transactionId=transaction_id,
            )
        except Exception:
            print("WARNING: Aurora bootstrap rollback failed.")
        raise
    print("Aurora schema bootstrap completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
