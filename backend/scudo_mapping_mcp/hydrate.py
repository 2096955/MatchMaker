"""
M6 hydration — replay the canonical mapping bundle into the local FalkorDB
working graph at container startup.

This is the resilience pin from the SCUDO matching strategy: FalkorDB is the
fast non-authoritative tier. A stale or empty FalkorDB serves confident-but-
wrong matches — exactly the drift class the rank-signal counter bug was. Pulling
the canonical bundle from S3 at boot and replaying it via ``bundle.import_bundle``
is the structural defence. The container's health check MUST NOT pass until
``hydrate()`` returns — that is what stops a half-hydrated FalkorDB serving
"confident-but-empty" results during a rolling deploy.

CONTRACT:

  - Pulls the canonical bundle JSON from S3 (default
    ``s3://${S3_WORKING_SET_BUCKET}/canonical/bundle-latest.json``; override
    with ``SCUDO_CANONICAL_BUNDLE_URI`` or ``SCUDO_CANONICAL_BUNDLE_KEY``).
  - Replays via ``bundle.import_bundle`` — idempotent (``upsert_precedent``
    uses MERGE), so re-running on the same bundle into the same store leaves
    the graph identical on every axis.
  - Cold-start (no bundle yet) is a logged WARN, not an error. First deploy
    has nothing to replay; ``HydrationResult.skipped_no_bundle = True``.
  - Anything else that breaks the contract (S3 access denied, corrupt JSON,
    schema mismatch, bundle format-major mismatch) is a hard ``HydrationError``.
    Better unhealthy than half-hydrated.

The canonical source is S3 today; once M7 wires Neptune, the canonical source
becomes a Neptune-side export. Same ``import_bundle`` entry point either way.
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from .bundle import import_bundle
from .config import settings
from .models import MappingBundle

_log = logging.getLogger(__name__)


# Test injection hook — lets the smoke suite point at a FakeS3Client without
# importing boto3. Same pattern as ``frames._set_s3_client_for_test``.
_s3_client_for_test = None


def _set_s3_client_for_test(client) -> None:
    global _s3_client_for_test
    _s3_client_for_test = client


def _s3_client():
    if _s3_client_for_test is not None:
        return _s3_client_for_test
    import boto3  # lazy — mock-only runs don't need boto3 installed
    return boto3.client("s3")


@dataclass(frozen=True)
class HydrationResult:
    """Summary of one ``hydrate()`` call.

    ``skipped_no_bundle`` is True when the canonical S3 key didn't exist
    (cold start). Every other field is zero in that case.
    """
    skipped_no_bundle: bool
    bundle_version: Optional[str]
    bundle_taxonomy_version: Optional[str]
    bundle_source_env: Optional[str]
    applied: int
    skipped_unknown_node: int
    skipped_out_of_scope: int
    total: int


class HydrationError(RuntimeError):
    """Raised on hard failure that should leave the container unhealthy.

    Specifically: S3 access denied / misconfiguration, corrupt JSON, schema
    mismatch, or bundle format-major mismatch. NOT raised on "no bundle yet"
    — that's a logged WARN and ``HydrationResult.skipped_no_bundle = True``.
    """


_DEFAULT_BUNDLE_KEY = "canonical/bundle-latest.json"


def _bundle_s3_location() -> tuple[str, str]:
    """Return (bucket, key) for the canonical bundle.

    Resolution order:
      1. ``SCUDO_CANONICAL_BUNDLE_URI`` env var (``s3://bucket/key``) overrides
         everything — the explicit pin for environments that point at a shared
         canonical bucket (e.g. a Persistence MCP writing to one bucket, a
         Match&Verify MCP hydrating from the same).
      2. ``settings.s3_bucket`` + ``SCUDO_CANONICAL_BUNDLE_KEY`` env var
         (defaults to ``canonical/bundle-latest.json``).
    """
    uri = (os.getenv("SCUDO_CANONICAL_BUNDLE_URI") or "").strip()
    if uri:
        if not uri.startswith("s3://"):
            raise HydrationError(
                f"SCUDO_CANONICAL_BUNDLE_URI must be 's3://bucket/key', got {uri!r}"
            )
        rest = uri[len("s3://"):]
        bucket, _, key = rest.partition("/")
        if not bucket or not key:
            raise HydrationError(
                f"SCUDO_CANONICAL_BUNDLE_URI malformed (need s3://bucket/key): {uri!r}"
            )
        return bucket, key

    bucket = (settings.s3_bucket or "").strip()
    if not bucket:
        raise HydrationError(
            "Cannot resolve canonical bundle location: neither "
            "SCUDO_CANONICAL_BUNDLE_URI nor S3_WORKING_SET_BUCKET is set."
        )
    key = (os.getenv("SCUDO_CANONICAL_BUNDLE_KEY") or "").strip() \
        or _DEFAULT_BUNDLE_KEY
    return bucket, key


def _is_no_such_key(exc: BaseException) -> bool:
    """Match the S3 NoSuchKey error by class name without importing botocore.

    Same heuristic as ``frames._is_no_such_key`` — covers FakeS3Client's
    ``S3NoSuchKey`` (tests) and botocore's ``ClientError`` (prod).
    """
    name = type(exc).__name__
    if name in {"NoSuchKey", "S3NoSuchKey"}:
        return True
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code", "")
        return code in {"NoSuchKey", "404"}
    return False


def _read_body(body) -> bytes:
    """Drain the S3 GetObject body to bytes — handles BytesIO (tests) and
    botocore.response.StreamingBody (prod) uniformly."""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if hasattr(body, "read"):
        data = body.read()
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
    if isinstance(body, io.IOBase):
        return body.read()
    raise HydrationError(
        f"Unexpected S3 Body type {type(body).__name__}; cannot read bytes"
    )


def hydrate(strict: bool = True) -> HydrationResult:
    """Replay the canonical bundle into the configured store.

    Returns a ``HydrationResult`` summarising what happened. The container's
    startup orchestration MUST block until this returns or raises — that's
    what stops the health check passing on a half-hydrated FalkorDB.

    Args:
        strict: If True (default), any S3 error other than NoSuchKey raises
            ``HydrationError``. If False (degraded-mode override), non-NoSuchKey
            S3 errors are logged WARN and the result reports
            ``skipped_no_bundle=True`` so the container at least comes up.

    Raises:
        HydrationError: on corrupt JSON, schema mismatch, format-major
            mismatch, or (when ``strict=True``) any S3 error other than
            NoSuchKey.
    """
    bucket, key = _bundle_s3_location()
    _log.info("hydrate: reading canonical bundle from s3://%s/%s", bucket, key)

    s3 = _s3_client()
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 — botocore.ClientError variants
        if _is_no_such_key(exc):
            _log.warning(
                "hydrate: no canonical bundle at s3://%s/%s — cold start; "
                "FalkorDB stays empty until the first export lands at this key",
                bucket, key,
            )
            return _empty_result(skipped_no_bundle=True)
        msg = f"hydrate: failed to read s3://{bucket}/{key}: " \
              f"{type(exc).__name__}: {exc}"
        if strict:
            raise HydrationError(msg) from exc
        _log.warning("%s (degraded-mode override; container will start empty)", msg)
        return _empty_result(skipped_no_bundle=True)

    body_bytes = _read_body(resp["Body"])
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HydrationError(
            f"hydrate: s3://{bucket}/{key} is not valid JSON: {exc}"
        ) from exc

    try:
        bundle = MappingBundle.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError + anything weird
        raise HydrationError(
            f"hydrate: s3://{bucket}/{key} does not match MappingBundle shape: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    _log.info(
        "hydrate: bundle ok (version=%s, taxonomy_version=%s, "
        "source_env=%s, patterns=%d); replaying via import_bundle",
        bundle.version, bundle.taxonomy_version, bundle.source_env,
        len(bundle.patterns),
    )

    try:
        summary = import_bundle(bundle)
    except ValueError as exc:
        # _enforce_format_compat raises ValueError on major mismatch — hard fail.
        raise HydrationError(
            f"hydrate: import_bundle refused the bundle: {exc}"
        ) from exc

    _log.info(
        "hydrate: applied=%d skipped_unknown_node=%d "
        "skipped_out_of_scope=%d total=%d",
        summary.applied, summary.skipped_unknown_node,
        summary.skipped_out_of_scope, summary.total,
    )

    return HydrationResult(
        skipped_no_bundle=False,
        bundle_version=bundle.version,
        bundle_taxonomy_version=bundle.taxonomy_version,
        bundle_source_env=bundle.source_env,
        applied=summary.applied,
        skipped_unknown_node=summary.skipped_unknown_node,
        skipped_out_of_scope=summary.skipped_out_of_scope,
        total=summary.total,
    )


def _empty_result(*, skipped_no_bundle: bool) -> HydrationResult:
    return HydrationResult(
        skipped_no_bundle=skipped_no_bundle,
        bundle_version=None,
        bundle_taxonomy_version=None,
        bundle_source_env=None,
        applied=0,
        skipped_unknown_node=0,
        skipped_out_of_scope=0,
        total=0,
    )


if __name__ == "__main__":  # pragma: no cover — operator entry point
    # CLI: python -m scudo_mapping_mcp.hydrate
    # Useful for an init-container or manual operator triage.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = hydrate(strict=True)
    except HydrationError as exc:
        _log.error("hydration failed: %s", exc)
        raise SystemExit(2) from exc
    if result.skipped_no_bundle:
        _log.warning("hydration: skipped (no canonical bundle present)")
        raise SystemExit(0)
    _log.info(
        "hydration: applied %d/%d patterns (bundle version=%s, taxonomy=%s)",
        result.applied, result.total, result.bundle_version,
        result.bundle_taxonomy_version,
    )
    raise SystemExit(0)
