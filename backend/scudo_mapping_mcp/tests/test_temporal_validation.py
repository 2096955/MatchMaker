"""Temporal-compatibility validation — flag-gated date/period comparator.

Closes a hand-verified ABSENCE: before this file there was NO temporal field
on ``VendorProductRef`` and NO date/period/vintage/as-of comparator anywhere
in ``matching.py`` / ``validations.py`` / ``models.py``. Two vendor products
identical in name but covering different periods (2015-2018 archive vs
2024-2025 current) were indistinguishable to the engine. DCAT temporal
coverage IS extracted upstream (``models_dcat.DcatDataset.temporal_coverage``)
and was dropped on the floor.

Behind its OWN opt-in flag ``SCUDO_TEMPORAL_VALIDATION`` (default off,
call-time read — measured-rollout discipline identical to
``SCUDO_INPUT_COMPLETENESS_VALIDATION`` / ``SCUDO_ASSET_CLASS_VALIDATION``):
with the flag off, ``run_validations`` output is byte-identical to today.

Semantics (the hard part) mirror ``data_class_match``'s absence handling:
a MISSING or UNPARSEABLE temporal declaration on EITHER side is a PASS,
never a fail. Only a positive, deterministic disagreement — both sides
declare parseable coverage AND the intervals are disjoint — is a required
FAIL. A missing date must never fail a match.
"""

from __future__ import annotations

import os

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")

from datetime import date

import pytest

from scudo_mapping_mcp import config as cfg
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.validations import (
    _intervals_overlap,
    _parse_temporal_interval,
    required_failures,
    run_validations,
)

FLAG = "SCUDO_TEMPORAL_VALIDATION"
COMPLETENESS_FLAG = "SCUDO_INPUT_COMPLETENESS_VALIDATION"

# The pre-existing validation list, in stable order. Flag off MUST reproduce
# exactly this (existing artifacts and tests pin it).
BASELINE_NAMES = [
    "scope_compatible",
    "identifier_resolves",
    "data_class_match",
    "name_length",
    "description_length",
]


