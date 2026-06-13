"""
Normalise & Calibrate MCP — a READ-ONLY utility tier alongside the trust
gradient. Port 8004; tier name "normalise" on the McpHost.

WHY THIS SERVER EXISTS (mirrors the matching-engine review)
-----------------------------------------------------------
The matcher scores name+description similarity; a RAG/dense retriever
improves RECALL on those fields only. PRECISION comes from structure, and
the structured fields were either buried or absent:

  - Identifiers (ISIN/CUSIP/SEDOL/RIC/FIGI/ticker) need a deterministic
    JOIN, not an embedding — ``normalise.resolve_identifiers`` is that join,
    and ``normalise.validate_identifier`` checksums them at the boundary.
  - Dates were matched NOWHERE in the engine — ``normalise.normalise_date``
    canonicalises them to ISO-8601 and fails closed on ambiguity, the
    precondition for any future effective-dating validation.
  - ``data_class_match`` is pass-by-default until both sides declare a
    class — ``normalise.classify_data_class`` populates the vendor side
    from a controlled CDAO vocabulary (never guessed).
  - The adapter seam (SCUDO_VENDOR_ADAPTERS) was config-without-
    implementation — ``normalise.normalise_record`` runs the registry that
    consumes it, and REJECTS rows with no primary key instead of
    synthesising row-order ids (M8 contract / I8).
  - The two IRI mints disagree (review finding G1) —
    ``normalise.translate_iri`` returns both so golden data joins
    explicitly.
  - The floor/band thresholds are uncalibrated — ``calibrate.replay_golden``
    replays a golden set through retrieval-only and reports precision@1,
    recall-into-the-anchor-window, and a threshold sweep.

TRUST POSTURE
-------------
Same discipline as the Ingestion MCP (tier 1): this server reads untrusted
vendor rows and the store's READ surface only. It does NOT import the write
surface (``feedback``, ``bundle``, ``store.upsert_*``) and does NOT hold the
verdict signing key — smoke gate ``TRUST_normalise_mcp_imports_no_writers``
asserts the import boundary statically via the AST. Adding this server does
not change the trust gradient: it cannot mint verdicts and it cannot write.

SCOPE GATE
----------
``frames.check_scope`` fires on every vendor-taking tool (same layer-1
posture as ingestion): an out-of-scope vendor is refused with a reason,
including inside calibration (golden data for an unentitled vendor is a
data problem to surface, not to score around).

Run locally with:
    python -m scudo_mapping_mcp.normalise_mcp
"""
from __future__ import annotations

import json
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .calibration import GoldenPair, replay_golden
from .config import PRIORITY_VENDORS
from .frames import all_frames, check_scope
from .models import VendorProductRef
from .normalisation import (
    KNOWN_IDENTIFIER_SCHEMES,
    classify_data_class,
    normalise_date,
    normalise_record,
    resolve_identifiers,
    translate_iri,
    validate_identifier,
)

_RO = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

mcp = FastMCP("scudo_normalise_mcp")


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class NormaliseRecordInput(_Base):
    vendor: str = Field(
        ..., min_length=1,
        description=f"One of: {', '.join(PRIORITY_VENDORS)}",
    )
    row: dict = Field(..., description="One raw vendor row, untouched.")


class NormaliseDateInput(_Base):
    value: str = Field(..., min_length=1)


class ClassifyInput(_Base):
    value: str = Field(..., min_length=1)


class ValidateIdentifierInput(_Base):
    scheme: str = Field(
        ..., min_length=1,
        description=f"One of: {', '.join(KNOWN_IDENTIFIER_SCHEMES)}",
    )
    value: str = Field(..., min_length=1)


class ResolveIdentifiersInput(_Base):
    values: list[str] = Field(..., min_length=1, max_length=50)


class TranslateIriInput(_Base):
    vendor: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)


class ReplayGoldenInput(_Base):
    pairs: list[GoldenPair] = Field(..., min_length=1, max_length=500)
    max_candidates: int = Field(8, ge=1, le=25)
    include_outcomes: bool = Field(
        False,
        description="Include per-pair outcomes (verbose) alongside the metrics.",
    )


