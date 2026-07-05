"""Provenance and exposition gates for the matching graph fixture."""

from __future__ import annotations

import json
import os


def _run_main_and_load() -> dict:
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")

    from scudo.build_matching_graph import main

    main()
    fixture = os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "matching-graph.json"
    )
    with open(fixture, encoding="utf-8") as fh:
        return json.load(fh)


def test_matching_graph_is_knowledge_graph_schema():
    """KnowledgeGraph top-level contract: version, kind, project, layers, tour."""
    g = _run_main_and_load()

    assert g.get("version") == "1.0.0", "version must be 1.0.0"
    assert g.get("kind") == "codebase", "kind must be codebase"

    project = g.get("project", {})
    assert project.get("name"), "project.name must be non-empty"
    assert project.get("description"), "project.description must be non-empty"
    assert project.get("analyzedAt"), "project.analyzedAt must be present"

    # All nodes have required fields
    for n in g["nodes"]:
        assert n.get("id"), f"node missing id: {n}"
        assert n.get("name"), f"{n['id']} missing name"
        assert n.get("summary"), f"{n['id']} missing summary"

    # Layers present and non-empty
    layers = g.get("layers", [])
    assert len(layers) >= 5, f"expected at least 5 layers, got {len(layers)}"
    layer_ids = {lyr["id"] for lyr in layers}
    assert "layer:etl" in layer_ids
    assert "layer:matching-engine" in layer_ids
    assert "layer:orchestration" in layer_ids
    assert "layer:japi-persist" in layer_ids
    assert "layer:cdao-market-data" in layer_ids
    assert "layer:cdao-reference-data" in layer_ids

    # Tour present and covers the three vendor products
    tour = g.get("tour", [])
    assert len(tour) >= 4, f"expected at least 4 tour steps, got {len(tour)}"
    tour_titles = [t["title"] for t in tour]
    assert any(
        "LSEG" in t or "LSEG-EQ-PX" in t or "Equity" in t for t in tour_titles
    ), "tour missing LSEG-EQ-PX step"
    assert any("SPG" in t or "FX" in t or "SPGLOBAL" in t for t in tour_titles), (
        "tour missing SPG-FX step"
    )
    assert any("ICE" in t or "Corporate" in t for t in tour_titles), (
        "tour missing ICE-CA step"
    )

    # No Marketing anywhere
    blob = json.dumps(g).lower()
    assert "marketing" not in blob, "Marketing domain still present in KnowledgeGraph"


def test_vendor_nodes_use_canonical_mds_iri():
    """Vendor product node ids must use the canonical mds.<vendor>:<uuid5>
    scheme (the runtime source of truth), not a hand-rolled vendor:<slug>:<ref>.
    The human-readable product ref stays recoverable from name/summary.
    """
    import re
    import uuid

    g = _run_main_and_load()

    vendor_nodes = [n for n in g["nodes"] if "vendor" in (n.get("tags") or [])]
    assert vendor_nodes, "expected at least one vendor product node"

    iri_re = re.compile(r"^mds\.[a-z0-9]+:[0-9a-f-]{36}$")
    for n in vendor_nodes:
        assert iri_re.match(n["id"]), (
            f"vendor node id {n['id']!r} is not mds.<vendor>:<uuid5>"
        )
        # uuid5 part must be a valid UUID
        uuid.UUID(n["id"].split(":", 1)[1])
        # legacy hand-rolled scheme must be gone
        assert not n["id"].startswith("vendor:"), (
            f"{n['id']} still uses legacy vendor: scheme"
        )

    # The three demo product refs remain discoverable in node text.
    blob = json.dumps(g)
    for ref in ("LSEG-EQ-PX", "SPG-FX", "ICE-CA"):
        assert ref in blob, f"product ref {ref} lost from graph text"


def test_every_node_is_labelled_and_expositional():
    """Every node has a non-empty summary, correct IRI scheme, no Marketing."""
    g = _run_main_and_load()

    for n in g["nodes"]:
        assert n.get("summary"), f"{n['id']} missing expositional summary"
        assert n.get("name"), f"{n['id']} missing name"
        assert not n["id"].startswith("urn:cdao:"), (
            f"{n['id']} uses fabricated urn:cdao: scheme"
        )
        assert not n["id"].startswith("cdao:"), f"{n['id']} uses legacy cdao: scheme"

    blob = json.dumps(g).lower()
    assert "marketing" not in blob, "incoherent Marketing branch still present"
    assert "urn:cdao" not in blob, "fabricated urn:cdao scheme still present"

    # Candidate edges carry band + weight in data
    candidate_edges = [e for e in g["edges"] if e.get("type") == "similar_to"]
    if candidate_edges:
        for e in candidate_edges:
            data = e.get("data", {})
            assert data.get("band"), f"similar_to edge missing data.band: {e}"
            assert "weight" in data, f"similar_to edge missing data.weight: {e}"

    # meta.json has correct provenance
    meta_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "meta.json")
    assert os.path.exists(meta_path), "meta.json not written"
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta.get("dataProvenance") == "synthetic", (
        "meta.json dataProvenance must be synthetic"
    )
    assert meta.get("bands", {}).get("pass") == 0.80, (
        "meta.json bands.pass must be 0.80"
    )
    assert meta.get("bands", {}).get("borderline") == 0.70, (
        "meta.json bands.borderline must be 0.70"
    )
