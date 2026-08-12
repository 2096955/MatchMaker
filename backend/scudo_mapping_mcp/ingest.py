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
from .models import (
    ConceptualEdge,
    ConceptualEdgeKind,
    ConceptualNodeKind,
    VendorProductRef,
    conceptual_iri,
    conceptual_node_from_fixture_raw,
)
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

# M10 — illustrative conceptual-enrichment layer, anchored to one Concept
# (jpmorgan:data:cdao:concept:equity-prices). Additive only: seeding this is
# never required for the cost ladder to function, so a missing/malformed
# file degrades to "no conceptual layer" rather than blocking taxonomy seed.
_CONCEPTUAL_LAYER_FIXTURE = (
    Path(__file__).resolve().parents[1] / "scudo" / "fixtures" / "conceptual_layer.json"
)

_COL_ALIASES = {
    "product_id": ("product_id", "id", "code", "ticker", "symbol", "sku"),
    "name": ("name", "product", "product_name", "title", "label", "description_short"),
    "description": ("description", "desc", "details", "long_description", "notes"),
}


def seed_taxonomy() -> int:
    """Seed taxonomy from the configured loader (cdao fixture or DCAT RDF)."""
    from .config import settings
    from .loaders.taxonomy_loader import load_taxonomy_nodes

    store = get_store()
    override = os.getenv("SCUDO_TAXONOMY_SEED", "").strip()
    if override:
        from scudo.seed_falkordb import _load_fixture, _to_taxonomy_node

        nodes = _load_fixture(override)
        for raw in nodes:
            store.upsert_taxonomy_node(_to_taxonomy_node(raw))
        return len(nodes)

    nodes = load_taxonomy_nodes(settings)
    for node in nodes:
        store.upsert_taxonomy_node(node)
    return len(nodes)


def seed_conceptual_layer(path: Path | None = None) -> int:
    """Seed the illustrative M10 conceptual-enrichment layer (Section 10c).

    Reads ``conceptual_layer.json`` (``local_id``-keyed nodes/edges anchored
    to one ``concept_iri``), resolves each ``local_id`` to a deterministic
    IRI via ``conceptual_iri``, and upserts nodes then edges. Returns the
    total upsert count (nodes + edges) so the caller can log it the same way
    ``seed_taxonomy`` does. A missing fixture file is not an error — this
    layer is additive metadata, never required for the cost ladder — so it
    returns 0 rather than raising.
    """
    fixture = path or _CONCEPTUAL_LAYER_FIXTURE
    if not fixture.exists():
        return 0
    data = json.loads(fixture.read_text(encoding="utf-8"))
    concept_iri = str(data["concept_iri"])
    store = get_store()

    local_to_iri: dict[str, str] = {}
    count = 0
    for raw in data.get("nodes", []):
        kind = ConceptualNodeKind(raw["kind"])
        local_id = str(raw["local_id"])
        iri = conceptual_iri(concept_iri, kind, local_id)
        local_to_iri[local_id] = iri
        store.upsert_conceptual_node(
            conceptual_node_from_fixture_raw(
                raw, iri=iri, kind=kind, concept_iri=concept_iri
            )
        )
        count += 1

    for raw in data.get("edges", []):
        store.upsert_conceptual_edge(
            ConceptualEdge(
                from_iri=local_to_iri[str(raw["from_local_id"])],
                to_iri=local_to_iri[str(raw["to_local_id"])],
                kind=ConceptualEdgeKind(raw["kind"]),
                label=str(raw.get("label") or ""),
            )
        )
        count += 1
    return count


def _pick(
    row: dict,
    key: str,
    col_aliases: dict[str, tuple[str, ...]] | None = None,
) -> str:
    aliases = col_aliases or _COL_ALIASES
    lower = {k.lower().strip(): v for k, v in row.items()}
    for alias in aliases[key]:
        al = alias.lower().strip()
        if al in lower and lower[al]:
            return str(lower[al]).strip()
    return ""


