"""Mock backend for vendor_catalogue_mcp.

This module is the ONLY seam that changes on JPMC AWS cutover. The contract in
`contract.py` (schemas + IRI mint) and the @mcp.tool decorators in `server.py`
are stable; here we read parquet from a local synthetic dir today and will read
vendor snapshots from S3/Glue tomorrow.

Concerns living here:
  • _read_vendor_frame      — parquet load (the one swap point on cutover)
  • _read_tombstone_frame   — sibling parquet of REMOVED records (cutover swaps too)
  • _normalise_row          — column → NormalisedProduct (drift lands here)
  • the four tool bodies    — list / get / schema / delta against those frames

Conventions the mock owns (will be re-specified for the real LSEG snapshot):
  • Required columns on the catalogue frame:
        vendor_product_ref, title, snapshot_id, ingested_at, version
  • Optional catalogue columns surfaced as typed fields:
        description, theme, asset_class, keywords_json, identifiers_json,
        raw_attributes_json
  • Internal storage columns (NOT advertised by get_schema):
        snapshot_id, ingested_at, version, keywords_json, identifiers_json,
        raw_attributes_json, created_at, modified_at, removed_at
  • Delta machinery requires `created_at` and `modified_at` on the catalogue
    frame and `removed_at` + `vendor_product_ref` on the tombstone frame.
    `get_delta` raises a clear error if those are absent.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .contract import (
    CatalogueSchema,
    ChangeType,
    DeltaResult,
    NormalisedProduct,
    ProductDelta,
    ProductPage,
    ProductRef,
    Provenance,
    SchemaField,
    VendorId,
    product_iri,
)

_DEFAULT_MOCK_DIR = Path(__file__).resolve().parent / "synthetic" / "catalogue"


def _mock_dir() -> Path:
    return Path(os.environ.get("VENDOR_CATALOGUE_MOCK_DIR", _DEFAULT_MOCK_DIR))


# Columns the contract reads directly off the typed surface (catalogue product
# fields the vendor describes). Used for two things: get_schema filters to
# these, and _normalise_row knows everything else is "drift" → raw_attributes.
_CATALOGUE_COLUMNS = {
    "vendor_product_ref", "title", "description", "theme", "asset_class",
    "keywords_json", "identifiers_json", "raw_attributes_json",
}
# Internal storage / provenance / delta-machinery columns — present on the
# frame but NOT part of the vendor catalogue surface get_schema advertises.
_STORAGE_COLUMNS = {
    "snapshot_id", "ingested_at", "version",
    "created_at", "modified_at", "removed_at",
}
_ALL_KNOWN_COLUMNS = _CATALOGUE_COLUMNS | _STORAGE_COLUMNS

_REQUIRED_CATALOGUE_FIELDS = {"vendor_product_ref", "title"}
_REQUIRED_STORAGE_FIELDS = {"snapshot_id", "ingested_at", "version"}


# ────────────────────────────────────────────────────────────────────────────
# Frame loader — the ONE function that changes on JPMC AWS cutover
# ────────────────────────────────────────────────────────────────────────────
# Keyed cache replaces lru_cache so VENDOR_CATALOGUE_MOCK_DIR changes
# invalidate properly (e.g. between tests setting the env var).
_FRAME_CACHE: dict[tuple[str, str], pd.DataFrame] = {}
_TOMBSTONE_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def clear_cache() -> None:
    """Drop both frame caches — call from tests when changing fixture dirs."""
    _FRAME_CACHE.clear()
    _TOMBSTONE_CACHE.clear()


def _vendor_value(vendor: Any) -> str:
    return vendor.value if hasattr(vendor, "value") else str(vendor)


def _load_parquet_concat(files: list[Path]) -> pd.DataFrame:
    frames = [pd.read_parquet(f, engine="pyarrow") for f in files]
    df = pd.concat(frames, ignore_index=True)
    for col in ("ingested_at", "created_at", "modified_at", "removed_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


def _read_vendor_frame(vendor: Any) -> pd.DataFrame:
    """Return a tabular frame of the vendor's raw catalogue rows.

    MOCK: read parquet matching `{vendor}_*.parquet` (excluding `_tombstones`)
          from the mock dir.
    JPMC: read the vendor's normalised snapshot from S3 (Glue-catalogued),
          located via JPMC's Ingestion Framework — same return shape.

    The contract above this line depends on the columns enumerated in the
    module docstring; the cutover implementation must preserve those names or
    introduce a mapping layer here before returning.
    """
    vendor_value = _vendor_value(vendor)
    cache_key = (vendor_value, str(_mock_dir()))
    if cache_key in _FRAME_CACHE:
        return _FRAME_CACHE[cache_key]

    pattern = f"{vendor_value}_*.parquet"
    files = sorted(
        f for f in _mock_dir().glob(pattern) if "_tombstones" not in f.name
    )
    if not files:
        raise FileNotFoundError(
            f"No mock parquet for vendor={vendor_value!r} in {_mock_dir()} "
            f"(pattern {pattern}). Run scripts/generate_sample_parquet.py."
        )
    df = _load_parquet_concat(files)
    _FRAME_CACHE[cache_key] = df
    return df


def _read_tombstone_frame(vendor: Any) -> pd.DataFrame:
    """Return the REMOVED-record sidecar frame for this vendor, or an empty one.

    MOCK: reads `{vendor}_tombstones.parquet` if present.
    JPMC: reads the vendor's tombstone partition on S3 (Glue-catalogued).
    Required columns: vendor_product_ref, removed_at. Optional: title.
    """
    vendor_value = _vendor_value(vendor)
    cache_key = (vendor_value, str(_mock_dir()))
    if cache_key in _TOMBSTONE_CACHE:
        return _TOMBSTONE_CACHE[cache_key]

    path = _mock_dir() / f"{vendor_value}_tombstones.parquet"
    if not path.exists():
        df = pd.DataFrame(columns=["vendor_product_ref", "removed_at", "title"])
        df["removed_at"] = pd.to_datetime(df["removed_at"], utc=True)
        _TOMBSTONE_CACHE[cache_key] = df
        return df
    df = _load_parquet_concat([path])
    _TOMBSTONE_CACHE[cache_key] = df
    return df


# ────────────────────────────────────────────────────────────────────────────
# Null / JSON / column-access helpers
# ────────────────────────────────────────────────────────────────────────────
def _is_missing(value: Any) -> bool:
    """True for None, NaN, pd.NA, pd.NaT, and any pandas missing sentinel."""
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    # pd.isna returns an ndarray for list/dict — non-scalar inputs aren't missing.
    if isinstance(result, bool):
        return result
    return False


def _loads_or(value: Any, default: Any) -> Any:
    if _is_missing(value):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _opt_str(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    return str(value)


def _require_column(row: pd.Series, name: str, *, df_columns: list[str]) -> Any:
    """Read a required column from a row; raise a diagnosable error otherwise."""
    if name not in row.index:
        raise ValueError(
            f"Required column {name!r} missing from vendor frame. "
            f"Available columns: {sorted(df_columns)}"
        )
    value = row[name]
    if _is_missing(value):
        raise ValueError(
            f"Required column {name!r} present but null for "
            f"vendor_product_ref={row.get('vendor_product_ref', '?')!r}."
        )
    return value


# ────────────────────────────────────────────────────────────────────────────
# Column → NormalisedProduct mapping
# ────────────────────────────────────────────────────────────────────────────
def _normalise_row(row: pd.Series, vendor: VendorId, df_columns: list[str]) -> NormalisedProduct:
    vpr = str(_require_column(row, "vendor_product_ref", df_columns=df_columns))
    title = str(_require_column(row, "title", df_columns=df_columns))

    raw_attributes = dict(_loads_or(row.get("raw_attributes_json"), {}) or {})
    # Defence in depth: a vendor that ships raw_attributes_json containing keys
    # that collide with typed catalogue fields would silently produce two
    # assertions for the same field. Strip those keys — the typed value wins.
    for collision in (_CATALOGUE_COLUMNS | _STORAGE_COLUMNS):
        raw_attributes.pop(collision, None)

    # Drift absorption: surface any unexpected columns into raw_attributes so
    # nothing native is lost when the real schema gains fields.
    for col in row.index:
        if col in _ALL_KNOWN_COLUMNS:
            continue
        val = row[col]
        if _is_missing(val):
            continue
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        raw_attributes.setdefault(col, val)

    ingested_at = _require_column(row, "ingested_at", df_columns=df_columns)
    if hasattr(ingested_at, "to_pydatetime"):
        ingested_at = ingested_at.to_pydatetime()
    if getattr(ingested_at, "tzinfo", None) is None:
        ingested_at = ingested_at.replace(tzinfo=timezone.utc)

    snapshot_id = str(_require_column(row, "snapshot_id", df_columns=df_columns))
    version_raw = _require_column(row, "version", df_columns=df_columns)
    try:
        version = int(version_raw)
    except (TypeError, ValueError):
        # Multi-file concat can promote int → float with NaN fills; missing
        # version is already rejected by _require_column, but a non-integer
        # float (e.g. 1.5) shouldn't silently truncate — fail loudly.
        raise ValueError(
            f"Column 'version' for vpr={vpr!r} is not coercible to int: {version_raw!r}"
        )

    provenance = Provenance(
        source=vendor,
        source_snapshot=snapshot_id,
        ingested_at=ingested_at,
        version=version,
    )

    identifiers = {
        k: str(v) for k, v in (_loads_or(row.get("identifiers_json"), {}) or {}).items()
    }

    return NormalisedProduct(
        iri=product_iri(vendor, vpr),
        vendor=vendor,
        vendor_product_ref=vpr,
        title=title,
        description=_opt_str(row.get("description")),
        theme=_opt_str(row.get("theme")),
        keywords=list(_loads_or(row.get("keywords_json"), []) or []),
        asset_class=_opt_str(row.get("asset_class")),
        identifiers=identifiers,
        raw_attributes=raw_attributes,
        provenance=provenance,
    )


# ────────────────────────────────────────────────────────────────────────────
# Filter-bound cursor — encodes vendor + modified_since alongside the offset
# so changing filters between pages raises rather than silently misalign.
# ────────────────────────────────────────────────────────────────────────────
def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _encode_cursor(*, vendor: VendorId, modified_since: Optional[datetime], offset: int) -> str:
    payload = json.dumps(
        {"v": _vendor_value(vendor), "f": _iso_or_none(modified_since), "o": offset},
        sort_keys=True,
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(
    cursor: str, *, vendor: VendorId, modified_since: Optional[datetime]
) -> int:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        v, f, offset = payload["v"], payload["f"], int(payload["o"])
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError(f"Malformed cursor: {cursor!r}")
    if v != _vendor_value(vendor):
        raise ValueError(
            f"Cursor was issued for vendor={v!r}; current call is for "
            f"vendor={_vendor_value(vendor)!r}. Re-list from page 1."
        )
    if f != _iso_or_none(modified_since):
        raise ValueError(
            "Cursor was issued for a different modified_since filter. "
            "Either repeat the original filter or re-list from page 1."
        )
    return offset


# ────────────────────────────────────────────────────────────────────────────
# list_products
# ────────────────────────────────────────────────────────────────────────────
def list_products(
    *,
    vendor: VendorId,
    cursor: Optional[str],
    limit: int,
    modified_since: Optional[datetime],
) -> ProductPage:
    df = _read_vendor_frame(vendor)
    if modified_since is not None:
        if "modified_at" not in df.columns:
            raise ValueError(
                "modified_since filter requires a 'modified_at' column on the "
                "vendor frame; column is absent."
            )
        ms = modified_since if modified_since.tzinfo else modified_since.replace(tzinfo=timezone.utc)
        df = df[df["modified_at"] > pd.Timestamp(ms)]

    df = df.sort_values("vendor_product_ref", kind="stable").reset_index(drop=True)

    start = _decode_cursor(cursor, vendor=vendor, modified_since=modified_since) if cursor else 0
    end = start + limit
    page = df.iloc[start:end]

    refs = [
        ProductRef(
            iri=product_iri(vendor, str(r["vendor_product_ref"])),
            vendor=vendor,
            vendor_product_ref=str(r["vendor_product_ref"]),
            title=str(r["title"]),
        )
        for _, r in page.iterrows()
    ]
    next_cursor = (
        _encode_cursor(vendor=vendor, modified_since=modified_since, offset=end)
        if end < len(df)
        else None
    )
    return ProductPage(items=refs, next_cursor=next_cursor)


# ────────────────────────────────────────────────────────────────────────────
# get_product
# ────────────────────────────────────────────────────────────────────────────
def get_product(*, vendor: VendorId, vendor_product_ref: str) -> NormalisedProduct:
    df = _read_vendor_frame(vendor)
    # Match via string-cast so non-string vpr columns (e.g. numeric refs from a
    # future vendor) still resolve symmetrically with list_products.
    hits = df[df["vendor_product_ref"].astype(str) == vendor_product_ref]
    if hits.empty:
        raise LookupError(
            f"No product with vendor_product_ref={vendor_product_ref!r} for vendor={vendor}"
        )
    row = hits.sort_values("version", ascending=False).iloc[0]
    return _normalise_row(row, vendor, list(df.columns))


# ────────────────────────────────────────────────────────────────────────────
# get_schema — catalogue-facing columns only
# ────────────────────────────────────────────────────────────────────────────
_SCHEMA_VERSION = "0.1.0-mock"  # bump when the real LSEG schema arrives


def _dtype_to_contract(dtype) -> str:
    s = str(dtype)
    if s.startswith("datetime64"):
        return "datetime"
    if "int" in s.lower():
        return "integer"
    if "float" in s.lower() or "decimal" in s.lower():
        return "decimal"
    if s == "bool":
        return "boolean"
    return "string"


def get_schema(*, vendor: VendorId) -> CatalogueSchema:
    df = _read_vendor_frame(vendor)
    # Only advertise catalogue-facing columns. Storage / provenance / delta-
    # machinery columns are implementation detail of this seam.
    fields = [
        SchemaField(
            name=col,
            type=_dtype_to_contract(df[col].dtype),
            required=col in _REQUIRED_CATALOGUE_FIELDS,
        )
        for col in df.columns
        if col in _CATALOGUE_COLUMNS
    ]
    return CatalogueSchema(vendor=vendor, schema_version=_SCHEMA_VERSION, fields=fields)


# ────────────────────────────────────────────────────────────────────────────
# get_delta — ADDED/UPDATED from the catalogue frame, REMOVED from tombstones
# ────────────────────────────────────────────────────────────────────────────
# Recognise watermarks of the form `{vendor}-<ISO-8601>` by stripping a known
# vendor prefix exactly — avoids hyphen-in-slug brittleness of rsplit().
_KNOWN_VENDOR_PREFIXES = tuple(sorted((v.value for v in VendorId), key=len, reverse=True))
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_since(since: str) -> datetime:
    """Accept ISO-8601 (with or without `Z`/offset) or `{vendor}-<ISO-8601>`.

    Always returns a UTC-aware datetime. The vendor prefix is identified by
    longest-match against the VendorId enum, so a slug like `sp-global` (if
    ever added) is stripped exactly rather than via positional split.
    """
    candidate = since
    for prefix in _KNOWN_VENDOR_PREFIXES:
        if candidate.startswith(prefix + "-"):
            candidate = candidate[len(prefix) + 1:]
            break

    if _DATE_ONLY_RE.match(candidate):
        return datetime.fromisoformat(candidate).replace(tzinfo=timezone.utc)

    try:
        return _ensure_utc(datetime.fromisoformat(candidate.replace("Z", "+00:00")))
    except ValueError:
        raise ValueError(f"Unparseable watermark: {since!r}")


def _format_next_watermark(vendor: VendorId, ts: datetime) -> str:
    """Emit `{vendor}-<full-ISO-8601>`. Full precision so re-feeding the
    returned watermark produces zero deltas on an unchanged frame."""
    ts = _ensure_utc(ts)
    return f"{_vendor_value(vendor)}-{ts.isoformat()}"


def _require_delta_columns(df: pd.DataFrame) -> None:
    missing = {"created_at", "modified_at"} - set(df.columns)
    if missing:
        raise ValueError(
            f"get_delta requires {sorted({'created_at', 'modified_at'})} on the "
            f"vendor frame; missing: {sorted(missing)}. The delta mechanism is a "
            "mock-side convention; the JPMC AWS seam must provide an equivalent "
            "change-tracking signal before this tool will function."
        )


def get_delta(*, vendor: VendorId, since: str) -> DeltaResult:
    df = _read_vendor_frame(vendor)
    tombstones = _read_tombstone_frame(vendor)

    _require_delta_columns(df)

    since_dt = _parse_since(since)
    since_ts = pd.Timestamp(since_dt)

    added_mask = df["created_at"] > since_ts
    updated_mask = (df["modified_at"] > since_ts) & ~added_mask

    def _ref_from_row(row: pd.Series) -> ProductRef:
        vpr = str(row["vendor_product_ref"])
        return ProductRef(
            iri=product_iri(vendor, vpr),
            vendor=vendor,
            vendor_product_ref=vpr,
            title=str(row.get("title", vpr)),
        )

    deltas: list[ProductDelta] = []
    for _, r in df[added_mask].iterrows():
        deltas.append(ProductDelta(change_type=ChangeType.ADDED, ref=_ref_from_row(r)))
    for _, r in df[updated_mask].iterrows():
        deltas.append(ProductDelta(change_type=ChangeType.UPDATED, ref=_ref_from_row(r)))

    # REMOVED — from tombstone sidecar.
    removed_count = 0
    if not tombstones.empty and "removed_at" in tombstones.columns:
        removed_mask = tombstones["removed_at"] > since_ts
        for _, r in tombstones[removed_mask].iterrows():
            deltas.append(ProductDelta(change_type=ChangeType.REMOVED, ref=_ref_from_row(r)))
        removed_count = int(removed_mask.sum())

    counts = {
        "added": int(added_mask.sum()),
        "updated": int(updated_mask.sum()),
        "removed": removed_count,
    }

    # Watermark = latest event time seen across catalogue + tombstone frames.
    # Full ISO precision so a same-day repeat poll does not re-emit deltas.
    candidates: list[pd.Timestamp] = []
    for col in ("modified_at", "created_at"):
        if col in df.columns and not df[col].empty:
            m = df[col].max()
            if pd.notna(m):
                candidates.append(m)
    if "removed_at" in tombstones.columns and not tombstones["removed_at"].empty:
        m = tombstones["removed_at"].max()
        if pd.notna(m):
            candidates.append(m)

    if candidates:
        latest = max(candidates)
        next_wm = _format_next_watermark(vendor, latest.to_pydatetime())
    else:
        next_wm = since

    return DeltaResult(
        vendor=vendor,
        since_watermark=since,
        next_watermark=next_wm,
        deltas=deltas,
        counts=counts,
    )
