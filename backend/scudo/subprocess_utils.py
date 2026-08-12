"""Bounded text subprocess execution with descendant cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence


def run_text_process(
    argv: Sequence[str],
    *,
    input_text: str,
    env: Mapping[str, str],
    timeout: float,
    timeout_label: str,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            process.kill()
        process.communicate()
        raise RuntimeError(
            f"{timeout_label} timed out after {timeout:g} seconds"
        ) from exc
    completed = subprocess.CompletedProcess(
        args=list(argv),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed
