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

from typing import Optional

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
        FieldRule(vendor_field="product_id", cdao_field="vendorRef", transform="identity"),
    ]


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
    results.append(Validation(
        name="scope_compatible", required=True,
        status="pass" if scope_allowed else "fail",
        detail="" if scope_allowed else f"Scope gate denied vendor {ref.vendor!r}.",
    ))

    # 2) identifier_resolves — required when a candidate node was proposed.
    if node is None:
        results.append(Validation(
            name="identifier_resolves", required=True, status="fail",
            detail="No candidate node available to resolve.",
        ))
    else:
        ok = bool(has_store_node and (node.iri or "").strip())
        results.append(Validation(
            name="identifier_resolves", required=True,
            status="pass" if ok else "fail",
            detail="" if ok else f"Node IRI {node.iri!r} not present in taxonomy store.",
        ))

    # 3) data_class_match — required only when both sides declare a class;
    #    pass-by-default otherwise so the demo runs without a class-tagged seed.
    vendor_class = _vendor_data_class(ref)
    nc = (node_data_class or "").strip().lower() if node_data_class else None
    if vendor_class and nc:
        ok = vendor_class == nc
        results.append(Validation(
            name="data_class_match", required=True,
            status="pass" if ok else "fail",
            detail="" if ok else f"vendor={vendor_class!r}, cdao={nc!r}",
        ))
    else:
        results.append(Validation(
            name="data_class_match", required=True, status="pass",
            detail="No declared class on one or both sides; pass-by-default.",
        ))

    # 4) name_length — warn-level field-format check.
    name_len = len(ref.name or "")
    results.append(Validation(
        name="name_length", required=False,
        status="pass" if name_len <= _NAME_LEN_CAP else "warn",
        detail=f"len={name_len} cap={_NAME_LEN_CAP}" if name_len > _NAME_LEN_CAP else "",
    ))

    # 5) description_length — warn-level field-format check.
    desc_len = len(ref.description or "")
    results.append(Validation(
        name="description_length", required=False,
        status="pass" if desc_len <= _DESCRIPTION_LEN_CAP else "warn",
        detail=(
            f"len={desc_len} cap={_DESCRIPTION_LEN_CAP}"
            if desc_len > _DESCRIPTION_LEN_CAP else ""
        ),
    ))

    return results


def required_failures(validations: list[Validation]) -> list[Validation]:
    """Return the subset of validations that block auto-mapping.

    The matcher uses this to force `needs_review` even at/above the 0.80 floor.
    """
    return [v for v in validations if v.required and v.status == "fail"]
