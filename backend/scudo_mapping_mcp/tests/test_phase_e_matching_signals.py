"""Phase E — Dataset matching signals (spec
2026-07-13-catalogue-rights-uml-gap-analysis.md, Phase E, steps 1-4 + 6).

The genuinely new signals are ``businessConcept`` / ``assetClass`` /
``superAssetClass`` (keyword/theme already flow). Contract pinned here:

  * STRUCTURED fields on ``TaxonomyNode`` — read unconditionally at load,
    NEVER pre-concatenated into ``definition``/``alt_labels`` (step 1).
  * Composition into matching text happens only at flag time behind the
    sub-flag ``SCUDO_TAXONOMY_UML_TEXT`` (default off) and is BM25-ONLY:
    ``taxonomy_dense_text`` stays label+definition so the new fields can
    never move ``Candidate.similarity`` (step 3 — floor neutrality against
    the Nigel-approved 0.80/0.70 bands, which this phase must not touch).
  * IRI-valued references resolve to prefLabels — never raw IRIs in text.
  * The alt-label channel is capped symmetrically with the 2000-char
    definition cap (BM25 avgdl is global).
"""

from __future__ import annotations

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import DCTERMS, RDF, SKOS
from rdflib.namespace import DCAT as RDF_DCAT

from scudo_mapping_mcp.csvw_aliases import CAT_ONTOLOGY_NS
from scudo_mapping_mcp.loaders.dcat_loader import load_dcat_taxonomy_from_graph
from scudo_mapping_mcp.models import TaxonomyNode, VendorProductRef
from scudo_mapping_mcp.store.memory_store import MemoryStore
from scudo_mapping_mcp.taxonomy_text import (
    taxonomy_bm25_doc,
    taxonomy_candidate_desc,
    taxonomy_dense_text,
)

CAT = Namespace(CAT_ONTOLOGY_NS)
EX = Namespace("https://example.com/cat/")

_ALL_FLAGS = (
    "SCUDO_TAXONOMY_TEXT",
    "SCUDO_TAXONOMY_TEXT_SHADOW",
    "SCUDO_TAXONOMY_UML_TEXT",
)


def _set_flags(monkeypatch, **flags):
    """Clear all taxonomy-text flags then set the requested ones, and
    resync the frozen settings snapshot (same pattern as
    test_taxonomy_text_shadow.py)."""
    for key in _ALL_FLAGS:
        monkeypatch.delenv(key, raising=False)
    for key, val in flags.items():
        monkeypatch.setenv(key, val)
    from scudo_mapping_mcp import config as cfg

    monkeypatch.setattr(cfg, "settings", cfg.Settings.from_env())


def _signal_node(**overrides) -> TaxonomyNode:
    base = dict(
        iri="cdao:eq-prices",
        label="Equity Prices",
        definition="End-of-day equity prices.",
        alt_labels=["Stocks"],
        business_concept="Pricing",
        asset_class="Equity",
        super_asset_class="Securities",
    )
    base.update(overrides)
    return TaxonomyNode(**base)


# ── Step 1 — structured fields, unconditional load, no text merging ───────


def test_taxonomy_node_signal_fields_default_none():
    node = TaxonomyNode(iri="x", label="L")
    assert node.business_concept is None
    assert node.asset_class is None
    assert node.super_asset_class is None


def test_loader_reads_signals_unconditionally_into_structured_fields(monkeypatch):
    """Flags all OFF: the loader still captures the cat: predicates into the
    structured fields — and none of them leak into any text surface."""
    _set_flags(monkeypatch)  # everything off
    g = Graph()
    ds = EX["eq-prices"]
    g.add((ds, RDF.type, RDF_DCAT.Dataset))
    g.add((ds, DCTERMS.title, Literal("Equity Prices")))
    g.add((ds, DCTERMS.description, Literal("End-of-day equity prices.")))
    g.add((ds, CAT.businessConcept, Literal("Pricing")))
    g.add((ds, CAT.assetClass, Literal("AC-10294")))
    g.add((ds, CAT.superAssetClass, Literal("PAC-88371")))

    node = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}[str(ds)]
    assert node.business_concept == "Pricing"
    assert node.asset_class == "AC-10294"
    assert node.super_asset_class == "PAC-88371"
    # Never pre-concatenated into definition / alt_labels / label.
    text = " ".join([node.label, node.definition, *node.alt_labels])
    for signal in ("Pricing", "AC-10294", "PAC-88371"):
        assert signal not in text


def test_loader_resolves_iri_valued_business_concept_to_pref_label(monkeypatch):
    """Step 3: IRI references resolve to prefLabels — never raw IRIs in
    text (tokeniser junk)."""
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    g = Graph()
    ds = EX["eq-prices"]
    concept = EX["PricingConcept"]
    g.add((ds, RDF.type, RDF_DCAT.Dataset))
    g.add((ds, DCTERMS.title, Literal("Equity Prices")))
    g.add((ds, CAT.businessConcept, concept))
    g.add((concept, SKOS.prefLabel, Literal("Pricing Concepts")))

    node = {n.iri: n for n in load_dcat_taxonomy_from_graph(g)}[str(ds)]
    assert node.business_concept == "Pricing Concepts"
    doc = taxonomy_bm25_doc(node)
    assert "Pricing Concepts" in doc
    assert str(concept) not in doc  # raw IRI never reaches matching text


# ── Step 1 — flag-off inertness (mirrors test_taxonomy_text_shadow.py) ────


