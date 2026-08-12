"""Match & Verify frame-resolution gate — the inline bypass and the fabricated frame.

TWO DEFECTS THIS FILE PINS
--------------------------
1. ``match_verify_mcp._frame`` short-circuited the frame lookup whenever the
   caller passed ANY inline ``name``/``description``, with no gate. Because the
   HMAC seal (``verdict.sign``) binds only
   ``(input_hash, mapped_node_iri, status, confidence, band, ts_ms)`` — NOT the
   frame text and NOT frame provenance — a verdict scored against arbitrary
   caller-supplied text is byte-indistinguishable, downstream, from one scored
   against the real ingested frame. Persistence cannot tell them apart.

2. When the frame was absent, ``_frame`` FABRICATED ``name=product_id`` and
   scored against it silently. That is not a rare path: the deployed Match &
   Verify service runs ``FRAME_SOURCE=mock`` (infra/scudo-dev-deploy.yaml:529)
   while the mock working set is a process-local dict (frames.py:53), so the
   M&V container never sees frames written by the ingestion container.

THE CONTRACT NOW
----------------
  - Inline text is honoured ONLY when ``SCUDO_MV_ALLOW_INLINE_FRAME`` is truthy.
    Default (unset) => always read the real frame. Fail-closed.
  - A missing frame is a typed REFUSAL (same envelope shape as
    ``persistence_mcp._refusal``), never a fabricated name. No refusal path
    emits a seal — Persistence gets nothing to trust.
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")

import pytest

from scudo_mapping_mcp import frames, match_verify_mcp
from scudo_mapping_mcp import matching as matching_mod
from scudo_mapping_mcp import verdict as verdict_seal
from scudo_mapping_mcp.match_verify_mcp import SimilarInput, VerifyInput
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.tests.fake_store import FakeStore

FLAG = "SCUDO_MV_ALLOW_INLINE_FRAME"

VENDOR = "LSEG"
PRODUCT_ID = "EQ-RT-001"
REAL_NAME = "Real Time Equity Prices"
REAL_DESCRIPTION = "Normalised intraday equity price ticks for listed venues."
ADVERSARIAL_NAME = "Reference Data Instrument Master"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from the fail-closed default, with an empty working set.

    The store is monkeypatched to a FakeStore for the same reason
    ``test_invariants.py`` does it: ``store.get_store`` is ``lru_cache``d, so
    whichever test imports first fixes the backend for the whole session —
    without this, running the file inside the full suite tries to dial the
    real FalkorDB on :6379.
    """
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("SCUDO_VERDICT_ALLOW_DEV", "1")
    monkeypatch.setenv("FRAME_SOURCE", "mock")

    fake = FakeStore(
        nodes=[
            TaxonomyNode(iri="cdao:equities", label="Equities"),
            TaxonomyNode(
                iri="cdao:eq-prices",
                label="Equity Prices",
                parent_iri="cdao:equities",
            ),
        ]
    )
    monkeypatch.setattr(match_verify_mcp, "get_store", lambda: fake)
    monkeypatch.setattr(matching_mod, "get_store", lambda: fake)

    frames.clear_frames()
    yield
    frames.clear_frames()


def _seed_real_frame() -> VendorProductRef:
    ref = VendorProductRef(
        vendor=VENDOR,
        product_id=PRODUCT_ID,
        name=REAL_NAME,
        description=REAL_DESCRIPTION,
        source_content_hash="a" * 64,
        source_file_audit_id="audit-123",
    )
    frames.put_frame(ref)
    return ref


# ──────────────────────────────────────────────────────────────────────
# Defect 1 — the ungated inline bypass
# ──────────────────────────────────────────────────────────────────────
def test_inline_text_is_ignored_by_default_when_a_real_frame_exists():
    """Flag unset => the ingested frame wins, not the caller's text."""
    _seed_real_frame()
    ref = match_verify_mcp._frame(VENDOR, PRODUCT_ID, ADVERSARIAL_NAME, "")
    assert ref.name == REAL_NAME, (
        f"inline text must not override the ingested frame when {FLAG} is unset"
    )
    assert ref.description == REAL_DESCRIPTION
    assert ref.source_content_hash == "a" * 64, (
        "the real frame's provenance must survive resolution"
    )


