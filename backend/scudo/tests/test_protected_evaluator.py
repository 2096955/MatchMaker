from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from scudo.scripts import protected_evaluator


def test_protected_evaluator_predictor_receives_only_blinded_fields():
    case = SimpleNamespace(
        case_id="one",
        vendor="lseg",
        vendor_product_ref="ONE",
        product_name="Prices",
        description="Equity prices",
        taxonomy_group="secret-taxonomy",
        tags=["secret-tag"],
        source="secret-source",
        split="holdout",
        expected_target_iri="secret-label",
        expected_abstain=False,
    )

    assert protected_evaluator._blinded_case(case) == {
        "case_id": "one",
        "vendor": "lseg",
        "vendor_product_ref": "ONE",
        "product_name": "Prices",
        "description": "Equity prices",
    }


def test_predictor_rows_are_strictly_normalized():
    rows = protected_evaluator._parse_prediction_rows(
        [
            {
                "case_id": "probe",
                "prediction": {
                    "target_iri": "target",
                    "confidence": 0.9,
                    "status": "mapped",
                    "band": "pass",
                    "abstained": False,
                    "auto_pass": True,
                    "rationale": "held-out result",
                },
            }
        ],
        expected_case_ids={"probe"},
    )

    assert rows["probe"].target_iri == "target"
    assert rows["probe"].confidence == 0.9


@pytest.mark.parametrize(
    "predictions",
    [
        None,
        {},
        [],
        [{"case_id": "probe"}],
        [{"case_id": "probe", "prediction": {}, "extra": True}],
        [
            {"case_id": "probe", "prediction": {"abstained": True}},
            {"case_id": "probe", "prediction": {"abstained": True}},
        ],
    ],
)
def test_predictor_rows_strictly_parse_complete_prediction_rows(predictions):
    with pytest.raises(ValueError, match="predictor"):
        protected_evaluator._parse_prediction_rows(
            predictions,
            expected_case_ids={"probe"},
        )


def test_protected_evaluator_bundle_tamper_and_traversal_rejected(tmp_path):
    root = tmp_path / "protected"
    root.mkdir()
    (root / "index.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(ValueError, match="strict slug"):
        protected_evaluator._load_allowlisted_bundle(root, "../escape")
    (root / "request.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="allowlisted"):
        protected_evaluator._load_allowlisted_bundle(root, "request")


@pytest.mark.skipif(os.name != "posix", reason="process-group assertion is POSIX-only")
def test_predictor_timeout_kills_spawned_child_without_orphan(tmp_path):
    child_pid_path = tmp_path / "child.pid"
    predictor = tmp_path / "hung_predictor.py"
    predictor.write_text(
        "import pathlib,subprocess,sys,time\n"
        "pid_path=pathlib.Path(sys.argv[1])\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "pid_path.write_text(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="timed out after 1"):
        protected_evaluator._run_predictor(
            [sys.executable, str(predictor), str(child_pid_path)],
            input_text="{}",
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=1,
        )

    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"predictor child {child_pid} remained alive after timeout")


@pytest.mark.parametrize("value", [0, 121])
def test_predictor_timeout_config_is_bounded(value):
    with pytest.raises(ValueError, match="1 and 120"):
        protected_evaluator._predictor_timeout_seconds(
            {"predictor_timeout_seconds": value}
        )
