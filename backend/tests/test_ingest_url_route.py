"""POST /api/mapping/ingest/url fetches a URL server-side and ingests it
through the real ingest_bytes pipeline. Hermetic: the actual HTTP fetch is
monkeypatched, no real network call."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}

_FAKE_HTML = (
    b"<html><head><title>Global Equity Prices</title></head>"
    b"<body><p>Real-time equity price feed for major exchanges.</p></body></html>"
)


class _FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def _patch_fetch(monkeypatch, html=_FAKE_HTML, resolve_to="93.184.216.34"):
    import scudo_mapping_mcp.url_ingest as url_ingest

    monkeypatch.setattr(url_ingest, "_default_resolve", lambda hostname: [resolve_to])
    monkeypatch.setattr(
        "requests.get",
        lambda url, timeout, stream, allow_redirects: _FakeResponse(html),
    )


def test_url_ingest_happy_path(monkeypatch):
    _patch_fetch(monkeypatch)
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG", "url": "http://example.com/product"},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ingested"] == 1
    assert body["products"][0]["name"] == "Global Equity Prices"
    assert body["products"][0]["vendor"] == "LSEG"


def test_url_ingest_requires_vendor():
    r = client.post(
        "/api/mapping/ingest/url",
        json={"url": "http://example.com/product"},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_url_ingest_requires_url():
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG"},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_url_ingest_rejects_unknown_vendor(monkeypatch):
    _patch_fetch(monkeypatch)
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "NotAVendor", "url": "http://example.com/product"},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_url_ingest_rejects_ssrf_blocked_address(monkeypatch):
    _patch_fetch(monkeypatch, resolve_to="127.0.0.1")
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG", "url": "http://sneaky.example/"},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert "disallowed" in r.get_json()["error"]


def test_url_ingest_maps_fetch_failure_to_502(monkeypatch):
    import scudo_mapping_mcp.url_ingest as url_ingest

    monkeypatch.setattr(
        url_ingest, "_default_resolve", lambda hostname: ["93.184.216.34"]
    )

    def _boom(url, timeout, stream, allow_redirects):
        import requests

        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("requests.get", _boom)
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG", "url": "http://example.com/product"},
        headers=AUTH,
    )
    assert r.status_code == 502
