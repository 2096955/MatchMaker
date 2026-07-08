"""SSRF-guarded website-URL ingestion: validate -> fetch -> extract title/text
-> synthesize one product row. The row is fed into the EXISTING, unmodified
ingest_bytes() pipeline by ingest.py's ingest_url() - this module owns only
the URL-specific mechanics (everything downstream of the synthesized row is
100% reused, not reinvented).

FAIL LOUD: every rejection (bad scheme, SSRF-blocked address, DNS failure,
fetch failure, oversized response) raises UrlIngestError (a ValueError) so
the Flask route can map it straight to a 400 - a live ingestion request must
never silently no-op.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

# Off-by-default, narrowly-scoped E2E/local-dev escape hatch: the local
# fixture HTTP server the live E2E suite drives (backend/tests/e2e/
# fixture_server.py) necessarily binds to loopback, which the SSRF guard
# below otherwise always rejects. Setting this env var relaxes ONLY the
# loopback check - private/link-local/reserved/multicast addresses remain
# blocked unconditionally (see
# test_override_env_var_does_not_widen_beyond_loopback). NEVER set in any
# deployed environment.
_ALLOW_LOOPBACK_ENV_VAR = "SCUDO_URL_INGEST_ALLOW_LOOPBACK"

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_EXCERPT_CHARS = 2000
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024

Resolver = Callable[[str], list]


class UrlIngestError(ValueError):
    """Any rejected or failed URL-ingestion input. Subclasses ValueError so
    the Flask route's existing ``except ValueError`` -> 400 pattern (matching
    /mapping/ingest's file-upload error handling) applies unchanged."""


def _default_resolve(hostname: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(hostname, None)]


def validate_public_http_url(url: str, *, resolve: Optional[Resolver] = None) -> str:
    """Return the validated hostname, or raise UrlIngestError. Rejects
    non-http(s) schemes and any hostname whose resolved address is
    loopback/private/link-local/reserved/multicast (SSRF guard covers the
    169.254.169.254 cloud metadata address as a link-local address) - unless
    the loopback-only escape hatch (``SCUDO_URL_INGEST_ALLOW_LOOPBACK``) is
    set, for the live E2E suite's local fixture server. ``resolve`` is
    injectable so tests never perform a real DNS lookup.

    ``resolve`` defaults to ``None`` and is resolved to ``_default_resolve``
    INSIDE the body (not as a bound default-parameter value) so that
    monkeypatching the module-level ``_default_resolve`` name (as tests do)
    is actually observed — a bound default (``resolve: Resolver =
    _default_resolve``) captures the function object once at import time and
    never sees a later monkeypatch of the module attribute.
    """
    if resolve is None:
        resolve = _default_resolve
    allow_loopback = bool(os.getenv(_ALLOW_LOOPBACK_ENV_VAR, "").strip())
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlIngestError(
            f"unsupported URL scheme: {parsed.scheme!r} (must be http/https)"
        )
    if not parsed.hostname:
        raise UrlIngestError("URL has no hostname")

    try:
        addresses = resolve(parsed.hostname)
    except socket.gaierror as e:
        raise UrlIngestError(f"cannot resolve host {parsed.hostname!r}: {e}") from e
    if not addresses:
        raise UrlIngestError(f"cannot resolve host {parsed.hostname!r}: no addresses")

    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_loopback and allow_loopback:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise UrlIngestError(
                f"URL host {parsed.hostname!r} resolves to a disallowed address "
                f"({addr}) - loopback/private/link-local/reserved/multicast "
                "addresses are blocked (SSRF guard)"
            )
    return parsed.hostname


def fetch_and_extract(
    url: str,
    *,
    resolve: Optional[Resolver] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: Optional[int] = None,
    getter: Optional[Callable[..., "requests.Response"]] = None,
) -> tuple[str, str]:
    """Validate + fetch a URL, returning (title, text_excerpt). ``resolve``
    and ``getter`` are both injectable so tests never perform a real network
    call. Both default to ``None`` and are resolved to the live
    ``requests.get`` / ``_default_resolve`` INSIDE the body, for the same
    monkeypatch-visibility reason as ``validate_public_http_url`` above.
    """
    if getter is None:
        getter = requests.get
    validate_public_http_url(url, resolve=resolve)
    cap = max_bytes if max_bytes is not None else _DEFAULT_MAX_BYTES

    # allow_redirects=False: a redirect (e.g. to http://169.254.169.254/) would
    # bypass the DNS check above entirely, since that check only inspects the
    # ORIGINAL hostname, never any redirect target - closes the cheapest SSRF
    # bypass at negligible cost. NOT closed: requests' own DNS resolution for
    # this exact same hostname is a separate lookup from the one performed by
    # `resolve` above (TOCTOU) - out of scope per the spec, noted as a gap.
    resp = getter(url, timeout=timeout, stream=True, allow_redirects=False)
    resp.raise_for_status()
    content = resp.content
    if len(content) > cap:
        raise UrlIngestError(f"response exceeds {cap} byte limit")

    from lxml import html as lxml_html

    tree = lxml_html.fromstring(content)
    title_els = tree.xpath("//title/text()")
    title = title_els[0].strip() if title_els else url
    for bad in tree.xpath("//script | //style"):
        bad.getparent().remove(bad)
    text = " ".join(tree.text_content().split())
    excerpt = text[:MAX_EXCERPT_CHARS]
    return title, excerpt


def synthesize_product_row(url: str, title: str, excerpt: str) -> dict:
    """Deterministic, replay-safe product_id derived from the URL via the
    standard RFC 4122 URL namespace (uuid.NAMESPACE_URL) - same URL always
    synthesizes the same row, matching the repo's existing uuid5-based IRI
    determinism convention (models.py's mds_iri)."""
    product_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url))
    return {"product_id": product_id, "name": title, "description": excerpt}
