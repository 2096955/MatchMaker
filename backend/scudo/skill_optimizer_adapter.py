"""Configured JSON subprocess adapter for protected skill optimization."""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Callable, Optional

from .subprocess_utils import run_text_process

Runner = Callable[..., Any]


def run_skill_optimizer_command(
    request: dict,
    *,
    command: str,
    runner: Optional[Runner] = None,
    timeout: float = 300.0,
    config_env: Optional[dict[str, str]] = None,
) -> str:
    if not command.strip():
        raise RuntimeError("SCUDO_SKILL_OPTIMIZER_COMMAND is not configured")
    child_env = {
        key: value
        for key, value in {
            "PATH": os.environ.get("PATH"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            **(config_env or {}),
        }.items()
        if value is not None
    }
    argv = shlex.split(command)
    if runner is None:
        result = run_text_process(
            argv,
            input_text=json.dumps(request, sort_keys=True),
            env=child_env,
            timeout=timeout,
            timeout_label="optimizer",
        )
    else:
        result = runner(
            argv,
            input=json.dumps(request, sort_keys=True),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            env=child_env,
        )
    try:
        payload = json.loads(result.stdout)
        candidate = payload["candidate_content"]
    except Exception as exc:
        raise RuntimeError("optimizer returned invalid JSON candidate output") from exc
    if not isinstance(candidate, str) or not candidate.strip():
        raise RuntimeError("optimizer returned empty candidate_content")
    return candidate
