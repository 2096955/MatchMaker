"""Aurora console bootstrap: schema-qualified DDL, no public→console relocate."""

from __future__ import annotations

import importlib.util
import re
from argparse import Namespace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _bootstrap_module():
    path = REPO / "infra" / "bootstrap_console_schema_data_api.py"
    spec = importlib.util.spec_from_file_location("aurora_bootstrap", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_sql_keeps_dollar_quoted_function_body_together():
    bootstrap = _bootstrap_module()

    statements = bootstrap.split_sql(
        """
        CREATE FUNCTION demo() RETURNS trigger AS $$
        BEGIN
            PERFORM 1;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        INSERT INTO demo_table (note) VALUES ('semicolon; inside a string');
        -- A comment containing ; must not create another statement.
        SELECT 1;
        """
    )

    assert len(statements) == 3
    assert "PERFORM 1;" in statements[0]
    assert "RETURN NEW;" in statements[0]
    assert "semicolon; inside a string" in statements[1]
    assert statements[2].strip().endswith("SELECT 1")


def test_init_db_sql_is_schema_qualified_and_has_no_relocate_dance():
    bootstrap = _bootstrap_module()
    sql = (REPO / "backend" / "init_db.sql").read_text(encoding="utf-8")
    statements = bootstrap.split_sql(sql)
    bootstrap.assert_console_ddl_is_schema_qualified(statements)

    # Executable SQL only (comments may mention the forbidden patterns).
    executable = []
    for statement in statements:
        stripped = re.sub(r"--[^\n]*", "", statement)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        executable.append(stripped.lower())
    joined = "\n".join(executable)
    assert "set search_path" not in joined
    assert "alter table public." not in joined
    for name in bootstrap.CONSOLE_TABLES:
        assert f"create table console.{name}" in joined or (
            f"create table if not exists console.{name}" in joined
        )


def test_assert_rejects_unqualified_create_and_relocate():
    bootstrap = _bootstrap_module()
    with pytest.raises(ValueError, match="unqualified CREATE TABLE"):
        bootstrap.assert_console_ddl_is_schema_qualified(
            ["CREATE TABLE tp_provider (id int)"]
        )
    with pytest.raises(ValueError, match="relocate"):
        bootstrap.assert_console_ddl_is_schema_qualified(
            ["ALTER TABLE public.tp_provider SET SCHEMA console"]
        )
    with pytest.raises(ValueError, match="search_path"):
        bootstrap.assert_console_ddl_is_schema_qualified(["SET search_path TO console"])


class _FakeRdsData:
    def __init__(self, *, fail_sql: str | None = None):
        self.fail_sql = fail_sql
        self.executed: list[dict] = []
        self.committed_statements: list[str] = []
        self.pending_statements: list[str] = []
        self.begin: dict | None = None
        self.committed: dict | None = None
        self.rolled_back: dict | None = None

    def execute_statement(self, **kwargs):
        self.executed.append(kwargs)
        if "information_schema.tables" in kwargs["sql"]:
            return {"records": [[{"longValue": 0}]]}
        if kwargs["sql"] == self.fail_sql:
            raise RuntimeError("simulated DDL failure")
        if "transactionId" in kwargs:
            self.pending_statements.append(kwargs["sql"])
        else:
            self.committed_statements.append(kwargs["sql"])
        return {}

    def begin_transaction(self, **kwargs):
        self.begin = kwargs
        return {"transactionId": "txn-1"}

    def commit_transaction(self, **kwargs):
        self.committed = kwargs
        self.committed_statements.extend(self.pending_statements)
        self.pending_statements.clear()

    def rollback_transaction(self, **kwargs):
        self.rolled_back = kwargs
        self.pending_statements.clear()


def _apply_args(tmp_path: Path) -> Namespace:
    sql_file = tmp_path / "init_db.sql"
    sql_file.write_text(
        "CREATE SCHEMA IF NOT EXISTS console;\n"
        "CREATE TABLE console.roles (id integer);\n"
    )
    return Namespace(
        region="us-east-1",
        cluster_arn="arn:aws:rds:us-east-1:123456789012:cluster:scudo",
        secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:scudo",
        database="scudo",
        sql_file=sql_file,
        apply=True,
    )


def test_bootstrap_executes_schema_changes_in_one_data_api_transaction(
    monkeypatch, tmp_path
):
    bootstrap = _bootstrap_module()
    client = _FakeRdsData()
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr(bootstrap, "_parse_args", lambda: _apply_args(tmp_path))

    assert bootstrap.main() == 0
    assert client.begin == {
        "resourceArn": "arn:aws:rds:us-east-1:123456789012:cluster:scudo",
        "secretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:scudo",
        "database": "scudo",
    }
    assert client.committed == {
        "resourceArn": "arn:aws:rds:us-east-1:123456789012:cluster:scudo",
        "secretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:scudo",
        "transactionId": "txn-1",
    }
    assert client.rolled_back is None
    assert client.pending_statements == []
    assert client.committed_statements
    ddl_calls = [
        call
        for call in client.executed
        if "information_schema.tables" not in call["sql"]
    ]
    assert ddl_calls
    assert all(call["transactionId"] == "txn-1" for call in ddl_calls)
    # No relocate dance in the executed SQL.
    assert not any("set schema console" in call["sql"].lower() for call in ddl_calls)


def test_bootstrap_rolls_back_schema_changes_when_a_statement_fails(
    monkeypatch, tmp_path
):
    bootstrap = _bootstrap_module()
    client = _FakeRdsData(fail_sql=bootstrap.SCUDO_SCHEMA_DDL[1])
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr(bootstrap, "_parse_args", lambda: _apply_args(tmp_path))

    with pytest.raises(RuntimeError, match="simulated DDL failure"):
        bootstrap.main()

    assert client.committed is None
    assert client.pending_statements == []
    assert client.committed_statements == []
    assert client.rolled_back == {
        "resourceArn": "arn:aws:rds:us-east-1:123456789012:cluster:scudo",
        "secretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:scudo",
        "transactionId": "txn-1",
    }
