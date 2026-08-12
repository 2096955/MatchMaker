"""
M5 — deterministic validations + default field-normalisation rules.

Per the build brief Section 10c: this layer makes every mapping a self-describing
artifact (field_normalisation + validations) and, critically, lets a REQUIRED
validation FORCE `needs_review` even at high similarity. The point is exactly
invariant I6 — invariants live OUTSIDE the model — so all checks here are pure
Python, run-always, no LLM judgement.

Validation set, in order:

  1. scope_compatible   (required) — echo of the deterministic scope gate.
  2. identifier_resolves(required) — the candidate node IRI exists in the store.
  3. data_class_match   (required) — STRICT only when BOTH sides declare a
                                     class. Truth table:
                                       vendor.data_class set, node class set,
                                       and equal (case-insensitive)  -> pass
                                       both set, not equal           -> fail
                                       either side missing            -> pass
                                                                        with detail
                                                                        "no
                                                                        declared
                                                                        class
                                                                        ... pass-
                                                                        by-default"
                                     Today's CDAO seed carries no class on
                                     any node, so this is effectively pass-
                                     by-default in the prototype. Tightens
                                     automatically once node class tags
                                     land (M6 bundle or matching seed).
  4. name_length        (warn)     — vendor.name within real upstream length caps.
  5. description_length (warn)     — vendor.description within length caps.
  6. input_completeness (required) — ONLY when SCUDO_INPUT_COMPLETENESS_VALIDATION
                                     is truthy (default OFF — flag off, this
                                     entry is not emitted at all and the list
                                     above is byte-identical to before).
                                     Fails on thin input: empty/whitespace
                                     name (query degrades to the raw
                                     product_id), name shorter than 3 chars,
                                     bare-identifier name (all caps/digits/
                                     separators, no natural-language words),
                                     or empty description (thin input
                                     empirically scores HIGHER on the
                                     Jaro-Winkler dense arm). Generic but
                                     well-formed single-word names are NOT
                                     flagged here — near-tie ambiguity is
                                     the margin gate's job (matching.py).
  7. temporal_compatible(required) — ONLY when SCUDO_TEMPORAL_VALIDATION is
                                     truthy (default OFF — flag off, this
                                     entry is not emitted at all). Closes
                                     the "no temporal field or comparator
                                     anywhere" gap: two products identical
                                     in name but covering different periods
                                     (2015-2018 archive vs 2024-2025
                                     current) were previously
                                     indistinguishable to the engine.
                                     Truth table mirrors data_class_match
                                     (3) exactly:
                                       both sides declare a PARSEABLE
                                       interval, intervals overlap  -> pass
                                       both parseable, disjoint      -> fail
                                       either side missing, or free
                                       text the parser cannot read   -> pass
                                                                        with
                                                                        "pass-
                                                                        by-
                                                                        default"
                                     A missing or unparseable date NEVER
                                     fails a match — the only failure is a
                                     positive, deterministic disagreement.

Failure semantics (used by the matcher):

  - Any required `fail` -> matcher forces `needs_review` regardless of similarity.
  - `warn` -> recorded on the artifact but does not change status.
  - All `pass` (required) -> floor decides auto_mapped vs needs_review as before.

Field normalisation:

  default_field_rules() returns the baseline (name -> prefLabel, description ->
  comment, product_id -> vendorRef). Per-pattern overrides will land via the
  M6 mapping bundle and replace these on a confirmed-precedent basis.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Optional

from .config import (
    env_input_completeness_validation_enabled,
    env_temporal_validation_enabled,
)
from .models import FieldRule, TaxonomyNode, Validation, VendorProductRef

# Length caps mirror real upstream failure classes (picklist-not-in-domain,
# length-exceeded). They are warnings, not blockers — Section 10c calls these
# out as warn-level field-format checks.
_NAME_LEN_CAP = 200
_DESCRIPTION_LEN_CAP = 2000


def default_field_rules() -> list[FieldRule]:
    """Return the baseline vendor -> CDAO field-normalisation rules.

    A stub set; the M6 mapping bundle ships per-pattern overrides that replace
    these on confirmed precedents. Kept stable so a re-export is reproducible.
    """
    return [
        FieldRule(vendor_field="name", cdao_field="prefLabel", transform="trim"),
        FieldRule(vendor_field="description", cdao_field="comment", transform="trim"),
        FieldRule(
            vendor_field="product_id", cdao_field="vendorRef", transform="identity"
        ),
    ]


def _looks_like_bare_identifier(name: str) -> bool:
    """True when a (stripped, non-empty) vendor name reads as a raw product
    code rather than a natural-language product name — e.g. "EQUITY-PRICES",
    "EQP_RT_001", "X1REF". Heuristic, deliberately conservative: every token
    (split on space/dash/underscore/dot/slash) must be all-caps-or-digits AND
    at least one token must contain a digit or the whole name must contain a
    separator. A single ordinary capitalised word ("Prices") is NOT a bare
    identifier — generic-word ambiguity is the margin gate's concern, and an
    acronym product name like "FX" is already caught by the min-length rule.
    """
    tokens = [t for t in re.split(r"[ \-_./]+", name) if t]
    if not tokens:
        return False
    if not all(t.isupper() or t.isdigit() for t in tokens):
        return False
    has_digit = any(any(ch.isdigit() for ch in t) for t in tokens)
    has_separator = bool(re.search(r"[\-_./]", name)) or len(tokens) > 1
    return has_digit or has_separator


def _vendor_data_class(ref: VendorProductRef) -> Optional[str]:
    """Pull a declared data_class from the raw vendor row, case-insensitive."""
    if not ref.raw:
        return None
    for key in ("data_class", "dataClass", "asset_class", "assetClass"):
        v = ref.raw.get(key)
        if v:
            return str(v).strip().lower()
    return None


# ── Temporal comparator (SCUDO_TEMPORAL_VALIDATION) ────────────────────────
#
# Deterministic, pure-Python, no LLM judgement (I6). Design rule that drives
# every branch below: this comparator may only ever produce a POSITIVE
# disagreement. Anything it does not fully understand parses to None, and
# None on either side is a pass. Being deliberately narrow is the safety
# property — a parser that guesses is a parser that fails real matches.

# A closed, ISO-8601-anchored grammar. Two-digit years, month names and
# ordinals are NOT accepted: guessing "Jan 19" or "19" costs a real match.
_YEAR_RE = re.compile(r"^(\d{4})$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_MONTH_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# Range separators, longest first so "/" inside " to " can never win. The
# ISO-8601 interval separator is "/"; the others are the shapes vendor rows
# actually carry.
_RANGE_SEPARATORS: tuple[str, ...] = (" to ", " -- ", "/", " – ", " — ", "..")
# ISO-8601-2 open-ended interval marker.
_OPEN_BOUND = ".."

# Vendor raw-row keys that carry a COVERAGE interval. Deliberately excludes
# bare "period" and anything update-frequency-shaped: DCAT's
# ``update_period`` is a refresh CADENCE ("daily"), not a coverage window,
# and comparing a cadence against a coverage interval would fail matches for
# no reason.
_VENDOR_TEMPORAL_KEYS: tuple[str, ...] = (
    "temporal_coverage",
    "temporalCoverage",
    "coverage_period",
    "coveragePeriod",
    "coverage_start",
    "coverageStart",
)

# SNAPSHOT-POINT keys — deliberately NOT coverage.
#
# ``as_of`` / ``vintage`` answer "when was this extract taken?", not "what
# period does this data cover". Treating them as a one-day coverage window was
# a false-positive generator: a completeness critic measured `as_of=2025-01-15`
# against a node covering 2019-2020 and got a required-FAIL on a record that
# would otherwise have AUTO_MAPPED at confidence 0.87. An `as_of` of *today* is
# the most ordinary thing a vendor feed emits, so this blocked routine matches.
#
# A snapshot date is not a disagreement about coverage, and the rule this
# feature is built on is that only a positive, provable disagreement may fail.
# These keys are therefore ignored for compatibility. If a real coverage
# meaning is ever wanted for one of them, pair it in
# ``_VENDOR_TEMPORAL_START_END_PAIRS`` — do not move it back here.
_VENDOR_TEMPORAL_SNAPSHOT_KEYS: tuple[str, ...] = (
    "vintage",
    "as_of",
    "asOf",
    "asof",
)

# Start/end key PAIRS. A start key found here is read together with its end
# counterpart; a start with no end is an OPEN-ENDED (ongoing) interval, never a
# single day. See the comment in ``_vendor_temporal_coverage``.
_VENDOR_TEMPORAL_START_END_PAIRS: tuple[tuple[str, str], ...] = (
    ("coverage_start", "coverage_end"),
    ("coverageStart", "coverageEnd"),
    ("start_date", "end_date"),
    ("startDate", "endDate"),
)

# A parsed interval: (inclusive lower bound or None=open, inclusive upper
# bound or None=open).
_Interval = tuple[Optional[date], Optional[date]]


def _parse_temporal_point(text: str) -> Optional[tuple[date, date]]:
    """Parse ONE endpoint into the (earliest, latest) day it can denote.

    A year widens to the whole year, a year-month to the whole month, a full
    date to itself. Returns None for anything outside the closed grammar or
    for an impossible calendar value ("2019-13", "2019-02-30").

    TOTAL BY CONSTRUCTION. Every failure mode must return None, never raise:
    the whole feature rests on "a missing or unknown date never fails a
    match", and an exception here is strictly worse than a fail — it
    propagates out of run_validations and takes the request with it. An
    external reviewer found ``"0000"`` doing exactly that (``date(0, 1, 1)``
    raises ValueError: year 0 is out of range), which the regex grammar
    happily admits. The wrapper below catches construction errors so a
    malformed date degrades to "unparseable" — the documented pass-by-default
    path — rather than a 500.
    """
    try:
        return _parse_temporal_point_inner(text)
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_temporal_point_inner(text: str) -> Optional[tuple[date, date]]:
    token = text.strip()
    if not token:
        return None

    m = _YEAR_RE.match(token)
    if m:
        year = int(m.group(1))
        return date(year, 1, 1), date(year, 12, 31)

    m = _YEAR_MONTH_RE.match(token)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            return None
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)

    m = _YEAR_MONTH_DAY_RE.match(token)
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None  # e.g. 2019-02-30
        return day, day

    return None


def _split_temporal_range(text: str) -> Optional[tuple[str, str]]:
    """Split a range expression into its two endpoint tokens, or None.

    Handles the explicit separators, plus the bare ``YYYY-YYYY`` shape (which
    is ambiguous with year-month and so is matched by its own pattern, not by
    splitting on "-").
    """
    for sep in _RANGE_SEPARATORS:
        if sep in text:
            parts = text.split(sep)
            if len(parts) != 2:
                return None  # "2019/2020/2021" is not an interval
            return parts[0].strip(), parts[1].strip()
    m = re.match(r"^(\d{4})\s*-\s*(\d{4})$", text)
    if m:
        return m.group(1), m.group(2)
    return None


def _parse_temporal_interval(text: Optional[str]) -> Optional[_Interval]:
    """Parse a free-text temporal declaration into an inclusive interval.

    Returns None — meaning "carries no comparable interval", which the
    validation treats as pass-by-default — for empty/absent input, for prose
    the closed grammar does not cover ("historical", "ongoing"), for
    impossible calendar values, for a reversed range, and for a fully-open
    ``../..`` (which constrains nothing).
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    split = _split_temporal_range(stripped)
    if split is None:
        point = _parse_temporal_point(stripped)
        return None if point is None else (point[0], point[1])

    low_token, high_token = split
    low_open = low_token == _OPEN_BOUND or low_token == ""
    high_open = high_token == _OPEN_BOUND or high_token == ""
    if low_open and high_open:
        return None  # "../.." constrains nothing

    low: Optional[date] = None
    high: Optional[date] = None
    if not low_open:
        parsed_low = _parse_temporal_point(low_token)
        if parsed_low is None:
            return None
        low = parsed_low[0]  # widen outward: earliest day the token denotes
    if not high_open:
        parsed_high = _parse_temporal_point(high_token)
        if parsed_high is None:
            return None
        high = parsed_high[1]  # widen outward: latest day the token denotes

    if low is not None and high is not None and low > high:
        return None  # reversed / nonsense range — do not guess an intent
    return low, high