@pytest.fixture(autouse=True)
def _isolate_flags(monkeypatch):
    """Every test in this file starts from BOTH optional flags unset, so a
    stray env var from the wider suite can never make an assertion vacuous."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(COMPLETENESS_FLAG, raising=False)


def _node(temporal: str | None = None) -> TaxonomyNode:
    return TaxonomyNode(
        iri="cdao:eq-prices",
        label="Equity Prices",
        temporal_coverage=temporal,
    )


def _ref(temporal: str | None = None, raw: dict | None = None) -> VendorProductRef:
    return VendorProductRef(
        vendor="LSEG",
        product_id="EQ-RT-001",
        name="Equity Prices Real Time",
        description="Real-time equity pricing feed.",
        temporal_coverage=temporal,
        raw=raw or {},
    )


def _run(ref: VendorProductRef, node: TaxonomyNode | None, **kwargs):
    return run_validations(
        ref, node, scope_allowed=True, has_store_node=node is not None, **kwargs
    )


def _temporal(validations):
    return [v for v in validations if v.name == "temporal_compatible"]


# ── flag OFF (default): output byte-identical to today ─────────────────────


def test_flag_unset_emits_no_temporal_entry():
    """Even with a MAXIMALLY disagreeing pair on both sides, the flag-off
    list is exactly the pre-existing five entries."""
    results = _run(_ref("2015-01-01/2018-12-31"), _node("2024-01-01/2025-12-31"))
    assert [v.name for v in results] == BASELINE_NAMES
    assert _temporal(results) == []


def test_flag_zero_emits_no_temporal_entry(monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    results = _run(_ref("2015-01-01/2018-12-31"), _node("2024-01-01/2025-12-31"))
    assert [v.name for v in results] == BASELINE_NAMES
    assert _temporal(results) == []


def test_flag_off_disjoint_pair_produces_no_required_failure():
    """The default-safe guarantee stated as the matcher sees it: with the
    flag off a disjoint pair blocks nothing."""
    results = _run(_ref("2015"), _node("2024"))
    assert required_failures(results) == []


def test_flag_defaults_off_in_config(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert cfg.env_temporal_validation_enabled() is False
    monkeypatch.setenv(FLAG, "on")
    assert cfg.env_temporal_validation_enabled() is True


def test_temporal_field_is_optional_on_both_models():
    """Existing construction sites (137 of them) must keep working untouched:
    the field is Optional with a None default on BOTH sides."""
    ref = VendorProductRef(vendor="LSEG", product_id="EQ-1")
    assert ref.temporal_coverage is None
    node = TaxonomyNode(iri="cdao:x", label="X")
    assert node.temporal_coverage is None


# ── flag ON: absence is ALWAYS a pass (the core safety semantic) ───────────


def test_both_sides_silent_passes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref(None), _node(None))
    (v,) = _temporal(results)
    assert v.status == "pass"
    assert v.required is True
    assert "pass-by-default" in v.detail


def test_vendor_only_declares_passes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref("2019-01-01/2020-12-31"), _node(None)))
    assert v.status == "pass"
    assert "pass-by-default" in v.detail


def test_node_only_declares_passes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(None), _node("2019-01-01/2020-12-31")))
    assert v.status == "pass"
    assert "pass-by-default" in v.detail


@pytest.mark.parametrize(
    "junk", ["", "   ", "historical", "ongoing", "real-time", "N/A", "daily"]
)
def test_unparseable_vendor_text_passes(monkeypatch, junk):
    """Free text that carries no interval must never fail a match."""
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(junk), _node("2019-01-01/2020-12-31")))
    assert v.status == "pass"
    assert "pass-by-default" in v.detail


def test_unparseable_on_both_sides_passes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref("whenever"), _node("as available")))
    assert v.status == "pass"


def test_no_candidate_node_passes(monkeypatch):
    """node=None (no-candidates / out-of-scope paths) must not fail here —
    identifier_resolves already owns that failure."""
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref("2019"), None))
    assert v.status == "pass"
    assert "pass-by-default" in v.detail


# ── flag ON: positive agreement passes ─────────────────────────────────────


@pytest.mark.parametrize(
    "vendor,node",
    [
        ("2019-01-01/2020-12-31", "2019-01-01/2020-12-31"),  # identical
        ("2019-01-01/2020-12-31", "2020-06-01/2022-12-31"),  # partial overlap
        ("2019-01-01/2025-12-31", "2021-01-01/2021-12-31"),  # contained
        ("2019-01-01/2019-12-31", "2019-12-31/2020-12-31"),  # shared endpoint
        ("2019", "2019"),  # bare years
        ("2019-2021", "2020"),  # year range vs year
        ("2019-01-01/..", "2030-01-01/2031-12-31"),  # open upper bound
        ("../2020-12-31", "1999-01-01/2001-12-31"),  # open lower bound
        ("2020-06", "2020-06-15"),  # year-month vs exact day
    ],
)
def test_overlapping_declarations_pass(monkeypatch, vendor, node):
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(vendor), _node(node)))
    assert v.status == "pass", f"{vendor!r} vs {node!r} should overlap"
    assert v.detail == ""


# ── flag ON: positive disagreement is a REQUIRED FAIL ─────────────────────


@pytest.mark.parametrize(
    "vendor,node",
    [
        ("2015-01-01/2018-12-31", "2024-01-01/2025-12-31"),
        ("2019", "2024"),
        ("2015-2018", "2019-2021"),
        ("2020-01", "2020-03"),
        ("2020-06-15", "2020-06-16"),
        ("../2018-12-31", "2019-01-01/.."),
    ],
)
def test_disjoint_declarations_required_fail(monkeypatch, vendor, node):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref(vendor), _node(node))
    (v,) = _temporal(results)
    assert v.status == "fail", f"{vendor!r} vs {node!r} should be disjoint"
    assert v.required is True
    # Detail must quote BOTH declarations so a reviewer can adjudicate.
    assert vendor in v.detail
    assert node in v.detail
    # And the matcher must see it as a blocker.
    assert [f.name for f in required_failures(results)] == ["temporal_compatible"]


# ── flag ON: vendor raw-row fallback (mirrors _vendor_data_class) ──────────


@pytest.mark.parametrize(
    "key",
    [
        "temporal_coverage",
        "temporalCoverage",
        "coverage_period",
        "coveragePeriod",
    ],
)
def test_vendor_raw_row_coverage_keys_are_read(monkeypatch, key):
    """COVERAGE keys describe the period the data spans, so a disjoint one is
    a real disagreement and must fail."""
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(None, raw={key: "2015"}), _node("2024")))
    assert v.status == "fail"
    assert "2015" in v.detail


@pytest.mark.parametrize("key", ["vintage", "as_of", "asOf", "asof"])
def test_vendor_raw_row_snapshot_keys_are_not_coverage(monkeypatch, key):
    """SNAPSHOT keys answer "when was this extract taken", not "what does it
    cover". This test previously asserted the opposite (status == "fail"),
    which a completeness critic showed blocks ordinary feeds: an ``as_of`` of
    today is disjoint from every historical dataset. Same principle as
    ``test_update_period_key_is_not_treated_as_coverage`` below."""
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(None, raw={key: "2015"}), _node("2024")))
    assert v.status == "pass"


def test_explicit_field_wins_over_raw_row(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ref = _ref("2024", raw={"temporal_coverage": "2015"})
    (v,) = _temporal(_run(ref, _node("2024")))
    assert v.status == "pass"


def test_update_period_key_is_not_treated_as_coverage(monkeypatch):
    """``period`` is deliberately NOT in the raw key list — DCAT's
    ``update_period`` is an update FREQUENCY, not a coverage interval, and
    conflating them would fail matches on a frequency mismatch."""
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref(None, raw={"period": "2015"}), _node("2024")))
    assert v.status == "pass"
    assert "pass-by-default" in v.detail


# ── flag ON: explicit node_temporal_coverage kwarg overrides the node ─────


def test_node_temporal_kwarg_overrides_node_field(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    (v,) = _temporal(_run(_ref("2015"), _node("2015"), node_temporal_coverage="2024"))
    assert v.status == "fail"


# ── flag ON: list shape / ordering stays diffable ─────────────────────────


def test_temporal_entry_is_appended_last(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    results = _run(_ref(None), _node(None))
    assert [r.name for r in results[:-1]] == BASELINE_NAMES
    assert results[-1].name == "temporal_compatible"


def test_both_optional_flags_on_keeps_stable_order(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(COMPLETENESS_FLAG, "1")
    results = _run(_ref(None), _node(None))
    assert [r.name for r in results] == BASELINE_NAMES + [
        "input_completeness",
        "temporal_compatible",
    ]


# ── comparator unit tests ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2019", (date(2019, 1, 1), date(2019, 12, 31))),
        ("2019-05", (date(2019, 5, 1), date(2019, 5, 31))),
        ("2020-02", (date(2020, 2, 1), date(2020, 2, 29))),  # leap year
        ("2019-02", (date(2019, 2, 1), date(2019, 2, 28))),
        ("2020-06-15", (date(2020, 6, 15), date(2020, 6, 15))),
        ("2019-2021", (date(2019, 1, 1), date(2021, 12, 31))),
        ("2019 to 2021", (date(2019, 1, 1), date(2021, 12, 31))),
        ("2019/2021", (date(2019, 1, 1), date(2021, 12, 31))),
        (
            "2019-01-01/2020-12-31",
            (date(2019, 1, 1), date(2020, 12, 31)),
        ),
        ("2019-01-01/..", (date(2019, 1, 1), None)),
        ("../2020-12-31", (None, date(2020, 12, 31))),
        ("  2019-01-01 / 2020-12-31  ", (date(2019, 1, 1), date(2020, 12, 31))),
    ],
)
def test_parse_temporal_interval_accepts(text, expected):
    assert _parse_temporal_interval(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "historical",
        "2019-13",  # month out of range
        "2019-02-30",  # not a real day
        "2021/2019",  # reversed
        "2021-2019",  # reversed year range
        "../..",  # carries no information
        "2019/2020/2021",  # not an interval
        "19",  # not a 4-digit year
        "2019-1-1",  # not zero-padded ISO
    ],
)
def test_parse_temporal_interval_rejects(text):
    assert _parse_temporal_interval(text) is None


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ((date(2019, 1, 1), date(2020, 1, 1)), (date(2019, 6, 1), None), True),
        ((None, date(2018, 12, 31)), (date(2019, 1, 1), None), False),
        ((None, None), (date(2019, 1, 1), date(2019, 12, 31)), True),
        (
            (date(2019, 1, 1), date(2019, 12, 31)),
            (date(2020, 1, 1), date(2020, 12, 31)),
            False,
        ),
    ],
)
def test_intervals_overlap(a, b, expected):
    assert _intervals_overlap(a, b) is expected
    assert _intervals_overlap(b, a) is expected  # symmetric


# ──────────────────────────────────────────────────────────────────────
# Open-ended (ongoing) vendor coverage must not be read as a single day
# ──────────────────────────────────────────────────────────────────────
# Found by an adversarial verifier and reproduced end-to-end: a row carrying
# only `coverage_start` -- the standard shape for an ongoing feed -- collapsed
# to that one day, so an genuinely overlapping node required-FAILED. That
# breaks the feature's core rule: only a positive, provable disagreement may
# fail a match.


def _temporal_status(raw: dict, node_coverage: str):
    from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
    from scudo_mapping_mcp.validations import run_validations

    node = TaxonomyNode(iri="jpmorgan:data:cdao:eq", label="Equity Prices")
    ref = VendorProductRef(
        vendor="LSEG", product_id="X1", name="Equity Prices", raw=raw
    )
    hits = [
        v
        for v in run_validations(
            ref,
            node,
            scope_allowed=True,
            has_store_node=True,
            node_temporal_coverage=node_coverage,
        )
        if v.name == "temporal_compatible"
    ]
    return hits[0].status if hits else None


def test_ongoing_feed_overlapping_a_later_node_passes(monkeypatch):
    """`coverage_start` with no end = ongoing. 2020->present DOES overlap
    2024-2025; failing it is a false positive on a correct match."""
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status({"coverage_start": "2020-01-01"}, "2024-01-01/2025-01-01")
        == "pass"
    )


def test_ongoing_feed_starting_after_the_node_ends_still_fails(monkeypatch):
    """Open-ended must not mean "matches everything". A feed starting 2020
    genuinely cannot cover a 2015-2018 archive."""
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status({"coverage_start": "2020-01-01"}, "2015-01-01/2018-12-31")
        == "fail"
    )


def test_start_and_end_keys_are_read_as_a_pair(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status(
            {"coverage_start": "2015-01-01", "coverage_end": "2018-12-31"},
            "2024-01-01/2025-01-01",
        )
        == "fail"
    )


def test_camel_case_start_end_pair_is_read_too(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status(
            {"coverageStart": "2015-01-01", "coverageEnd": "2018-12-31"},
            "2024-01-01/2025-01-01",
        )
        == "fail"
    )


# ──────────────────────────────────────────────────────────────────────
# Snapshot-point keys are NOT coverage
# ──────────────────────────────────────────────────────────────────────
# Found by a completeness critic, and it is the second half of the same
# defect as the open-ended fix above. `as_of` / `vintage` answer "when was
# this extract taken", not "what period does it cover". Read as a one-day
# window, an as_of of TODAY is disjoint from every historical dataset --
# measured at confidence 0.87, i.e. a record that would have AUTO_MAPPED
# was blocked instead. A snapshot date is not a disagreement about coverage.


@pytest.mark.parametrize("key", ["as_of", "asOf", "asof", "vintage"])
def test_snapshot_point_keys_never_fail_a_match(monkeypatch, key):
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status({key: "2025-01-15"}, "2019-01-01/2020-12-31") == "pass"
    ), f"{key} is a snapshot timestamp, not a coverage claim -- it must not fail"


def test_snapshot_keys_are_not_in_the_coverage_key_list():
    """Structural pin: moving one back into _VENDOR_TEMPORAL_KEYS silently
    re-breaks every feed that emits it."""
    from scudo_mapping_mcp import validations as v

    overlap = set(v._VENDOR_TEMPORAL_SNAPSHOT_KEYS) & set(v._VENDOR_TEMPORAL_KEYS)
    assert not overlap, f"snapshot keys treated as coverage: {sorted(overlap)}"


def test_real_coverage_keys_still_discriminate(monkeypatch):
    """Negative control: ignoring snapshot keys must not disable the check."""
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status(
            {"temporal_coverage": "2015-01-01/2018-12-31"}, "2019-01-01/2020-12-31"
        )
        == "fail"
    )
    assert (
        _temporal_status(
            {"temporal_coverage": "2019-06-01/2021-01-01"}, "2019-01-01/2020-12-31"
        )
        == "pass"
    )


# ──────────────────────────────────────────────────────────────────────
# The parser must be TOTAL — malformed input degrades, never raises
# ──────────────────────────────────────────────────────────────────────
# An external reviewer found "0000" raising ValueError (date(0,1,1) is out of
# range) through a grammar that admits it. An exception is strictly worse than
# a fail here: it escapes run_validations and takes the request with it, and
# the feature's whole contract is "an unknown date never fails a match".


@pytest.mark.parametrize(
    "bad",
    [
        "0000",
        "0000-01-01",
        "9999-99",
        "2020-13-01",
        "2020-02-30",
        "garbage",
        "2020-01-01/P1Y",
        "2020-01-01T00:00:00Z/2020-06-01T00:00:00Z",
        "2020-12-31/2020-01-01",
    ],
)
def test_unparseable_input_passes_and_never_raises(monkeypatch, bad):
    monkeypatch.setenv(FLAG, "1")
    assert _temporal_status({"temporal_coverage": bad}, "2019-01-01/2020-12-31") == "pass"


def test_parser_helper_is_total():
    """Direct unit check on the parser, independent of the validation wrapper."""
    from scudo_mapping_mcp import validations as v

    for bad in ("0000", "0000-01-01", "99999", "2020-02-30", ""):
        assert v._parse_temporal_point(bad) is None, bad


def test_real_disjoint_still_fails_after_the_totality_guard(monkeypatch):
    """Negative control: making the parser total must not disable the check."""
    monkeypatch.setenv(FLAG, "1")
    assert (
        _temporal_status(
            {"temporal_coverage": "2015-01-01/2018-12-31"}, "2019-01-01/2020-12-31"
        )
        == "fail"
    )
