"""``vendor_signature`` must canonicalise vendor casing like every other identity key.

THE DEFECT
----------
Three keys are derived from ``(vendor, ...)`` and two of them lower-cased the
vendor while the third did not:

    models.mds_iri          f"{vendor.strip().lower()}::{product_id.strip()}"   lower
    verdict.input_hash      f"{vendor.strip().lower()}::{product_id.strip()}"   lower
    store.vendor_signature  f"{vendor.strip()}::{base}"                         NOT lower

So ``"LSEG"`` and ``"lseg"`` produced the SAME IRI and the SAME seal identity
but DIFFERENT rank signals. That is the worst shape a fork can take: identity
converges, so nothing looks broken, while ``rank_signals_for`` silently buckets
the two casings apart and the precedent-driven ordering boost quietly
under-performs. No error, no failing test -- just degraded matching.

Distinct from the other two casing issues in this area (the scope gate
rejecting ``'lseg'``, and precedent lookup keying on raw vendor/product_id).
Fixing those would not have fixed this.

MIGRATION NOTE
--------------
``signature`` is denormalised onto the ``VendorProduct`` node and queried by
exact match (``MATCH (v:VendorProduct {signature:$sig})``), so rows written
under a mixed-case vendor before this change keep their old signature until
re-written. Bundle import is self-healing -- ``bundle.export``/``import``
RECOMPUTE the signature via ``store.vendor_signature`` rather than trusting the
stored snapshot -- so an export/import cycle re-keys them. Live graph rows that
never round-trip need a one-off re-write; see the write-up.
"""

from __future__ import annotations

import pytest

from scudo_mapping_mcp import verdict
from scudo_mapping_mcp.models import mds_iri
from scudo_mapping_mcp.store.base import RetrievalStore

_sig = RetrievalStore.vendor_signature


@pytest.mark.parametrize(
    "a,b",
    [
        ("LSEG", "lseg"),
        ("LSEG", "Lseg"),
        ("S&P Global", "s&p global"),
        ("FactSet", "factset"),
        ("  LSEG  ", "lseg"),
    ],
)
def test_signature_is_case_insensitive_in_vendor(a: str, b: str) -> None:
    assert _sig(a, "Equity Prices", "X1") == _sig(b, "Equity Prices", "X1")


def test_signature_agrees_with_the_other_two_identity_keys_on_casing() -> None:
    """The actual invariant: all three keys must fork or converge TOGETHER.

    A future edit that re-introduces case sensitivity in any one of them
    fails here rather than silently splitting rank signals.
    """
    upper, lower = "LSEG", "lseg"
    pid, name = "X1", "Equity Prices"

    iri_converges = mds_iri(upper, pid) == mds_iri(lower, pid)
    hash_converges = verdict.input_hash(upper, pid) == verdict.input_hash(lower, pid)
    sig_converges = _sig(upper, name, pid) == _sig(lower, name, pid)

    assert iri_converges is True, "mds_iri regressed to case-sensitive"
    assert hash_converges is True, "verdict.input_hash regressed to case-sensitive"
    assert sig_converges is True, (
        "vendor_signature is case-sensitive while mds_iri and input_hash are "
        "not -- identity converges but rank signals fork, which degrades "
        "matching silently"
    )


def test_signature_still_discriminates_on_the_things_it_should() -> None:
    """Negative control: lower-casing the vendor must not collapse everything.

    Without this, ``return "constant"`` would pass every test above.
    """
    assert _sig("LSEG", "Equity Prices", "X1") != _sig(
        "Bloomberg", "Equity Prices", "X1"
    )
    assert _sig("LSEG", "Equity Prices", "X1") != _sig("LSEG", "Bond Prices", "X1")


def test_name_normalisation_is_unchanged() -> None:
    """The pre-existing name handling (lower + whitespace collapse, falling
    back to product_id) must be untouched by the vendor fix."""
    assert _sig("LSEG", "  Equity   Prices  ", "X1") == _sig(
        "LSEG", "equity prices", "X1"
    )
    # Empty name falls back to product_id.
    assert _sig("LSEG", "", "X1") == _sig("LSEG", "", "x1".upper())
    assert "x1" in _sig("LSEG", "", "X1")


def test_signature_shape_is_preserved() -> None:
    """Still ``<vendor>::<base>`` -- the fix normalises, it does not reshape."""
    assert _sig("LSEG", "Equity Prices", "X1") == "lseg::equity prices"
