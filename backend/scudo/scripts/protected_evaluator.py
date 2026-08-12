"""Standalone Ed25519 protected evaluator: JSON stdin -> signed envelope stdout."""

from __future__ import annotations

import json
import os
import sys
import hashlib
import shlex

from pydantic import BaseModel, ConfigDict

from scudo.matching_self_improvement import (
    EvaluationPolicy,
    GoldenSet,
    MatchingPrediction,
    TrustedEvaluationEvidence,
    trusted_evidence_for,
    issue_signed_evaluation_envelope,
)
from scudo.subprocess_utils import run_text_process


class _PredictorOutputRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    prediction: MatchingPrediction


def _candidate_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _blinded_case(case) -> dict:
    return {
        "case_id": case.case_id,
        "vendor": case.vendor,
        "vendor_product_ref": case.vendor_product_ref,
        "product_name": case.product_name,
        "description": case.description,
    }


def _parse_prediction_rows(
    predictions: list,
    *,
    expected_case_ids: set[str],
) -> dict[str, MatchingPrediction]:
    try:
        if not isinstance(predictions, list) or not predictions:
            raise ValueError
        rows = [_PredictorOutputRow.model_validate(row) for row in predictions]
    except Exception as exc:
        raise ValueError("predictor output rows are malformed") from exc
    case_ids = [row.case_id for row in rows]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("predictor output case IDs must be unique")
    if set(case_ids) != expected_case_ids:
        raise ValueError("predictor case IDs must exactly match blinded cases")
    return {row.case_id: row.prediction for row in rows}


def _load_allowlisted_bundle(root_path, request_id: str) -> dict:
    if (
        not isinstance(request_id, str)
        or not request_id
        or not all(char.isalnum() or char in {"-", "_"} for char in request_id)
    ):
        raise ValueError("evaluation_request_id must be a strict slug")
    root = os.path.realpath(str(root_path))
    bundle_path = os.path.realpath(os.path.join(root, f"{request_id}.json"))
    if os.path.dirname(bundle_path) != root:
        raise ValueError("evaluation request escapes protected root")
    with open(os.path.join(root, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    with open(bundle_path, "rb") as handle:
        raw_bundle = handle.read()
    if hashlib.sha256(raw_bundle).hexdigest() != index.get(request_id):
        raise RuntimeError("protected evaluation bundle hash is not allowlisted")
    return json.loads(raw_bundle)


def _predictor_timeout_seconds(bundle: dict) -> int:
    timeout = int(bundle.get("predictor_timeout_seconds", 30))
    if not 1 <= timeout <= 120:
        raise ValueError("predictor_timeout_seconds must be between 1 and 120")
    return timeout


def _run_predictor(
    argv: list[str],
    *,
    input_text: str,
    env: dict[str, str],
    timeout_seconds: int,
):
    return run_text_process(
        argv,
        input_text=input_text,
        env=env,
        timeout=timeout_seconds,
        timeout_label="protected predictor",
    )


def main() -> int:
    request = json.load(sys.stdin)
    request_id = request["evaluation_request_id"]
    root = os.environ.get("SCUDO_PROTECTED_EVALUATION_ROOT")
    if not root:
        raise RuntimeError("SCUDO_PROTECTED_EVALUATION_ROOT is required")
    bundle = _load_allowlisted_bundle(root, request_id)
    repeat_count = int(bundle.get("repeat_count", 0))
    if repeat_count < 2:
        raise ValueError("protected evaluator repeat_count must be at least 2")
    predictor_command = bundle.get("predictor_command")
    if not isinstance(predictor_command, str) or not predictor_command.strip():
        raise ValueError("protected bundle requires predictor_command")
    predictor_timeout_seconds = _predictor_timeout_seconds(bundle)

    def build_evidence(split: str) -> TrustedEvaluationEvidence:
        golden = GoldenSet.model_validate(bundle["golden_set"])
        cases = golden.cases_for_split(split)
        blinded_cases = [_blinded_case(case) for case in cases]
        runs = []
        predictor_env = {
            key: value
            for key, value in {
                "PATH": os.environ.get("PATH"),
                "PYTHONPATH": os.environ.get("PYTHONPATH"),
            }.items()
            if value is not None
        }
        for run_index in range(repeat_count):
            result = _run_predictor(
                shlex.split(predictor_command),
                input_text=json.dumps(
                    {
                        "candidate_content": request["candidate_content"],
                        "run_index": run_index,
                        "cases": blinded_cases,
                    },
                    sort_keys=True,
                ),
                env=predictor_env,
                timeout_seconds=predictor_timeout_seconds,
            )
            output = json.loads(result.stdout)
            if output.get("candidate_content_hash") != _candidate_hash(
                request["candidate_content"]
            ):
                raise ValueError("predictor did not echo candidate_content_hash")
            expected = {case.case_id for case in cases}
            by_case = _parse_prediction_rows(
                output.get("predictions"),
                expected_case_ids=expected,
            )
            runs.append(by_case)
        return trusted_evidence_for(
            golden,
            policy=EvaluationPolicy.model_validate(bundle["policy"]),
            split=split,
            prediction_runs=runs,
        )

    holdout_evidence = build_evidence("holdout")
    adversarial_evidence = build_evidence("adversarial")
    private_key = os.environ.get("SCUDO_EVALUATION_PRIVATE_KEY")
    if not private_key:
        raise RuntimeError("SCUDO_EVALUATION_PRIVATE_KEY is required")
    envelope = issue_signed_evaluation_envelope(
        candidate_content=request["candidate_content"],
        artifact_id=request["artifact_id"],
        artifact_version=int(request["artifact_version"]),
        artifact_kind=request.get("artifact_kind", "matching_skill"),
        trusted_evidence=holdout_evidence,
        adversarial_evidence=adversarial_evidence,
        candidate_version=request.get(
            "candidate_version", f"candidate-{request['artifact_version']}"
        ),
        baseline_version=request.get("baseline_version"),
        evaluator_id=bundle["evaluator_id"],
        evaluator_version=bundle["evaluator_version"],
        private_key_pem=private_key,
    )
    print(envelope.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
