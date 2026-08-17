#!/usr/bin/env python3
"""Local end-to-end: /run → publish → /project."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["SCUDO_LOCAL"] = "1"

from scudo import local_state
from scudo.handler import handle


def main() -> int:
    local_state.reset()
    run = handle(
        {
            "path": "/run",
            "httpMethod": "POST",
            "headers": {"x-api-key": "local-dev-key"},
            "body": {
                "vendor": "lseg",
                "vendor_product_ref": "LSEG-IBES-EST-001",
                "name": "equity research estimates",
                "description": "sell-side equity research estimates",
            },
        }
    )
    print(json.dumps(run, indent=2, default=str))
    if run["statusCode"] != 200 or run["body"].get("outcome") != "published":
        return 1
    proj = handle({"path": "/project", "httpMethod": "POST", "headers": {}, "body": {}})
    print(json.dumps(proj, indent=2, default=str))
    return 0 if proj["body"].get("dispatched", 0) >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
