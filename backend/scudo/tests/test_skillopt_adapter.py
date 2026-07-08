"""CLI-based adapter for the REAL microsoft/SkillOpt-Sleep tool
(`scudo.skillopt_adapter`) — built against VERIFIED ground truth, not
guessed: `skillopt-sleep`'s CLI flags/subcommands (skillopt_sleep/__main__.py),
its TaskRecord field schema (skillopt_sleep/types.py), and its tasks-file
payload shape (skillopt_sleep/tasks_file.py::make_tasks_payload) were all
fetched directly from github.com/microsoft/SkillOpt this session via
`gh api repos/microsoft/SkillOpt/contents/...`.

Fully hermetic: `which`/`runner` are both injectable, so no test ever
requires the real `skillopt-sleep` binary to be installed, and none spawns a
real subprocess.
"""

from __future__ import annotations

import json

import pytest

from scudo.skillopt_adapter import (
    SkillOptSleepUnavailableError,
    find_skillopt_sleep_binary,
    run_skillopt_sleep_dry_run,
    trajectory_to_task_record,
    write_tasks_file,
)

_SAMPLE_TRAJECTORY = {
    "vendor": "LSEG",
    "vendor_product_ref": "LSEG-EQ-PX",
    "target_iri": "jpmorgan:data:cdao:concept:equity-prices",
    "confidence": 0.93,
    "rationale": "Field names and description match the CDAO Equity Prices concept.",
    "decided_at": 1751900000.0,
}


def test_trajectory_to_task_record_maps_verified_fields():
    """Every key here is a REAL TaskRecord dataclass field (types.py) -
    id/project/intent/attempted_solution/outcome/tags/source_sessions/split/
    origin - not invented."""
    record = trajectory_to_task_record(_SAMPLE_TRAJECTORY)

    assert record["project"] == "scudo-matching"
    assert "LSEG-EQ-PX" in record["intent"]
    assert "LSEG" in record["intent"]
    assert record["attempted_solution"] == "jpmorgan:data:cdao:concept:equity-prices"
    assert (
        record["outcome"] == "success"
    )  # only PUBLISHED/verified outcomes are ever recorded
    assert record["reference_kind"] == "none"
    assert (
        record["split"] == "train"
    )  # skillopt-sleep's own assign_splits computes val/test
    assert record["origin"] == "real"
    assert "scudo-matching" in record["tags"]
    assert record["id"]


def test_trajectory_to_task_record_is_deterministic():
    r1 = trajectory_to_task_record(_SAMPLE_TRAJECTORY)
    r2 = trajectory_to_task_record(_SAMPLE_TRAJECTORY)
    assert r1["id"] == r2["id"]

    other = dict(_SAMPLE_TRAJECTORY, vendor_product_ref="SPG-FX")
    assert trajectory_to_task_record(other)["id"] != r1["id"]


def test_write_tasks_file_produces_verified_payload_shape(tmp_path):
    """format/project/transcript_source/n_sessions/target_skill_path/reviewed/
    tasks are the EXACT keys make_tasks_payload() produces."""
    out_path = tmp_path / "tasks.json"
    written = write_tasks_file(
        [_SAMPLE_TRAJECTORY],
        project="scudo-matching",
        target_skill_path="skill.md",
        path=str(out_path),
    )
    assert written == str(out_path)
    payload = json.loads(out_path.read_text())

    assert payload["format"] == "skillopt_sleep.tasks.v1"
    assert payload["project"] == "scudo-matching"
    assert payload["target_skill_path"] == "skill.md"
    assert payload["reviewed"] is False
    assert len(payload["tasks"]) == 1
    assert (
        payload["tasks"][0]["attempted_solution"]
        == "jpmorgan:data:cdao:concept:equity-prices"
    )


def test_find_skillopt_sleep_binary_returns_none_when_not_on_path():
    assert find_skillopt_sleep_binary(which=lambda name: None) is None


def test_find_skillopt_sleep_binary_returns_path_when_present():
    assert (
        find_skillopt_sleep_binary(which=lambda name: "/usr/local/bin/skillopt-sleep")
        == "/usr/local/bin/skillopt-sleep"
    )


def test_run_skillopt_sleep_dry_run_raises_when_binary_unavailable():
    with pytest.raises(SkillOptSleepUnavailableError, match="skillopt-sleep"):
        run_skillopt_sleep_dry_run(
            tasks_file_path="/tmp/tasks.json",
            target_skill_path="/tmp/skill.md",
            project="scudo-matching",
            which=lambda name: None,
        )


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


_REAL_REPORT_SHAPE = {
    "night": 3,
    "accepted": True,
    "gate_action": "adopt",
    "no_edits_reason": "",
    "baseline": 0.62,
    "candidate": 0.81,
    "n_tasks": 12,
    "n_sessions": 5,
    "n_accepted_edits": 2,
    "n_rejected_edits": 1,
    "edits": [{"target": "skill.md", "op": "add", "content": "..."}],
    "rejected_edits": [],
    "notes": "",
    "staging_dir": "/tmp/staging/night-3",
    "adopted": False,
}


def test_run_skillopt_sleep_dry_run_invokes_verified_cli_flags():
    """Every flag asserted here is drawn directly from
    skillopt_sleep/__main__.py's argparse setup - dry-run subcommand,
    --tasks-file, --target-skill-path, --project, --backend mock, --json."""
    captured = {}

    def fake_runner(argv, capture_output, text, timeout, check):
        captured["argv"] = argv
        return _FakeCompletedProcess(stdout=json.dumps(_REAL_REPORT_SHAPE))

    report = run_skillopt_sleep_dry_run(
        tasks_file_path="/tmp/tasks.json",
        target_skill_path="/tmp/skill.md",
        project="scudo-matching",
        which=lambda name: "/usr/local/bin/skillopt-sleep",
        runner=fake_runner,
    )

    argv = captured["argv"]
    assert argv[0] == "/usr/local/bin/skillopt-sleep"
    assert "dry-run" in argv
    assert "--tasks-file" in argv and "/tmp/tasks.json" in argv
    assert "--target-skill-path" in argv and "/tmp/skill.md" in argv
    assert "--project" in argv and "scudo-matching" in argv
    assert "--backend" in argv and "mock" in argv
    assert "--json" in argv

    assert report["accepted"] is True
    assert report["candidate"] == 0.81
    assert report["staging_dir"] == "/tmp/staging/night-3"


def test_run_skillopt_sleep_dry_run_raises_clear_error_on_invalid_json_output():
    def fake_runner(argv, capture_output, text, timeout, check):
        return _FakeCompletedProcess(stdout="not json")

    with pytest.raises(RuntimeError, match="skillopt-sleep"):
        run_skillopt_sleep_dry_run(
            tasks_file_path="/tmp/tasks.json",
            target_skill_path="/tmp/skill.md",
            project="scudo-matching",
            which=lambda name: "/usr/local/bin/skillopt-sleep",
            runner=fake_runner,
        )


def test_adapter_module_is_never_imported_by_lambda_handler():
    """Same live-Lambda-boundary discipline as skillopt_sleep_runner.py and
    skill_gate.py — this adapter is an OFFLINE tool, never touched by the
    live request path."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scudo.lambda_handler; import sys; "
            "print('scudo.skillopt_adapter' in sys.modules)",
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
