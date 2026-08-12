"""Vendor-Catalogue MCP — Contract (v0.1)

The load-bearing surface: Pydantic schemas, the VendorId enum, and the
deterministic IRI mint. Both `server` (which hosts the @mcp.tool decorators)
and `mock_backend` (which implements them) import from here, eliminating the
type-injection seam — the module file IS the seam, not individual callables.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field


# ────────────────────────────────────────────────────────────────────────────
# Vendor identity + deterministic IRI minting
# ────────────────────────────────────────────────────────────────────────────
class VendorId(str, Enum):
    LSEG = "lseg"
    SPGLOBAL = "spglobal"
    BLOOMBERG = "bloomberg"
    ICE = "ice"
    FACTSET = "factset"


_IRI_NAMESPACE = uuid5(NAMESPACE_URL, "https://mds.jpmc.internal/catalogue")


def product_iri(vendor: VendorId, vendor_product_ref: str) -> str:
    """NON-CANONICAL, DEMO-ONLY product IRI. Never use for anything that reaches
    the store.

    Shape is `mds.<vendor>:<uuid5>` and it is deterministic in its inputs, but it
    is NOT the system's identity mint. The canonical mint is
    ``scudo_mapping_mcp.models.mds_iri`` — it uses a different namespace seed
    (``6f2a9c4e-…``) and a different key (``"<vendor>::<ref>"``, double colon,
    lower-cased), so the two produce DIFFERENT uuid5s for the same product:

        mds_iri("S&P Global", "SPGI-1") -> mds.sandpglobal:724e610b-9dfb-5012-9125-fe7e16e99eff
        product_iri(SPGLOBAL, "SPGI-1") -> mds.spglobal:848af514-595e-55f5-b34c-f9a7ccdfc712

    WHY IT IS LEFT DIVERGENT (deliberate, 2026-08). This package is parallel demo
    code: a synthetic-parquet catalogue behind the stdio MCP server and the
    ``/api/catalogue`` HTTP facade. ``product_iri`` has ZERO callers outside this
    package, and nothing in ``scudo_mapping_mcp`` imports ``vendor_catalogue_mcp``
    at all, so no IRI minted here is a MERGE key for a VendorProduct node. Making
    it delegate to ``mds_iri`` would change every IRI the catalogue facade already
    serves (and every ``ProductRef.iri`` in its cursors/deltas) to buy nothing on
    the store side — strictly more risk than labelling it. If this package ever
    starts feeding the matcher or the store, DELETE this function and call
    ``scudo_mapping_mcp.models.mds_iri`` instead; do not "align" it here.

    Guarded by ``backend/scudo/tests/test_iri_mint_parity.py``, which fails if
    this symbol is ever imported into ``scudo`` or ``scudo_mapping_mcp``.
    """
    u = uuid5(_IRI_NAMESPACE, f"{vendor.value}:{vendor_product_ref}")
    return f"mds.{vendor.value}:{u}"


# ────────────────────────────────────────────────────────────────────────────
# Provenance envelope
# ────────────────────────────────────────────────────────────────────────────
class Authority(str, Enum):
    AUTHORITATIVE = "authoritative_vendor_assertion"
    INFERRED = "inferred"


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: VendorId
    source_snapshot: str = Field(..., examples=["lseg-2026-05-19"])
    ingested_at: datetime
    version: int = Field(..., ge=1)
    authority: Authority = Field(default=Authority.AUTHORITATIVE)


# ────────────────────────────────────────────────────────────────────────────
# Canonical normalised product
# ────────────────────────────────────────────────────────────────────────────
class NormalisedProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iri: str = Field(..., examples=["mds.lseg:9e420bc7-2a1f-5c3d-8e4a-1b2c3d4e5f60"])
    vendor: VendorId
    vendor_product_ref: str = Field(..., min_length=1, examples=["LSEG-IBES-EST-001"])

    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    theme: Optional[str] = Field(default=None, examples=["Investment Data"])
    keywords: list[str] = Field(default_factory=list, max_length=50)

    asset_class: Optional[str] = Field(default=None, examples=["Equities"])
    identifiers: dict[str, str] = Field(default_factory=dict)

    raw_attributes: dict = Field(default_factory=dict)

    provenance: Provenance


class ProductRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iri: str
    vendor: VendorId
    vendor_product_ref: str
    title: str


# ────────────────────────────────────────────────────────────────────────────
# Schema + drift awareness
# ────────────────────────────────────────────────────────────────────────────
class SchemaField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str = Field(..., description="string|integer|decimal|boolean|date|datetime")
    required: bool = False


class CatalogueSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId
    schema_version: str = Field(..., examples=["1.2.0"])
    fields: list[SchemaField]


# ────────────────────────────────────────────────────────────────────────────
# Deltas — boundary semantics: `since` is EXCLUSIVE (strict `>`). The watermark
# returned by `next_watermark` is the latest event time SEEN, full ISO-8601
# precision; re-feeding it yields zero deltas on a static frame. Rows with a
# modified_at exactly equal to a prior watermark are not re-emitted.
# ────────────────────────────────────────────────────────────────────────────
class ChangeType(str, Enum):
    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"


class ProductDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_type: ChangeType
    ref: ProductRef


class DeltaResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId
    since_watermark: str
    next_watermark: str
    deltas: list[ProductDelta]
    counts: dict[str, int] = Field(default_factory=dict)


class ProductPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProductRef]
    next_cursor: Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# Tool inputs
# ────────────────────────────────────────────────────────────────────────────
class ListProductsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId = Field(default=VendorId.LSEG)
    cursor: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)
    modified_since: Optional[datetime] = None


class GetProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId = Field(default=VendorId.LSEG)
    vendor_product_ref: str = Field(..., min_length=1, examples=["LSEG-IBES-EST-001"])


class GetSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId = Field(default=VendorId.LSEG)


class GetDeltaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId = Field(default=VendorId.LSEG)
    since: str = Field(
        ...,
        description=(
            "Watermark to fetch deltas after — EXCLUSIVE. Accepts either an "
            "ISO-8601 timestamp (e.g. '2026-05-12T00:00:00Z') or a "
            "`{vendor}-<ISO-8601>` form as emitted by `next_watermark`."
        ),
        examples=["2026-05-12T00:00:00Z", "lseg-2026-05-19T14:32:00+00:00"],
    )


class DescribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: VendorId = Field(default=VendorId.LSEG)
