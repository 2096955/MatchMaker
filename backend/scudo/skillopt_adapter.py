"""CLI-based adapter for the REAL microsoft/SkillOpt-Sleep tool — NEVER
imported by lambda_handler.py (the live Lambda hot path; see
test_skillopt_adapter.py::test_adapter_module_is_never_imported_by_lambda_handler).

Ground truth verified this session directly from github.com/microsoft/SkillOpt
(`gh api repos/microsoft/SkillOpt/contents/...`), NOT guessed:

  - `skillopt-sleep` is a real installed CLI entry point
    (pyproject.toml: `[project.scripts] skillopt-sleep =
    "skillopt_sleep.__main__:main"`).
  - Its subcommands/flags (skillopt_sleep/__main__.py): `run`/`dry-run`/
    `status`/`adopt`/`harvest`/`schedule`/`unschedule`, with
    `--tasks-file`, `--target-skill-path`, `--project`, `--backend
    mock|claude|codex|copilot`, `--json` among the common flags.
  - Its TaskRecord field schema (skillopt_sleep/types.py): id, project,
    intent, context_excerpt, system, attempted_solution, outcome,
    reference_kind, reference, judge, tags, source_sessions, split, origin,
    derived_from.
  - Its tasks-file payload shape (skillopt_sleep/tasks_file.py::
    make_tasks_payload): format="skillopt_sleep.tasks.v1", project,
    transcript_source, n_sessions, target_skill_path, reviewed, tasks.
  - Its `dry-run --json` report shape (skillopt_sleep/__main__.py::
    _report_payload): night, accepted, gate_action, no_edits_reason,
    baseline, candidate, n_tasks, n_sessions, n_accepted_edits,
    n_rejected_edits, edits, rejected_edits, notes, staging_dir, adopted.

NOT verified, and deliberately NOT implemented here (would be guessing):
whether SCUDO's vendor-mapping trajectories are a MEANINGFULLY SCORABLE
training signal for SkillOpt-Sleep's replay/judge machinery
(skillopt_sleep/replay.py, judges.py) — that tool's scoring appears built
around coding-session transcripts with a judge/rubric/reference to compare
against, and SCUDO's trajectories carry no such reference (reference_kind is
always "none" below). Whether skillopt-sleep's held-out gate produces a
meaningful score for this domain, rather than a degenerate one, is a real
open question for whoever runs this for real the first time — not a
technical unknown, a domain-fit one.

The Python API surface (importing `skillopt`/`skillopt_sleep` as a library)
remains unverified — only the CLI's documented flags and the two JSON
schemas above were confirmed. This module therefore only shells out to the
CLI; it never imports the `skillopt_sleep` package.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from typing import Any, Callable, Optional

Which = Callable[[str], Optional[str]]
Runner = Callable[..., Any]

_BINARY_NAME = "skillopt-sleep"
# Deterministic, replay-safe TaskRecord ids - same namespace convention as
# the rest of this codebase (models.py's mds_iri uses uuid.uuid5 the same way).
_TASK_ID_NAMESPACE = uuid.UUID("2f6b9a7d-9c1a-4b3e-9a1a-6c9f7d0e2b41")


class SkillOptSleepUnavailableError(RuntimeError):
    """Raised when the `skillopt-sleep` CLI binary is not on PATH."""


def find_skillopt_sleep_binary(*, which: Optional[Which] = None) -> Optional[str]:
    """Return the path to the `skillopt-sleep` binary, or None if it isn't
    installed. `which` is injectable so tests never depend on the real
    binary being present."""
    if which is None:
        which = shutil.which
    return which(_BINARY_NAME)


def trajectory_to_task_record(trajectory: dict) -> dict:
    """Map ONE SCUDO trajectory (the exact shape
    `aurora_memory.record_trajectory`/`harvest_trajectories` produce:
    vendor, vendor_product_ref, target_iri, confidence, rationale,
    decided_at) to a verified `TaskRecord`-shaped dict (types.py's exact
    field names). Every field's derivation is explicit, not fabricated:

      id                  deterministic uuid5 of (vendor, vendor_product_ref)
      project             fixed "scudo-matching" - identifies this domain
      intent              natural-language restatement of the REAL fields
      attempted_solution  the REAL target_iri that was mapped to
      context_excerpt      the REAL rationale
      outcome             "success" only for an explicitly auto-passed,
                          published/auto-mapped decision; all review,
                          abstention, and rejected decisions are failures
      reference_kind      "none" - no rubric/exact-answer is recorded
                          alongside a trajectory today (see module docstring
                          for why this may limit skillopt-sleep's scoring)
      split               "train" - skillopt-sleep's own assign_splits()
                          computes train/val/test on load; this module must
                          not duplicate that logic
      origin              "real" - mined from an actual verified outcome,
                          not synthetic/dream-augmented
    """
    vendor = trajectory["vendor"]
    vendor_product_ref = trajectory["vendor_product_ref"]
    task_id = str(uuid.uuid5(_TASK_ID_NAMESPACE, f"{vendor}::{vendor_product_ref}"))
    decision_outcome = str(trajectory.get("outcome", "")).strip().lower()
    mined_outcome = (
        "success"
        if trajectory.get("auto_pass") is True
        and decision_outcome in {"published", "auto_mapped", "approved"}
        else "failure"
    )
    return {
        "id": task_id,
        "project": "scudo-matching",
        "intent": (
            f"Map vendor product {vendor_product_ref!r} ({vendor}) to the "
            "correct CDAO catalogue concept."
        ),
        "context_excerpt": trajectory.get("rationale", ""),
        "system": "",
        "attempted_solution": trajectory.get("target_iri", ""),
        "outcome": mined_outcome,
        "reference_kind": "none",
        "reference": "",
        "judge": {},
        "tags": ["scudo-matching", vendor],
        "source_sessions": [],
        "split": "train",
        "origin": "real",
        "derived_from": "",
    }


def write_tasks_file(
    trajectories: list,
    *,
    project: str,
    target_skill_path: str,
    path: str,
) -> str:
    """Write a verified `skillopt_sleep.tasks.v1` tasks-file (the exact
    payload shape `tasks_file.py::make_tasks_payload` produces) from a list
    of SCUDO trajectories, for `--tasks-file` to replay instead of
    harvesting live session transcripts (which SCUDO has none of - this is
    the seam skillopt-sleep itself documents for exactly this case)."""
    payload = {
        "format": "skillopt_sleep.tasks.v1",
        "project": project,
        "transcript_source": "",
        "n_sessions": 0,
        "target_skill_path": target_skill_path,
        "reviewed": False,
        "tasks": [trajectory_to_task_record(t) for t in trajectories],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def run_skillopt_sleep_dry_run(
    *,
    tasks_file_path: str,
    target_skill_path: str,
    project: str,
    which: Optional[Which] = None,
    runner: Optional[Runner] = None,
    timeout: float = 300.0,
) -> dict:
    """Invoke the REAL `skillopt-sleep dry-run` CLI (harvest is skipped in
    favor of `--tasks-file`; `--backend mock` so no real LLM calls happen
    even if skillopt-sleep is installed) and parse its `--json` report.
    Raises `SkillOptSleepUnavailableError` if the binary isn't on PATH -
    the "fail clearly when SkillOpt is unavailable" default path. `which`/
    `runner` are injectable so tests never spawn a real subprocess.
    """
    binary = find_skillopt_sleep_binary(which=which)
    if binary is None:
        raise SkillOptSleepUnavailableError(
            f"the `{_BINARY_NAME}` CLI is not installed/on PATH. Install "
            "the `skillopt` PyPI package (which provides this entry point), "
            "or inject a fake `runner=`/`which=` for testing."
        )

    if runner is None:
        runner = subprocess.run

    argv = [
        binary,
        "dry-run",
        "--tasks-file",
        tasks_file_path,
        "--target-skill-path",
        target_skill_path,
        "--project",
        project,
        "--backend",
        "mock",
        "--json",
    ]
    result = runner(argv, capture_output=True, text=True, timeout=timeout, check=True)

    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            f"`{_BINARY_NAME} dry-run --json` did not return valid JSON: "
            f"{e}. stdout was: {result.stdout!r}"
        ) from e
