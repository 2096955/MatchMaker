"""Live Aurora smoke/diagnostic for scudo.agent_memory.

Verifies env/IAM/schema readiness when real Aurora env vars are present,
skips clearly (exit 77, the conventional "test skipped" code) when they are
absent, and never performs a write unless --write-smoke-test is explicitly
passed. These tests inject a fake `execute` callable — no real RDS Data API
call, no real AWS credentials, ever.
"""

from __future__ import annotations

from scudo.scripts.aurora_smoke import (
    _env_validation_errors,
    _parse_args,
    check_agent_memory_table,
    run_write_read_delete_smoke_test,
)


def _args(*flags):
    return _parse_args(["aurora_smoke", *flags])


def test_no_flags_means_no_write_smoke_test():
    args = _args()
    assert args.write_smoke_test is False


def test_write_smoke_test_flag_is_explicit_opt_in():
    args = _args("--write-smoke-test")
    assert args.write_smoke_test is True


def test_env_validation_reports_missing_vars(monkeypatch):
    monkeypatch.delenv("SCUDO_AURORA_CLUSTER_ARN", raising=False)
    monkeypatch.delenv("SCUDO_AURORA_SECRET_ARN", raising=False)
    monkeypatch.delenv("SCUDO_AURORA_DATABASE_NAME", raising=False)
    assert _env_validation_errors() == [
        "SCUDO_AURORA_CLUSTER_ARN",
        "SCUDO_AURORA_SECRET_ARN",
        "SCUDO_AURORA_DATABASE_NAME",
    ]


def test_env_validation_passes_when_all_present(monkeypatch):
    monkeypatch.setenv("SCUDO_AURORA_CLUSTER_ARN", "arn:aws:rds:...")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "arn:aws:secretsmanager:...")
    monkeypatch.setenv("SCUDO_AURORA_DATABASE_NAME", "scudo")
    assert _env_validation_errors() == []


def test_check_agent_memory_table_reports_ok_on_successful_query():
    def fake_execute(sql, params):
        assert "scudo.agent_memory" in sql
        return {"records": []}

    ok, message = check_agent_memory_table(execute=fake_execute)
    assert ok is True
    assert "agent_memory" in message


def test_check_agent_memory_table_surfaces_raw_error_on_failure():
    """No guessing at specific IAM/schema-missing error substrings (never
    verified against a real RDS Data API error this session) — the raw
    exception is surfaced as-is for a human to read."""

    def fake_execute(sql, params):
        raise RuntimeError('relation "scudo.agent_memory" does not exist')

    ok, message = check_agent_memory_table(execute=fake_execute)
    assert ok is False
    assert "does not exist" in message


def test_write_read_delete_smoke_test_round_trips_and_cleans_up():
    calls = []

    def fake_execute(sql, params):
        calls.append(sql.strip().split()[0].lower())  # first SQL keyword
        return {"records": []}

    ok, message = run_write_read_delete_smoke_test(execute=fake_execute)
    assert ok is True
    assert calls == [
        "insert",
        "select",
        "delete",
    ]  # write, read-back, cleanup — in order


def test_write_read_delete_smoke_test_reports_failure_without_raising():
    def fake_execute(sql, params):
        raise RuntimeError("access denied")

    ok, message = run_write_read_delete_smoke_test(execute=fake_execute)
    assert ok is False
    assert "access denied" in message


def test_aurora_smoke_module_is_never_imported_by_lambda_handler():
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scudo.lambda_handler; import sys; "
            "print('scudo.scripts.aurora_smoke' in sys.modules)",
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