def test_bm25_doc_byte_identical_when_uml_flag_off(monkeypatch):
    """SCUDO_TAXONOMY_UML_TEXT off ⇒ matching text is byte-identical with
    today's composition, even with signal fields populated."""
    node = _signal_node()
    baseline = _signal_node(
        business_concept=None, asset_class=None, super_asset_class=None
    )

    # Text flag ON, UML sub-flag OFF: today's label+alts+definition doc.
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on")
    assert taxonomy_bm25_doc(node) == taxonomy_bm25_doc(baseline)
    assert taxonomy_bm25_doc(node) == "Equity Prices Stocks End-of-day equity prices."

    # Everything OFF: label only, exactly as today.
    _set_flags(monkeypatch)
    assert taxonomy_bm25_doc(node) == "Equity Prices"

    # UML sub-flag ON but parent text flag OFF: still label only (sub-flag
    # semantics — UML text rides the ontology-text channel, which is gated).
    _set_flags(monkeypatch, SCUDO_TAXONOMY_UML_TEXT="on")
    assert taxonomy_bm25_doc(node) == "Equity Prices"


def test_bm25_doc_includes_signals_when_both_flags_on(monkeypatch):
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    doc = taxonomy_bm25_doc(_signal_node())
    for token in ("Equity Prices", "Stocks", "Pricing", "Securities"):
        assert token in doc
    assert "End-of-day equity prices." in doc


def test_uml_flag_is_call_time_env_read_not_frozen_snapshot(monkeypatch):
    """Removing SCUDO_TAXONOMY_UML_TEXT from the environment must turn the
    channel OFF in the SAME process, even if the frozen settings snapshot
    was built while it was on — the live check is the env, matching the
    call-time-read contract of the other SCUDO_ flags."""
    from scudo_mapping_mcp import config as cfg
    from scudo_mapping_mcp.taxonomy_text import taxonomy_uml_text_enabled

    node = _signal_node()

    # Process "started" with both flags on: snapshot captures uml=True.
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    assert cfg.settings.taxonomy_uml_text is True
    assert taxonomy_uml_text_enabled() is True
    assert "Pricing" in taxonomy_bm25_doc(node)

    # Operator deletes the env var — snapshot still says True, but the
    # live channel must go dark immediately.
    monkeypatch.delenv("SCUDO_TAXONOMY_UML_TEXT", raising=False)
    assert cfg.settings.taxonomy_uml_text is True  # stale snapshot, by design
    assert taxonomy_uml_text_enabled() is False
    assert "Pricing" not in taxonomy_bm25_doc(node)


# ── Step 3 — floor-neutral channel: dense text NEVER sees the signals ─────


def test_dense_text_is_label_and_definition_only_even_when_flags_on(monkeypatch):
    """Pin: the new fields can never move Candidate.similarity — dense text
    is byte-identical with/without signals, on both flag settings."""
    node = _signal_node()
    stripped = _signal_node(
        business_concept=None, asset_class=None, super_asset_class=None
    )

    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    assert taxonomy_dense_text(node) == taxonomy_dense_text(stripped)
    assert taxonomy_dense_text(node) == "Equity Prices End-of-day equity prices."
    assert taxonomy_candidate_desc(node) == taxonomy_candidate_desc(stripped)

    _set_flags(monkeypatch)
    assert taxonomy_dense_text(node) == "Equity Prices"


def test_memory_store_similarity_unmoved_by_signals_when_flag_on(monkeypatch):
    """End-to-end floor neutrality on the legacy jaro path: identical
    Candidate.similarity with and without signal fields, flags fully on."""
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    monkeypatch.delenv("SCUDO_DENSE_BACKEND", raising=False)
    monkeypatch.delenv("SCUDO_USE_OPUS_DENSE", raising=False)
    ref = VendorProductRef(vendor="LSEG", product_id="p", name="Equity Prices")

    with_signals = MemoryStore(nodes=[_signal_node()])
    without_signals = MemoryStore(
        nodes=[
            _signal_node(
                business_concept=None, asset_class=None, super_asset_class=None
            )
        ]
    )
    sim_with = {
        c.node.iri: c.similarity for c in with_signals.find_similar_products(ref)
    }
    sim_without = {
        c.node.iri: c.similarity for c in without_signals.find_similar_products(ref)
    }
    assert sim_with == sim_without


# ── Step 3 — symmetric cap on the alt-label channel ───────────────────────


def test_alt_label_channel_capped_symmetrically_with_definition_cap(monkeypatch):
    """BM25 avgdl is global — uncapped alt enrichment shifts scores of
    unenriched docs too. The alt+signal block truncates at the same
    2000-char cap ``models_dcat._join_definition`` applies to definitions."""
    _set_flags(monkeypatch, SCUDO_TAXONOMY_TEXT="on", SCUDO_TAXONOMY_UML_TEXT="on")
    filler = ["alt" + str(i) for i in range(500)]  # > 2000 chars joined
    node = _signal_node(
        alt_labels=filler,
        business_concept="BEYONDCAPTOKEN",
        definition="Def.",
    )
    doc = taxonomy_bm25_doc(node)
    # label + capped alt block + definition, with separators.
    assert len(doc) <= len("Equity Prices") + 1 + 2000 + 1 + len("Def.")
    assert "BEYONDCAPTOKEN" not in doc  # fell past the symmetric cap
    assert doc.endswith("Def.")  # definition still composed after the block
