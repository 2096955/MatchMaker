"""
Normalisation — deterministic field canonicalisation for untrusted vendor rows.

WHY THIS MODULE EXISTS (the gap analysis it answers)
----------------------------------------------------
The matching engine scores ``name + description`` similarity and nothing
else. Three classes of field therefore never participate in matching today,
and each needs a DETERMINISTIC treatment, not a retrieval/RAG one:

  - IDENTIFIERS (ISIN / CUSIP / SEDOL / RIC / FIGI / ticker) — embeddings
    are weak on exact codes; the BM25 sidecar only partially recovers them.
    An exact identifier is a JOIN key, not a similarity input. This module
    validates them (checksum where the scheme defines one, pattern
    otherwise) so an exact-match rung can sit ABOVE retrieval in the
    cost ladder.
  - DATES — no temporal field exists on ``VendorProductRef`` and no
    validation touches time. This module normalises date-like values to
    ISO-8601 and FAILS CLOSED on ambiguity (03/04/2024 is "ambiguous",
    never a guess) so effective-dating can become a deterministic
    validation later.
  - DATA CLASS — ``validations.data_class_match`` is pass-by-default until
    BOTH sides declare a class. This module maps vendor asset-class strings
    onto the CDAO controlled vocabulary so the VENDOR side of that truth
    table is actually populated. Unknown class -> None with a reason;
    never guessed.

It also closes two seams the review flagged as declared-but-unimplemented:

  - ADAPTER REGISTRY — ``settings.vendor_adapters`` (SCUDO_VENDOR_ADAPTERS)
    was parsed and smoke-tested but consumed by nothing. The registry here
    is keyed by exactly those tokens. Per the M8 contract in ``frames.py``,
    adapters NEVER synthesise a primary key: a row with no PK field is
    REJECTED with a reason (quarantine semantics), because synthesised
    row-order ids re-fork every IRI on re-upload (breaks I8).
  - IRI TRANSLATION — ``models.mds_iri`` and
    ``vendor_catalogue_mcp.contract.product_iri`` mint DIFFERENT IRIs for
    the same (vendor, product). ``translate_iri`` returns both, so golden
    data minted under either scheme can be joined explicitly instead of
    silently missing.

DISCIPLINE
----------
Pure Python, no model judgement, no store writes, no MCP/Flask imports.
Everything here is replay-safe: same input -> same output, always.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .config import settings
from .models import VendorProductRef, mds_iri


# ──────────────────────────────────────────────────────────────────────
# Result contracts
# ──────────────────────────────────────────────────────────────────────

class IdentifierResult(BaseModel):
    """One identifier, validated against its scheme's own rules."""
    scheme: str
    value: str
    valid: bool
    # "checksum" — the scheme defines a check digit and we verified it
    #              (ISIN / CUSIP / SEDOL). The strong signal.
    # "pattern"  — structural shape only (RIC / FIGI / ticker). Weaker;
    #              honest about what was actually checked.
    check: Literal["checksum", "pattern"]
    detail: str = ""


class NormalisedDate(BaseModel):
    """A date-like value canonicalised to ISO-8601, or refused with a reason.

    ``status`` semantics (fail-closed, mirrors check_scope's posture):
      ok        — exactly one deterministic reading; ``iso`` is set.
      ambiguous — more than one valid reading (e.g. 03/04/2024) or an
                  unresolvable century (2-digit year). NEVER guessed.
      invalid   — no supported reading at all.
    """
    field: str = ""
    raw: str
    status: Literal["ok", "ambiguous", "invalid"]
    iso: Optional[str] = None
    detail: str = ""


class DataClassResult(BaseModel):
    """Vendor asset-class string mapped onto the CDAO controlled vocabulary."""
    raw: str
    cdao_iri: Optional[str] = None
    matched: bool = False
    detail: str = ""


