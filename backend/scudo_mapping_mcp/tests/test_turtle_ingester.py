"""P4 — Turtle upload routing on the vendor ingest surface."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "p0_ontology_minimal.ttl"


def _ingest():
    os.environ.setdefault("STORE_BACKEND", "memory")
    os.environ.setdefault("FRAME_SOURCE", "mock")
    from scudo_mapping_mcp.ingest import ingest_bytes

    return ingest_bytes


def test_turtle_ingester_handles_suffixes():
    from scudo_mapping_mcp.turtle_ingester import TurtleIngester

    assert TurtleIngester.handles("taxonomy.ttl")
    assert TurtleIngester.handles("ontology.TURTLE")
    assert not TurtleIngester.handles("products.csv")


def test_ingest_bytes_routes_turtle_to_taxonomy_seed(monkeypatch):
    from scudo_mapping_mcp.store.memory_store import MemoryStore

    store = MemoryStore()
    monkeypatch.setattr("scudo_mapping_mcp.turtle_ingester.get_store", lambda: store)
    data = _FIXTURE.read_bytes()
    frames = _ingest()("LSEG", "taxonomy.ttl", data, upsert=True)
    assert frames == []
    assert len(store._nodes) > 0


def test_ingest_bytes_turtle_fail_closed_on_invalid_rdf():
    with pytest.raises(ValueError, match="invalid turtle"):
        _ingest()(
            "LSEG", "bad.ttl", b"@prefix ex: <http://ex/> .\nex:a [", upsert=False
        )


def test_ingest_bytes_turtle_fail_closed_on_empty_file():
    with pytest.raises(ValueError, match="empty turtle"):
        _ingest()("LSEG", "empty.ttl", b"   \n", upsert=False)


def test_ingest_bytes_csv_with_csvw_metadata_uses_aliases():
    csvw = {
        "tableSchema": {
            "columns": [
                {"name": "VendorSymbol", "titles": ["Product ID"]},
                {"name": "Title"},
            ]
        }
    }
    csv = b"VendorSymbol,Title\nABC-1,Alpha\n"
    frames = _ingest()(
        "LSEG",
        "vendor.csv",
        csv,
        upsert=False,
        csvw_metadata=csvw,
    )
    assert len(frames) == 1
    assert frames[0].product_id == "ABC-1"
    assert frames[0].name == "Alpha"


def test_ingest_bytes_csvw_only_json_fail_closed():
    body = b'{"tableSchema":{"columns":[{"name":"id"}]}}'
    with pytest.raises(ValueError, match="CSVW metadata cannot be ingested alone"):
        _ingest()("LSEG", "metadata.json", body, upsert=False)