def test_inline_text_is_honoured_only_when_the_flag_is_on(monkeypatch):
    """The dev/testing path still exists — but only behind an explicit opt-in."""
    _seed_real_frame()
    monkeypatch.setenv(FLAG, "1")
    ref = match_verify_mcp._frame(VENDOR, PRODUCT_ID, ADVERSARIAL_NAME, "")
    assert ref.name == ADVERSARIAL_NAME
    assert ref.source_content_hash is None, (
        "an inline frame has no upstream provenance and must not claim any"
    )


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_flag_truthy_spellings_enable_inline(monkeypatch, value):
    _seed_real_frame()
    monkeypatch.setenv(FLAG, value)
    ref = match_verify_mcp._frame(VENDOR, PRODUCT_ID, ADVERSARIAL_NAME, "")
    assert ref.name == ADVERSARIAL_NAME, f"{FLAG}={value!r} should enable inline"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_flag_falsy_and_garbage_spellings_stay_closed(monkeypatch, value):
    """Anything that isn't an explicit truthy token keeps the gate shut."""
    _seed_real_frame()
    monkeypatch.setenv(FLAG, value)
    ref = match_verify_mcp._frame(VENDOR, PRODUCT_ID, ADVERSARIAL_NAME, "")
    assert ref.name == REAL_NAME, f"{FLAG}={value!r} must NOT open the inline path"


def test_verify_mapping_seals_the_real_frames_name_not_the_inline_text():
    """End-to-end: the sealed verdict describes the ingested product."""
    _seed_real_frame()
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(
                vendor=VENDOR,
                product_id=PRODUCT_ID,
                name=ADVERSARIAL_NAME,
                description="",
            )
        )
    )
    resp = json.loads(raw)
    assert "refusal" not in resp, resp
    assert resp["verdict"]["product_name"] == REAL_NAME, resp["verdict"]


# ──────────────────────────────────────────────────────────────────────
# Defect 2 — the fabricated frame
# ──────────────────────────────────────────────────────────────────────
def test_missing_frame_refuses_instead_of_fabricating_a_name():
    """The old code returned VendorProductRef(name=product_id). Never again."""
    with pytest.raises(match_verify_mcp.FrameRefusal) as excinfo:
        match_verify_mcp._frame(VENDOR, PRODUCT_ID, "", "")
    assert excinfo.value.reason == "frame_not_found"


def test_missing_frame_refuses_even_when_the_inline_flag_is_on(monkeypatch):
    """The flag opens the inline path; it does NOT resurrect fabrication.

    Flag on but NO inline text supplied => still a frame lookup, still a
    refusal when the frame is absent.
    """
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(match_verify_mcp.FrameRefusal) as excinfo:
        match_verify_mcp._frame(VENDOR, PRODUCT_ID, "", "")
    assert excinfo.value.reason == "frame_not_found"


def test_verify_mapping_refuses_and_emits_no_seal_when_the_frame_is_missing():
    """The whole point: no frame => no signed verdict for Persistence to trust."""
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert resp["refused"] is True, resp
    assert resp["refusal"]["reason"] == "frame_not_found", resp
    assert "seal" not in resp, "a refusal must never carry a seal"
    assert "verdict" not in resp, "a refusal must never carry a verdict"


def test_verify_mapping_refuses_when_only_inline_text_is_offered():
    """Flag off + no ingested frame + inline text => refusal, and it SAYS so."""
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(
                vendor=VENDOR,
                product_id=PRODUCT_ID,
                name=ADVERSARIAL_NAME,
                description="anything at all",
            )
        )
    )
    resp = json.loads(raw)
    assert resp["refused"] is True, resp
    assert resp["refusal"]["reason"] == "frame_not_found", resp
    assert resp["refusal"]["inline_ignored"] is True, (
        "the refusal must tell the operator their inline text was dropped by "
        "the gate — otherwise the flag is invisible in production"
    )
    assert "seal" not in resp


