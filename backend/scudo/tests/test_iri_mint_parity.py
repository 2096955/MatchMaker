"""IRI mint parity — every mint that can reach the store must agree byte-for-byte.

BACKGROUND (the defect these tests pin)

There were THREE deterministic vendor-product IRI mints in this tree and they
disagreed on all three axes — namespace seed, slug rule, AND key separator:

    scudo_mapping_mcp.models.mds_iri   seed 6f2a9c4e-…, key "<vendor>::<ref>",
                                       slug = lower + drop " " + "&" -> "and"
    scudo.lambda_handler (inline)      seed uuid5(NAMESPACE_URL, ".../catalogue"),
                                       key "<vendor>:<ref>", slug = RAW vendor
    vendor_catalogue_mcp.contract      seed uuid5(NAMESPACE_URL, ".../catalogue"),
                                       .product_iri                       key "<VendorId.value>:<ref>", slug = enum value

Hand-verified on ("S&P Global", "SPGI-1"):

    models      -> mds.sandpglobal:724e610b-9dfb-5012-9125-fe7e16e99eff
    lambda      -> mds.S&P Global:e61257bf-6273-53b5-9b39-bb1980c205c8   <- MALFORMED
    contract    -> mds.spglobal:848af514-595e-55f5-b34c-f9a7ccdfc712

The Lambda one was the live path and it interpolated the RAW vendor string, so a
real in-scope vendor name emitted an IRI containing a SPACE and an AMPERSAND —
violating the documented ``mds.<vendor>:<uuid5>`` convention (config.py:42-43)
and blocked by the orchestrator publish gate (``_IRI_DETERMINISM``).

WHY IT MATTERS (blast radius — narrower than it looks)

Precedent reuse does NOT key on the IRI: matching.py -> falkordb_store.py keys on
the (vendor, product_id) literals, rank signals key on a derived signature, and
MappingPattern carries no vendor-product IRI (the golden-bundle importer re-mints
locally). So golden-bundle import was never broken by this.

The real damage is I8 (deterministic identity): the IRI is the MERGE key for the
VendorProduct node (``MERGE (v:VendorProduct {iri:$v})``). Divergent mints fork
NODES — two rows for one logical product — which undermines the
single-positive-precedent invariant.

WHAT IS PINNED HERE

  1. lambda_handler's mint == models.mds_iri, byte-for-byte.
  2. The offline replication inside lambda_handler == models.mds_iri,
     byte-for-byte (this is the anti-drift pin: the replication exists so the
     Lambda never hard-fails when scudo_mapping_mcp is not vendored, and it must
     never be allowed to silently diverge from its source of truth).
  3. Every minted IRI for an in-scope vendor is whitespace-free and matches the
     documented mds.<slug>:<uuid5> shape, and passes the publish gate.
  4. vendor_catalogue_mcp.contract.product_iri (parallel demo code, deliberately
     left non-canonical) stays OUT of every store-reaching import path.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# The documented convention (scudo_mapping_mcp/config.py:42-43): mds.<vendor>:<uuid5>.
# Slug is lower-case alphanumeric (the canonical rule folds " " away and "&"->"and");
# suffix is a lower-case hyphenated UUID.
_CANONICAL_IRI = re.compile(
    r"^mds\.[a-z0-9]+:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
)

# (vendor, vendor_product_ref) matrix. "S&P Global" is the case that exposed the
# defect: raw interpolation emits a SPACE and an AMPERSAND into the IRI.
_CASES = [
    ("S&P Global", "SPGI-1"),
    ("lseg", "LSEG-IBES-EST-001"),
    ("LSEG", "LSEG-EQ-1"),
    ("Bloomberg", "BBG-EQ-77"),
    ("ICE", "ICE-FI-02"),
    ("FactSet", "FDS-REF-11"),
    ("  S&P Global  ", "  SPGI-1  "),  # strip() parity
]


def _mds_iri():
    from scudo_mapping_mcp.models import mds_iri

    return mds_iri


# ────────────────────────────────────────────────────────────────────────────
# 1. The live Lambda mint agrees with the canonical mint
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("vendor,ref", _CASES)
def test_lambda_vendor_iri_equals_canonical_mds_iri(vendor, ref):
    """scudo.lambda_handler's mint must be byte-identical to models.mds_iri.

    This is the whole fix: one mint, one identity, one VendorProduct node.
    """
    from scudo import lambda_handler

    assert lambda_handler._canonical_vendor_iri(vendor, ref) == _mds_iri()(vendor, ref)


# ────────────────────────────────────────────────────────────────────────────
# 2. The offline replication cannot drift from its source of truth
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("vendor,ref", _CASES)
def test_offline_replication_is_byte_identical_to_canonical(vendor, ref):
    """lambda_handler keeps a dependency-free replication of the canonical
    algorithm so the deployed Lambda still mints correctly when the
    scudo_mapping_mcp package is not vendored into the image (matcher_bridge's
    lazy-import contract). A replication that drifts silently re-forks every
    IRI in the system, so it is pinned here, byte-for-byte."""
    from scudo import lambda_handler

    assert lambda_handler._mds_iri_offline(vendor, ref) == _mds_iri()(vendor, ref)


def test_canonical_mint_prefers_the_real_module_and_falls_back_cleanly(monkeypatch):
    """When scudo_mapping_mcp is importable the canonical module IS used; when
    the import fails the mint must still succeed (fail-safe, never a hard
    request failure) and return the same string."""
    from scudo import lambda_handler

    vendor, ref = "S&P Global", "SPGI-1"
    expected = _mds_iri()(vendor, ref)

    # Real-module path.
    assert lambda_handler._canonical_vendor_iri(vendor, ref) == expected

    # Simulate the "package not vendored in the Lambda image" case.
    real_import = __import__

    def _blocked(name, *a, **kw):
        if name.startswith("scudo_mapping_mcp"):
            raise ImportError("simulated: package not vendored in the image")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _blocked)
    assert lambda_handler._canonical_vendor_iri(vendor, ref) == expected


# ────────────────────────────────────────────────────────────────────────────
# 3. Shape: no whitespace, no illegal characters, publish gate accepts
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("vendor,ref", _CASES)
def test_minted_iri_has_no_whitespace_and_matches_documented_shape(vendor, ref):
    from scudo import lambda_handler

    iri = lambda_handler._canonical_vendor_iri(vendor, ref)
    assert not any(ch.isspace() for ch in iri), f"whitespace in minted IRI: {iri!r}"
    assert "&" not in iri, f"ampersand in minted IRI: {iri!r}"
    assert _CANONICAL_IRI.match(iri), f"IRI is not mds.<slug>:<uuid5>: {iri!r}"


def test_every_priority_vendor_mints_an_iri_the_publish_gate_accepts():
    """The orchestrator publish gate (_IRI_DETERMINISM) is the last line before a
    triple reaches the store. The pre-fix Lambda mint FAILED it for "S&P Global"
    (space + "&" are outside the allowed vendor-segment character class)."""
    from scudo import lambda_handler
    from scudo.orchestrator import _IRI_DETERMINISM
    from scudo_mapping_mcp.config import PRIORITY_VENDORS

    for vendor in PRIORITY_VENDORS:
        iri = lambda_handler._canonical_vendor_iri(vendor, "REF-1")
        assert _IRI_DETERMINISM.match(iri), f"publish gate rejects {iri!r}"


def test_pre_fix_malformed_iri_is_still_rejected_by_the_publish_gate():
    """Guard against 'fixing' the gate instead of the mint: the old malformed
    form must remain unacceptable."""
    from scudo.orchestrator import _IRI_DETERMINISM

    assert not _IRI_DETERMINISM.match(
        "mds.S&P Global:e61257bf-6273-53b5-9b39-bb1980c205c8"
    )


# ────────────────────────────────────────────────────────────────────────────
# 4. The assembled BriefBundle carries the canonical IRI end to end
# ────────────────────────────────────────────────────────────────────────────
def test_assembled_bundle_vendor_product_iri_is_canonical(monkeypatch):
    """_build_bundle_assembler stamps vendor_product_iri AND
    vendor_assertion["iri"] AND precedent.source_iri from the same mint — all
    three must be canonical."""
    from scudo import aurora_memory, lambda_handler
    from scudo.schemas import IntakeRequest, Route

    monkeypatch.setattr(
        aurora_memory,
        "consult_priors",
        lambda **kw: aurora_memory.Priors(
            precedent={
                "target_iri": "jpmorgan:data:cdao:EquityResearch",
                "confidence": 0.91,
                "rationale": "prior run",
            },
            rules=[],
        ),
    )
    monkeypatch.setattr(aurora_memory, "consult_best_skill", lambda: None)

    vendor, ref = "S&P Global", "SPGI-1"
    expected = _mds_iri()(vendor, ref)

    assemble = lambda_handler._build_bundle_assembler(
        {"vendor": vendor, "vendor_product_ref": ref}
    )
    bundle = assemble(
        IntakeRequest(vendor=vendor, vendor_product_ref=ref, has_precedent=True),
        Route.EXTEND_MAPPING,
    )

    assert bundle.vendor_product_iri == expected
    assert bundle.vendor_assertion["iri"] == expected
    assert bundle.precedent is not None
    assert bundle.precedent.source_iri == expected
    assert not any(ch.isspace() for ch in bundle.vendor_product_iri)


# ────────────────────────────────────────────────────────────────────────────
# 5. The third (demo-only) mint stays off every store-reaching path
# ────────────────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[2]


def test_product_iri_is_not_imported_by_any_store_reaching_module():
    """vendor_catalogue_mcp.contract.product_iri is deliberately left as a THIRD,
    non-canonical mint (see the decision recorded in its docstring). That is only
    safe while it stays parallel demo code. This guard fails the moment someone
    wires it into scudo/ or scudo_mapping_mcp/ — the packages whose writes reach
    the real store.

    Note the reachability that DOES exist and is intentionally allowed:
    scudo.catalogue.get_product_via_mcp launches vendor_catalogue_mcp.server as a
    stdio SUBPROCESS, so backend/scudo/tests/smoke.py's assembler sees a
    product_iri-minted IRI. That path publishes to InMemoryPublishSink only.
    """
    offenders = []
    self_path = Path(__file__).resolve()
    for pkg in ("scudo", "scudo_mapping_mcp"):
        for path in (_BACKEND / pkg).rglob("*.py"):
            if path.resolve() == self_path:
                continue  # this guard names the symbol in order to check it
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith(("import ", "from ")):
                    continue
                if "vendor_catalogue_mcp" in stripped and "product_iri" in stripped:
                    offenders.append(f"{path}:{lineno}: {stripped}")
    assert not offenders, (
        "the non-canonical demo mint was imported into a store-reaching package:\n"
        + "\n".join(offenders)
    )


def test_product_iri_docstring_marks_itself_non_canonical():
    """Decision #2: product_iri is left behaviourally UNCHANGED (zero callers
    outside its own package; changing it would shift every IRI the
    /api/catalogue HTTP facade already serves, for no store-side benefit) and is
    instead labelled loudly. Pin the label so it cannot be quietly dropped."""
    from vendor_catalogue_mcp.contract import product_iri

    doc = (product_iri.__doc__ or "").upper()
    assert "NON-CANONICAL" in doc
    assert "MDS_IRI" in doc, "the docstring must name the canonical mint"
