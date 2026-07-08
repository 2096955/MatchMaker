"""P1 — confidence floor calibration harness."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scudo_mapping_mcp.config import pass_threshold
from scudo_mapping_mcp.scripts.calibrate_confidence_floor import (
    LabelledCase,
    band,
    precision_pass,
)

BACKEND = Path(__file__).resolve().parents[1]


def test_band_uses_rounded_pass_threshold():
    assert band(0.85, 0.80, 0.05) == "PASS"
    assert band(0.84, 0.80, 0.05) == "BORDERLINE"
    assert band(0.74, 0.80, 0.05) == "FAIL"


def test_precision_pass_demo_cases():
    cases = [
        LabelledCase(0.92, "positive"),
        LabelledCase(0.88, "positive"),
        LabelledCase(0.81, "positive"),
        LabelledCase(0.79, "negative"),
    ]
    p = precision_pass(cases, floor=0.80, half=0.05)
    assert p == 1.0
    assert pass_threshold(0.80, 0.05) == 0.85


def test_calibration_script_runs():
    import os

    script = BACKEND / "scripts" / "calibrate_confidence_floor.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND.parent)
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(BACKEND.parent),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pass_precision=" in proc.stdout
    assert "default_floor=0.75" in proc.stdout
