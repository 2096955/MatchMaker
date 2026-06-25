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
import os
from pathlib import Path
from typing import Callable, Optional

from .frames import put_frame
from .models import TaxonomyNode, VendorProductRef
from .store import get_store

# Callback signature for streaming ETL stage telemetry. ``stage`` is one of the
# canonical ETL stage keys (received/decode/parse/validate/sink); ``detail`` is a
# small JSON-serialisable dict of real counts/notes for that stage. Used by the
# SSE ingest endpoint to light up the ETL graph nodes with live data — NOT a
# simulation: every count is the actual value produced by this pipeline.
StageCallback = Callable[[str, dict], None]

# Max parsed rows per upload (A3 — bound resource use). Byte size is capped
# earlier by Flask MAX_CONTENT_LENGTH; this bounds row explosion.
_MAX_ROWS = int(os.getenv("SCUDO_MAX_ROWS", "10000"))

# Canonical illustrative taxonomy — same fixture FalkorDB seeding uses.
_CATALOGUE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "scudo" / "fixtures" / "cdao_catalogue.json"
)

_COL_ALIASES = {
    "product_id": ("product_id", "id", "code", "ticker", "symbol", "sku"),
    "name": ("name", "product", "product_name", "title", "label", "description_short"),
    "description": ("description", "desc", "details", "long_description", "notes"),
}


def _coerce_ref(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("@id")
    return str(value) if value else None


def _normalize_catalogue_node(raw: dict) -> dict:
    iri = _coerce_ref(raw.get("iri") or raw.get("@id"))
    label = raw.get("label") or raw.get("prefLabel") or raw.get("title")
    parent = _coerce_ref(
        raw.get("parent_iri") or raw.get("inSubdomain") or raw.get("inDomain")
    )
    if not label and iri:
        label = iri.rsplit(":", 1)[-1].replace("-", " ").replace("_", " ").title()
    return {"iri": iri, "label": label, "parent_iri": parent}


def _load_catalogue_fixture(path: Path | None = None) -> list[dict]:
    fixture = path or _CATALOGUE_FIXTURE
    data = json.loads(fixture.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("nodes") or data.get("@graph") or []
    nodes: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        node = _normalize_catalogue_node(raw)
        if node.get("iri") and node.get("label"):
            nodes.append(node)
    return nodes


def seed_taxonomy() -> int:
    """Seed taxonomy from the canonical catalogue fixture (jpmorgan:data:cdao IRIs)."""
    store = get_store()
    override = os.getenv("SCUDO_TAXONOMY_SEED", "").strip()
    if override:
        from scudo.seed_falkordb import _load_fixture, _to_taxonomy_node

        nodes = _load_fixture(override)
        for raw in nodes:
            store.upsert_taxonomy_node(_to_taxonomy_node(raw))
        return len(nodes)

    count = 0
    for raw in _load_catalogue_fixture():
        store.upsert_taxonomy_node(
            TaxonomyNode(
                iri=str(raw["iri"]),
                label=str(raw["label"]),
                parent_iri=raw.get("parent_iri") or None,
            )
        )
        count += 1
    return count


def _pick(row: dict, key: str) -> str:
    lower = {k.lower().strip(): v for k, v in row.items()}
    for alias in _COL_ALIASES[key]:
        if alias in lower and lower[alias]:
            return str(lower[alias]).strip()
    return ""


def _rows_to_frames(
    vendor: str, rows: list[dict]
) -> tuple[list[VendorProductRef], int]:
    """Turn rows into frames. Rows with no usable ``product_id`` (the vendor's
    native primary key) are REJECTED — not synthesized into a ``row-N`` frame —
    so the reported reject count is truthful. Returns ``(frames, rejected)``.
    """
    frames: list[VendorProductRef] = []
    rejected = 0
    for row in rows:
        pid = _pick(row, "product_id")
        if not pid:
            # null/empty primary key → quarantine (matches the upstream ETL
            # contract: rows with a null PK go to the rejected bucket, never
            # get a fabricated id).
            rejected += 1
            continue
        frames.append(
            VendorProductRef(
                vendor=vendor,
                product_id=pid,
                name=_pick(row, "name"),
                description=_pick(row, "description"),
                raw=row,
            )
        )
    return frames, rejected


def ingest_bytes(
    vendor: str,
    filename: str,
    data: bytes,
    upsert: bool = True,
    on_stage: Optional[StageCallback] = None,
) -> list[VendorProductRef]:
    """Parse a vendor file into frames and (optionally) upsert them.

    When ``on_stage`` is supplied, emits a real ETL stage event after each
    pipeline step with actual counts — mirroring the architecture's
    EventBridge → SQS → Lambda → Validate/Transform → S3/DynamoDB flow. These
    are NOT simulated: every count is produced by this run.
    """

    def emit(stage: str, **detail) -> None:
        if on_stage is not None:
            on_stage(stage, detail)

    # received — the upload arrives (EventBridge/SQS framing in the architecture)
    emit("received", filename=filename, vendor=vendor, bytes=len(data))

    # decode + parse — Lambda worker reads the object
    text = data.decode("utf-8", errors="replace")
    fmt = "json" if filename.lower().endswith(".json") else "csv"
    if fmt == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e
        # Accept either a top-level list of objects, or {"products": [objects]}.
        # Anything else (bare scalar, list of non-objects, products-not-a-list)
        # is a client error → ValueError → HTTP 400, not a 500.
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and "products" in payload:
            rows = payload["products"]
        elif isinstance(payload, dict):
            rows = [payload]  # a single product object
        else:
            raise ValueError(
                "JSON must be a list of product objects or "
                '{"products": [...]} — got a bare scalar.'
            )
        if not isinstance(rows, list):
            raise ValueError('JSON "products" must be a list of objects.')
        if not all(isinstance(r, dict) for r in rows):
            raise ValueError("each product row must be a JSON object.")
    else:  # default to CSV / TSV
        delimiter = "\t" if filename.lower().endswith((".tsv", ".tab")) else ","
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    # Bound row count so a huge file can't exhaust memory/CPU (A3). The byte
    # ceiling is enforced earlier by Flask MAX_CONTENT_LENGTH; this caps rows.
    if len(rows) > _MAX_ROWS:
        raise ValueError(
            f"too many rows: {len(rows)} exceeds the {_MAX_ROWS} limit "
            f"(set SCUDO_MAX_ROWS to change)."
        )
    emit("parse", format=fmt, rows=len(rows))

    # validate / transform — rows → typed frames; rows with no usable
    # product_id are rejected (quarantine), counted truthfully.
    frames, rejected = _rows_to_frames(vendor, rows)
    emit("validate", valid=len(frames), rejected=rejected)

    # sink — persist to the working set (+ store upsert: S3/DynamoDB analogue)
    store = get_store() if upsert else None
    for f in frames:
        put_frame(f)
        if store is not None:
            store.upsert_vendor_product(f)
    emit("sink", persisted=len(frames), upserted=bool(store))

    return frames