def _intervals_overlap(a: _Interval, b: _Interval) -> bool:
    """True when two inclusive, possibly half-open intervals share any day.

    An open bound (None) extends to infinity in that direction, so it can
    only ever increase overlap — the fail-open direction.
    """
    a_low, a_high = a
    b_low, b_high = b
    if a_high is not None and b_low is not None and a_high < b_low:
        return False
    if b_high is not None and a_low is not None and b_high < a_low:
        return False
    return True


def _vendor_temporal_coverage(ref: VendorProductRef) -> Optional[str]:
    """Pull the vendor's declared temporal coverage.

    The explicit model field wins; the raw row is a fallback for ingest paths
    that have not been taught the field yet (same shape as
    ``_vendor_data_class``).
    """
    declared = (ref.temporal_coverage or "").strip()
    if declared:
        return declared
    if not ref.raw:
        return None

    # A lone START key means an ONGOING feed, not a one-day window. Pairing it
    # with its matching end key (when present) and otherwise emitting an
    # OPEN-ENDED interval is load-bearing: without it, "coverage_start=2020-01-01"
    # collapsed to the single day 2020-01-01 and a genuinely overlapping node
    # (2024-2025) was declared DISJOINT — a required-FAIL on a correct match.
    # Found by an adversarial verifier and reproduced end-to-end. A missing or
    # unknown date must never fail a match; only a positive, provable
    # disagreement may.
    for start_key, end_key in _VENDOR_TEMPORAL_START_END_PAIRS:
        start = ref.raw.get(start_key)
        if not start or not str(start).strip():
            continue
        start_text = str(start).strip()
        end = ref.raw.get(end_key)
        end_text = str(end).strip() if end else ""
        return f"{start_text}/{end_text}" if end_text else f"{start_text}/"

    for key in _VENDOR_TEMPORAL_KEYS:
        v = ref.raw.get(key)
        if v:
            text = str(v).strip()
            if text:
                return text
    return None