class NormalisedFrame(BaseModel):
    """The output of one adapter pass over one untrusted vendor row.

    ``ok=False`` means REJECTED (quarantine), with ``rejection_reason`` set.
    The canonical rejection is a missing primary key — the adapter does NOT
    synthesise one (M8 contract; synthesised ids break I8 replay-safety).
    """
    ok: bool
    vendor: str
    adapter_id: str
    product_id: Optional[str] = None
    pk_field: Optional[str] = None
    name: str = ""
    description: str = ""
    data_class: Optional[DataClassResult] = None
    identifiers: list[IdentifierResult] = Field(default_factory=list)
    dates: list[NormalisedDate] = Field(default_factory=list)
    rejection_reason: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)

    def to_vendor_product_ref(self) -> VendorProductRef:
        """Project onto the matcher's frame shape. Only valid when ok."""
        if not self.ok or not self.product_id:
            raise ValueError(
                f"cannot build VendorProductRef from a rejected frame: "
                f"{self.rejection_reason}"
            )
        return VendorProductRef(
            vendor=self.vendor,
            product_id=self.product_id,
            name=self.name,
            description=self.description,
            raw=self.raw,
        )


# ──────────────────────────────────────────────────────────────────────
# Identifier schemes — checksum where the scheme defines one
# ──────────────────────────────────────────────────────────────────────

