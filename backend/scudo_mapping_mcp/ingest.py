"""
Ingestion — turns dropped files into vendor frames, and seeds a small CDAO
taxonomy so the demo matches out of the box.

This is the local stand-in for build_lexical_index(): on upload we parse the file
into VendorProductRef rows, drop them in the working set, and (optionally) upsert
them into the store. CSV and JSON are supported; the parser is intentionally
forgiving about column names.

NAMESPACE NOTE — DO NOT consolidate with ``backend/routes/ingest.py``.
This module turns vendor-supplied files into ``VendorProductRef`` rows for the
mapping pipeline. ``routes/ingest.py`` (the ``ingest_bp`` blueprint) is the
separate ETL trigger that runs the format-specific engines in
``backend/ingestion/`` and writes to the ``etl_run_log`` table. They share the
noun "ingest" and nothing else; merging them would couple the mapping package
to the ETL orchestrator and break the transport-agnostic seam.
"""
from __future__ import annotations

import csv
import io
import json

from .frames import put_frame
from .models import TaxonomyNode, VendorProductRef, mds_iri
from .store import get_store

# A small slice of a CDAO-style market-data taxonomy for the demo.
_CDAO_SEED: list[tuple[str, str, str | None]] = [
    # (iri, label, parent_iri)
    ("cdao:root", "Market Data", None),
    ("cdao:equities", "Equities", "cdao:root"),
    ("cdao:eq-prices", "Equity Prices", "cdao:equities"),
    ("cdao:eq-ref", "Equity Reference Data", "cdao:equities"),
    ("cdao:eq-corp", "Corporate Actions", "cdao:equities"),
    ("cdao:fixed-income", "Fixed Income", "cdao:root"),
    ("cdao:fi-gov", "Government Bonds", "cdao:fixed-income"),
    ("cdao:fi-corp", "Corporate Bonds", "cdao:fixed-income"),
    ("cdao:fi-yield", "Yield Curves", "cdao:fixed-income"),
    ("cdao:fx", "Foreign Exchange", "cdao:root"),
    ("cdao:fx-spot", "FX Spot Rates", "cdao:fx"),
    ("cdao:fx-fwd", "FX Forwards", "cdao:fx"),
    ("cdao:indices", "Indices & Benchmarks", "cdao:root"),
    ("cdao:idx-eq", "Equity Indices", "cdao:indices"),
    ("cdao:idx-rates", "Rates Benchmarks", "cdao:indices"),
    ("cdao:ratings", "Credit Ratings", "cdao:root"),
    ("cdao:esg", "ESG & Sustainability", "cdao:root"),
    ("cdao:derivatives", "Derivatives", "cdao:root"),
    ("cdao:deriv-options", "Listed Options", "cdao:derivatives"),
    ("cdao:deriv-futures", "Listed Futures", "cdao:derivatives"),
]

_COL_ALIASES = {
    "product_id": ("product_id", "id", "code", "ticker", "symbol", "sku"),
    "name": ("name", "product", "product_name", "title", "label", "description_short"),
    "description": ("description", "desc", "details", "long_description", "notes"),
}


def seed_taxonomy() -> int:
    store = get_store()
    for iri, label, parent in _CDAO_SEED:
        store.upsert_taxonomy_node(TaxonomyNode(iri=iri, label=label, parent_iri=parent))
    return len(_CDAO_SEED)


def _pick(row: dict, key: str) -> str:
    lower = {k.lower().strip(): v for k, v in row.items()}
    for alias in _COL_ALIASES[key]:
        if alias in lower and lower[alias]:
            return str(lower[alias]).strip()
    return ""


def _rows_to_frames(vendor: str, rows: list[dict]) -> list[VendorProductRef]:
    frames: list[VendorProductRef] = []
    for i, row in enumerate(rows):
        pid = _pick(row, "product_id") or f"row-{i+1}"
        frames.append(
            VendorProductRef(
                vendor=vendor,
                product_id=pid,
                name=_pick(row, "name"),
                description=_pick(row, "description"),
                raw=row,
            )
        )
    return frames


def ingest_bytes(vendor: str, filename: str, data: bytes, upsert: bool = True) -> list[VendorProductRef]:
    text = data.decode("utf-8", errors="replace")
    if filename.lower().endswith(".json"):
        payload = json.loads(text)
        rows = payload if isinstance(payload, list) else payload.get("products", [payload])
    else:  # default to CSV / TSV
        delimiter = "\t" if filename.lower().endswith((".tsv", ".tab")) else ","
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    frames = _rows_to_frames(vendor, rows)
    store = get_store() if upsert else None
    for f in frames:
        put_frame(f)
        if store is not None:
            store.upsert_vendor_product(f)
    return frames
