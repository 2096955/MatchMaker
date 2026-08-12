from __future__ import annotations

import json
import sys

import pytest

from scudo.protected_evaluator_adapter import run_protected_evaluator_command
from scudo.skill_optimizer_adapter import run_skill_optimizer_command


def test_protected_evaluator_adapter_excludes_sensitive_promoter_environment(
    monkeypatch,
):
    monkeypatch.setenv("SCUDO_EVALUATION_PRIVATE_KEY", "private")
    monkeypatch.setenv("SCUDO_PROTECTED_EVALUATION_ROOT", "/secret/root")
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "aurora-secret")
    captured = {}

    def runner(argv, **kwargs):
        captured.update(kwargs)
        return type("Result", (), {"stdout": "{}"})()

    with pytest.raises(RuntimeError, match="invalid SignedEvaluationEnvelope"):
        run_protected_evaluator_command(
            {
                "candidate_content": "candidate",
                "artifact_id": "matching-skill-2",
                "artifact_version": 2,
                "artifact_kind": "matching_skill",
                "candidate_version": "candidate-2",
                "evaluation_request_id": "request-1",
            },
            command="python evaluator.py",
            runner=runner,
        )

    assert set(captured["env"]) <= {"PATH", "PYTHONPATH"}


def test_protected_evaluator_adapter_rejects_malformed_output():
    def runner(argv, **kwargs):
        return type("Result", (), {"stdout": "not-json"})()

    with pytest.raises(RuntimeError, match="invalid SignedEvaluationEnvelope"):
        run_protected_evaluator_command(
            {
                "candidate_content": "candidate",
                "artifact_id": "matching-skill-2",
                "artifact_version": 2,
                "artifact_kind": "matching_skill",
                "candidate_version": "candidate-2",
                "evaluation_request_id": "request-1",
            },
            command="python evaluator.py",
            runner=runner,
        )


@pytest.mark.parametrize(
    "command",
    [
        "python backend/scudo/scripts/protected_evaluator.py",
        "python protected_evaluator.py",
        "python -m scudo.scripts.protected_evaluator",
    ],
)
def test_protected_evaluator_adapter_rejects_direct_bundled_command(command):
    with pytest.raises(RuntimeError, match="independently provisioned.*forbidden"):
        run_protected_evaluator_command(
            {
                "candidate_content": "candidate",
                "artifact_id": "matching-skill-2",
                "artifact_version": 2,
                "artifact_kind": "matching_skill",
                "candidate_version": "candidate-2",
                "evaluation_request_id": "request-1",
            },
            command=command,
        )


def test_optimizer_adapter_uses_minimal_environment(monkeypatch):
    monkeypatch.setenv("SCUDO_EVALUATION_PRIVATE_KEY", "private")
    monkeypatch.setenv("SCUDO_EVALUATION_PUBLIC_KEY", "public")
    monkeypatch.setenv("SCUDO_SKILL_PROMOTION_KEY", "promotion")
    monkeypatch.setenv("SCUDO_AURORA_SECRET_ARN", "aurora")
    monkeypatch.setenv("SCUDO_PROTECTED_EVALUATION_ROOT", "/root")
    captured = {}

    def runner(argv, **kwargs):
        captured.update(kwargs)
        return type(
            "Result",
            (),
            {"stdout": json.dumps({"candidate_content": "candidate"})},
        )()

    assert (
        run_skill_optimizer_command(
            {"trajectories": []},
            command="python optimizer.py",
            runner=runner,
        )
        == "candidate"
    )
    assert set(captured["env"]) <= {"PATH", "PYTHONPATH"}


@pytest.mark.parametrize(
    ("call", "kwargs", "message"),
    [
        (
            run_skill_optimizer_command,
            {"request": {"trajectories": []}, "command": ""},
            "optimizer timed out",
        ),
        (
            run_protected_evaluator_command,
            {
                "request": {
                    "candidate_content": "candidate",
                    "artifact_id": "matching-skill-2",
                    "artifact_version": 2,
                    "artifact_kind": "matching_skill",
                    "candidate_version": "candidate-2",
                    "evaluation_request_id": "request-1",
                },
                "command": "",
            },
            "protected evaluator timed out",
        ),
    ],
)
def test_adapter_timeout_terminates_process_group(tmp_path, call, kwargs, message):
    script = tmp_path / "hang.py"
    script.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    kwargs["command"] = f"{sys.executable} {script}"

    with pytest.raises(RuntimeError, match=message):
        call(**kwargs, timeout=0.1)
