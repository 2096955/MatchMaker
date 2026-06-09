"""Smoke driver — exercises every tool body and the post-review regressions.

Run:
  python -m vendor_catalogue_mcp.tests.smoke
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from vendor_catalogue_mcp import mock_backend
from vendor_catalogue_mcp.server import (
    DescribeInput, GetDeltaInput, GetProductInput, GetSchemaInput,
    ListProductsInput, VendorId,
    catalogue_describe, catalogue_get_delta, catalogue_get_product,
    catalogue_get_schema, catalogue_list_products,
    product_iri,
)


async def main() -> None:
    vendor = VendorId.LSEG
    mock_backend.clear_cache()

    # ── describe (now includes itself, fix #15) ───────────────────────────
    desc = await catalogue_describe(DescribeInput(vendor=vendor))
    print("describe:", json.dumps(desc, indent=2))
    assert desc["vendor"] == "lseg"
    assert "catalogue_describe" in desc["tools"], "describe must list itself"

    # ── list_products: full sweep + pagination exhaustion ─────────────────
    page1 = await catalogue_list_products(ListProductsInput(vendor=vendor, limit=10))
    print(f"\nlist_products page1: {len(page1.items)} items, next_cursor={page1.next_cursor!r}")
    assert len(page1.items) == 10
    assert page1.next_cursor is not None

    seen = list(page1.items)
    cursor = page1.next_cursor
    while cursor:
        p = await catalogue_list_products(
            ListProductsInput(vendor=vendor, limit=10, cursor=cursor)
        )
        seen.extend(p.items)
        cursor = p.next_cursor
    print(f"list_products exhausted: total seen = {len(seen)}")
    assert len(seen) == 29

    # ── Cursor must be bound to its filter (fix #2). Page with a filter,
    #    then try to reuse that cursor without the filter → ValueError.
    filtered_p1 = await catalogue_list_products(
        ListProductsInput(
            vendor=vendor, limit=5,
            modified_since=datetime(2026, 4, 20, tzinfo=timezone.utc),
        )
    )
    if filtered_p1.next_cursor:
        try:
            await catalogue_list_products(
                ListProductsInput(vendor=vendor, limit=5, cursor=filtered_p1.next_cursor)
            )
        except ValueError as e:
            print(f"\ncursor-filter binding OK: {e}")
        else:
            raise AssertionError("Cursor must reject mismatched modified_since filter")

    # ── get_product + replay-safety ───────────────────────────────────────
    target = page1.items[0]
    prod = await catalogue_get_product(
        GetProductInput(vendor=vendor, vendor_product_ref=target.vendor_product_ref)
    )
    print(f"\nget_product: {prod.vendor_product_ref}  {prod.title}")
    print(f"  iri:          {prod.iri}")
    print(f"  identifiers:  {prod.identifiers}")
    print(f"  raw_attrs:    {sorted(prod.raw_attributes.keys())}")
    assert product_iri(vendor, prod.vendor_product_ref) == prod.iri

    # raw_attributes must NOT contain typed-field keys (fix #5)
    typed_keys = {"title", "version", "snapshot_id", "ingested_at", "description",
                  "theme", "asset_class", "vendor_product_ref", "created_at",
                  "modified_at", "removed_at"}
    leaked = typed_keys & set(prod.raw_attributes.keys())
    assert not leaked, f"raw_attributes leaked typed keys: {leaked}"

    # ── get_schema returns ONLY catalogue-facing columns (fix #3) ─────────
    schema = await catalogue_get_schema(GetSchemaInput(vendor=vendor))
    print(f"\nget_schema: version={schema.schema_version}")
    for f in schema.fields:
        print(f"  - {f.name:24s}  type={f.type:9s}  required={f.required}")
    field_names = {f.name for f in schema.fields}
    assert {"vendor_product_ref", "title"} <= field_names
    # Storage columns must be hidden.
    for hidden in ("snapshot_id", "ingested_at", "version", "created_at",
                   "modified_at", "removed_at"):
        assert hidden not in field_names, f"get_schema leaked storage column {hidden!r}"

    # ── delta: early watermark catches everything; counts include REMOVED ─
    early = await catalogue_get_delta(
        GetDeltaInput(vendor=vendor, since="2026-01-01T00:00:00Z")
    )
    print(f"\nget_delta(since=2026-01-01): counts={early.counts}")
    print(f"  next_watermark={early.next_watermark!r}")
    assert early.counts["added"] + early.counts["updated"] == 29
    assert early.counts["removed"] >= 2, "expected at least 2 tombstones (fix #12)"
    removed_refs = {d.ref.vendor_product_ref for d in early.deltas
                    if d.change_type.value == "removed"}
    assert "LSEG-RETIRED-101" in removed_refs
    assert "LSEG-RETIRED-102" in removed_refs

    # ── Re-feeding next_watermark must produce 0 deltas (fix #1) ──────────
    replay = await catalogue_get_delta(
        GetDeltaInput(vendor=vendor, since=early.next_watermark)
    )
    print(f"get_delta(since={early.next_watermark!r}): counts={replay.counts}")
    assert replay.counts == {"added": 0, "updated": 0, "removed": 0}, (
        f"watermark replay must yield zero deltas (got {replay.counts})"
    )

    # ── Bare ISO date (no time/Z) must be accepted and treated as UTC (fix #8)
    bare = await catalogue_get_delta(GetDeltaInput(vendor=vendor, since="2026-04-20"))
    print(f"get_delta(since=2026-04-20): counts={bare.counts}")
    assert bare.counts["added"] + bare.counts["updated"] < 29

    # ── Vendor-prefixed watermark parses to the same instant ──────────────
    vp = await catalogue_get_delta(GetDeltaInput(vendor=vendor, since="lseg-2026-04-20"))
    assert vp.counts == bare.counts, "vendor-prefixed form should match ISO equivalent"

    # ── _parse_since: malformed watermark raises ──────────────────────────
    try:
        await catalogue_get_delta(GetDeltaInput(vendor=vendor, since="garbage"))
    except ValueError as e:
        print(f"\nmalformed-watermark rejected OK: {e}")
    else:
        raise AssertionError("Expected ValueError for malformed watermark")

    # ── Missing required column produces a diagnosable error (fix #10) ────
    # Simulate by passing a hand-crafted row to _normalise_row directly.
    import pandas as pd
    bad_row = pd.Series({"vendor_product_ref": "X", "title": "t"})
    try:
        mock_backend._normalise_row(bad_row, vendor, list(bad_row.index))
    except ValueError as e:
        print(f"\nmissing-column error OK: {e}")
        assert "snapshot_id" in str(e) or "ingested_at" in str(e) or "version" in str(e)
    else:
        raise AssertionError("Expected ValueError naming the missing required column")

    print("\nSMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
