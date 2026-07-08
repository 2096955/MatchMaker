"""
Two deterministic guards that sit in front of the matcher.

1. _read_vendor_frame — the single mock->S3 cutover point. Local reads come from
   the in-memory working set populated by uploads; prod reads the same shape from
   the S3 working-set bucket. Nothing else in the codebase reads vendor data.

2. check_scope — the rights/scope gate. It is DETERMINISTIC by design: it looks
   up a pre-materialised entitlement, it does not interpret contract text with a
   model. Similarity is not entitlement. Anything the lookup can't adjudicate
   returns allowed=False with a reason and is routed to a human — never
   green-lit by judgement. In prod this lookup hits the ODRL entitlements in
   Neptune; here it's a static allow-list keyed by vendor.

M8 SWAP CONTRACT — what the upstream pipeline must guarantee for
``_read_s3_frame`` to behave correctly (mirrors the brief Section 12 GATE
"FRAME_SOURCE=s3 returns a frame equal in shape to the mock path"):

  - One JSON object per (vendor, product_id) at
    ``s3://{s3_bucket}/{s3_prefix}{vendor}/{product_id}.json``.
    NOT parquet, NOT per-file shards — the read here is single-key get_object.
  - The body deserialises into ``VendorProductRef(**json)`` cleanly. A bad
    shape raises ``FrameDataError`` (NOT a plain ValidationError, so the
    HTTP route can distinguish "upstream wrote bad data" from
    "client sent a bad body" and surface a 502 instead of a 400).
  - ``product_id`` is the vendor's NATIVE primary key, verbatim. mds_iri()
    lowercases vendor but not product_id; if the pipeline ever rewrites
    product_id, every IRI silently re-forks (breaks I8). The upstream
    pipeline rejects rows where the primary-key field is null to its
    rejected/ bucket — it does NOT synthesise.
  - The reader populates ``source_content_hash`` (sha256 of the body bytes)
    and ``source_file_audit_id`` (from S3 ``x-amz-meta-file-audit-id``
    metadata when present) on every successful read, so the federated audit
    join with the upstream audit table is mechanical, not fuzzy.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from pydantic import ValidationError

from .config import settings
from .models import ScopeResult, VendorProductRef

_log = logging.getLogger(__name__)

# In-memory working set for the local prototype (populated by file uploads).
# Keyed by (vendor, product_id). The S3 path replaces this dict, same shape.
_WORKING_SET: dict[tuple[str, str], VendorProductRef] = {}

# Lazy-initialised boto3 client. Held at module scope so the production
# Lambda/ECS task keeps a warm client across reads, and so tests can inject
# a fake via ``_set_s3_client_for_test``.
_s3_client: Optional[Any] = None


class FrameDataError(Exception):
    """The S3 object exists but does not match the VendorProductRef shape.

    Distinguished from Pydantic's ValidationError so the HTTP route can
    map "upstream wrote bad data" to 502 instead of "client sent a bad
    request body" -> 400. Fail-closed at the boundary per I3 / Section 16.
    """


def put_frame(ref: VendorProductRef) -> None:
    _WORKING_SET[(ref.vendor, ref.product_id)] = ref


def all_frames() -> list[VendorProductRef]:
    return list(_WORKING_SET.values())


def clear_frames() -> None:
    _WORKING_SET.clear()


def _set_s3_client_for_test(client: Optional[Any]) -> None:
    """Test hook — inject a fake S3 client (or None to clear)."""
    global _s3_client
    _s3_client = client


def _get_s3_client() -> Any:
    """Return the cached boto3 client, importing it lazily on first use.

    Lazy so the package doesn't hard-require boto3 for local FalkorDB runs;
    falkordb_store imports the falkordb client the same way for the same
    reason.
    """
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    try:
        import boto3  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "boto3 is required for FRAME_SOURCE=s3. "
            "Install with: pip install boto3>=1.34"
        ) from e
    _s3_client = boto3.client("s3")
    return _s3_client


def _is_no_such_key(e: BaseException) -> bool:
    """True if the exception represents an absent S3 key.

    boto3 raises ``botocore.exceptions.ClientError`` with
    ``response['Error']['Code'] == 'NoSuchKey'``; test fakes raise a class
    named ``S3NoSuchKey`` to avoid pulling in botocore. Either is treated
    as "key absent" (mirrors the mock path's ``dict.get`` returning None).
    """
    response = getattr(e, "response", None) or {}
    code = (
        (response.get("Error") or {}).get("Code", "")
        if isinstance(response, dict)
        else ""
    )
    if code in ("NoSuchKey", "404", "NotFound"):
        return True
    return type(e).__name__ in ("NoSuchKey", "S3NoSuchKey")


def _s3_key_for(vendor: str, product_id: str) -> str:
    """The canonical key shape per the M8 SWAP CONTRACT.

    Documented separately so a contract test can import and assert against
    it — making the contract surface tight rather than a string literal
    sprinkled through the code.
    """
    prefix = settings.s3_prefix or ""
    return f"{prefix}{vendor}/{product_id}.json"


def _read_s3_frame(vendor: str, product_id: str) -> Optional[VendorProductRef]:
    """Read one product's frame from S3. Returns None when the key is absent.

    Raises:
        FrameDataError: the object exists but its body does not match the
            VendorProductRef shape (Pydantic validation failed, or the body
            is not parseable JSON). Fail-closed at the boundary.
        RuntimeError: settings.s3_bucket is unset (misconfiguration), or
            boto3 isn't installed.
        Anything else (e.g. credentials, transport): propagates so the
            route layer can map to a 503 store-unavailable response.
    """
    bucket = settings.s3_bucket
    if not bucket:
        raise RuntimeError("FRAME_SOURCE=s3 requires S3_WORKING_SET_BUCKET to be set")
    key = _s3_key_for(vendor, product_id)
    client = _get_s3_client()

    try:
        resp = client.get_object(Bucket=bucket, Key=key)
    except Exception as e:  # noqa: BLE001
        if _is_no_such_key(e):
            return None
        raise

    body = resp.get("Body")
    if hasattr(body, "read"):
        body_bytes = body.read()
    elif isinstance(body, (bytes, bytearray)):
        body_bytes = bytes(body)
    elif isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        raise FrameDataError(f"S3 object at s3://{bucket}/{key} has no readable body")

    content_hash = hashlib.sha256(body_bytes).hexdigest()

    # x-amz-meta-* user metadata; boto3 strips the prefix and lowercases.
    metadata = resp.get("Metadata") or {}
    audit_id = metadata.get("file-audit-id") or metadata.get("file_audit_id") or None

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FrameDataError(
            f"S3 object at s3://{bucket}/{key} is not valid JSON: {e}"
        ) from e

    if not isinstance(payload, dict):
        raise FrameDataError(f"S3 object at s3://{bucket}/{key} is not a JSON object")

    # The reader OVERWRITES source_* fields on the payload — they are
    # READER-COMPUTED provenance, not pipeline-supplied. An upstream that
    # tries to set them gets silently ignored, which is the right answer
    # for an audit field.
    payload["source_content_hash"] = content_hash
    payload["source_file_audit_id"] = audit_id

    try:
        return VendorProductRef(**payload)
    except ValidationError as e:
        raise FrameDataError(
            f"S3 object at s3://{bucket}/{key} does not match "
            f"VendorProductRef shape: {e}"
        ) from e


def _read_vendor_frame(vendor: str, product_id: str) -> Optional[VendorProductRef]:
    """THE swap point. Local: working set. Prod (M8): S3 object, same shape."""
    if settings.frame_source == "s3":
        return _read_s3_frame(vendor, product_id)
    return _WORKING_SET.get((vendor, product_id))


def check_scope(ref: VendorProductRef) -> ScopeResult:
    """Deterministic entitlement check. No model in this path.

    CONTRACT — TOTAL: this function NEVER raises. Any internal exception
    (a malformed ``ref``, or, once wired, a transient failure of the ODRL
    rights-lookup against Neptune) is converted to DENY with a reason.
    Fail-closed per I3 means "exception => deny", not merely "False => deny".
    Callers (``matching.map_vendor_product``, ``feedback.apply_decision``,
    ``bundle.import_bundle``) can therefore reason about the result
    without try/except scaffolding.
    """
    try:
        # Touch vendor early so malformed refs fail closed with the legacy
        # smoke reason string (scope_check_failure_is_treated_as_deny).
        _ = ref.vendor
        from .rights_odrl import evaluate_odrl_scope

        return evaluate_odrl_scope(ref)
    except Exception as e:  # noqa: BLE001 — fail-closed by design
        return ScopeResult(
            allowed=False,
            reason=f"scope check failed: {type(e).__name__}: {e}",
        )