def _rows_to_frames(
    vendor: str,
    rows: list[dict],
    *,
    col_aliases: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[VendorProductRef], int]:
    """Turn rows into frames. Rows with no usable ``product_id`` (the vendor's
    native primary key) are REJECTED — not synthesized into a ``row-N`` frame —
    so the reported reject count is truthful. Returns ``(frames, rejected)``.
    """
    frames: list[VendorProductRef] = []
    rejected = 0
    for row in rows:
        pid = _pick(row, "product_id", col_aliases)
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
                name=_pick(row, "name", col_aliases),
                description=_pick(row, "description", col_aliases),
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
    csvw_metadata: Optional[dict] = None,
) -> list[VendorProductRef]:
    """Parse a vendor file into frames and (optionally) upsert them.

    When ``on_stage`` is supplied, emits a real ETL stage event after each
    pipeline step with actual counts — mirroring the architecture's
    EventBridge → SQS → Lambda → Validate/Transform → S3/DynamoDB flow. These
    are NOT simulated: every count is produced by this run.

    ``csvw_metadata`` optionally supplies a CSVW Table Schema document whose
    column names/titles extend the default CSV column alias map.
    """

    def emit(stage: str, **detail) -> None:
        if on_stage is not None:
            on_stage(stage, detail)

    from scudo_mapping_mcp.turtle_ingester import TurtleIngester

    if TurtleIngester.handles(filename):
        TurtleIngester().ingest(data, filename=filename, on_stage=on_stage)
        return []

    col_aliases: dict[str, tuple[str, ...]] = dict(_COL_ALIASES)
    if csvw_metadata is not None:
        from scudo_mapping_mcp.csvw_aliases import merge_col_aliases

        col_aliases = merge_col_aliases(col_aliases, csvw_metadata)

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
            from scudo_mapping_mcp.csvw_aliases import is_csvw_document

            if is_csvw_document(payload):
                raise ValueError(
                    "CSVW metadata cannot be ingested alone; pass csvw_metadata with CSV"
                )
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
        try:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
        except csv.Error as e:
            # csv.Error subclasses Exception, NOT ValueError, so it escaped the
            # routes' "except (UnicodeDecodeError, ValueError) -> 400" handlers
            # and surfaced as an opaque HTTP 500 (D2). A file this parser cannot
            # read is a CLIENT error; re-raise in the ValueError family the
            # routes already classify as 400, naming the file and the attempted
            # format. NOT a format decision: which formats are supported is a
            # separate open question — this only fixes the classification.
            raise ValueError(f"{filename} is not valid CSV: {e}") from e

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
    frames, rejected = _rows_to_frames(vendor, rows, col_aliases=col_aliases)
    emit("validate", valid=len(frames), rejected=rejected)

    # sink — persist to the working set (+ store upsert: S3/DynamoDB analogue)
    store = get_store() if upsert else None
    for f in frames:
        put_frame(f)
        if store is not None:
            store.upsert_vendor_product(f)
    emit("sink", persisted=len(frames), upserted=bool(store))

    return frames


def ingest_url(
    vendor: str,
    url: str,
    upsert: bool = True,
    on_stage: Optional[StageCallback] = None,
    **fetch_kwargs,
) -> list[VendorProductRef]:
    """Fetch a website URL server-side, synthesize ONE vendor-product row
    from its title/text, and run it through the SAME real ingest_bytes
    pipeline used for file uploads - no parallel ingestion path, no
    fabricated success. ``fetch_kwargs`` forwards to
    ``url_ingest.fetch_and_extract`` (e.g. a fake ``resolve``/``getter`` in
    tests).
    """
    from .url_ingest import fetch_and_extract, synthesize_product_row

    title, excerpt = fetch_and_extract(url, **fetch_kwargs)
    row = synthesize_product_row(url, title, excerpt)
    data = json.dumps([row]).encode("utf-8")
    filename = f"{row['product_id']}.json"
    return ingest_bytes(vendor, filename, data, upsert=upsert, on_stage=on_stage)
