"""Subprocess adapter for a separately configured protected evaluator."""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Callable, Optional

from .matching_self_improvement import SignedEvaluationEnvelope
from .subprocess_utils import run_text_process

Runner = Callable[..., Any]


def _validated_evaluator_argv(command: str) -> list[str]:
    argv = shlex.split(command)
    lowered = [part.lower().replace("\\", "/") for part in argv]
    directly_targets_bundled = any(
        part.endswith("/protected_evaluator.py")
        or part == "protected_evaluator.py"
        or "scudo.scripts.protected_evaluator" in part
        for part in lowered
    )
    if directly_targets_bundled:
        raise RuntimeError(
            "SCUDO_PROTECTED_EVALUATOR_COMMAND must target an independently "
            "provisioned evaluator wrapper/service that owns its private key and "
            "protected evaluation root; direct bundled protected_evaluator.py "
            "execution is forbidden"
        )
    return argv


def run_protected_evaluator_command(
    request: dict,
    *,
    command: str,
    runner: Optional[Runner] = None,
    timeout: float = 300.0,
) -> SignedEvaluationEnvelope:
    if not command.strip():
        raise RuntimeError("SCUDO_PROTECTED_EVALUATOR_COMMAND is not configured")
    argv = _validated_evaluator_argv(command)
    allowed_request = {
        key: request[key]
        for key in (
            "candidate_content",
            "artifact_id",
            "artifact_version",
            "artifact_kind",
            "candidate_version",
            "baseline_version",
            "evaluation_request_id",
        )
        if key in request
    }
    required = {
        "candidate_content",
        "artifact_id",
        "artifact_version",
        "artifact_kind",
        "candidate_version",
        "evaluation_request_id",
    }
    if not required <= set(allowed_request):
        raise ValueError(
            "protected evaluator request is missing required identity fields"
        )
    child_env = {
        key: value
        for key, value in {
            "PATH": os.environ.get("PATH"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
        }.items()
        if value is not None
    }
    if runner is None:
        result = run_text_process(
            argv,
            input_text=json.dumps(allowed_request, sort_keys=True),
            env=child_env,
            timeout=timeout,
            timeout_label="protected evaluator",
        )
    else:
        result = runner(
            argv,
            input=json.dumps(allowed_request, sort_keys=True),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            env=child_env,
        )
    try:
        return SignedEvaluationEnvelope.model_validate_json(result.stdout)
    except Exception as exc:
        raise RuntimeError(
            "protected evaluator returned an invalid SignedEvaluationEnvelope"
        ) from exc