def _isin_valid(v: str) -> bool:
    """ISO 6166: 2 letters + 9 alphanumerics + Luhn check digit over the
    base-36 digit expansion."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", v):
        return False
    digits = "".join(str(int(c, 36)) for c in v)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _cusip_char(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c == "*":
        return 36
    if c == "@":
        return 37
    if c == "#":
        return 38
    return ord(c) - ord("A") + 10


def _cusip_valid(v: str) -> bool:
    """CUSIP: 8 chars + modulus-10 double-add-double check digit."""
    if not re.fullmatch(r"[0-9A-Z*@#]{8}[0-9]", v):
        return False
    total = 0
    for i, c in enumerate(v[:8]):
        x = _cusip_char(c)
        if i % 2 == 1:
            x *= 2
        total += x // 10 + x % 10
    return (10 - total % 10) % 10 == int(v[8])


_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9, 1)


def _sedol_valid(v: str) -> bool:
    """SEDOL: 6 chars (digits + consonants) + weighted check digit."""
    if not re.fullmatch(r"[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}[0-9]", v):
        return False
    total = sum(
        w * (int(c) if c.isdigit() else ord(c) - ord("A") + 10)
        for w, c in zip(_SEDOL_WEIGHTS, v)
    )
    return total % 10 == 0


# Pattern-only schemes — no public check digit to verify, so we are honest
# and report check="pattern" rather than implying a checksum was run.
_RIC_RE = re.compile(r"[0-9A-Z]{1,6}\.[A-Z]{1,4}")
_FIGI_RE = re.compile(r"[B-DF-HJ-NP-TV-Z0-9]{2}G[B-DF-HJ-NP-TV-Z0-9]{8}[0-9]")
_TICKER_RE = re.compile(r"[A-Z]{1,6}([./-][A-Z0-9]{1,4})?")


_CHECKSUM_SCHEMES = {
    "isin": _isin_valid,
    "cusip": _cusip_valid,
    "sedol": _sedol_valid,
}
_PATTERN_SCHEMES = {
    "ric": _RIC_RE,
    "figi": _FIGI_RE,
    "ticker": _TICKER_RE,
}

KNOWN_IDENTIFIER_SCHEMES: tuple[str, ...] = tuple(
    sorted({*_CHECKSUM_SCHEMES, *_PATTERN_SCHEMES})
)


def validate_identifier(scheme: str, value: str) -> IdentifierResult:
    """Validate one identifier. Unknown scheme -> invalid with a reason
    (fail-closed; an unknown scheme is not silently treated as a pass)."""
    s = (scheme or "").strip().lower()
    v = (value or "").strip().upper()
    if s in _CHECKSUM_SCHEMES:
        ok = _CHECKSUM_SCHEMES[s](v)
        return IdentifierResult(
            scheme=s, value=v, valid=ok, check="checksum",
            detail="" if ok else f"{s} checksum/shape failed",
        )
    if s in _PATTERN_SCHEMES:
        ok = bool(_PATTERN_SCHEMES[s].fullmatch(v))
        return IdentifierResult(
            scheme=s, value=v, valid=ok, check="pattern",
            detail="" if ok else f"{s} pattern failed",
        )
    return IdentifierResult(
        scheme=s, value=v, valid=False, check="pattern",
        detail=f"unknown scheme {s!r}; known: {', '.join(KNOWN_IDENTIFIER_SCHEMES)}",
    )


# ──────────────────────────────────────────────────────────────────────
# Date normalisation — deterministic, fail-closed on ambiguity
# ──────────────────────────────────────────────────────────────────────

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})([T ].*)?")
_YMD_SLASH_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_YMD_COMPACT_RE = re.compile(r"(\d{8})")
_DMY_OR_MDY_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
_TWO_DIGIT_YEAR_RE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2}")
_EPOCH_RE = re.compile(r"\d{10}|\d{13}")

_MONTH_NAME_FORMATS = (
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%B-%Y",
)


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalise_date(value: str, *, field: str = "") -> NormalisedDate:
    """Canonicalise one date-like value to ISO-8601 (YYYY-MM-DD).

    Supported deterministic readings: ISO-8601 (with optional time part),
    YYYY/MM/DD, YYYYMMDD, epoch seconds/milliseconds, and month-name forms.
    DD/MM/YYYY vs MM/DD/YYYY is accepted only when EXACTLY ONE reading is a
    valid calendar date; otherwise the value is ``ambiguous``. Two-digit
    years are ``ambiguous`` (century unknown). We never guess.
    """
    raw = (value or "").strip()
    if not raw:
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="empty value")

    m = _ISO_DATE_RE.fullmatch(raw)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=d.isoformat(), detail="iso-8601")
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="iso-shaped but not a calendar date")

    m = _YMD_SLASH_RE.fullmatch(raw)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=d.isoformat(), detail="yyyy/mm/dd")
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="yyyy/mm/dd shaped but not a calendar date")

    if _YMD_COMPACT_RE.fullmatch(raw):
        d = _safe_date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
        if d:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=d.isoformat(), detail="yyyymmdd")
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="8 digits but not yyyymmdd")

    if _EPOCH_RE.fullmatch(raw):
        ts = int(raw)
        if len(raw) == 13:
            ts //= 1000
        try:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            d = None
        if d and 1970 <= d.year <= 2100:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=d.isoformat(), detail="epoch")
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="epoch out of plausible range")

    if _TWO_DIGIT_YEAR_RE.fullmatch(raw):
        return NormalisedDate(
            field=field, raw=raw, status="ambiguous",
            detail="2-digit year — century unknown; never guessed",
        )

    m = _DMY_OR_MDY_RE.fullmatch(raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day_first = _safe_date(y, b, a)   # a=day, b=month
        month_first = _safe_date(y, a, b)  # a=month, b=day
        if day_first and month_first:
            if day_first == month_first:  # e.g. 05/05/2024
                return NormalisedDate(field=field, raw=raw, status="ok",
                                      iso=day_first.isoformat(),
                                      detail="day==month; unambiguous")
            return NormalisedDate(
                field=field, raw=raw, status="ambiguous",
                detail=(
                    f"both {day_first.isoformat()} (day-first) and "
                    f"{month_first.isoformat()} (month-first) are valid; "
                    "never guessed"
                ),
            )
        if day_first:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=day_first.isoformat(),
                                  detail="only day-first reading is valid")
        if month_first:
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=month_first.isoformat(),
                                  detail="only month-first reading is valid")
        return NormalisedDate(field=field, raw=raw, status="invalid",
                              detail="no valid calendar reading")

    for fmt in _MONTH_NAME_FORMATS:
        try:
            d = datetime.strptime(raw, fmt).date()
            return NormalisedDate(field=field, raw=raw, status="ok",
                                  iso=d.isoformat(), detail=f"format {fmt!r}")
        except ValueError:
            continue

    return NormalisedDate(field=field, raw=raw, status="invalid",
                          detail="no supported date reading")


# ──────────────────────────────────────────────────────────────────────
# Data class — CDAO controlled vocabulary (mirrors ingest._CDAO_SEED roots)
# ──────────────────────────────────────────────────────────────────────

_DATA_CLASS_VOCAB: dict[str, str] = {
    "equity": "cdao:equities", "equities": "cdao:equities",
    "stock": "cdao:equities", "stocks": "cdao:equities",
    "shares": "cdao:equities", "eq": "cdao:equities",
    "fixed income": "cdao:fixed-income", "fixed_income": "cdao:fixed-income",
    "fixedincome": "cdao:fixed-income", "fi": "cdao:fixed-income",
    "bond": "cdao:fixed-income", "bonds": "cdao:fixed-income",
    "credit": "cdao:fixed-income", "debt": "cdao:fixed-income",
    "fx": "cdao:fx", "foreign exchange": "cdao:fx",
    "currency": "cdao:fx", "currencies": "cdao:fx",
    "index": "cdao:indices", "indices": "cdao:indices", "indexes": "cdao:indices",
    "benchmark": "cdao:indices", "benchmarks": "cdao:indices",
    "rating": "cdao:ratings", "ratings": "cdao:ratings",
    "credit rating": "cdao:ratings", "credit ratings": "cdao:ratings",
    "esg": "cdao:esg", "sustainability": "cdao:esg", "climate": "cdao:esg",
    "derivative": "cdao:derivatives", "derivatives": "cdao:derivatives",
    "option": "cdao:derivatives", "options": "cdao:derivatives",
    "future": "cdao:derivatives", "futures": "cdao:derivatives",
    "swap": "cdao:derivatives", "swaps": "cdao:derivatives",
}


def classify_data_class(value: str) -> DataClassResult:
    """Map a vendor asset-class string onto the CDAO vocabulary.

    Deterministic synonym lookup, never a guess: an unmatched value returns
    ``cdao_iri=None`` with a reason, which leaves ``data_class_match`` in
    its documented pass-by-default state rather than fabricating a class.
    """
    raw = value or ""
    key = " ".join(re.sub(r"[^a-z0-9_ ]", " ", raw.strip().lower()).split())
    iri = _DATA_CLASS_VOCAB.get(key)
    if iri:
        return DataClassResult(raw=raw, cdao_iri=iri, matched=True)
    return DataClassResult(
        raw=raw, cdao_iri=None, matched=False,
        detail=f"no deterministic CDAO mapping for {key!r}; never guessed",
    )


# ──────────────────────────────────────────────────────────────────────
# Vendor adapter registry — finally consumes settings.vendor_adapters
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VendorAdapter:
    """Declarative per-vendor field map. Field lists are PRIORITY-ORDERED:
    the first present, non-empty column wins. ``pk_fields`` deliberately
    leads with the generic primary-key names already used by the legacy
    ``ingest._COL_ALIASES`` heuristic so frames ingested before the adapter
    layer keep the same identity (I8)."""
    adapter_id: str
    pk_fields: tuple[str, ...]
    name_fields: tuple[str, ...] = (
        "name", "product", "product_name", "title", "label",
    )
    description_fields: tuple[str, ...] = (
        "description", "desc", "details", "long_description", "notes",
    )
    class_fields: tuple[str, ...] = (
        "data_class", "dataclass", "asset_class", "assetclass", "class",
    )
    # scheme -> column aliases carrying that scheme's identifier
    identifier_fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("isin", ("isin",)),
        ("cusip", ("cusip",)),
        ("sedol", ("sedol",)),
        ("ric", ("ric", "reuters_code")),
        ("figi", ("figi", "bbgid", "bbg_id")),
        ("ticker", ("ticker", "symbol")),
    )
    date_fields: tuple[str, ...] = (
        "effective_date", "as_of", "as_of_date", "valid_from", "valid_to",
        "launch_date", "last_updated", "modified_at",
    )


_GENERIC_PK = ("product_id", "id", "code", "sku")

# Keyed by the SCUDO_VENDOR_ADAPTERS token shape (see config._default_vendor_adapters).
_ADAPTERS: dict[str, VendorAdapter] = {
    "lseg": VendorAdapter(
        adapter_id="lseg",
        pk_fields=_GENERIC_PK + ("permid", "ric"),
    ),
    "sp_global": VendorAdapter(
        adapter_id="sp_global",
        pk_fields=_GENERIC_PK + ("gvkey", "cusip"),
    ),
    "bloomberg": VendorAdapter(
        adapter_id="bloomberg",
        pk_fields=_GENERIC_PK + ("figi", "bbgid", "ticker"),
    ),
    "ice": VendorAdapter(
        adapter_id="ice",
        pk_fields=_GENERIC_PK + ("ice_code", "isin"),
    ),
    "factset": VendorAdapter(
        adapter_id="factset",
        pk_fields=_GENERIC_PK + ("fsym_id", "factset_id"),
    ),
}


def _adapter_token(vendor: str) -> str:
    """Same normalisation rule as config._default_vendor_adapters:
    lower-case, spaces -> underscores, every other non-alphanumeric stripped.
    "S&P Global" -> "sp_global"."""
    s = (vendor or "").lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", s)


def adapter_for(vendor: str) -> Optional[VendorAdapter]:
    """Resolve the wired adapter for a vendor, honouring the env contract.

    An adapter is available only when (a) it exists in the registry AND
    (b) its token is listed in ``settings.vendor_adapters``. Fail-closed:
    an unwired vendor gets None, not the generic heuristic.
    """
    token = _adapter_token(vendor)
    if token not in settings.vendor_adapters:
        return None
    return _ADAPTERS.get(token)


def _pick(lower_row: dict, fields: tuple[str, ...]) -> tuple[str, str]:
    """Return (field, value) for the first present, non-empty alias."""
    for f in fields:
        v = lower_row.get(f)
        if v is not None and str(v).strip():
            return f, str(v).strip()
    return "", ""


def normalise_record(vendor: str, row: dict) -> NormalisedFrame:
    """Run one untrusted vendor row through the wired adapter.

    Rejection semantics (NOT exceptions — the caller routes on ``ok``):
      - no adapter wired for the vendor   -> rejected, adapter_not_wired
      - no primary-key column present     -> rejected, missing_primary_key
        (the adapter NEVER synthesises an id; see module docstring)
    Everything else degrades to warnings on an ok frame.
    """
    adapter = adapter_for(vendor)
    if adapter is None:
        return NormalisedFrame(
            ok=False, vendor=vendor, adapter_id="",
            rejection_reason=(
                f"adapter_not_wired: token {_adapter_token(vendor)!r} not in "
                f"SCUDO_VENDOR_ADAPTERS {settings.vendor_adapters!r}"
            ),
            raw=dict(row or {}),
        )

    lower = {str(k).lower().strip(): v for k, v in (row or {}).items()}
    warnings: list[str] = []

    pk_field, pk_value = _pick(lower, adapter.pk_fields)
    if not pk_value:
        return NormalisedFrame(
            ok=False, vendor=vendor, adapter_id=adapter.adapter_id,
            rejection_reason=(
                "missing_primary_key: none of "
                f"{list(adapter.pk_fields)} present and non-empty. "
                "Per the M8 contract the adapter does NOT synthesise ids "
                "(row-order ids re-fork IRIs on re-upload; breaks I8). "
                "Route to quarantine."
            ),
            raw=dict(row or {}),
        )

    _, name = _pick(lower, adapter.name_fields)
    _, description = _pick(lower, adapter.description_fields)
    if not name:
        warnings.append("no name column found; matcher recall will suffer")

    data_class: Optional[DataClassResult] = None
    _, raw_class = _pick(lower, adapter.class_fields)
    if raw_class:
        data_class = classify_data_class(raw_class)
        if not data_class.matched:
            warnings.append(
                f"data_class {raw_class!r} has no CDAO mapping; "
                "data_class_match stays pass-by-default for this row"
            )

    identifiers: list[IdentifierResult] = []
    for scheme, aliases in adapter.identifier_fields:
        f, v = _pick(lower, aliases)
        if v:
            result = validate_identifier(scheme, v)
            identifiers.append(result)
            if not result.valid:
                warnings.append(
                    f"identifier {scheme}={v!r} failed {result.check} check"
                )

    dates: list[NormalisedDate] = []
    for f in adapter.date_fields:
        v = lower.get(f)
        if v is not None and str(v).strip():
            nd = normalise_date(str(v), field=f)
            dates.append(nd)
            if nd.status != "ok":
                warnings.append(f"date {f}={v!r}: {nd.status} ({nd.detail})")

    return NormalisedFrame(
        ok=True, vendor=vendor, adapter_id=adapter.adapter_id,
        product_id=pk_value, pk_field=pk_field,
        name=name, description=description,
        data_class=data_class, identifiers=identifiers, dates=dates,
        warnings=warnings, raw=dict(row or {}),
    )


# ──────────────────────────────────────────────────────────────────────
# Identifier exact-join over the working set — a JOIN, not a similarity
# ──────────────────────────────────────────────────────────────────────

class IdentifierMatch(BaseModel):
    vendor: str
    product_id: str
    iri: str
    matched_field: str
    matched_value: str


# Every alias any adapter knows about, flattened — used to harvest
# identifier values out of a frame's untyped ``raw`` dict.
_ALL_IDENTIFIER_ALIASES: tuple[str, ...] = tuple(sorted({
    alias
    for adapter in _ADAPTERS.values()
    for _, aliases in adapter.identifier_fields
    for alias in aliases
}))


def resolve_identifiers(
    values: list[str],
    frames: list[VendorProductRef],
) -> list[IdentifierMatch]:
    """Exact, case-insensitive identifier join over the supplied frames.

    Compares each query value against (a) the frame's product_id and
    (b) any identifier-alias column in the frame's ``raw`` dict. This is
    the deterministic rung that should beat retrieval: an exact ISIN hit
    is identity, not similarity. Read-only; the caller supplies the frame
    list (typically ``frames.all_frames()``) so this module stays pure.
    """
    wanted = {v.strip().upper() for v in values if v and v.strip()}
    if not wanted:
        return []
    out: list[IdentifierMatch] = []
    for ref in frames:
        candidates: list[tuple[str, str]] = [("product_id", ref.product_id)]
        for alias in _ALL_IDENTIFIER_ALIASES:
            raw_v = (ref.raw or {}).get(alias)
            if raw_v is not None and str(raw_v).strip():
                candidates.append((alias, str(raw_v).strip()))
        for field, value in candidates:
            if value.upper() in wanted:
                out.append(IdentifierMatch(
                    vendor=ref.vendor, product_id=ref.product_id,
                    iri=ref.iri, matched_field=field, matched_value=value,
                ))
    return out


# ──────────────────────────────────────────────────────────────────────
# IRI translation — bridges the two mints (review finding G1)
# ──────────────────────────────────────────────────────────────────────

class IriTranslation(BaseModel):
    vendor: str
    product_id: str
    matching_engine_iri: str
    catalogue_iri: Optional[str] = None
    note: str = ""


def translate_iri(vendor: str, product_id: str) -> IriTranslation:
    """Return BOTH canonical IRIs for one (vendor, product).

    ``matching_engine_iri`` is ``models.mds_iri`` (fixed uuid5 seed,
    "S&P Global" -> "sandpglobal"). ``catalogue_iri`` is
    ``vendor_catalogue_mcp.contract.product_iri`` (NAMESPACE_URL seed,
    "spglobal"). They are NOT equal for the same inputs — that is the
    point of this function: golden data minted under either scheme can be
    joined explicitly rather than silently missing precedent reuse.
    """
    engine_iri = mds_iri(vendor, product_id)
    try:
        from vendor_catalogue_mcp.contract import (  # noqa: PLC0415 — sibling package, lazy
            VendorId,
            product_iri,
        )
    except ImportError:
        return IriTranslation(
            vendor=vendor, product_id=product_id,
            matching_engine_iri=engine_iri,
            note="vendor_catalogue_mcp not importable in this deployment",
        )
    token = re.sub(r"[^a-z0-9]", "", (vendor or "").lower())
    try:
        vid = VendorId(token)
    except ValueError:
        return IriTranslation(
            vendor=vendor, product_id=product_id,
            matching_engine_iri=engine_iri,
            note=f"no catalogue VendorId for token {token!r}",
        )
    return IriTranslation(
        vendor=vendor, product_id=product_id,
        matching_engine_iri=engine_iri,
        catalogue_iri=product_iri(vid, product_id),
        note="the two mints intentionally differ; join via this translation",
    )
