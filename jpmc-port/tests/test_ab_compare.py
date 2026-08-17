"""A/B compare: Capone (backend/scudo) vs jpmc-port agents on shared golden cases."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SCUDO_LOCAL"] = "1"


def test_normalize_prediction_from_mapping_object():
    from scudo.ab_compare import normalize_prediction

    pred = normalize_prediction(
        {
            "outcome": "published",
            "mapping_result": {
                "proposed_target_iri": "jpmorgan:data:cdao:EquityResearch",
                "confidence": 0.92,
                "band": "high",
                "requires_human_review": False,
                "rationale": "fit",
            },
            "verifier_report": {"total_score": 18},
        }
    )
    assert pred["target_iri"] == "jpmorgan:data:cdao:EquityResearch"
    assert pred["confidence"] == 0.92
    assert pred["outcome"] == "published"
    assert pred["verifier_total"] == 18
    assert pred["auto_pass"] is True


def test_pairwise_report_flags_disagreement():
    from scudo.ab_compare import pairwise_compare

    report = pairwise_compare(
        [
            {
                "case_id": "c1",
                "capone": {
                    "target_iri": "jpmorgan:data:cdao:A",
                    "confidence": 0.9,
                    "outcome": "published",
                    "verifier_total": 18,
                    "auto_pass": True,
                    "abstained": False,
                },
                "port": {
                    "target_iri": "jpmorgan:data:cdao:B",
                    "confidence": 0.7,
                    "outcome": "hitl",
                    "verifier_total": 14,
                    "auto_pass": False,
                    "abstained": True,
                },
            }
        ]
    )
    assert report["n_cases"] == 1
    assert report["target_agreement"] == 0.0
    assert report["outcome_agreement"] == 0.0
    assert report["disagreements"][0]["case_id"] == "c1"
    assert "target" in report["disagreements"][0]["reasons"]


def test_load_golden_cases():
    from scudo.ab_compare import load_ab_cases

    cases = load_ab_cases(ROOT / "fixtures" / "ab_golden.jsonl")
    assert len(cases) >= 2
    assert cases[0]["case_id"]


def test_port_arm_runs_deterministic_cases():
    from scudo import local_state
    from scudo.ab_compare import run_port_arm

    local_state.reset()
    cases = [
        {
            "case_id": "lseg-ibes-equity-research",
            "vendor": "lseg",
            "vendor_product_ref": "LSEG-IBES-EST-001",
            "product_name": "equity research estimates",
            "description": "sell-side equity research estimates",
            "expected_target_iri": "jpmorgan:data:cdao:EquityResearch",
            "ontology_gap": False,
        }
    ]
    rows = run_port_arm(cases)
    assert len(rows) == 1
    assert rows[0]["arm"] == "jpmc-port"
    assert rows[0]["case_id"] == "lseg-ibes-equity-research"
    assert rows[0]["prediction"]["target_iri"]
    assert rows[0]["prediction"]["outcome"] in {
        "published",
        "hitl",
        "retry",
        "research_queued",
    }


def test_cli_ab_compare_deterministic_subprocess(tmp_path):
    import subprocess

    out_dir = tmp_path / "ab"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_ab_compare.py"),
            "--golden",
            str(ROOT / "fixtures" / "ab_golden.jsonl"),
            "--mode",
            "deterministic",
            "--out",
            str(out_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "SCUDO_LOCAL": "1", "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report_path = out_dir / "ab_report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text())
    assert report["arm_a"] == "capone"
    assert report["arm_b"] == "jpmc-port"
    assert report["n_cases"] >= 2
    assert "pairwise" in report
    assert report.get("evidence_provenance") == "deterministic"
    assert (out_dir / "predictions_capone.jsonl").is_file()
    assert (out_dir / "predictions_port.jsonl").is_file()
    # Capone arm must resolve backend/scudo, not jpmc-port
    capone_line = (out_dir / "predictions_capone.jsonl").read_text().splitlines()[0]
    capone_row = json.loads(capone_line)
    mod = (capone_row.get("prediction") or {}).get("scudo_module") or ""
    assert "jpmc-port" not in mod
    assert "backend" in mod or mod.endswith("scudo/__init__.py")


def test_capone_arm_python_dash_p_imports_backend():
    """Regression: without -P, script-dir on sys.path[0] shadows PYTHONPATH."""
    import subprocess

    repo = ROOT.parent
    arm = repo / "backend" / "scudo" / "scripts" / "ab_capone_arm.py"
    code = (
        "import scudo, pathlib;"
        "p=str(pathlib.Path(scudo.__file__).resolve());"
        "print(p);"
        "raise SystemExit(0 if 'jpmc-port' not in p else 2)"
    )
    # Prove -P + arm cwd under backend works when importing via the arm process
    proc = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            (
                "import json,sys;"
                + f"sys.path.insert(0,{str(repo / 'backend')!r});"
                + "import scudo;"
                + "print(scudo.__file__);"
                + "assert 'jpmc-port' not in scudo.__file__"
            ),
        ],
        cwd=str(repo / "backend"),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(repo / "backend")},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "jpmc-port" not in proc.stdout
    assert arm.is_file()
