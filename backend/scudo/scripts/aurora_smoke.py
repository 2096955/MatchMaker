"""Live Aurora smoke/diagnostic for `scudo.agent_memory`.

Verifies env/IAM/schema readiness when real Aurora env vars are present;
skips clearly (exit 77 — the conventional Automake/CI "test skipped" code,
distinct from both success (0) and failure (1)) when credentials/env are
absent, since that's the EXPECTED state for local/CI runs without Aurora
secrets, not a diagnostic failure.

Read-only by default: the connectivity/schema check is a single `SELECT 1
FROM scudo.agent_memory LIMIT 1`. The only WRITE this script can ever
perform — a single throwaway row, immediately read back then deleted — is
gated behind the explicit `--write-smoke-test` flag (never on by default).

Never imported by lambda_handler.py — see
test_aurora_smoke.py::test_aurora_smoke_module_is_never_imported_by_lambda_handler.
Heavy/live imports (aurora_store) stay lazy inside main() so importing this
module offline never touches Aurora or the network.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Callable, Optional

_REQUIRED_AURORA_ENV_VARS = (
    "SCUDO_AURORA_CLUSTER_ARN",
    "SCUDO_AURORA_SECRET_ARN",
    "SCUDO_AURORA_DATABASE_NAME",
)

SKIPPED_EXIT_CODE = 77

Execute = Callable[[str, list], dict]


def _env_validation_errors() -> list[str]:
    """Which required Aurora env vars are missing. Pure — no AWS call."""
    import os

    return [name for name in _REQUIRED_AURORA_ENV_VARS if not os.environ.get(name)]


def check_agent_memory_table(*, execute: Optional[Execute] = None) -> tuple[bool, str]:
    """Read-only connectivity+schema check against scudo.agent_memory.

    No guessing at specific IAM-denied/schema-missing error substrings (not
    verified against a real RDS Data API error this session) — on failure,
    the raw exception message is surfaced as-is for a human to diagnose,
    rather than a fabricated classification.
    """
    if execute is None:
        from scudo import aurora_store

        execute = aurora_store._execute
    try:
        execute("select 1 from scudo.agent_memory limit 1", [])
    except Exception as e:  # noqa: BLE001 — surfaced verbatim, not swallowed
        return False, f"{type(e).__name__}: {e}"
    return True, "scudo.agent_memory is reachable and queryable"


def run_write_read_delete_smoke_test(
    *, execute: Optional[Execute] = None
) -> tuple[bool, str]:
    """Explicitly gated (--write-smoke-test): round-trips ONE throwaway row
    (memory_key='smoke-test:aurora-diagnostic:<uuid4>') — insert, read back,
    delete — proving write access without leaving any residue."""
    if execute is None:
        from scudo import aurora_store

        execute = aurora_store._execute
    from scudo import aurora_store as _aurora_store_module

    key = f"smoke-test:aurora-diagnostic:{uuid.uuid4()}"
    try:
        execute(
            "insert into scudo.agent_memory (memory_key, memory_type, "
            "updated_at_ms, payload) values (:memory_key, :memory_type, "
            ":updated_at_ms, :payload::jsonb)",
            [
                _aurora_store_module._str_param("memory_key", key),
                _aurora_store_module._str_param("memory_type", "smoke_test"),
                _aurora_store_module._str_param(
                    "updated_at_ms", str(int(time.time() * 1000))
                ),
                _aurora_store_module._json_param("payload", {"smoke_test": True}),
            ],
        )
        execute(
            "select payload from scudo.agent_memory where memory_key = :memory_key",
            [_aurora_store_module._str_param("memory_key", key)],
        )
        execute(
            "delete from scudo.agent_memory where memory_key = :memory_key",
            [_aurora_store_module._str_param("memory_key", key)],
        )
    except Exception as e:  # noqa: BLE001 — surfaced verbatim, not swallowed
        return False, f"{type(e).__name__}: {e}"
    return True, f"round-tripped and cleaned up smoke-test row {key!r}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify env/IAM/schema readiness for scudo.agent_memory. "
        "Read-only by default — pass --write-smoke-test to also verify write "
        "access via a single throwaway row that is immediately cleaned up."
    )
    parser.add_argument(
        "--write-smoke-test",
        action="store_true",
        help="Also round-trip (insert/read/delete) one throwaway row to "
        "verify write access. Off by default (read-only diagnostic only).",
    )
    return parser.parse_args(argv[1:])


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns a process exit code: 0 = all checks passed,
    1 = a check failed, 77 = skipped (Aurora env vars absent)."""
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv)

    missing = _env_validation_errors()
    if missing:
        print(
            f"[aurora_smoke] SKIPPED: missing required env var(s): "
            f"{', '.join(missing)}. This is the expected state in local/CI "
            f"environments without Aurora credentials, not a failure.",
            file=sys.stderr,
        )
        return SKIPPED_EXIT_CODE

    ok, message = check_agent_memory_table()
    print(
        f"[aurora_smoke] connectivity/schema check: {'OK' if ok else 'FAILED'} — {message}"
    )
    if not ok:
        return 1

    if args.write_smoke_test:
        ok2, message2 = run_write_read_delete_smoke_test()
        print(
            f"[aurora_smoke] write/read/delete smoke test: "
            f"{'OK' if ok2 else 'FAILED'} — {message2}"
        )
        if not ok2:
            return 1

    print("[aurora_smoke] DONE: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
