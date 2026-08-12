"""The Flask ``_frame`` must obey the same fail-closed rules as the MCP one.

WHY THIS FILE EXISTS
--------------------
The inline-frame defect was fixed in ``scudo_mapping_mcp/match_verify_mcp.py``,
but ``backend/routes/mapping.py`` held a **byte-equivalent copy** of the same
function with both original defects intact:

  1. inline ``name``/``description`` unconditionally short-circuited the real
     frame lookup, with no gate, so a caller could score arbitrary text against
     a real ``product_id``;
  2. a missing frame silently fabricated ``name=product_id`` rather than
     refusing.

That copy is not dead code. It backs four live endpoints --
``/mapping/similar`` (:407), ``/mapping/map`` (:493), ``/mapping/decision``
(:675) and ``/mapping/agent/run`` (:1461) -- which are the routes the deployed
console actually calls. Fixing only the MCP left the live path open, so this
duplicate is the one that mattered most.

Found by an adversarial verifier, not by the original fixer: the fixer stayed
inside its briefed file, and the brief named the wrong file.
"""

from __future__ import annotations

import os
import sys
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("STORE_BACKEND", "memory")

from scudo_mapping_mcp.frames import put_frame  # noqa: E402
from scudo_mapping_mcp.models import VendorProductRef  # noqa: E402

routes_mapping = pytest.importorskip("routes.mapping")

_FLAG = "SCUDO_MV_ALLOW_INLINE_FRAME"


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)


@pytest.fixture
def seeded():
    put_frame(
        VendorProductRef(
            vendor="LSEG",
            product_id="FLASK-X1",
            name="REAL Equity Prices",
            description="the ingested description",
        )
    )
    return "LSEG", "FLASK-X1"


def test_inline_text_is_ignored_by_default(seeded):
    """Flag off (production default): the ingested frame wins."""
    vendor, pid = seeded
    ref = routes_mapping._frame(vendor, pid, "ATTACKER SUPPLIED", "evil")
    assert ref.name == "REAL Equity Prices", (
        "inline text overrode the real frame -- the Flask copy is still fail-open"
    )
    assert ref.description == "the ingested description"


def test_inline_text_is_honoured_when_explicitly_enabled(seeded, monkeypatch):
    monkeypatch.setenv(_FLAG, "1")
    vendor, pid = seeded
    ref = routes_mapping._frame(vendor, pid, "INLINE NAME", "inline desc")
    assert ref.name == "INLINE NAME"


def test_missing_frame_does_not_fabricate_a_name():
    """The old code returned ``name=product_id``, so the matcher scored an
    invented label and the caller could not tell. Whatever the new behaviour
    is, it must not silently pass the product_id off as a name."""
    ref_or_none = routes_mapping._frame("LSEG", "NO-SUCH-PRODUCT-999")
    if ref_or_none is not None:
        assert ref_or_none.name != "NO-SUCH-PRODUCT-999", (
            "missing frame still fabricates name=product_id"
        )


def test_flag_shared_with_the_mcp_gate():
    """Both copies read the SAME env var, so an operator cannot open one
    ingress while believing they closed both."""
    from scudo_mapping_mcp import match_verify_mcp as mv

    assert mv._INLINE_FRAME_FLAG == _FLAG
