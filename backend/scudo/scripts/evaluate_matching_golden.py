"""Evaluate saved agent or matcher outputs against a versioned golden set.

Input predictions are JSONL rows with ``case_id`` and either a ``result``
object or result fields at the top level. The result may be the agent
``MappingResult`` shape or the deterministic matcher ``MappingResult`` shape.

This script is report-only. It never writes Aurora, changes thresholds, or
promotes an artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scudo.matching_self_improvement import (
    EvaluationPolicy,
    evaluate_golden_set,
    load_golden_set,
)


def _load_predictions(path: str | Path) -> dict[str, dict]:
    predictions: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid prediction JSON at {path}:{line_no}: {exc}"
                ) from exc
            case_id = row.get("case_id")
            if not case_id:
                raise ValueError(f"prediction row {path}:{line_no} has no case_id")
            if case_id in predictions:
                raise ValueError(f"duplicate prediction case_id: {case_id}")
            result = row.get("result")
            predictions[case_id] = result if isinstance(result, dict) else row
    return predictions


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate agent/matcher JSONL results on a golden-set split."
    )
    parser.add_argument("--golden-set", required=True)
    parser.add_argument("--golden-version", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--split", choices=("holdout", "adversarial"), default="holdout")
    parser.add_argument("--min-cases", type=int, default=1)
    parser.add_argument("--min-exact-match-rate", type=float, default=0.95)
    parser.add_argument("--min-abstention-recall", type=float, default=1.0)
    parser.add_argument("--max-false-auto-pass-rate", type=float, default=0.0)
    parser.add_argument("--max-brier-score", type=float, default=0.10)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        golden = load_golden_set(args.golden_set, version=args.golden_version)
        predictions = _load_predictions(args.predictions)
        selected = golden.cases_for_split(args.split)
        missing = [case.case_id for case in selected if case.case_id not in predictions]
        if missing:
            raise ValueError(
                f"predictions are missing {args.split} case(s): {', '.join(missing)}"
            )

        report = evaluate_golden_set(
            golden,
            lambda case: predictions[case.case_id],
            candidate_version=args.candidate_version,
            split=args.split,
            policy=EvaluationPolicy(
                min_cases=args.min_cases,
                min_exact_match_rate=args.min_exact_match_rate,
                min_abstention_recall=args.min_abstention_recall,
                max_false_auto_pass_rate=args.max_false_auto_pass_rate,
                max_brier_score=args.max_brier_score,
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