def test_find_candidates_refuses_when_the_frame_is_missing():
    """Same gate on the exploratory tool — no fabricated frame to retrieve on."""
    raw = asyncio.run(
        match_verify_mcp.find_candidates(
            SimilarInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert resp["refused"] is True, resp
    assert resp["refusal"]["reason"] == "frame_not_found", resp
    assert "candidates" not in resp


def test_refusal_envelope_matches_the_persistence_mcp_shape():
    """Keep one refusal vocabulary across the trust gradient."""
    from scudo_mapping_mcp import persistence_mcp

    mv = json.loads(match_verify_mcp._refusal("frame_not_found", detail="x"))
    pe = json.loads(persistence_mcp._refusal("frame_not_found", detail="x"))
    assert mv["refusal"] == pe["refusal"], (mv, pe)


# ──────────────────────────────────────────────────────────────────────
# Non-regression — the happy path still signs a verifiable seal
# ──────────────────────────────────────────────────────────────────────
def test_happy_path_still_signs_a_seal_that_verifies():
    _seed_real_frame()
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert "refusal" not in resp, resp
    result = verdict_seal.verify(
        resp["seal"],
        expected_vendor=VENDOR,
        expected_product_id=PRODUCT_ID,
    )
    assert result.ok, result.reason
    assert (result.payload or {}).get("v") == 2, (
        "seal version must stay v=2 — binding frame provenance into the "
        "payload would need a verdict.py change (out of scope here)"
    )


def test_find_candidates_still_returns_candidates_for_a_real_frame():
    _seed_real_frame()
    raw = asyncio.run(
        match_verify_mcp.find_candidates(
            SimilarInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert "refused" not in resp, resp
    assert "candidates" in resp and "count" in resp, resp


def test_verify_mapping_surfaces_unsealed_frame_provenance():
    """Provenance is REPORTED (unsealed) so an operator can see which frame
    was scored. It is deliberately NOT inside the HMAC — see the module
    docstring in match_verify_mcp for why that needs a seal-version bump.
    """
    _seed_real_frame()
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert resp["frame"]["source"] == "mock", resp["frame"]
    assert resp["frame"]["content_hash"] == "a" * 64, resp["frame"]
    assert resp["frame"]["file_audit_id"] == "audit-123", resp["frame"]
    assert resp["frame"]["sealed"] is False, (
        "must not imply the provenance is covered by the HMAC"
    )


def test_verify_mapping_marks_inline_provenance_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(
                vendor=VENDOR,
                product_id=PRODUCT_ID,
                name=ADVERSARIAL_NAME,
                description="",
            )
        )
    )
    resp = json.loads(raw)
    assert resp["frame"]["source"] == "inline", resp["frame"]
    assert resp["frame"]["content_hash"] is None, resp["frame"]


def test_reported_frame_source_tracks_the_source_actually_read(monkeypatch):
    """Provenance must follow ``frames.settings`` — the module global
    ``_read_vendor_frame`` branches on — not a separately imported copy.
    The smoke suite's ``_swap_settings`` rebinds exactly that attribute, so
    a divergent read here would report "mock" for an S3-sourced frame.
    """
    import dataclasses

    swapped = dataclasses.replace(
        frames.settings, frame_source="s3", s3_bucket="test-bucket"
    )
    monkeypatch.setattr(frames, "settings", swapped)
    # Stub the reader so no boto3 / network is involved — we are asserting
    # the LABEL, not the S3 transport (frames.py owns that).
    monkeypatch.setattr(
        match_verify_mcp,
        "_read_vendor_frame",
        lambda v, p: VendorProductRef(
            vendor=v, product_id=p, name=REAL_NAME, source_content_hash="b" * 64
        ),
    )
    raw = asyncio.run(
        match_verify_mcp.verify_mapping(
            VerifyInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert resp["frame"]["source"] == "s3", resp["frame"]
    assert resp["frame"]["content_hash"] == "b" * 64, resp["frame"]


def test_refusal_names_the_frame_source_that_was_searched(monkeypatch):
    """An operator debugging a refusal needs to know WHICH source missed."""
    import dataclasses

    swapped = dataclasses.replace(frames.settings, frame_source="s3")
    monkeypatch.setattr(frames, "settings", swapped)
    monkeypatch.setattr(match_verify_mcp, "_read_vendor_frame", lambda v, p: None)
    raw = asyncio.run(
        match_verify_mcp.find_candidates(
            SimilarInput(vendor=VENDOR, product_id=PRODUCT_ID, name="", description="")
        )
    )
    resp = json.loads(raw)
    assert resp["refusal"]["frame_source"] == "s3", resp


def test_agent_host_path_degrades_gracefully_on_a_refusal():
    """The agent's host branch parses find_candidates output. A refusal
    envelope has no "candidates" key — it must yield an empty candidate
    list (=> NEEDS_REVIEW downstream), never raise or fabricate.
    """
    from scudo_mapping_mcp.agent import _candidates_from_host_result

    refusal = json.loads(match_verify_mcp._refusal("frame_not_found", detail="x"))
    assert _candidates_from_host_result(refusal) == []
