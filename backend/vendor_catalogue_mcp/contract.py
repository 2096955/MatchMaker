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
    """Canonical product IRI: `mds.<vendor>:<uuid5>`. Deterministic in inputs."""
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