def _scope_refusal(vendor: str) -> Optional[str]:
    scope = check_scope(VendorProductRef(vendor=vendor, product_id="_probe"))
    if not scope.allowed:
        return json.dumps({"error": "out_of_scope", "reason": scope.reason})
    return None


@mcp.tool(
    name="normalise.normalise_record",
    annotations={"title": "Normalise one untrusted vendor row", **_RO},
)
async def normalise_record_tool(params: NormaliseRecordInput) -> str:
    """Run one raw vendor row through the wired adapter registry.

    Lifts product_id / name / description / data_class / identifiers /
    dates deterministically. Rejects (does NOT synthesise) when the row
    has no primary key — quarantine semantics per the M8 contract.
    """
    refusal = _scope_refusal(params.vendor)
    if refusal:
        return refusal
    frame = normalise_record(params.vendor, params.row)
    return frame.model_dump_json()


@mcp.tool(
    name="normalise.normalise_date",
    annotations={"title": "Canonicalise a date-like value to ISO-8601", **_RO},
)
async def normalise_date_tool(params: NormaliseDateInput) -> str:
    """Deterministic date normalisation. Ambiguous readings (03/04/2024,
    2-digit years) are refused with status='ambiguous' — never guessed."""
    return normalise_date(params.value).model_dump_json()


@mcp.tool(
    name="normalise.classify_data_class",
    annotations={"title": "Map an asset-class string to the CDAO vocabulary", **_RO},
)
async def classify_data_class_tool(params: ClassifyInput) -> str:
    """Controlled-vocabulary lookup onto the CDAO class roots. Unknown
    values return cdao_iri=null with a reason (pass-by-default upstream
    is preserved, not papered over)."""
    return classify_data_class(params.value).model_dump_json()


@mcp.tool(
    name="normalise.validate_identifier",
    annotations={"title": "Validate one identifier against its scheme", **_RO},
)
async def validate_identifier_tool(params: ValidateIdentifierInput) -> str:
    """Checksum validation for ISIN/CUSIP/SEDOL; pattern validation for
    RIC/FIGI/ticker. The 'check' field is honest about which ran."""
    return validate_identifier(params.scheme, params.value).model_dump_json()


@mcp.tool(
    name="normalise.resolve_identifiers",
    annotations={"title": "Exact identifier join over the working set", **_RO},
)
async def resolve_identifiers_tool(params: ResolveIdentifiersInput) -> str:
    """Deterministic exact-match join: identifier values against every
    ingested frame's product_id and identifier columns. An exact ISIN hit
    is identity, not similarity — use this BEFORE asking Match & Verify
    to score anything."""
    matches = resolve_identifiers(params.values, all_frames())
    return json.dumps({
        "count": len(matches),
        "matches": [m.model_dump(mode="json") for m in matches],
    })


@mcp.tool(
    name="normalise.translate_iri",
    annotations={"title": "Translate between the two canonical IRI mints", **_RO},
)
async def translate_iri_tool(params: TranslateIriInput) -> str:
    """Return BOTH IRIs for one (vendor, product): the matching engine's
    mds_iri and the vendor-catalogue mint. They intentionally differ;
    golden data minted under either scheme joins via this translation."""
    refusal = _scope_refusal(params.vendor)
    if refusal:
        return refusal
    return translate_iri(params.vendor, params.product_id).model_dump_json()


@mcp.tool(
    name="calibrate.replay_golden",
    annotations={"title": "Replay a golden set through retrieval-only", **_RO},
)
async def replay_golden_tool(params: ReplayGoldenInput) -> str:
    """Calibration harness: precision@1, recall into the specialist anchor
    window, MRR, band distribution at current settings, and a floor sweep
    with auto-map precision per threshold. Read-only; precedent reuse and
    the specialist are deliberately excluded from the measurement."""
    report = replay_golden(
        list(params.pairs),
        max_candidates=params.max_candidates,
        include_outcomes=params.include_outcomes,
    )
    return report.model_dump_json()


if __name__ == "__main__":
    # Distinct default port so all four MCPs co-run locally without a clash
    # (8001 ingestion / 8002 match-verify / 8003 persistence / 8004 here).
    mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("MCP_PORT", "8004"))
    mcp.run(transport="streamable-http")
