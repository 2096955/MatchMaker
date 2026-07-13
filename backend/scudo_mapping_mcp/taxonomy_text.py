"""Taxonomy text helpers for BM25 nomination and dense-arm candidate descriptions.

Gated by ``Settings.taxonomy_text_enabled`` (``SCUDO_TAXONOMY_TEXT``). When off,
behaviour matches the pre-ontology era: label-only BM25 and empty candidate_desc.
Extended ``TaxonomyNode`` fields (definition, alt_labels) may still be stored and
read from the graph; they do not affect matching until the flag is on.

Invariants (unchanged by ontology text injection)
---------------------------------------------------
* **Dense-score-only**: ``Candidate.similarity`` is always the dense-arm score
  (Jaro-Winkler, Opus per-pair, or Opus multi-path). Never BM25. Never RRF.
* **BM25-nominator-only** (``use_opus_dense`` / ``multi_path_retrieve`` path):
  ``taxonomy_bm25_doc`` widens lexical recall for the BM25 pre-filter only.
  Pre-filtered candidates carry ``similarity=0.0`` until dense rescore runs.
* **Legacy store path** (default): BM25 + RRF may reorder results but
  ``Candidate.similarity`` remains the raw dense score from the dense arm.
"""

from __future__ import annotations

import os

from .models import TaxonomyNode


def taxonomy_text_enabled() -> bool:
    if "SCUDO_TAXONOMY_TEXT" in os.environ:
        from .config import env_taxonomy_text_enabled

        return env_taxonomy_text_enabled()
    from .config import settings

    return settings.taxonomy_text_enabled


def taxonomy_uml_text_enabled() -> bool:
    """Phase E sub-flag ``SCUDO_TAXONOMY_UML_TEXT`` — compose the UML
    matching signals (business_concept / asset_class / super_asset_class)
    into the BM25 doc. Rides the ontology-text channel: no effect unless
    ``SCUDO_TAXONOMY_TEXT`` is also on. BM25-ONLY — ``taxonomy_dense_text``
    never sees these fields, so they can never move ``Candidate.similarity``
    (floor neutrality against the 0.80/0.70 bands).

    ALWAYS a live env read — never the frozen ``settings`` snapshot. A
    process started with the flag on must go dark the moment the operator
    removes the var (call-time-read contract; ``Settings.taxonomy_uml_text``
    remains for introspection only)."""
    from .config import env_taxonomy_uml_text_enabled

    return env_taxonomy_uml_text_enabled()


def taxonomy_candidate_desc(node: TaxonomyNode) -> str:
    """SKOS/DCAT definition text for the dense arm when the flag is on."""
    if not taxonomy_text_enabled():
        return ""
    return (node.definition or "").strip()


def taxonomy_dense_text(node: TaxonomyNode) -> str:
    """Composed label + definition for Jaro-Winkler when text is enabled.

    PIN (Phase E floor neutrality): this stays label + definition ONLY.
    The UML signal fields route through the BM25/alt-label channel and are
    deliberately excluded here so they can never move the dense score the
    0.80/0.70 gate reads (``tests/test_phase_e_matching_signals.py``).
    """
    label = (node.label or "").strip()
    if not taxonomy_text_enabled():
        return label
    desc = taxonomy_candidate_desc(node)
    return f"{label} {desc}".strip() if desc else label


def taxonomy_bm25_doc(node: TaxonomyNode) -> str:
    """BM25 document: label + alt_labels + definition (recall widening only).

    Used exclusively for BM25 nomination / lexical sidecar ranking. Must never
    be written to ``Candidate.similarity`` — see ``retrieval._bm25_prefilter``.
    """
    return _bm25_doc_with_text_flag(node, text_on=taxonomy_text_enabled())


# Symmetric with ``models_dcat._DEFINITION_MAX_LEN`` (the 2000-char
# definition cap): BM25 avgdl is global, so an uncapped alt/signal block
# would shift the scores of UNenriched docs too.
_ALT_BLOCK_MAX_LEN = 2000


def _cap_alt_block(block: str) -> str:
    if len(block) > _ALT_BLOCK_MAX_LEN:
        return block[: _ALT_BLOCK_MAX_LEN - 3] + "..."
    return block


