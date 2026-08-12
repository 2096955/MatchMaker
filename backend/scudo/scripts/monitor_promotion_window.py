"""Offline entrypoint for an externally scheduled signed monitoring window."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..promotion_monitor import monitor_promotion_window


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope", type=Path)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--public-key-file", type=Path, required=True)
    args = parser.parse_args()
    os.environ["SCUDO_MONITORING_AUDIENCE"] = args.audience
    os.environ["SCUDO_MONITORING_DEPLOYMENT_ID"] = args.deployment_id
    os.environ["SCUDO_MONITORING_KEY_ID"] = args.key_id
    os.environ["SCUDO_MONITORING_PUBLIC_KEY"] = args.public_key_file.read_text(
        encoding="utf-8"
    )
    outcome = monitor_promotion_window(
        envelope=json.loads(args.envelope.read_text(encoding="utf-8"))
    )
    print(outcome.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
