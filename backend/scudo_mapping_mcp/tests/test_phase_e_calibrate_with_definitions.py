"""Phase E step 5 (tooling half only) — make ``--with-definitions`` real.

Previously the flag was parsed and ignored. Now the harness carries
labelled precedent edges (query text + candidate node + human label) and
actually RE-SCORES them with and without definition enrichment, so the
comparison the shadow-window sign-off needs is computable offline.

Strictly NO band change: the harness reports; the 0.80/0.70 bands are a
Nigel-approved 5-zone contract and any move is a separate human sign-off.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scudo_mapping_mcp.models import TaxonomyNode
from scudo_mapping_mcp.scripts.calibrate_confidence_floor import (
    LabelledPrecedent,
    demo_labelled_precedents,
    rescore_cases,
)
from scudo_mapping_mcp.store.falkordb_store import _jaro_winkler

BACKEND = Path(__file__).resolve().parents[1]


def _tiny_fixture() -> list[LabelledPrecedent]:
    return [
        # Positive whose label diverges but definition matches the query:
        # enrichment should RAISE its score.
        LabelledPrecedent(
            query_text="real time equity prices feed",
            node=TaxonomyNode(
                iri="cdao:eq",
                label="EQP",
                definition="real time equity prices feed",
            ),
            label="positive",
        ),
        # Negative whose label is string-close but definition diverges:
        # enrichment should LOWER its score.
        LabelledPrecedent(
            query_text="equity prices",
            node=TaxonomyNode(
                iri="cdao:fx",
                label="equity pricez",
                definition=(
                    "foreign exchange spot rates settlement calendar totally "
                    "unrelated corpus of words"
                ),
            ),
            label="negative",
        ),
    ]


def test_rescore_is_deterministic():
    fixture = _tiny_fixture()
    a = rescore_cases(fixture, with_definitions=True)
    b = rescore_cases(fixture, with_definitions=True)
    assert [(c.score, c.label) for c in a] == [(c.score, c.label) for c in b]


def test_rescore_without_definitions_scores_label_only():
    fixture = _tiny_fixture()
    cases = rescore_cases(fixture, with_definitions=False)
    for precedent, case in zip(fixture, cases):
        assert case.label == precedent.label
        assert case.score == round(
            _jaro_winkler(precedent.query_text, precedent.node.label), 4
        )


def test_rescore_with_definitions_scores_label_plus_definition():
    fixture = _tiny_fixture()
    cases = rescore_cases(fixture, with_definitions=True)
    for precedent, case in zip(fixture, cases):
        composed = f"{precedent.node.label} {precedent.node.definition}".strip()
        assert case.score == round(_jaro_winkler(precedent.query_text, composed), 4)


def test_enrichment_moves_scores_in_the_expected_directions():
    fixture = _tiny_fixture()
    without = {c.score for c in [rescore_cases(fixture, with_definitions=False)[0]]}
    plain = rescore_cases(fixture, with_definitions=False)
    enriched = rescore_cases(fixture, with_definitions=True)
    assert without  # fixture sanity
    # Positive with matching definition gains; negative with divergent
    # definition drops.
    assert enriched[0].score > plain[0].score
    assert enriched[1].score < plain[1].score


def test_demo_labelled_precedents_shape():
    fixture = demo_labelled_precedents()
    assert fixture, "harness must ship a labelled demo fixture"
    assert all(p.label in ("positive", "negative") for p in fixture)
    assert all(isinstance(p.node, TaxonomyNode) for p in fixture)


def test_script_with_definitions_reports_both_scorings():
    script = BACKEND / "scripts" / "calibrate_confidence_floor.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND.parent)
    proc = subprocess.run(
        [sys.executable, str(script), "--with-definitions"],
        cwd=str(BACKEND.parent),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "definitions=True" in out
    # Both baseline and enriched sweeps are reported so the comparison the
    # exit criteria need is visible in one run.
    assert "definitions_off" in out
    assert "definitions_on" in out
    assert "pass_precision=" in out
    # The report names the ONE scorer actually implemented — no misleading
    # backend header.
    assert "scorer: jaro-winkler (4dp)" in out
    # The harness never proposes a band move — bands are a 5-zone contract.
    assert "default_floor=0.75" in out


def test_script_rejects_unimplemented_dense_backend():
    """--dense-backend is hard-constrained to the one implemented scorer:
    asking for opus must fail loudly, not print an opus header over
    Jaro-Winkler numbers."""
    script = BACKEND / "scripts" / "calibrate_confidence_floor.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND.parent)
    proc = subprocess.run(
        [sys.executable, str(script), "--dense-backend", "opus"],
        cwd=str(BACKEND.parent),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "jaro_winkler" in (proc.stderr + proc.stdout)
