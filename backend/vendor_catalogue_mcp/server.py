"""Vendor-Catalogue MCP — server entry point.

The schemas and IRI mint live in `contract.py` (the load-bearing surface).
The implementations live in `mock_backend.py` (the single S3/Glue swap seam).
This file just wires them together via @mcp.tool decorators.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import mock_backend
from .contract import (
    Authority,
    CatalogueSchema,
    ChangeType,
    DeltaResult,
    DescribeInput,
    GetDeltaInput,
    GetProductInput,
    GetSchemaInput,
    ListProductsInput,
    NormalisedProduct,
    ProductDelta,
    ProductPage,
    ProductRef,
    Provenance,
    SchemaField,
    VendorId,
    product_iri,
)

__all__ = [
    "Authority", "CatalogueSchema", "ChangeType", "DeltaResult",
    "DescribeInput", "GetDeltaInput", "GetProductInput", "GetSchemaInput",
    "ListProductsInput", "NormalisedProduct", "ProductDelta", "ProductPage",
    "ProductRef", "Provenance", "SchemaField", "VendorId",
    "catalogue_list_products", "catalogue_get_product",
    "catalogue_get_schema", "catalogue_get_delta", "catalogue_describe",
    "product_iri", "mcp",
]


mcp = FastMCP("vendor_catalogue_mcp")

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
              "idempotentHint": True, "openWorldHint": False}


# ────────────────────────────────────────────────────────────────────────────
# THE CONTRACT — tool signatures
# ────────────────────────────────────────────────────────────────────────────
@mcp.tool(name="catalogue_list_products", annotations=_READ_ONLY)
async def catalogue_list_products(params: ListProductsInput) -> ProductPage:
    """List a vendor's catalogue products as lightweight refs, with cursor paging."""
    return mock_backend.list_products(
        vendor=params.vendor,
        cursor=params.cursor,
        limit=params.limit,
        modified_since=params.modified_since,
    )


@mcp.tool(name="catalogue_get_product", annotations=_READ_ONLY)
async def catalogue_get_product(params: GetProductInput) -> NormalisedProduct:
    """Return one fully normalised, DCAT-aligned product record with provenance."""
    return mock_backend.get_product(
        vendor=params.vendor,
        vendor_product_ref=params.vendor_product_ref,
    )


@mcp.tool(name="catalogue_get_schema", annotations=_READ_ONLY)
async def catalogue_get_schema(params: GetSchemaInput) -> CatalogueSchema:
    """Return the vendor's declared catalogue schema (field names, types, required)."""
    return mock_backend.get_schema(vendor=params.vendor)


@mcp.tool(name="catalogue_get_delta", annotations=_READ_ONLY)
async def catalogue_get_delta(params: GetDeltaInput) -> DeltaResult:
    """Return products added/updated/removed since a watermark (exclusive)."""
    return mock_backend.get_delta(vendor=params.vendor, since=params.since)


@mcp.tool(name="catalogue_describe", annotations=_READ_ONLY)
async def catalogue_describe(params: DescribeInput) -> dict:
    """Registry-facing capability descriptor for this vendor."""
    return {
        "vendor": params.vendor.value,
        "tools": [
            "catalogue_list_products",
            "catalogue_get_product",
            "catalogue_get_schema",
            "catalogue_get_delta",
            "catalogue_describe",
        ],
        "iri_pattern": f"mds.{params.vendor.value}:<uuid>",
        "replay_safe": True,
        "asserts": "vendor_catalogue_only",
    }


if __name__ == "__main__":
    # stdio for local dev; switch to streamable HTTP on JPMC AWS.
    mcp.run()
