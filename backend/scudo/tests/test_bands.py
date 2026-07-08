"""Confidence-band boundary tests.

Pins the exact-boundary behaviour of the PASS/BORDERLINE/FAIL gate so the
floating-point defect (0.75 + 0.05 == 0.8000000000000001, which would push a
score of exactly 0.80 into BORDERLINE) cannot regress.

Canonical contract (5-zone alignment, 2026-07-04):
    PASS       similarity >= 0.80
    BORDERLINE 0.70 <= similarity < 0.80
    FAIL       similarity < 0.70
"""

from __future__ import annotations

import os


def _band():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo.build_matching_graph import _band_from_score

    return _band_from_score


def test_exact_pass_boundary_is_pass():
    """A score of exactly 0.80 must be PASS, not BORDERLINE (the float bug)."""
    assert _band()(0.80) == "pass"


def test_exact_borderline_lower_boundary_is_borderline():
    """A score of exactly 0.70 must be BORDERLINE, not FAIL."""
    assert _band()(0.70) == "borderline"


def test_just_below_pass_is_borderline():
    assert _band()(0.7999) == "borderline"


def test_just_below_borderline_is_fail():
    assert _band()(0.6999) == "fail"


def test_clear_pass_and_fail():
    band = _band()
    assert band(0.95) == "pass"
    assert band(0.10) == "fail"


def test_pass_threshold_is_exactly_0_80():
    """The computed pass threshold must equal 0.80 exactly (no float drift)."""
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo.build_matching_graph import PASS_THRESHOLD, BORDERLINE_THRESHOLD

    assert PASS_THRESHOLD == 0.80, f"PASS_THRESHOLD drifted: {PASS_THRESHOLD!r}"
    assert BORDERLINE_THRESHOLD == 0.70, (
        f"BORDERLINE_THRESHOLD drifted: {BORDERLINE_THRESHOLD!r}"
    )


def test_runtime_gate_thresholds_are_exact():
    """The live matcher gate (matching.py) must compute exact band edges.

    The float defect lived at the runtime call site (``floor + half``). With
    the live 0.75/0.05 config, an exact-0.80 score must clear the PASS gate.
    """
    from scudo_mapping_mcp.matching import _gate_thresholds

    pass_t, border_t = _gate_thresholds(0.75, 0.05)
    assert pass_t == 0.80, f"runtime pass threshold drifted: {pass_t!r}"
    assert border_t == 0.70, f"runtime borderline threshold drifted: {border_t!r}"
    assert 0.80 >= pass_t, "score of exactly 0.80 must clear the PASS gate"
