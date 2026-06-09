"""
HMAC verdict seal — the integrity contract between Match & Verify and
Persistence MCPs in the three-MCP trust gradient.

WHY THIS EXISTS
---------------
With write capability physically isolated to Persistence MCP, the question
becomes: how does Persistence know a MappingResult arriving via
``persist.commit_mapping`` was actually produced by Match & Verify's
deterministic matcher (scope gate + validations + 0.80 floor) and not
synthesised by the agent? Answer: Match & Verify computes an HMAC-SHA256
seal over the verdict, using a key that only Match & Verify and Persistence
hold. The agent carries the seal but cannot forge it.

WHAT'S SEALED
-------------
The seal binds together:
  - input_hash       — sha256("{vendor}::{product_id}") — the identity
                        the verdict applies to. Persistence re-derives
                        this from the call args and refuses on mismatch
                        (replay-protection: a valid seal for product X
                        can't be reused against product Y).
  - mapped_node_iri  — the candidate the matcher chose.
  - status           — auto_mapped | needs_review | out_of_scope | …
  - confidence       — the matcher's raw similarity for the gate.
  - band             — cost-ladder band: pass | borderline | fail | n/a.
                        v=2 payloads carry this; v=1 payloads default to
                        "n/a" on read (forward-compatible).
  - ts               — unix epoch ms when M&V signed.

PAYLOAD VERSIONING
------------------
v=1 payloads (pre-band) are still accepted on verify — band defaults to
"n/a" so older callers keep working. New seals signed today are v=2 and
carry the band field. Persistence MCP can gate on sealed_band (e.g.,
refuse to write APPROVED if band=fail without explicit override) without
breaking in-flight legacy seals.

CONTRACT
--------
  - SIGNING_KEY loaded from env ``SCUDO_VERDICT_SIGNING_KEY`` (≥32 bytes
    of randomness). Production must inject via Secrets Manager scoped to
    M&V + Persistence task roles only. The Ingestion MCP task does NOT
    have IAM to read this key.
  - Dev fallback: when ``SCUDO_VERDICT_ALLOW_DEV=1`` and the env var is
    unset, a fixed dev key is used. Same fail-closed posture as auth.py —
    a misconfigured prod that leaves ALLOW_DEV=1 keeps a visible dev
    signature in audit, rather than silently signing with a default key.
  - Staleness: default max age 300s (``SCUDO_VERDICT_MAX_AGE_SECONDS``).
    The agent is not allowed to sit on a verdict for hours then quietly
    commit it.
  - This module is a pure-Python primitive — no Flask, no boto3, no MCP
    SDK. Imported by ``match_verify_mcp`` (to sign) and ``persistence_mcp``
    (to verify). Deliberately NOT imported by ``ingestion_mcp``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────
# Key material — single swap point
# ──────────────────────────────────────────────────────────────────────
_DEV_KEY = (
    b"scudo-dev-verdict-key-do-not-use-in-prod-"
    b"0123456789abcdef0123456789abcdef"
)

_TRUTHY = {"1", "true", "yes", "on"}


def _dev_allowed() -> bool:
    return (
        os.getenv("SCUDO_VERDICT_ALLOW_DEV", "").strip().lower() in _TRUTHY
    )


def _load_signing_key() -> bytes:
    """Return the HMAC key bytes. Fails loud on misconfiguration."""
    raw = (os.getenv("SCUDO_VERDICT_SIGNING_KEY", "") or "").strip()
    if raw:
        return raw.encode("utf-8")
    if _dev_allowed():
        return _DEV_KEY
    raise RuntimeError(
        "SCUDO_VERDICT_SIGNING_KEY is not set. The Match & Verify and "
        "Persistence MCPs cannot sign / verify verdicts without it. Set "
        "SCUDO_VERDICT_ALLOW_DEV=1 only for local development."
    )


def _max_age_seconds() -> int:
    raw = os.getenv("SCUDO_VERDICT_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return 300
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return 300


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class VerificationResult:
    """Outcome of ``verify``. ``ok=True`` → trust the verdict; else don't."""
    ok: bool
    reason: str = ""
    payload: Optional[dict] = None


