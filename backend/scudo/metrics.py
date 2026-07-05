"""Structured CloudWatch EMF metric emission.

Structured stdout ONLY — the Embedded Metric Format is parsed by CloudWatch when
the code runs in Lambda, and JPM's observability framework (Datadog → Dynatrace)
forwards from CloudWatch. No new deps, no Grafana, no OTel. ``emit`` is a no-op
unless ``AWS_LAMBDA_FUNCTION_NAME`` is set, so local runs, tests and the console
stay silent. Counters only — keep it cheap.
"""

from __future__ import annotations

import json
import os
import time

_NAMESPACE = "SCUDO"


def emit(
    metric: str, value: float = 1, *, unit: str = "Count", dims: dict | None = None
) -> None:
    """Emit one CloudWatch EMF metric line to stdout. No-op outside Lambda."""
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return
    dims = dims or {}
    line = {
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
    print(json.dumps(line))
