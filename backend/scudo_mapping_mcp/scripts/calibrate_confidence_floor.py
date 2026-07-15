#!/usr/bin/env python3
"""Offline confidence-floor calibration harness (WS7 P1; Phase E step 5).

Sweeps floor/half-width over labelled precedent edges and reports precision
for the PASS band. Run manually; artifacts go to stdout (not checked in).

Phase E (tooling half): ``--with-definitions`` is REAL — the harness carries
labelled precedent edges (query text + candidate TaxonomyNode + human label)
and actually re-scores them with the deterministic dense stand-in, with and
without definition enrichment, so the with/without comparison the shadow
window's exit criteria need is computable offline.

STRICTLY NO BAND CHANGE: this harness only reports. The 0.80/0.70 bands are
a Nigel-approved 5-zone contract (FE ``DEFAULT_BANDS`` sync) — any move is a
separate human sign-off, never something this script performs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from scudo_mapping_mcp.config import (
    BORDERLINE_HALF_WIDTH,
    CONFIDENCE_FLOOR,
    borderline_threshold,
    pass_threshold,
)
from scudo_mapping_mcp.models import TaxonomyNode


@dataclass
class LabelledCase:
    score: float
    label: str  # "positive" | "negative"


@dataclass
class LabelledPrecedent:
    """One labelled precedent edge: the vendor-side query text, the candidate
    CDAO node it was mapped to, and the human's verdict on that mapping."""

    query_text: str
    node: TaxonomyNode
    label: str  # "positive" | "negative"


def band(score: float, floor: float, half: float) -> str:
    if score >= pass_threshold(floor, half):
        return "PASS"
    if score >= borderline_threshold(floor, half):
        return "BORDERLINE"
    return "FAIL"


def precision_pass(cases: list[LabelledCase], floor: float, half: float) -> float:
    passed = [c for c in cases if band(c.score, floor, half) == "PASS"]
    if not passed:
        return 0.0
    tp = sum(1 for c in passed if c.label == "positive")
    return tp / len(passed)


def rescore_cases(
    precedents: list[LabelledPrecedent], *, with_definitions: bool
) -> list[LabelledCase]:
    """Re-score labelled precedents with the deterministic dense stand-in.

    ``with_definitions=False`` scores query vs label only (today's flag-off
    composition); ``True`` scores query vs label+definition — the exact
    text ``taxonomy_dense_text`` would compose with SCUDO_TAXONOMY_TEXT on.
    Deterministic by construction (Jaro-Winkler, no network), and rounded to
    4 dp to match ``Candidate.similarity``'s round-trip precision.
    """
    from scudo_mapping_mcp.store.falkordb_store import _jaro_winkler

    out: list[LabelledCase] = []
    for p in precedents:
        label = (p.node.label or "").strip()
        if with_definitions:
            desc = (p.node.definition or "").strip()
            candidate_text = f"{label} {desc}".strip() if desc else label
        else:
            candidate_text = label
        score = round(_jaro_winkler(p.query_text, candidate_text), 4)
        out.append(LabelledCase(score=score, label=p.label))
    return out


def demo_labelled_precedents() -> list[LabelledPrecedent]:
    """Small labelled demo fixture — REPLACE with real exported precedent
    edges (M6 bundle / HITL decisions) for an actual calibration run."""
    return [
        LabelledPrecedent(
            query_text="equity prices real time feed",
            node=TaxonomyNode(
                iri="cdao:eq-prices",
                label="Equity Prices",
                definition="Real-time and end-of-day equity price feed.",
            ),
            label="positive",
        ),
        LabelledPrecedent(
            query_text="equity index constituents",
            node=TaxonomyNode(
                iri="cdao:index-constituents",
                label="Index Constituents",
                definition="Constituent membership of equity indices.",
            ),
            label="positive",
        ),
        LabelledPrecedent(
            query_text="fx spot rates",
            node=TaxonomyNode(
                iri="cdao:fx-spot",
                label="FX Spot Rates",
                definition="Foreign exchange spot rate quotations.",
            ),
            label="positive",
        ),
        LabelledPrecedent(
            query_text="equity prices",
            node=TaxonomyNode(
                iri="cdao:fixed-income",
                label="equity pricez",
                definition="Fixed income bond reference data and coupons.",
            ),
            label="negative",
        ),
        LabelledPrecedent(
            query_text="commodity futures settlement",
            node=TaxonomyNode(
                iri="cdao:esg-scores",
                label="ESG Scores",
                definition="Environmental, social and governance ratings.",
            ),
            label="negative",
        ),
    ]


def _sweep(cases: list[LabelledCase], *, tag: str) -> None:
    for floor in (0.75, 0.80, 0.85):
        for half in (0.03, 0.05, 0.07):
            p = precision_pass(cases, floor, half)
            print(
                f"{tag} floor={floor:.2f} half={half:.2f} "
                f"pass_edge={pass_threshold(floor, half):.2f} "
                f"pass_precision={p:.3f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="SCUDO confidence floor calibration")
    # Hard-constrained to the ONE implemented scorer: rescore_cases is
    # Jaro-Winkler only, so accepting "opus" here would print a misleading
    # header over Jaro-Winkler numbers. Widen choices only when a second
    # scorer is actually wired into rescore_cases.
    parser.add_argument(
        "--dense-backend",
        default="jaro_winkler",
        choices=["jaro_winkler"],
    )
    parser.add_argument("--with-definitions", action="store_true")
    args = parser.parse_args()

    print(f"backend={args.dense_backend} definitions={args.with_definitions}")
    print("scorer: jaro-winkler (4dp)")

    precedents = demo_labelled_precedents()
    baseline = rescore_cases(precedents, with_definitions=False)
    _sweep(baseline, tag="definitions_off")

    if args.with_definitions:
        enriched = rescore_cases(precedents, with_definitions=True)
        _sweep(enriched, tag="definitions_on")
        for p, b, e in zip(precedents, baseline, enriched):
            print(
                f"case iri={p.node.iri} label={p.label} "
                f"score_off={b.score:.4f} score_on={e.score:.4f} "
                f"delta={e.score - b.score:+.4f}"
            )

    print(f"default_floor={CONFIDENCE_FLOOR} default_half={BORDERLINE_HALF_WIDTH}")
    print(
        "NOTE: report only — bands 0.80/0.70 are a 5-zone contract; "
        "any floor move is a separate human sign-off."
    )


if __name__ == "__main__":
    main()
