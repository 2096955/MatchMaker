"""Route-level validation of the per-request gate-window override (Phase 2c).

``POST /api/mapping/map`` and ``POST /api/mapping/agent/run`` accept an
optional band-window override (``confidence_floor`` + ``borderline_half_width``).
The two keys travel together and must satisfy:

    0 <= floor - half < floor + half <= 1

A malformed window is a 400 BEFORE the matcher runs; a valid one (or its
absence) is accepted. Presence is keyed off the JSON KEY, not the value, so an
explicit ``null`` is rejected rather than silently treated as "use defaults".
This file pins the validation contract via the Flask test client (same fixture
pattern as tests/test_mapping_graph_route.py).

Run PER-FILE:

    PYTHONPATH=. python3 -m pytest \
        scudo/tests/test_threshold_route_validation.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")
os.environ.setdefault("SCUDO_DENSE_BACKEND", "jaro_winkler")  # no network

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import app  # noqa: E402

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}


def _base_body(**extra):
    body = {
        "vendor": "LSEG",
        "product_id": "LSEG-EQ-PX",
        "name": "Global Equity Prices",
    }
    body.update(extra)
    return body


# ──────────────────────────────────────────────────────────────────────────
# /mapping/map — invalid windows → 400
# ──────────────────────────────────────────────────────────────────────────
def test_map_bad_band_pass_cut_below_fail_cut_is_400():
    """Force the window to invert with a negative half (half=-0.05) → 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.80, borderline_half_width=-0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_bad_band_pass_cut_over_one_is_400():
    """floor + half > 1 (PASS edge above 1.0) → 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.98, borderline_half_width=0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_bad_band_zero_half_is_400():
    """half <= 0 makes the borderline band empty → 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.80, borderline_half_width=0.0),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_bad_band_fail_cut_negative_is_400():
    """floor - half < 0 (FAIL edge below zero) → 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.03, borderline_half_width=0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_lone_floor_is_400():
    """One key without the other is a 400 (they travel together)."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.80),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_lone_half_is_400():
    """The other lone key (half present, floor absent) is also a 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(borderline_half_width=0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_both_null_is_400():
    """Explicit JSON null for BOTH keys is present-but-invalid → 400, NOT
    silently treated as 'use defaults' (the presence-vs-value fix). A naive
    body.get(...) would return None for both and wrongly accept it.
    """
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=None, borderline_half_width=None),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_floor_null_half_valid_is_400():
    """One key explicit-null, the other a valid number → 400."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=None, borderline_half_width=0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_map_non_numeric_band_is_400():
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor="high", borderline_half_width=0.05),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


# ──────────────────────────────────────────────────────────────────────────
# /mapping/map — valid / absent windows → not 400
# ──────────────────────────────────────────────────────────────────────────
def test_map_valid_band_is_not_400():
    """A well-formed window is accepted (validation passes; the matcher runs)."""
    r = client.post(
        "/api/mapping/map",
        headers=AUTH,
        json=_base_body(confidence_floor=0.75, borderline_half_width=0.05),
    )
    assert r.status_code != 400, r.get_data(as_text=True)


def test_map_no_band_is_not_400():
    """Omitting the window entirely is the default path — never a 400."""
    r = client.post("/api/mapping/map", headers=AUTH, json=_base_body())
    assert r.status_code != 400, r.get_data(as_text=True)


# ──────────────────────────────────────────────────────────────────────────
# /mapping/agent/run
# ──────────────────────────────────────────────────────────────────────────
def test_agent_run_bad_band_is_400():
    r = client.post(
        "/api/mapping/agent/run",
        headers=AUTH,
        json=_base_body(confidence_floor=0.80, borderline_half_width=0.0),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_agent_run_both_null_is_400():
    """Explicit JSON null for both keys on the agent path → 400 (presence
    vs value), never silently 'use defaults'.
    """
    r = client.post(
        "/api/mapping/agent/run",
        headers=AUTH,
        json=_base_body(confidence_floor=None, borderline_half_width=None),
    )
    assert r.status_code == 400, r.get_data(as_text=True)


def test_agent_run_valid_band_is_not_400():
    r = client.post(
        "/api/mapping/agent/run",
        headers=AUTH,
        json=_base_body(confidence_floor=0.75, borderline_half_width=0.05),
    )
    assert r.status_code != 400, r.get_data(as_text=True)
