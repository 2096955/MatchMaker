from __future__ import annotations

import json

from scudo.scripts.evaluate_matching_golden import main


def test_golden_evaluation_cli_accepts_engine_and_agent_rows(tmp_path, capsys):
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "engine",
                        "vendor": "lseg",
                        "vendor_product_ref": "LSEG-1",
                        "expected_target_iri": "jpmorgan:data:cdao:EquityPrices",
                        "split": "holdout",
                    }
                ),
                json.dumps(
                    {
                        "case_id": "agent",
                        "vendor": "ice",
                        "vendor_product_ref": "ICE-1",
                        "expected_abstain": True,
                        "split": "holdout",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "engine",
                        "result": {
                            "mapped_node_iri": "jpmorgan:data:cdao:EquityPrices",
                            "confidence": 0.92,
                            "status": "auto_mapped",
                        },
                    }
                ),
                json.dumps(
                    {
                        "case_id": "agent",
                        "result": {
                            "proposed_target_iri": "jpmorgan:data:cdao:Prices",
                            "confidence": 0.55,
                            "requires_human_review": True,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--golden-set",
            str(golden),
            "--golden-version",
            "golden-1",
            "--predictions",
            str(predictions),
            "--candidate-version",
            "candidate-1",
            "--min-exact-match-rate",
            "1.0",
            "--max-brier-score",
            "1.0",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is True
    assert output["metrics"]["false_auto_pass_rate"] == 0.0
    assert output["by_vendor"]["ice"]["abstention_recall"] == 1.0


def test_golden_evaluation_cli_returns_distinct_code_for_invalid_input(
    tmp_path, capsys
):
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-1",
                "expected_target_iri": "jpmorgan:data:cdao:EquityPrices",
                "split": "holdout",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("{}\n", encoding="utf-8")

    exit_code = main(
        [
            "--golden-set",
            str(golden),
            "--golden-version",
            "golden-1",
            "--predictions",
            str(predictions),
            "--candidate-version",
            "candidate-1",
        ]
    )

    assert exit_code == 2
    assert "ERROR" in capsys.readouterr().err
