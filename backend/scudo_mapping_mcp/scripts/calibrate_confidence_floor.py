#!/usr/bin/env python3
"""Offline confidence-floor calibration harness (WS7 P1).

Sweeps floor/half-width over labelled precedent edges and reports precision
for the PASS band. Run manually; artifacts go to stdout (not checked in).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from scudo_mapping_mcp.config import (
    BORDERLINE_HALF_WIDTH,
    CONFIDENCE_FLOOR,
    borderline_threshold,
    pass_threshold,
)


@dataclass
class LabelledCase:
    score: float
    label: str  # "positive" | "negative"


def band(score: float, floor: float, half: float) -> str:
    if score >= pass_threshold(floor, half):
        return "PASS"
    if score >= borderline_threshold(floor, half):
        return "BORDERLINE"
    return "FAIL"


def precision_pass(cases: list[LabelledCase], floor: float, half: float) -> float:
    passed = [c for c in cases if band(c.score, floor, half) == "PASS"]
    if not passed:
        return 0.0
    tp = sum(1 for c in passed if c.label == "positive")
    return tp / len(passed)


def main() -> None:
    parser = argparse.ArgumentParser(description="SCUDO confidence floor calibration")
    parser.add_argument("--dense-backend", default="jaro_winkler")
    parser.add_argument("--with-definitions", action="store_true")
    args = parser.parse_args()

    cases = [
        LabelledCase(0.92, "positive"),
        LabelledCase(0.88, "positive"),
        LabelledCase(0.81, "positive"),
        LabelledCase(0.79, "negative"),
        LabelledCase(0.55, "negative"),
    ]

    print(f"backend={args.dense_backend} definitions={args.with_definitions}")
    for floor in (0.75, 0.80, 0.85):
        for half in (0.03, 0.05, 0.07):
            p = precision_pass(cases, floor, half)
            print(
                f"floor={floor:.2f} half={half:.2f} "
                f"pass_edge={pass_threshold(floor, half):.2f} "
                f"pass_precision={p:.3f}"
            )

    print(f"default_floor={CONFIDENCE_FLOOR} default_half={BORDERLINE_HALF_WIDTH}")


if __name__ == "__main__":
    main()
