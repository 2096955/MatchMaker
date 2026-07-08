"""P4 — CSVW column alias parsing and normalization."""

from __future__ import annotations

from scudo_mapping_mcp.csvw_aliases import (
    merge_col_aliases,
    normalize_csvw_aliases,
)
from scudo_mapping_mcp.ingest import _COL_ALIASES


def test_normalize_csvw_aliases_from_column_names_and_titles():
    doc = {
        "tableSchema": {
            "columns": [
                {"name": "Ticker", "titles": ["Product ID"]},
                {"name": "Product Name"},
                {"name": "Summary", "titles": ["Description"]},
            ]
        }
    }
    aliases = normalize_csvw_aliases(doc)
    assert "Ticker" in aliases["product_id"]
    assert "Product ID" in aliases["product_id"]
    assert "Product Name" in aliases["name"]
    assert "Summary" in aliases["description"]
    assert "Description" in aliases["description"]


def test_normalize_csvw_single_column_dict():
    doc = {
        "csvw:tableSchema": {
            "csvw:columns": {
                "name": "symbol",
                "titles": "Vendor Symbol",
            }
        }
    }
    aliases = normalize_csvw_aliases(doc)
    assert aliases["product_id"] == ("symbol", "Vendor Symbol")


def test_merge_col_aliases_extends_defaults_without_dropping():
    doc = {
        "tableSchema": {
            "columns": [{"name": "VendorSymbol", "titles": ["Symbol Code"]}]
        }
    }
    merged = merge_col_aliases(dict(_COL_ALIASES), doc)
    assert "ticker" in merged["product_id"]
    assert "VendorSymbol" in merged["product_id"]
    assert "Symbol Code" in merged["product_id"]
