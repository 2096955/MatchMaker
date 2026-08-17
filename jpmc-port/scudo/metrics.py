"""CloudWatch EMF — no-op outside Lambda."""

from __future__ import annotations

import json
import os
import time

_NAMESPACE = "SCUDO"


def emit(
    metric: str, value: float = 1, *, unit: str = "Count", dims: dict | None = None
) -> None:
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return
    dims = dims or {}
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": _NAMESPACE,
                            "Dimensions": [list(dims.keys())] if dims else [[]],
                            "Metrics": [{"Name": metric, "Unit": unit}],
                        }
                    ],
                },
                metric: value,
                **dims,
            }
        )
    )