def _bm25_doc_with_text_flag(
    node: TaxonomyNode, *, text_on: bool, uml_on: bool | None = None
) -> str:
    """BM25 document with an explicit text flag (for shadow comparison).

    ``uml_on`` defaults to the live SCUDO_TAXONOMY_UML_TEXT flag. When off,
    the composition is byte-identical with the pre-Phase-E doc (label +
    alt_labels + definition). When on, the UML signal fields join the
    alt-label block, and the whole alt+signal block is capped at
    ``_ALT_BLOCK_MAX_LEN`` (symmetric with the definition cap).
    """
    parts = [(node.label or "").strip()]
    if text_on:
        if uml_on is None:
            uml_on = taxonomy_uml_text_enabled()
        alt_parts = [lbl.strip() for lbl in node.alt_labels if lbl and lbl.strip()]
        if uml_on:
            seen = set(alt_parts)
            for signal in (
                node.business_concept,
                node.asset_class,
                node.super_asset_class,
            ):
                s = (signal or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    alt_parts.append(s)
            block = _cap_alt_block(" ".join(alt_parts))
            if block:
                parts.append(block)
        else:
            parts.extend(alt_parts)
        desc = (node.definition or "").strip()
        if desc:
            parts.append(desc)
    return " ".join(p for p in parts if p)


def taxonomy_text_shadow_enabled() -> bool:
    if "SCUDO_TAXONOMY_TEXT_SHADOW" in os.environ:
        from .config import env_taxonomy_text_shadow_enabled

        return env_taxonomy_text_shadow_enabled()
    from .config import settings

    return settings.taxonomy_text_shadow


def shadow_bm25_top_iris(
    query_text: str,
    universe: list[TaxonomyNode],
    store,
    *,
    top_n: int,
) -> set[str]:
    """BM25 top-N IRIs as if ``SCUDO_TAXONOMY_TEXT`` were on."""
    docs = [
        (node.iri, _bm25_doc_with_text_flag(node, text_on=True)) for node in universe
    ]
    bm25 = store.bm25_scores(query_text, docs)
    by_iri = {n.iri: n for n in universe}
    ranked_iris = sorted(
        by_iri.keys(),
        key=lambda iri: (-bm25.get(iri, 0.0), iri),
    )[:top_n]
    return set(ranked_iris)


def maybe_log_taxonomy_text_shadow(
    query_text: str,
    universe: list[TaxonomyNode],
    store,
    nominated: list,
    *,
    top_n: int,
) -> dict[str, set[str]] | None:
    """When shadow is on and live text is off, record the BM25 nomination diff.

    Wired into BOTH nomination paths (Phase E step 2): the multi-path
    ``retrieval._bm25_prefilter`` route AND the DEFAULT legacy store BM25
    sidecars (``memory_store`` / ``falkordb_store``) — previously only the
    multi-path route carried it, so shadow mode produced zero signal in
    default environments.

    Returns the diff dict for tests. A non-empty diff emits a COUNTED
    CloudWatch EMF metric (``TaxonomyShadowDiff`` = added + removed, via
    ``scudo.metrics.emit`` — no-op outside Lambda) plus the DEBUG log with
    the IRIs for correlation.
    """
    if not taxonomy_text_shadow_enabled() or taxonomy_text_enabled():
        return None

    production = {c.node.iri for c in nominated}
    shadow = shadow_bm25_top_iris(query_text, universe, store, top_n=top_n)
    diff = {
        "added": shadow - production,
        "removed": production - shadow,
    }
    if diff["added"] or diff["removed"]:
        _emit_shadow_diff_metric(len(diff["added"]), len(diff["removed"]))
        import logging

        logging.getLogger(__name__).debug(
            "SCUDO_TAXONOMY_TEXT shadow BM25 diff added=%s removed=%s",
            sorted(diff["added"]),
            sorted(diff["removed"]),
        )
    return diff


def _emit_shadow_diff_metric(added: int, removed: int) -> None:
    """Best-effort EMF counter for the shadow nomination diff (778b47a
    pattern). Metrics are never load-bearing — never let one break matching."""
    try:
        from scudo import metrics

        metrics.emit("TaxonomyShadowDiff", added + removed)
    except Exception:  # noqa: BLE001 — metrics must never break the matcher
        pass
