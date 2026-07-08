"""SSRF-guarded URL fetch + HTML title/text extraction for /mapping/ingest/url.

Fully hermetic: DNS resolution and the actual HTTP fetch are both injected,
so no test ever performs a real network call.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from scudo_mapping_mcp.url_ingest import (
    UrlIngestError,
    fetch_and_extract,
    synthesize_product_row,
    validate_public_http_url,
)


def _resolve_to(*ips):
    def _resolve(hostname):
        return list(ips)

    return _resolve


def test_rejects_non_http_scheme():
    with pytest.raises(UrlIngestError, match="scheme"):
        validate_public_http_url(
            "ftp://example.com/file", resolve=_resolve_to("93.184.216.34")
        )


def test_rejects_url_with_no_hostname():
    with pytest.raises(UrlIngestError, match="hostname"):
        validate_public_http_url("http:///path", resolve=_resolve_to("93.184.216.34"))


def test_rejects_loopback_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("127.0.0.1")
        )


def test_rejects_loopback_address_when_override_env_var_absent(monkeypatch):
    """Default posture: strict, no override -> loopback blocked."""
    monkeypatch.delenv("SCUDO_URL_INGEST_ALLOW_LOOPBACK", raising=False)
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("127.0.0.1")
        )


def test_allows_loopback_address_when_override_env_var_set(monkeypatch):
    """E2E-only escape hatch (off by default): the local fixture HTTP server
    used by the live E2E suite binds to loopback, which the SSRF guard
    otherwise always rejects - this narrow, explicit, off-by-default
    override exists ONLY so that suite can drive a real local target
    instead of the real internet."""
    monkeypatch.setenv("SCUDO_URL_INGEST_ALLOW_LOOPBACK", "1")
    hostname = validate_public_http_url(
        "http://127.0.0.1/", resolve=_resolve_to("127.0.0.1")
    )
    assert hostname == "127.0.0.1"


def test_override_env_var_does_not_widen_beyond_loopback(monkeypatch):
    """The override is scoped to loopback ONLY - private/link-local/reserved/
    multicast addresses must still be blocked even with the override set,
    proving this isn't a blanket SSRF-guard bypass."""
    monkeypatch.setenv("SCUDO_URL_INGEST_ALLOW_LOOPBACK", "1")
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("10.0.0.5")
        )
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("169.254.169.254")
        )


def test_rejects_private_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("10.0.0.5")
        )


def test_rejects_link_local_metadata_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url(
            "http://sneaky.example/", resolve=_resolve_to("169.254.169.254")
        )


def test_accepts_public_looking_address():
    hostname = validate_public_http_url(
        "http://example.com/page", resolve=_resolve_to("93.184.216.34")
    )
    assert hostname == "example.com"


def test_rejects_dns_resolution_failure():
    import socket

    def _resolve(hostname):
        raise socket.gaierror("nope")

    with pytest.raises(UrlIngestError, match="cannot resolve"):
        validate_public_http_url("http://doesnotexist.invalid/", resolve=_resolve)


class _FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_fetch_and_extract_returns_title_and_excerpt():
    html = b"<html><head><title>Widgets Inc</title></head><body><script>evil()</script><p>Great widgets for sale.</p></body></html>"

    def fake_getter(url, timeout, stream, allow_redirects):
        return _FakeResponse(html)

    title, excerpt = fetch_and_extract(
        "http://example.com/",
        resolve=_resolve_to("93.184.216.34"),
        getter=fake_getter,
    )
    assert title == "Widgets Inc"
    assert "Great widgets for sale." in excerpt
    assert "evil()" not in excerpt  # script content must be stripped


def test_fetch_and_extract_truncates_long_excerpt():
    long_text = "word " * 1000
    html = f"<html><head><title>T</title></head><body><p>{long_text}</p></body></html>".encode()

    def fake_getter(url, timeout, stream, allow_redirects):
        return _FakeResponse(html)

    _, excerpt = fetch_and_extract(
        "http://example.com/",
        resolve=_resolve_to("93.184.216.34"),
        getter=fake_getter,
    )
    assert len(excerpt) <= 2000


def test_fetch_and_extract_rejects_oversized_response():
    html = b"<html><head><title>T</title></head><body>x</body></html>"

    def fake_getter(url, timeout, stream, allow_redirects):
        return _FakeResponse(html)

    with pytest.raises(UrlIngestError, match="byte limit"):
        fetch_and_extract(
            "http://example.com/",
            resolve=_resolve_to("93.184.216.34"),
            getter=fake_getter,
            max_bytes=10,
        )


def test_synthesize_product_row_is_deterministic():
    row1 = synthesize_product_row("http://example.com/a", "Title", "Excerpt")
    row2 = synthesize_product_row("http://example.com/a", "Title", "Excerpt")
    assert row1 == row2
    assert row1["name"] == "Title"
    assert row1["description"] == "Excerpt"
    assert row1["product_id"]  # non-empty, deterministic uuid5 string

    row3 = synthesize_product_row("http://example.com/b", "Title", "Excerpt")
    assert row3["product_id"] != row1["product_id"]  # different URL -> different id
