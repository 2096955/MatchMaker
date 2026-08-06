"""
M6 — the portable mapping bundle.

The cutover artifact. Build the mapping library on the mock side, export the
bundle, import it to seed UAT / Atlas. The bundle is versioned, diffable,
and intentionally flat + IRI-keyed + ISO-timestamped so the same shape can
later be emitted as RDF triples (DCAT + adapted-ODRL) on the Neptune side
without re-deriving anything.

What's in / what's not:

  IN  — every CONFIRMED MAPPED_TO precedent (approve / override). For each:
        the (vendor, product) identity, the canonical IRIs, the human's
        recorded decision and its preserved confidence + ISO-8601 timestamp,
        the corresponding rank-signal approval count, the M5 self-describing
        artifact (field_normalisation + validations), and a snapshot of the
        ``vendor_signature`` so the importer keys its rank-signal exactly the
        same way.

  OUT — provisional edges (I5 / Section 10a CAVEAT: never treat unattended
        auto-mapping results as ground truth). Negative precedents are also
        out — the cutover seeds POSITIVE mappings, not the dead-ends.

Idempotency:

  ``import_bundle`` writes precedents via ``upsert_precedent`` (single-positive-
  precedent invariant; the bundle's decided_at_ms is preserved). The rank
  signal is DERIVED from precedent edges (see ``RetrievalStore.rank_signals_for``)
  so re-import does NOT need to "set" it — the imported edges ARE the signal.
  A bundle imported twice into the same store leaves the graph identical
  on both axes.

  Patterns whose ``mapped_node_iri`` is absent in the importer's taxonomy are
  SKIPPED (not faked) and counted in the summary. Patterns whose vendor is
  out of scope locally are also skipped — invariant I3 fail-closed.

  The bundle's ``rank`` field is DIAGNOSTIC — a snapshot of the approval
  count at export time. The importer ignores it; the live signal is the
  count of imported edges sharing the same signature.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional

from .frames import check_scope
from .models import (
    BundleConflict,
    BundleImportSummary,
    BundleProvenance,
    FieldRule,
    MappingBundle,
    MappingPattern,
    TaxonomyNode,
    Validation,
    VendorProductRef,
)
from .store import get_store
from .validations import default_field_rules

# Bumped on bundle schema change; importers MUST refuse mismatches at major.
BUNDLE_FORMAT_VERSION = "1.0.0"


# ──────────────────────────────────────────────────────────────────────────
# Time conversion — single point of truth so FalkorDB ms ↔ ISO is consistent
# ──────────────────────────────────────────────────────────────────────────
def _ms_to_iso(ms: int) -> str:
    """Epoch milliseconds (UTC) -> ISO-8601 with millisecond precision."""
    if ms is None or int(ms) <= 0:
        # Defensive: a pattern with no decided_at would be unusable; surface
        # the missing-timestamp condition rather than silently inventing one.
        raise ValueError("decided_at_ms must be a positive epoch-ms value")
    secs, milli = divmod(int(ms), 1000)
    dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{milli:03d}Z"


def _iso_to_ms(iso: str) -> int:
    """ISO-8601 UTC -> epoch milliseconds."""
    if not iso:
        raise ValueError("decided_at iso string is empty")
    # Accept both "Z" and "+00:00" suffixes; normalise.
    s = iso.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _taxonomy_hash(nodes: list[TaxonomyNode]) -> str:
    """Deterministic fingerprint of the taxonomy seed.

    Sorted tuples in, sha256 of the JSON in, first 12 hex chars out.
    Stable across runs and processes (no dict ordering, no time).
    """
    payload = json.dumps(
        sorted([
            (n.iri, n.label or "", n.parent_iri or "")
            for n in nodes
        ]),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ──────────────────────────────────────────────────────────────────────────
# Export
# ──────────────────────────────────────────────────────────────────────────
def export_bundle(
    source_env: Optional[str] = None,
    *,
    created_at: Optional[str] = None,
    version: str = BUNDLE_FORMAT_VERSION,
) -> MappingBundle:
    """Build a MappingBundle from the live store's CONFIRMED precedents.

    Args:
        source_env: Logical environment name (e.g. ``"mock-local"``,
            ``"jpmc-uat"``). Defaults to ``SCUDO_SOURCE_ENV`` env var or
            ``"unknown"`` — explicit pass-in is preferred so the bundle's
            provenance reads correctly downstream.
        created_at: ISO-8601 UTC. Defaults to now. Tests pass an explicit
            value so re-export of the same store is byte-identical.
        version: Bundle FORMAT semver. Bumped on schema change.

    Returns:
        A MappingBundle ready to ``model_dump_json()`` or pass to
        ``import_bundle``.
    """
    store = get_store()
    nodes = store.list_taxonomy_nodes()
    taxonomy_version = _taxonomy_hash(nodes)
    raw_precedents = store.list_confirmed_precedents()

    rules = default_field_rules()

    patterns: list[MappingPattern] = []
    for rec in raw_precedents:
        sig = store.vendor_signature(
            rec.get("vendor", ""),
            rec.get("product_name", ""),
            rec.get("product_id", ""),
        )
        rank = store.rank_signals_for(sig).get(rec.get("mapped_node_iri", ""), 0)
        patterns.append(MappingPattern(
            vendor=rec["vendor"],
            product_id=rec["product_id"],
            product_name=rec.get("product_name") or "",
            description=rec.get("description") or "",
            signature=sig,
            mapped_node_iri=rec["mapped_node_iri"],
            mapped_node_label=rec.get("mapped_node_label") or "",
            confidence=float(rec.get("confidence") or 1.0),
            rank=int(rank or 0),
            field_normalisation=rules,
            validations=[Validation(
                name="precedent_human_confirmed",
                status="pass",
                required=False,
                detail="Status from prior HITL decision; recorded in bundle.",
            )],
            provenance=BundleProvenance(
                decided_by=rec.get("decided_by") or "",
                decided_at=_ms_to_iso(int(rec.get("decided_at_ms") or 0)),
                decision=rec.get("decision") or "approve",
                # M8 federated-audit fields, carried through the bundle so
                # the importer can replay decision-time source identity
                # onto the new precedent edge. None on the mock / inline
                # paths — no upstream provenance to record.
                source_content_hash=rec.get("source_content_hash"),
                source_file_audit_id=rec.get("source_file_audit_id"),
            ),
        ))

    return MappingBundle(
        version=version,
        created_at=created_at or _now_iso(),
        source_env=(source_env or os.getenv("SCUDO_SOURCE_ENV") or "unknown"),
        taxonomy_version=taxonomy_version,
        patterns=patterns,
    )


def _now_iso() -> str:
    """ISO-8601 UTC with millisecond precision. Centralised for diffability."""
    dt = datetime.now(timezone.utc)
    ms = int(dt.microsecond / 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


# ──────────────────────────────────────────────────────────────────────────
# Import
# ──────────────────────────────────────────────────────────────────────────
def import_bundle(
    bundle: MappingBundle,
    *,
    on_conflict: str = "refuse",
) -> BundleImportSummary:
    """Seed the live store with the bundle's CONFIRMED precedents.

    Idempotent: re-importing the same bundle into the same store leaves the
    graph unchanged. ``upsert_precedent`` enforces the single-positive-
    precedent invariant; the rank signal is DERIVED from precedent edges
    (``RetrievalStore.rank_signals_for``), so the precedent write IS the
    signal update — no separate seam call.

    The bundle's ``field_normalisation`` and ``validations`` fields are
    EXPORTER-SNAPSHOT diagnostics (see ``MappingPattern`` docstring). The
    current seam does not persist per-pattern overrides on the precedent
    edge; the importer's runtime re-derives both via ``default_field_rules``
    and ``run_validations``.

    Patterns are skipped (not failed) when:
      - the ``mapped_node_iri`` is unknown to the importer's taxonomy, or
      - the vendor is not in the importer's scope (I3 fail-closed locally).

    CONFLICT DETECTION (the merge case)
    -----------------------------------
    A bundle and the importer's local canon are two INDEPENDENT decision
    streams. ``upsert_precedent`` resolves a single stream correctly — an
    override supersedes a prior approval — by wiping existing MAPPED_TO
    edges. Applied blindly across streams that is a silent overwrite: a
    human's local approval replaced by a bundle from another environment,
    counted as ``applied``, with nothing surfaced to review.

    So before writing, the importer reads the existing precedent. When one
    exists and names a DIFFERENT node:

      on_conflict="refuse"    (default) — the pattern is NOT written. Local
                                canon stands, the disagreement is recorded
                                in ``summary.conflicts``. Fail-closed, and
                                consistent with how the scope gate and the
                                unknown-node path already behave.
      on_conflict="overwrite" — the bundle wins, but the overwrite is still
                                RECORDED in ``summary.conflicts`` with
                                ``resolution="overwritten"``. Use for a
                                deliberate re-baseline, never as a default.

    An identical decision (same target IRI) is NOT a conflict, so replay and
    hydration stay idempotent.

    Args:
        bundle: The versioned bundle to import.
        on_conflict: ``"refuse"`` (default) or ``"overwrite"``.

    Raises:
        ValueError: unparseable/incompatible bundle version, or an
            unrecognised ``on_conflict`` value (fail-closed: an unknown
            policy is never silently treated as permissive).

    Returns:
        BundleImportSummary with counts, any conflicts, and the source/local
        taxonomy fingerprints so operators can spot divergence.
    """
    if on_conflict not in ("refuse", "overwrite"):
        raise ValueError(
            f"on_conflict must be 'refuse' | 'overwrite', got {on_conflict!r}"
        )

    _enforce_format_compat(bundle.version)

    store = get_store()
    local_nodes = store.list_taxonomy_nodes()
    local_taxonomy_version = _taxonomy_hash(local_nodes)

    applied = 0
    skipped_unknown = 0
    skipped_scope = 0
    conflicts: list[BundleConflict] = []

    for p in bundle.patterns:
        node = store.get_taxonomy_node(p.mapped_node_iri)
        if node is None:
            skipped_unknown += 1
            continue

        # Construct the ref with the SAME source identity the original
        # decision was made against — so the importer's upsert_precedent
        # persists it onto the new edge, closing the federated audit chain
        # across environments.
        ref = VendorProductRef(
            vendor=p.vendor,
            product_id=p.product_id,
            name=p.product_name,
            description=p.description,
            source_content_hash=p.provenance.source_content_hash,
            source_file_audit_id=p.provenance.source_file_audit_id,
        )
        if not check_scope(ref).allowed:
            skipped_scope += 1
            continue

        # MERGE CASE — read before write. An existing precedent naming a
        # DIFFERENT node means two decision streams disagree; writing would
        # wipe the local edge silently (see upsert_precedent's
        # single-positive-precedent invariant). Same node = same decision,
        # which is the idempotent replay path and NOT a conflict.
        existing = store.get_precedent_mapping(p.vendor, p.product_id)
        if existing is not None and existing.mapped_node_iri and \
                existing.mapped_node_iri != p.mapped_node_iri:
            conflicts.append(BundleConflict(
                vendor=p.vendor,
                product_id=p.product_id,
                local_node_iri=existing.mapped_node_iri,
                incoming_node_iri=p.mapped_node_iri,
                local_node_label=existing.mapped_node_label or "",
                incoming_node_label=p.mapped_node_label or node.label or "",
                resolution="refused" if on_conflict == "refuse" else "overwritten",
            ))
            if on_conflict == "refuse":
                continue

        store.upsert_precedent(
            ref=ref, node=node,
            decision=p.provenance.decision,
            decided_by=p.provenance.decided_by,
            confidence=p.confidence,
            provisional=False,
            decided_at_ms=_iso_to_ms(p.provenance.decided_at),
        )

        # Rank signal is DERIVED from precedent edges (see store seam) — the
        # upsert above IS the signal update. No separate write here means
        # re-importing the same bundle is idempotent on every axis: the
        # precedent MERGEs to itself, the derived count cannot drift.
        #
        # field_normalisation and validations on the pattern are exporter-
        # snapshot diagnostics (see MappingPattern docstring); the seam does
        # not persist them on the precedent edge, and the importer's runtime
        # re-derives them from current code. Intentionally not read here.

        applied += 1

    return BundleImportSummary(
        total=len(bundle.patterns),
        applied=applied,
        skipped_unknown_node=skipped_unknown,
        skipped_out_of_scope=skipped_scope,
        conflicted=len(conflicts),
        conflicts=conflicts,
        taxonomy_version_source=bundle.taxonomy_version,
        taxonomy_version_local=local_taxonomy_version,
    )


def _enforce_format_compat(version: str) -> None:
    """Refuse bundles whose major version doesn't match this build.

    A patch / minor bump is forward-compatible; a major bump means schema
    change and the importer cannot guess. Fail loudly rather than silently
    misinterpret an unknown shape.
    """
    try:
        our_major = int(BUNDLE_FORMAT_VERSION.split(".")[0])
        their_major = int(version.split(".")[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"unparseable bundle version {version!r}") from exc
    if our_major != their_major:
        raise ValueError(
            f"bundle format major version {their_major} incompatible with "
            f"importer version {BUNDLE_FORMAT_VERSION}"
        )