def run_validations(
    ref: VendorProductRef,
    node: Optional[TaxonomyNode],
    *,
    scope_allowed: bool,
    has_store_node: bool,
    node_data_class: Optional[str] = None,
    node_temporal_coverage: Optional[str] = None,
) -> list[Validation]:
    """Run the M5 validation set against a candidate mapping.

    Args:
        ref: The vendor product being mapped.
        node: The candidate CDAO node, or None if the matcher has no candidate
            (no-candidates / out-of-scope code paths).
        scope_allowed: Whether the scope gate passed for this ref.
        has_store_node: Whether the store could independently resolve the
            candidate node IRI (proves it exists in the taxonomy).
        node_data_class: The node's declared data_class, when the CDAO seed
            carries one. Pass-by-default when omitted.
        node_temporal_coverage: The node's declared temporal coverage,
            OVERRIDING ``node.temporal_coverage`` when supplied — the same
            explicit-plumbing shape as ``node_data_class``, so a caller can
            feed the store's own copy of the node (or a DCAT
            ``temporal_coverage`` the loader has not yet projected). Falls
            back to ``node.temporal_coverage``; pass-by-default when both
            are absent. Read only when SCUDO_TEMPORAL_VALIDATION is on.

    Returns:
        A list of Validation results, in stable order so a bundle export is
        diffable.
    """
    results: list[Validation] = []

    # 1) scope_compatible — required. Echo of the scope gate so the artifact
    #    self-documents why an out-of-scope vendor was blocked.
    results.append(
        Validation(
            name="scope_compatible",
            required=True,
            status="pass" if scope_allowed else "fail",
            detail="" if scope_allowed else f"Scope gate denied vendor {ref.vendor!r}.",
        )
    )

    # 2) identifier_resolves — required when a candidate node was proposed.
    if node is None:
        results.append(
            Validation(
                name="identifier_resolves",
                required=True,
                status="fail",
                detail="No candidate node available to resolve.",
            )
        )
    else:
        ok = bool(has_store_node and (node.iri or "").strip())
        results.append(
            Validation(
                name="identifier_resolves",
                required=True,
                status="pass" if ok else "fail",
                detail=""
                if ok
                else f"Node IRI {node.iri!r} not present in taxonomy store.",
            )
        )

    # 3) data_class_match — required only when both sides declare a class;
    #    pass-by-default otherwise so the demo runs without a class-tagged seed.
    vendor_class = _vendor_data_class(ref)
    nc = (node_data_class or "").strip().lower() if node_data_class else None
    if vendor_class and nc:
        ok = vendor_class == nc
        results.append(
            Validation(
                name="data_class_match",
                required=True,
                status="pass" if ok else "fail",
                detail="" if ok else f"vendor={vendor_class!r}, cdao={nc!r}",
            )
        )
    else:
        results.append(
            Validation(
                name="data_class_match",
                required=True,
                status="pass",
                detail="No declared class on one or both sides; pass-by-default.",
            )
        )

    # 4) name_length — warn-level field-format check.
    name_len = len(ref.name or "")
    results.append(
        Validation(
            name="name_length",
            required=False,
            status="pass" if name_len <= _NAME_LEN_CAP else "warn",
            detail=f"len={name_len} cap={_NAME_LEN_CAP}"
            if name_len > _NAME_LEN_CAP
            else "",
        )
    )

    # 5) description_length — warn-level field-format check.
    desc_len = len(ref.description or "")
    results.append(
        Validation(
            name="description_length",
            required=False,
            status="pass" if desc_len <= _DESCRIPTION_LEN_CAP else "warn",
            detail=(
                f"len={desc_len} cap={_DESCRIPTION_LEN_CAP}"
                if desc_len > _DESCRIPTION_LEN_CAP
                else ""
            ),
        )
    )

    # 6) input_completeness — required, flag-gated (default OFF). Guards the
    #    Jaro-Winkler thin-input inversion: name-only input scores HIGHER
    #    than the same record with its description (0.913 vs 0.822
    #    empirically), and a nameless record falls back to matching on the
    #    raw product_id (0.969). Flag off: nothing is emitted so existing
    #    artifacts stay byte-identical.
    if env_input_completeness_validation_enabled():
        problems: list[str] = []
        name = (ref.name or "").strip()
        description = (ref.description or "").strip()
        if not name:
            problems.append(
                "Vendor name is empty/whitespace — the retrieval query "
                "degrades to the raw product_id."
            )
        elif len(name) < 3:
            problems.append(
                f"Vendor name {name!r} is shorter than 3 characters — too "
                "short to be a product name."
            )
        elif _looks_like_bare_identifier(name):
            problems.append(
                f"Vendor name {name!r} looks like a bare identifier "
                "(caps/digits/separators, no natural-language words) — the "
                "query carries no semantic content, only string shape."
            )
        if not description:
            problems.append(
                "Vendor description is empty — single-field match; string "
                "similarity is unreliable without a description (thin input "
                "empirically scores HIGHER on the Jaro-Winkler dense arm)."
            )
        results.append(
            Validation(
                name="input_completeness",
                required=True,
                status="fail" if problems else "pass",
                detail="; ".join(problems),
            )
        )

    # 7) temporal_compatible — required, flag-gated (default OFF). Closes the
    #    "no temporal field or comparator anywhere" gap: without it, two
    #    products identical in name but covering different periods are
    #    indistinguishable to the engine. Pass-by-default on absence in
    #    EXACTLY the shape data_class_match uses above — a missing or
    #    unparseable date must never fail a match; only a positive,
    #    deterministic disagreement (both sides parseable AND disjoint) is a
    #    required failure. Flag off: nothing is emitted so existing artifacts
    #    stay byte-identical.
    if env_temporal_validation_enabled():
        vendor_temporal = _vendor_temporal_coverage(ref)
        node_temporal_raw = node_temporal_coverage
        if node_temporal_raw is None and node is not None:
            node_temporal_raw = node.temporal_coverage
        node_temporal = (node_temporal_raw or "").strip() or None

        vendor_interval = _parse_temporal_interval(vendor_temporal)
        node_interval = _parse_temporal_interval(node_temporal)

        if vendor_interval is not None and node_interval is not None:
            ok = _intervals_overlap(vendor_interval, node_interval)
            results.append(
                Validation(
                    name="temporal_compatible",
                    required=True,
                    status="pass" if ok else "fail",
                    detail=""
                    if ok
                    else (
                        f"Temporal coverage is disjoint: vendor="
                        f"{vendor_temporal!r}, cdao={node_temporal!r}."
                    ),
                )
            )
        else:
            results.append(
                Validation(
                    name="temporal_compatible",
                    required=True,
                    status="pass",
                    detail=(
                        "No comparable temporal coverage on one or both "
                        "sides; pass-by-default."
                    ),
                )
            )

    return results


def required_failures(validations: list[Validation]) -> list[Validation]:
    """Return the subset of validations that block auto-mapping.

    The matcher uses this to force `needs_review` even at/above the 0.80 floor.
    """
    return [v for v in validations if v.required and v.status == "fail"]
