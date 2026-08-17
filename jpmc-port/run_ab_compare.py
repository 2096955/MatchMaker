#!/usr/bin/env python3
"""A/B compare Capone (backend/scudo) vs jpmc-port agents on a shared golden set.

Arms run in separate subprocesses because both packages are named ``scudo``.

The Capone arm MUST be launched with ``python -P`` from
``backend/scudo/scripts/ab_capone_arm.py``. Without ``-P``, Python puts the
script directory on ``sys.path[0]`` ahead of ``PYTHONPATH``, and a script living
under ``jpmc-port/`` silently imports ``jpmc-port/scudo`` (port-vs-port).

Usage:
  cd jpmc-port
  SCUDO_LOCAL=1 python run_ab_compare.py \\
    --golden fixtures/ab_golden.jsonl \\
    --mode deterministic \\
    --out /tmp/scudo-ab

  # Unstubbed anthropic A/B via Anthropic-compatible endpoint (shim / Bedrock proxy):
  # model id is as-configured; capture echoed_model via run_opus_smoke for identity claims
  unset SCUDO_LOCAL
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
  export ANTHROPIC_API_KEY="$(tr -d '[:space:]' < ~/.codex/shim-router/router.key)"
  python run_ab_compare.py --golden fixtures/ab_golden.jsonl --mode anthropic --out /tmp/scudo-ab-opus

  # Native AWS Bedrock (requires IAM credentials):
  unset SCUDO_LOCAL
  python run_ab_compare.py --golden fixtures/ab_golden.jsonl --mode bedrock --out /tmp/scudo-ab-bedrock
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
BACKEND = REPO / "backend"
CAPONE_ARM = BACKEND / "scudo" / "scripts" / "ab_capone_arm.py"


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capone vs jpmc-port agent A/B compare")
    p.add_argument("--golden", type=Path, default=ROOT / "fixtures" / "ab_golden.jsonl")
    p.add_argument(
        "--mode",
        choices=("deterministic", "bedrock", "anthropic"),
        default="deterministic",
    )
    p.add_argument("--out", type=Path, required=True)
    return p.parse_args()


def _run_capone(cases: list[dict], mode: str) -> list[dict]:
    if not CAPONE_ARM.is_file():
        raise RuntimeError(f"Capone arm missing: {CAPONE_ARM}")
    env = os.environ.copy()
    # Only backend on PYTHONPATH; -P prevents script-dir shadowing.
    env["PYTHONPATH"] = str(BACKEND)
    env.setdefault("SCUDO_RDF_BACKEND", "fake")
    if mode == "deterministic":
        env["SCUDO_LOCAL"] = "1"
    else:
        env.pop("SCUDO_LOCAL", None)
    # python -P: do not prepend the script's directory to sys.path
    proc = subprocess.run(
        [sys.executable, "-P", str(CAPONE_ARM)],
        input=json.dumps({"cases": cases, "mode": mode}),
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"capone arm failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    payload = json.loads(proc.stdout)
    # Hard proof the Capone arm did not import jpmc-port
    for row in payload.get("rows") or []:
        mod = (row.get("prediction") or {}).get("scudo_module") or ""
        if "jpmc-port" in mod:
            raise RuntimeError(
                f"Capone arm imported jpmc-port scudo ({mod}). "
                "Harness is broken — refuse to report agreement."
            )
    return payload["rows"]


def _run_port(cases: list[dict], mode: str) -> list[dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    if mode == "deterministic":
        env["SCUDO_LOCAL"] = "1"
        env["SCUDO_AGENT_MODE"] = "deterministic"
    else:
        env.pop("SCUDO_LOCAL", None)
        env["SCUDO_AGENT_MODE"] = mode
    # Redirect stdout→stderr during the arm so Strands tool chatter cannot
    # corrupt the JSON payload the harness parses.
    code = (
        "import json,sys;"
        f"sys.path.insert(0,{str(ROOT)!r});"
        "from scudo.ab_compare import run_port_arm;"
        f"mode={mode!r};"
        "cases=json.load(sys.stdin);"
        "out=sys.stdout; sys.stdout=sys.stderr;"
        "rows=run_port_arm(cases, mode=mode);"
        "sys.stdout=out;"
        "print(json.dumps({'rows': rows}, default=str))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"port arm failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return json.loads(proc.stdout)["rows"]


def main() -> int:
    args = _parse()
    sys.path.insert(0, str(ROOT))
    from scudo.ab_compare import (
        load_ab_cases,
        merge_ab_report,
        write_predictions_jsonl,
    )

    if not BACKEND.is_dir():
        raise SystemExit(f"Capone backend not found at {BACKEND}")

    cases = load_ab_cases(args.golden)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"A/B mode={args.mode} cases={len(cases)}", flush=True)
    print(f"Capone arm: python -P {CAPONE_ARM}", flush=True)
    print("Running Capone arm…", flush=True)
    capone_rows = _run_capone(cases, args.mode)
    print("Running jpmc-port arm…", flush=True)
    port_rows = _run_port(cases, args.mode)

    write_predictions_jsonl(args.out / "predictions_capone.jsonl", capone_rows)
    write_predictions_jsonl(args.out / "predictions_port.jsonl", port_rows)

    report = merge_ab_report(
        cases=cases,
        capone_rows=capone_rows,
        port_rows=port_rows,
        mode=args.mode,
    )
    report_path = args.out / "ab_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["pairwise"], indent=2), flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
