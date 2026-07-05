"""SCUDO_SPECIALIST_BACKEND seam: local | strands | rest."""

from __future__ import annotations

import json

import pytest

from scudo_mapping_mcp.models import Candidate, TaxonomyNode, VendorProductRef


def _ref() -> VendorProductRef:
    return VendorProductRef(vendor="LSEG", product_id="X-1", name="Some Product")


def _candidate(iri: str = "cdao:on", similarity: float = 0.77) -> Candidate:
    return Candidate(node=TaxonomyNode(iri=iri, label="On"), similarity=similarity)


def test_local_strands_and_rest_return_callables(monkeypatch):
    from scudo_mapping_mcp.specialist import resolve_specialist

    monkeypatch.delenv("SCUDO_SPECIALIST_BACKEND", raising=False)

    assert callable(resolve_specialist("local"))
    assert callable(resolve_specialist("strands"))
    assert callable(resolve_specialist("rest"))


def test_unknown_backend_raises(monkeypatch):
    from scudo_mapping_mcp.specialist import resolve_specialist

    monkeypatch.delenv("SCUDO_SPECIALIST_BACKEND", raising=False)

    with pytest.raises(ValueError, match="SCUDO_SPECIALIST_BACKEND"):
        resolve_specialist("nonsense")


def test_env_selects_backend_when_no_explicit_arg(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "rest")
    assert callable(sp.resolve_specialist())

    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "strands")
    assert callable(sp.resolve_specialist())


def test_explicit_backend_overrides_env(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "rest")
    monkeypatch.setattr(sp, "make_rest_specialist", lambda: "local-scorer")

    assert sp.resolve_specialist("local") == "local-scorer"


def test_specialist_from_env_preserves_legacy_default(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.delenv("SCUDO_SPECIALIST_BACKEND", raising=False)
    assert sp.specialist_from_env() is None

    monkeypatch.setenv("SCUDO_SPECIALIST_BACKEND", "local")
    assert callable(sp.specialist_from_env())


def test_strands_backend_abstains_when_bridge_missing(monkeypatch):
    from scudo_mapping_mcp.specialist import resolve_specialist

    monkeypatch.delenv("SCUDO_SPECIALIST_BACKEND", raising=False)
    spec = resolve_specialist("strands")
    assert spec is not None
    assert spec(_ref(), [_candidate()]) is None


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self.data = body
        self.status = status


class _FakePool:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self._status = status
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append((method, url))
        return _FakeResp(self._body, self._status)


def test_rest_specialist_abstains_without_url(monkeypatch):
    from scudo_mapping_mcp.specialist import make_http_specialist

    monkeypatch.delenv("SCUDO_SPECIALIST_REST_URL", raising=False)
    monkeypatch.delenv("SCUDO_SPECIALIST_URL", raising=False)

    spec = make_http_specialist()

    assert spec(_ref(), [_candidate()]) is None


def test_rest_specialist_maps_in_list_pick(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_REST_URL", "http://spec.local/map")
    monkeypatch.setattr(
        sp,
        "_pool",
        lambda: _FakePool(json.dumps({"iri": "cdao:on", "confidence": 0.79}).encode()),
    )
    spec = sp.make_http_specialist()

    out = spec(_ref(), [_candidate()])

    assert out is not None
    assert out.node.iri == "cdao:on"
    assert abs(out.similarity - 0.79) < 1e-9


def test_rest_specialist_off_list_pick_abstains(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_REST_URL", "http://spec.local/map")
    monkeypatch.setattr(
        sp,
        "_pool",
        lambda: _FakePool(json.dumps({"iri": "cdao:HALLUCINATED"}).encode()),
    )
    spec = sp.make_http_specialist()

    assert spec(_ref(), [_candidate()]) is None


def test_rest_specialist_abstains_on_http_error(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_REST_URL", "http://spec.local/map")

    class _BoomPool:
        def request(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(sp, "_pool", lambda: _BoomPool())
    spec = sp.make_http_specialist()

    assert spec(_ref(), [_candidate()]) is None


def test_rest_specialist_abstains_on_bad_status(monkeypatch):
    from scudo_mapping_mcp import specialist as sp

    monkeypatch.setenv("SCUDO_SPECIALIST_REST_URL", "http://spec.local/map")
    monkeypatch.setattr(
        sp,
        "_pool",
        lambda: _FakePool(json.dumps({"iri": "cdao:on"}).encode(), status=503),
    )
    spec = sp.make_http_specialist()

    assert spec(_ref(), [_candidate()]) is None
