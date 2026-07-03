"""P3 — real serialise_rights + SHACL validation (WS6)."""

from __future__ import annotations

import pytest

from scudo.rdf import backend, fake, real

_SAMPLE_RIGHTS = {
    "vendor_product_iri": "mds.lseg:00000000-0000-4000-8000-000000000001",
    "policy_uid": "policy-lseg-eq-001",
    "target_iri": "jpmorgan:data:cdao:equity-prices",
    "permissions": [{"action": "use"}],
    "ontology_snapshot": "cdao-2026-01",
}


def test_fake_serialise_rights_still_empty_stub():
    out = fake.serialise_rights(_SAMPLE_RIGHTS)
    assert out["triples"] == []
    assert out["conforms"] is True
    assert out["report"]["stub"] is True


def test_real_serialise_rights_emits_odrl_triples():
    out = real.serialise_rights(_SAMPLE_RIGHTS)
    assert out["triples"]
    preds = {t["predicate"] for t in out["triples"]}
    assert "rdf:type" in preds
    assert "odrl:uid" in preds
    assert out["report"]["engine"] == "scudo.rdf.real.serialise_rights"


def test_real_serialise_rights_validates_with_pyshacl():
    pytest.importorskip("pyshacl")
    out = real.serialise_rights(_SAMPLE_RIGHTS)
    assert out["conforms"] is True
    assert out["report"]["validation"]["engine"] == "scudo.rdf.real.validate_shapes"


def test_rdflib_backend_serialise_rights_uses_shacl_path(monkeypatch):
    pytest.importorskip("pyshacl")
    monkeypatch.setenv("SCUDO_RDF_BACKEND", "rdflib")
    out = backend.serialise_rights(_SAMPLE_RIGHTS)
    assert out["conforms"] is True
    assert out["report"]["engine"] == "scudo.rdf.real.serialise_rights"


def test_real_serialise_rights_fail_closed_without_uid():
    pytest.importorskip("pyshacl")
    bad = {
        "vendor_product_iri": "",
        "permissions": [{"action": "use"}],
    }
    out = real.serialise_rights(bad)
    assert out["conforms"] is False
