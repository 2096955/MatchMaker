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


def taxonomy_candidate_desc(node: TaxonomyNode) -> str:
    """SKOS/DCAT definition text for the dense arm when the flag is on."""
    if not taxonomy_text_enabled():
        return ""
    return (node.definition or "").strip()


def taxonomy_dense_text(node: TaxonomyNode) -> str:
    """Composed label + definition for Jaro-Winkler when text is enabled."""
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


def _bm25_doc_with_text_flag(node: TaxonomyNode, *, text_on: bool) -> str:
    """BM25 document with an explicit text flag (for shadow comparison)."""
    parts = [(node.label or "").strip()]
    if text_on:
        parts.extend(lbl.strip() for lbl in node.alt_labels if lbl and lbl.strip())
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
    """When shadow is on and live text is off, log BM25 nomination diff.

    Returns the diff dict for tests; production path logs at DEBUG only.
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
        import logging

        logging.getLogger(__name__).debug(
            "SCUDO_TAXONOMY_TEXT shadow BM25 diff added=%s removed=%s",
            sorted(diff["added"]),
            sorted(diff["removed"]),
        )
    return diff