def input_hash(vendor: str, product_id: str) -> str:
    """Deterministic identity binding for the seal.

    Lower-cases vendor (same rule as ``models.mds_iri``); preserves product_id
    verbatim because the brief makes product_id the vendor's native primary
    key. Returns lowercase hex sha256.
    """
    norm = f"{(vendor or '').strip().lower()}::{(product_id or '').strip()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def sign(
    *,
    vendor: str,
    product_id: str,
    mapped_node_iri: Optional[str],
    status: str,
    confidence: float,
    band: str = "n/a",
    ts_ms: Optional[int] = None,
) -> dict:
    """Sign a verdict. Returns the seal as a JSON-serialisable dict.

    The seal is the wire format that travels through the agent. It contains
    the canonical payload (base64) and the HMAC (base64). Persistence
    verifies both.

    New v=2 payloads carry ``band`` — the cost-ladder verdict band — so
    Persistence can gate on it directly without re-deriving from confidence.
    Default ``"n/a"`` keeps callers that don't know about bands working.
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    payload = {
        "v": 2,
        "input_hash": input_hash(vendor, product_id),
        "mapped_node_iri": mapped_node_iri or "",
        "status": status,
        "confidence": float(confidence),
        "band": str(band or "n/a"),
        "ts_ms": int(ts_ms),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    mac = hmac.new(_load_signing_key(), canonical, hashlib.sha256).digest()
    return {
        "payload_b64": base64.b64encode(canonical).decode("ascii"),
        "hmac_b64": base64.b64encode(mac).decode("ascii"),
    }


def verify(
    seal: dict,
    *,
    expected_vendor: str,
    expected_product_id: str,
    now_ms: Optional[int] = None,
) -> VerificationResult:
    """Verify a seal against the (vendor, product_id) the caller claims.

    Refuses (in order) on:
      - seal shape invalid
      - canonical payload not parseable
      - HMAC mismatch (forgery / tamper / wrong key)
      - ts older than max_age (replay)
      - input_hash mismatch (verdict for a different product replayed
        against this one)

    Returns ``VerificationResult.ok = True`` only when every check passes.
    """
    if not isinstance(seal, dict):
        return VerificationResult(False, "seal_shape_invalid")
    p_b64 = seal.get("payload_b64")
    h_b64 = seal.get("hmac_b64")
    if not isinstance(p_b64, str) or not isinstance(h_b64, str):
        return VerificationResult(False, "seal_shape_invalid")

    try:
        canonical = base64.b64decode(p_b64.encode("ascii"), validate=True)
        provided_mac = base64.b64decode(h_b64.encode("ascii"), validate=True)
    except (ValueError, binascii_error()):
        return VerificationResult(False, "seal_encoding_invalid")

    expected_mac = hmac.new(
        _load_signing_key(), canonical, hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected_mac, provided_mac):
        return VerificationResult(False, "seal_mismatch")

    try:
        payload = json.loads(canonical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return VerificationResult(False, "seal_payload_unreadable")
    if not isinstance(payload, dict) or payload.get("v") not in (1, 2):
        return VerificationResult(False, "seal_payload_unsupported_version")
    # Forward-compatible default — v=1 seals (pre-band) verify as
    # band="n/a" so legacy callers keep working through the rollout.
    payload.setdefault("band", "n/a")

    # Staleness — agent can't sit on a verdict for hours.
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    ts_ms = payload.get("ts_ms")
    if not isinstance(ts_ms, int):
        return VerificationResult(False, "seal_payload_no_timestamp")
    age_seconds = (now_ms - ts_ms) / 1000.0
    if age_seconds > _max_age_seconds():
        return VerificationResult(False, "seal_expired")
    # Allow a small skew tolerance for sealed-in-the-future timestamps.
    if age_seconds < -5:
        return VerificationResult(False, "seal_future_timestamp")

    # Identity binding — a verdict for product X cannot be replayed
    # against product Y. This is the I8 invariant lifted to the seal.
    expected_input = input_hash(expected_vendor, expected_product_id)
    if not hmac.compare_digest(
        expected_input, payload.get("input_hash", "")
    ):
        return VerificationResult(False, "identity_mismatch")

    return VerificationResult(ok=True, payload=payload)


def binascii_error() -> type:
    """Lazy-import binascii.Error to keep the module surface clean."""
    import binascii as _b
    return _b.Error


# ──────────────────────────────────────────────────────────────────────
# Public helpers — kept small so the surface is obvious to a reviewer
# ──────────────────────────────────────────────────────────────────────
__all__ = ["sign", "verify", "input_hash", "VerificationResult"]
