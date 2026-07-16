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

import re
from typing import Optional

from .config import env_input_completeness_validation_enabled
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


def run_validations(
    ref: VendorProductRef,
    node: Optional[TaxonomyNode],
    *,
    scope_allowed: bool,
    has_store_node: bool,
    node_data_class: Optional[str] = None,
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

    return results


def required_failures(validations: list[Validation]) -> list[Validation]:
    """Return the subset of validations that block auto-mapping.

    The matcher uses this to force `needs_review` even at/above the 0.80 floor.
    """
    return [v for v in validations if v.required and v.status == "fail"]
