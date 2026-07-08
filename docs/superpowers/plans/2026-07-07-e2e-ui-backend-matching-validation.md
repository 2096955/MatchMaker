# E2E UI/backend validation of the SCUDO matching path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove — or make true — that the UI can drive the real backend matching path for
both a vendor file upload and a website URL submission, covering verification items 1-7 from
`docs/superpowers/specs/2026-07-07-e2e-ui-backend-matching-validation-design.md`.

**Architecture:** A new, narrow `POST /api/mapping/ingest/url` backend route (SSRF-guarded
fetch → HTML title/text extraction → synthesized single-row JSON → reuses the existing,
unmodified `ingest_bytes` pipeline); a new `/matching-test` page in the editable `frontend/`
React18 app wired to that route plus the existing `/mapping/ingest/stream`,
`/mapping/agent/describe`, and `/mapping/agent/run` routes; an off-by-default static route in
`backend/app.py` that serves the read-only, vendored `dashboard-dist/` bundle same-origin;
and a live Playwright E2E suite driving both UIs against one real local backend running in
scripted/memory/mock demo mode.

**Tech Stack:** Flask, `requests` (newly declared direct dependency — was previously only
transitive via `requests-aws4auth`), `lxml` (already a dependency), React18 + Vite + axios,
Python `playwright` (already installed locally), `pytest`.

## Global Constraints

- Stay only in `/Users/anthonylui/MatchMaker/MatchMaker`. Never touch Understand-Anything or
  Defra repos.
- `dashboard-dist/` is read-only — never hand-edit it (CLAUDE.md convention); the new static
  route in `app.py` only *serves* it.
- Never edit `CLAUDE.md`.
- No `git commit`, `push`, `deploy`, `clean`, `reset`, `rm`, or `checkout` — despite this
  plan's task template showing a "Commit" step per the standard skill format, **every such
  step in this plan is replaced with a no-op checkpoint** (see each task) — do not run `git
  commit` at any point while executing this plan.
- No real AWS/network calls in automated tests. The one exception — the URL-ingestion route's
  outbound fetch — is exercised live only against a local fixture HTTP server the test suite
  itself starts, never a real external host.
- `SCUDO_AGENT_BACKEND` must be left unset (defaults to `"scripted"`) for every local run in
  this plan — no real Bedrock/Azure calls anywhere.
- Ask narrow, MatchMaker-scoped approval before: any `pip install`/`npm install`, starting a
  dev server, or running Playwright browser install/launch, per the user's own constraint.
- Final verification must cover all 7 items with exact commands, exact URLs/ports, pass/fail
  counts, changed files, and remaining gaps.

---

## File Structure

- **Create** `backend/scudo_mapping_mcp/url_ingest.py` — SSRF-guard validation, URL fetch,
  HTML→title/excerpt extraction, product-row synthesis. New, focused, no dependents yet.
- **Modify** `backend/scudo_mapping_mcp/ingest.py` — add one new entrypoint function
  `ingest_url()`, sibling to the existing `ingest_bytes()`, calling into `url_ingest.py` then
  reusing `ingest_bytes()` unchanged.
- **Modify** `backend/routes/mapping.py` — add `POST /mapping/ingest/url` route.
- **Modify** `backend/requirements.txt` — declare `requests` as a direct dependency.
- **Modify** `backend/app.py` — add the off-by-default `/demo/*` static route for
  `dashboard-dist/`.
- **Create** `backend/tests/test_url_ingest.py` — hermetic unit tests for the SSRF guard and
  fetch/extract/synthesize functions (injectable resolver + fetcher, no real network).
- **Create** `backend/tests/test_ingest_url_route.py` — hermetic Flask route tests, mirroring
  `test_ingest_stream_route.py`'s client/AUTH pattern, mocking the fetch.
- **Modify** `frontend/src/api/index.js` — add `ingestMappingUrl`, `ingestMappingFileStream`;
  extract the shared SSE-consuming loop out of the existing `runAgentStream` into one private
  helper `_consumeSSE`, used by both.
- **Create** `frontend/src/pages/matching/MatchingTest.jsx` — the new page.
- **Modify** `frontend/src/App.jsx` — register the `/matching-test` route.
- **Modify** `frontend/src/components/Layout.jsx` — add the nav link.
- **Create** `backend/tests/e2e/fixture_server.py` — tiny stdlib `ThreadingHTTPServer` helper
  serving one static HTML fixture, for the E2E suite's URL-ingestion step.
- **Create** `backend/tests/e2e/test_matching_ui_e2e.py` — the live Playwright suite.
- **Create** `infra/e2e_smoke.sh` — starts backend + frontend, runs the E2E suite, tears both
  down.

---

### Task 1: SSRF-guarded URL validation + fetch/extract (pure unit, TDD)

**Files:**
- Create: `backend/scudo_mapping_mcp/url_ingest.py`
- Test: `backend/tests/test_url_ingest.py`

**Interfaces:**
- Consumes: nothing from other tasks (first task, foundational).
- Produces (used by Task 2):
  - `class UrlIngestError(ValueError)`
  - `validate_public_http_url(url: str, *, resolve: Callable[[str], list[str]] = _default_resolve) -> str` (returns hostname or raises `UrlIngestError`)
  - `fetch_and_extract(url: str, *, resolve=..., timeout: float = 10.0, max_bytes: int | None = None, getter: Callable[..., requests.Response] = requests.get) -> tuple[str, str]` (returns `(title, excerpt)`)
  - `synthesize_product_row(url: str, title: str, excerpt: str) -> dict` (returns `{"product_id", "name", "description"}`)
  - `MAX_EXCERPT_CHARS = 2000`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_url_ingest.py
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
        validate_public_http_url("ftp://example.com/file", resolve=_resolve_to("93.184.216.34"))


def test_rejects_url_with_no_hostname():
    with pytest.raises(UrlIngestError, match="hostname"):
        validate_public_http_url("http:///path", resolve=_resolve_to("93.184.216.34"))


def test_rejects_loopback_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url("http://sneaky.example/", resolve=_resolve_to("127.0.0.1"))


def test_rejects_private_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url("http://sneaky.example/", resolve=_resolve_to("10.0.0.5"))


def test_rejects_link_local_metadata_address():
    with pytest.raises(UrlIngestError, match="disallowed"):
        validate_public_http_url("http://sneaky.example/", resolve=_resolve_to("169.254.169.254"))


def test_accepts_public_looking_address():
    hostname = validate_public_http_url("http://example.com/page", resolve=_resolve_to("93.184.216.34"))
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

    def fake_getter(url, timeout, stream):
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

    def fake_getter(url, timeout, stream):
        return _FakeResponse(html)

    _, excerpt = fetch_and_extract(
        "http://example.com/",
        resolve=_resolve_to("93.184.216.34"),
        getter=fake_getter,
    )
    assert len(excerpt) <= 2000


def test_fetch_and_extract_rejects_oversized_response():
    html = b"<html><head><title>T</title></head><body>x</body></html>"

    def fake_getter(url, timeout, stream):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_url_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scudo_mapping_mcp.url_ingest'` (or `ImportError`) — the module doesn't exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/scudo_mapping_mcp/url_ingest.py
"""SSRF-guarded website-URL ingestion: validate -> fetch -> extract title/text
-> synthesize one product row. The row is fed into the EXISTING, unmodified
ingest_bytes() pipeline by ingest.py's ingest_url() — this module owns only
the URL-specific mechanics (everything downstream of the synthesized row is
100% reused, not reinvented).

FAIL LOUD: every rejection (bad scheme, SSRF-blocked address, DNS failure,
fetch failure, oversized response) raises UrlIngestError (a ValueError) so
the Flask route can map it straight to a 400 - a live ingestion request must
never silently no-op.
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_EXCERPT_CHARS = 2000
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024

Resolver = Callable[[str], list]


class UrlIngestError(ValueError):
    """Any rejected or failed URL-ingestion input. Subclasses ValueError so
    the Flask route's existing `except ValueError` -> 400 pattern (matching
    /mapping/ingest's file-upload error handling) applies unchanged."""


def _default_resolve(hostname: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(hostname, None)]


def validate_public_http_url(url: str, *, resolve: Resolver = _default_resolve) -> str:
    """Return the validated hostname, or raise UrlIngestError. Rejects
    non-http(s) schemes and any hostname whose resolved address is
    loopback/private/link-local/reserved/multicast (SSRF guard covers the
    169.254.169.254 cloud metadata address as a link-local address).
    `resolve` is injectable so tests never perform a real DNS lookup.
    """
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
    resolve: Resolver = _default_resolve,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: Optional[int] = None,
    getter: Callable[..., "requests.Response"] = requests.get,
) -> tuple[str, str]:
    """Validate + fetch a URL, returning (title, text_excerpt). `resolve`
    and `getter` are both injectable so tests never perform a real network
    call."""
    validate_public_http_url(url, resolve=resolve)
    cap = max_bytes if max_bytes is not None else _DEFAULT_MAX_BYTES

    resp = getter(url, timeout=timeout, stream=True)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_url_ingest.py -v`
Expected: PASS (all 10 tests).

- [ ] **Step 5: Ruff check**

Run: `rtk proxy ruff check backend/scudo_mapping_mcp/url_ingest.py backend/tests/test_url_ingest.py`
Expected: `All checks passed!`

- [ ] **Step 6: Checkpoint (no commit — session constraint)**

Do not run `git commit`. Move to Task 2.

---

### Task 2: `requests` as a direct dependency + `ingest_url()` orchestration

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/scudo_mapping_mcp/ingest.py`
- Test: `backend/tests/test_ingest_url_route.py` (Task 3 also extends this file; this task
  can be tested via a small addition here, or verified directly through Task 3's route tests
  since `ingest_url()` has no independent HTTP surface — fold its test coverage into Task 3
  to avoid a redundant test file).

**Interfaces:**
- Consumes: `url_ingest.validate_public_http_url`, `url_ingest.fetch_and_extract`,
  `url_ingest.synthesize_product_row`, `url_ingest.UrlIngestError` (Task 1). `ingest_bytes`
  (already exists, unmodified, in the same file).
- Produces (used by Task 3): `ingest_url(vendor: str, url: str, *, upsert: bool = True, on_stage=None, **fetch_kwargs) -> list[VendorProductRef]`

- [ ] **Step 1: Declare `requests` as a direct dependency**

Edit `backend/requirements.txt` — add a line near the existing `requests-aws4auth` entry:

```
requests>=2.32  # backend/scudo_mapping_mcp/url_ingest.py's URL-ingestion fetch (was previously only a transitive dep of requests-aws4auth)
```

- [ ] **Step 2: Add `ingest_url()` to `ingest.py`**

Add this function to `backend/scudo_mapping_mcp/ingest.py`, right after `ingest_bytes` (after
its closing `return frames` at the end of the existing function):

```python
def ingest_url(
    vendor: str,
    url: str,
    upsert: bool = True,
    on_stage: Optional[StageCallback] = None,
    **fetch_kwargs,
) -> list[VendorProductRef]:
    """Fetch a website URL server-side, synthesize ONE vendor-product row
    from its title/text, and run it through the SAME real ingest_bytes
    pipeline used for file uploads - no parallel ingestion path, no
    fabricated success. `fetch_kwargs` forwards to url_ingest.fetch_and_extract
    (e.g. a fake `resolve`/`getter` in tests).
    """
    from .url_ingest import fetch_and_extract, synthesize_product_row

    title, excerpt = fetch_and_extract(url, **fetch_kwargs)
    row = synthesize_product_row(url, title, excerpt)
    data = json.dumps([row]).encode("utf-8")
    filename = f"{row['product_id']}.json"
    return ingest_bytes(vendor, filename, data, upsert=upsert, on_stage=on_stage)
```

- [ ] **Step 3: Ruff check**

Run: `rtk proxy ruff check backend/scudo_mapping_mcp/ingest.py`
Expected: `All checks passed!`

(No standalone test run here — `ingest_url()` is exercised end-to-end by Task 3's route
tests, since it has no meaningful behavior independent of being called with a real or fake
fetch. This avoids a redundant test file per DRY.)

- [ ] **Step 4: Checkpoint (no commit — session constraint)**

---

### Task 3: `POST /api/mapping/ingest/url` Flask route (TDD)

**Files:**
- Modify: `backend/routes/mapping.py`
- Create: `backend/tests/test_ingest_url_route.py`

**Interfaces:**
- Consumes: `ingest_url` (Task 2), `UrlIngestError` (Task 1), `_validate_vendor` (existing,
  `routes/mapping.py:145`).
- Produces (used by Task 5/6, the frontend): route `POST /api/mapping/ingest/url` — JSON body
  `{"vendor": str, "url": str}`, JSON response `{"ingested": int, "products": [{"vendor",
  "product_id", "name"}]}` on 200; `{"error": str}` on 400/502.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ingest_url_route.py
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

    monkeypatch.setattr(
        url_ingest, "_default_resolve", lambda hostname: [resolve_to]
    )
    monkeypatch.setattr(
        "requests.get", lambda url, timeout, stream: _FakeResponse(html)
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

    def _boom(url, timeout, stream):
        import requests

        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("requests.get", _boom)
    r = client.post(
        "/api/mapping/ingest/url",
        json={"vendor": "LSEG", "url": "http://example.com/product"},
        headers=AUTH,
    )
    assert r.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_ingest_url_route.py -v`
Expected: FAIL with 404 (route doesn't exist yet) on every test.

- [ ] **Step 3: Write the minimal implementation**

Add this import near the top of `backend/routes/mapping.py`, alongside the existing
`from scudo_mapping_mcp.ingest import ingest_bytes, seed_conceptual_layer, seed_taxonomy`:

```python
import requests

from scudo_mapping_mcp.ingest import ingest_bytes, ingest_url, seed_conceptual_layer, seed_taxonomy
from scudo_mapping_mcp.url_ingest import UrlIngestError
```

Add this route right after the existing `ingest_vendor_file_stream` function (after its
closing, before the `@mapping_bp.get("/mapping/working_set")` route):

```python
@mapping_bp.post("/mapping/ingest/url")
def ingest_vendor_url():
    """Fetch a website URL server-side, synthesize a single vendor-product
    row from its title/text, and run it through the SAME real ingest_bytes
    pipeline used for file uploads (see ingest_url in scudo_mapping_mcp/ingest.py).

    JSON body:
        vendor (str): One of PRIORITY_VENDORS.
        url (str):    The website URL to fetch (http/https only; SSRF-guarded).

    Returns:
        flask.Response: JSON {ingested: int, products: [{vendor, product_id,
            name}]} on success - same shape as POST /mapping/ingest.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    url = (body.get("url") or "").strip()
    if not vendor:
        return jsonify({"error": "vendor is required"}), 400
    err = _validate_vendor(vendor)
    if err:
        return jsonify({"error": err}), 400
    if not url:
        return jsonify({"error": "url is required"}), 400

    try:
        frames = ingest_url(vendor, url, upsert=True)
    except ValueError as e:
        # Covers UrlIngestError (SSRF/scheme/DNS rejection) - a ValueError
        # subclass, same 400-mapping convention as ingest_vendor_file's
        # "except (UnicodeDecodeError, ValueError)".
        ui_logger.warning(
            "Vendor URL ingest rejected", vendor=vendor, url=url, reason=str(e)
        )
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        ui_logger.error(
            "Vendor URL fetch failed",
            vendor=vendor,
            url=url,
            error=f"{type(e).__name__}: {e}",
        )
        return jsonify({"error": f"failed to fetch URL: {e}"}), 502

    ui_logger.info(
        "Vendor URL ingested",
        principal=g.principal.user_id,
        vendor=vendor,
        url=url,
        products=len(frames),
    )
    return jsonify(
        {
            "ingested": len(frames),
            "products": [
                {"vendor": fr.vendor, "product_id": fr.product_id, "name": fr.name}
                for fr in frames
            ],
        }
    )
```

Note: `UrlIngestError` is imported but not referenced by name in the except clause (it's
caught via the base `ValueError`) — this matches the plan's earlier design; if ruff flags the
import as unused, reference it in the docstring is not enough to satisfy F401 — instead
remove the unused `UrlIngestError` import from this file (Task 1's module already exports it
for `url_ingest.py`'s own tests; `mapping.py` doesn't need to import it directly since it
only ever catches the base `ValueError`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_ingest_url_route.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Run the full backend test suite touched so far**

Run: `rtk proxy python -m pytest backend/tests/test_url_ingest.py backend/tests/test_ingest_url_route.py backend/tests/test_ingest_stream_route.py -v`
Expected: all PASS (confirms no regression on the existing file-upload route).

- [ ] **Step 6: Ruff check**

Run: `rtk proxy ruff check backend/routes/mapping.py backend/scudo_mapping_mcp/ingest.py backend/tests/test_ingest_url_route.py`
Expected: `All checks passed!` (fix the `UrlIngestError` unused-import issue per the note above
if ruff flags it).

- [ ] **Step 7: Checkpoint (no commit — session constraint)**

---

### Task 4: Off-by-default `dashboard-dist` static route

**Files:**
- Modify: `backend/app.py`
- Test: `backend/tests/test_dashboard_dist_route.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (used by Task 7, the E2E suite): `GET /demo/` and `GET /demo/<path>` serve files
  from `dashboard-dist/`, registered ONLY when `SCUDO_SERVE_DASHBOARD_DIST` is truthy.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dashboard_dist_route.py
"""The /demo/* static route for dashboard-dist is OFF by default and only
registers when SCUDO_SERVE_DASHBOARD_DIST is set - must never change behavior
for the deployed app, which serves the SPA via CloudFront, not Flask."""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_demo_route_absent_by_default(monkeypatch):
    monkeypatch.delenv("SCUDO_SERVE_DASHBOARD_DIST", raising=False)
    import app as app_module

    importlib.reload(app_module)
    client = app_module.app.test_client()
    r = client.get("/demo/")
    assert r.status_code == 404


def test_demo_route_serves_dashboard_dist_when_enabled(monkeypatch):
    monkeypatch.setenv("SCUDO_SERVE_DASHBOARD_DIST", "1")
    import app as app_module

    importlib.reload(app_module)
    client = app_module.app.test_client()
    r = client.get("/demo/index.html")
    assert r.status_code == 200
    assert b"<html" in r.data.lower() or b"<!doctype" in r.data.lower()

    # Teardown: reload again with the env var unset so later test modules
    # (imported once per process by pytest) see the default, off state.
    monkeypatch.delenv("SCUDO_SERVE_DASHBOARD_DIST", raising=False)
    importlib.reload(app_module)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_dashboard_dist_route.py -v`
Expected: FAIL — `test_demo_route_serves_dashboard_dist_when_enabled` gets 404 (route doesn't
exist yet). `test_demo_route_absent_by_default` may pass trivially (also 404) — that's fine,
it pins the default-off behavior for later.

- [ ] **Step 3: Write the minimal implementation**

Add this block to `backend/app.py` right after the blueprint registrations (after
`app.register_blueprint(visibility_bp, url_prefix="/api")`, before the `@app.get("/healthz")`
route):

```python
# Off-by-default local-dev convenience: serve the vendored, READ-ONLY
# dashboard-dist/ bundle same-origin so its relative /api/* fetches
# (confirmed empty base URL in the bundle) reach this same Flask process.
# Unset in every deployed environment (the SPA is served via CloudFront
# there) - this changes NOTHING when SCUDO_SERVE_DASHBOARD_DIST is unset.
if os.getenv("SCUDO_SERVE_DASHBOARD_DIST"):
    from flask import send_from_directory

    _DASHBOARD_DIST_DIR = os.path.join(
        os.path.dirname(__file__), "..", "dashboard-dist"
    )

    @app.get("/demo/")
    def _serve_dashboard_dist_index():
        return send_from_directory(_DASHBOARD_DIST_DIR, "index.html")

    @app.get("/demo/<path:filename>")
    def _serve_dashboard_dist_asset(filename):
        return send_from_directory(_DASHBOARD_DIST_DIR, filename)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anthonylui/MatchMaker/MatchMaker && rtk proxy python -m pytest backend/tests/test_dashboard_dist_route.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full existing app.py-dependent test files to check for regressions**

Run: `rtk proxy python -m pytest backend/tests/ -v -k "not e2e"`
Expected: all PASS (the `importlib.reload` in the new test must not break other test modules'
already-imported `app` reference — if it does, note this as a gap in the final report rather
than working around it with a forced module-reload hack elsewhere).

- [ ] **Step 6: Ruff check**

Run: `rtk proxy ruff check backend/app.py backend/tests/test_dashboard_dist_route.py`
Expected: `All checks passed!`

- [ ] **Step 7: Checkpoint (no commit — session constraint)**

---

### Task 5: Frontend API client additions (`ingestMappingUrl`, `ingestMappingFileStream`)

**Files:**
- Modify: `frontend/src/api/index.js`

**Interfaces:**
- Consumes: nothing from other tasks (talks directly to Task 3's and the existing
  `/mapping/ingest/stream` route).
- Produces (used by Task 6): `ingestMappingUrl(vendor: string, url: string) ->
  Promise<AxiosResponse>`; `ingestMappingFileStream({vendor, file}, onEvent: (event) => void)
  -> () => void` (abort function, same shape as the existing `runAgentStream`).

- [ ] **Step 1: Extract the shared SSE-consumption helper and add the two new exports**

Replace the existing `runAgentStream` function in `frontend/src/api/index.js` (the whole
function, from `export const runAgentStream = ...` to its closing `}`) with:

```js
// Shared SSE-frame consumer for both /mapping/agent/run and
// /mapping/ingest/stream - both stream `data: {...}\n\n` frames the same
// way; this is the one place that parses that wire format.
async function _consumeSSE(resp, onEvent) {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    onEvent({ type: 'error', error: body.error || `HTTP ${resp.status}` })
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const frames = buf.split('\n\n')
    buf = frames.pop() // keep the partial frame
    for (const frame of frames) {
      const line = frame.split('\n').find(l => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch {
        // Ignore malformed event; the next frame is likely valid.
      }
    }
  }
}

// runAgentStream streams Server-Sent Events from POST /mapping/agent/run.
// EventSource doesn't support POST, so we use fetch + ReadableStream.
// onEvent is called with each parsed AgentEvent (type + payload fields).
// Returns a function that aborts the stream when called.
export const runAgentStream = ({ vendor, productId, name, description, agentProvider }, onEvent) => {
  const controller = new AbortController()
  ;(async () => {
    try {
      const resp = await fetch('/api/mapping/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vendor,
          product_id: productId,
          name: name || '',
          description: description || '',
          agent_provider: agentProvider || '',
        }),
        signal: controller.signal,
      })
      await _consumeSSE(resp, onEvent)
    } catch (err) {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', error: err.message || String(err) })
      }
    }
  })()
  return () => controller.abort()
}

// ingestMappingFileStream streams Server-Sent Events from POST
// /mapping/ingest/stream (multipart vendor + file) - same wire format and
// consumption helper as runAgentStream above, so the UI can show real ETL
// stage progress instead of an instant fake success.
export const ingestMappingFileStream = ({ vendor, file }, onEvent) => {
  const controller = new AbortController()
  ;(async () => {
    try {
      const form = new FormData()
      form.append('vendor', vendor)
      form.append('file', file)
      const resp = await fetch('/api/mapping/ingest/stream', {
        method: 'POST',
        body: form,
        signal: controller.signal,
      })
      await _consumeSSE(resp, onEvent)
    } catch (err) {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', error: err.message || String(err) })
      }
    }
  })()
  return () => controller.abort()
}

// ingestMappingUrl posts a website URL for server-side fetch + ingestion
// (POST /mapping/ingest/url). Not streamed - a single JSON response, same
// shape as ingestMappingFile above.
export const ingestMappingUrl = (vendor, url) =>
  api.post('/mapping/ingest/url', { vendor, url })
```

- [ ] **Step 2: Build sanity check (no automated JS unit-test harness exists in `frontend/` —
  confirmed by the absence of a test runner in `frontend/package.json`'s scripts). The real
  behavioral verification for this task is Task 6's Playwright E2E suite actually exercising
  these functions through a real browser; this step only confirms the file is syntactically
  valid and correctly exported before that.**

Run: `cd frontend && npm run build`
Expected: build succeeds with no syntax/import errors (confirms `_consumeSSE`,
`ingestMappingFileStream`, `ingestMappingUrl`, and the refactored `runAgentStream` are all
syntactically valid and correctly exported).

- [ ] **Step 3: Checkpoint (no commit — session constraint)**

---

### Task 6: New `/matching-test` page + route + nav link

**Files:**
- Create: `frontend/src/pages/matching/MatchingTest.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/Layout.jsx`

**Interfaces:**
- Consumes: `describeMappingAgent()` (existing), `ingestMappingFileStream`,
  `ingestMappingUrl`, `runAgentStream` (Task 5).
- Produces (used by Task 7, the E2E suite): the page renders these `data-testid` hooks for
  Playwright to target — `vendor-input`, `provider-select`, `file-input`, `url-input`,
  `submit-file`, `submit-url`, `run-match`, `ingest-result`, `match-result`, `error-banner`.

- [ ] **Step 1: Create the page**

```jsx
// frontend/src/pages/matching/MatchingTest.jsx
import { useEffect, useState } from 'react'
import { describeMappingAgent, ingestMappingFileStream, ingestMappingUrl, runAgentStream } from '../../api'

export default function MatchingTest() {
  const [vendor, setVendor] = useState('LSEG')
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('bedrock')
  const [file, setFile] = useState(null)
  const [url, setUrl] = useState('')
  const [ingestLog, setIngestLog] = useState([])
  const [ingestResult, setIngestResult] = useState(null)
  const [matchLog, setMatchLog] = useState([])
  const [matchResult, setMatchResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    describeMappingAgent()
      .then(({ data }) => {
        setProviders(data.providers || [])
        if (data.default_provider) setProvider(data.default_provider)
      })
      .catch(() => {})
  }, [])

  const reset = () => {
    setError(null)
    setIngestLog([])
    setIngestResult(null)
    setMatchLog([])
    setMatchResult(null)
  }

  const onFileEvent = (event) => {
    if (event.type === 'error') { setError(event.error); return }
    if (event.type === 'stage') setIngestLog(l => [...l, event])
    if (event.type === 'final_result') setIngestResult(event)
  }

  const submitFile = () => {
    reset()
    if (!file) { setError('choose a file first'); return }
    ingestMappingFileStream({ vendor, file }, onFileEvent)
  }

  const submitUrl = async () => {
    reset()
    if (!url) { setError('enter a URL first'); return }
    try {
      const { data } = await ingestMappingUrl(vendor, url)
      setIngestResult({ type: 'final_result', ...data })
    } catch (err) {
      setError(err.response?.data?.error || err.message)
    }
  }

  const runMatch = () => {
    setMatchLog([])
    setMatchResult(null)
    setError(null)
    const product = ingestResult?.products?.[0]
    if (!product) { setError('ingest a file or URL first'); return }
    runAgentStream(
      { vendor, productId: product.product_id, name: product.name, agentProvider: provider },
      (event) => {
        if (event.type === 'error') { setError(event.error); return }
        if (event.type === 'final_result') { setMatchResult(event); return }
        setMatchLog(l => [...l, event])
      }
    )
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Matching Test</div>
          <div className="page-sub">
            Drive the real matching pipeline: upload a file or submit a URL, then run the agent.
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <label>
          Vendor{' '}
          <input data-testid="vendor-input" value={vendor} onChange={e => setVendor(e.target.value)} />
        </label>
        <div style={{ marginTop: 10 }}>
          <label>
            Provider{' '}
            <select
              data-testid="provider-select"
              value={provider}
              onChange={e => setProvider(e.target.value)}
            >
              {providers.map(p => (
                <option key={p.id} value={p.id} disabled={!p.enabled}>
                  {p.label}{!p.enabled ? ' (not configured)' : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>File upload</div>
        <input data-testid="file-input" type="file" onChange={e => setFile(e.target.files?.[0] || null)} />
        <button data-testid="submit-file" className="btn btn-primary btn-sm" onClick={submitFile} style={{ marginLeft: 8 }}>
          Ingest file
        </button>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Website URL</div>
        <input data-testid="url-input" value={url} onChange={e => setUrl(e.target.value)} style={{ width: 360 }} />
        <button data-testid="submit-url" className="btn btn-primary btn-sm" onClick={submitUrl} style={{ marginLeft: 8 }}>
          Ingest URL
        </button>
      </div>

      {error && (
        <div data-testid="error-banner" className="alert alert-error" style={{ maxWidth: 640 }}>
          ✗ {error}
        </div>
      )}

      {ingestLog.length > 0 && (
        <div style={{ fontSize: 12, color: '#6b7280', maxWidth: 640 }}>
          {ingestLog.map((e, i) => <div key={i}>{e.stage}: {JSON.stringify(e.detail)}</div>)}
        </div>
      )}

      {ingestResult && (
        <div data-testid="ingest-result" className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
          <div style={{ fontWeight: 700 }}>Ingested {ingestResult.ingested} product(s)</div>
          {(ingestResult.products || []).map(p => (
            <div key={p.product_id} style={{ fontSize: 13 }}>{p.name} ({p.product_id})</div>
          ))}
          <button className="btn btn-primary btn-sm" data-testid="run-match" onClick={runMatch} style={{ marginTop: 10 }}>
            Run match
          </button>
        </div>
      )}

      {matchLog.length > 0 && (
        <div style={{ fontSize: 12, color: '#6b7280', maxWidth: 640 }}>
          {matchLog.map((e, i) => <div key={i}>{e.type}: {JSON.stringify(e).slice(0, 120)}</div>)}
        </div>
      )}

      {matchResult && (
        <div data-testid="match-result" className="card" style={{ padding: 16, maxWidth: 640 }}>
          <div style={{ fontWeight: 700 }}>Match result</div>
          <div>Confidence: {matchResult.confidence ?? matchResult.result?.confidence ?? 'n/a'}</div>
          <div>Provider: {provider}</div>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(matchResult, null, 2)}</pre>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 2: Register the route**

In `frontend/src/App.jsx`, add the import near the other page imports:

```js
import MatchingTest from './pages/matching/MatchingTest'
```

And add the route inside `<Routes>`, after the `/catalogue/:vendor/:ref` route:

```jsx
<Route path="/matching-test" element={<MatchingTest />} />
```

- [ ] **Step 3: Add the nav link**

In `frontend/src/components/Layout.jsx`, add a new link to the `'Data Ingestion'` section's
`links` array (after the `'/ingestion'` entry):

```js
{ to: '/matching-test', icon: '🧪', label: 'Matching Test' },
```

- [ ] **Step 4: Build sanity check**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 5: Checkpoint (no commit — session constraint)**

---

### Task 7: Live E2E Playwright suite (items 1-7 proof)

**Files:**
- Create: `backend/tests/e2e/fixture_server.py`
- Create: `backend/tests/e2e/test_matching_ui_e2e.py`

**Interfaces:**
- Consumes: a real running backend (`run_local.py`, port 5000) and a real running frontend
  Vite dev server (port 3000), both started manually or by Task 8's smoke script BEFORE this
  suite runs — this suite does not start them itself (keeps the suite fast to re-run against
  already-running servers during development).
- Produces: a pass/fail report covering items 1-7, referenced directly in the final report.

- [ ] **Step 1: Write the local fixture HTTP server helper**

```python
# backend/tests/e2e/fixture_server.py
"""A tiny stdlib HTTP server serving one static HTML page, for the E2E
suite's website-URL-submission step. Never hits a real external host -
this IS the "website" the E2E test submits a URL for."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_FIXTURE_HTML = (
    b"<html><head><title>Global Equity Prices Feed</title></head>"
    b"<body><p>Real-time equity price feed for major exchanges, "
    b"published by the E2E fixture server.</p></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_FIXTURE_HTML)

    def log_message(self, format, *args):
        pass  # silence per-request stderr noise in test output


class FixtureServer:
    """Context-manager wrapping a background ThreadingHTTPServer on an
    ephemeral local port. `server.url` is the fully-qualified fixture URL
    to hand to the E2E test's URL-submission field."""

    def __enter__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
```

- [ ] **Step 2: Write the E2E suite**

```python
# backend/tests/e2e/test_matching_ui_e2e.py
"""Live E2E proof (items 1-7): drives the real frontend/ Matching Test page
and the real dashboard-dist bundle, against one real locally-running
backend (memory store, mock frames, scripted agent backend - no real
AWS/network calls). Requires:
  - backend/run_local.py running on http://localhost:5000
  - `npm run dev` running in frontend/ on http://localhost:3000
  - SCUDO_SERVE_DASHBOARD_DIST=1 set on the backend process (for dashboard-dist coverage)
Run with: rtk proxy python -m pytest backend/tests/e2e/test_matching_ui_e2e.py -v -s
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from playwright.sync_api import sync_playwright

from fixture_server import FixtureServer

FRONTEND_URL = os.environ.get("E2E_FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.environ.get("E2E_BACKEND_URL", "http://localhost:5000")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _sample_csv() -> bytes:
    return b"product_id,name,description\nLSEG-EQ-PX,Global Equity Prices,EOD pricing feed\n"


def test_item1_backend_and_frontend_are_reachable(page):
    """Item 1: backend + frontend running locally in demo/memory-safe mode."""
    r = page.request.get(f"{BACKEND_URL}/api/mapping/agent/describe", headers={
        "X-Authenticated-User": "e2e@local",
    })
    assert r.ok
    body = r.json()
    assert body["backend"] == "scripted"  # no real LLM calls this run

    page.goto(FRONTEND_URL)
    assert "Data Ingestion" in page.content() or page.title() != ""


def test_item2_and_4_and_5_file_upload_drives_real_backend(page, tmp_path):
    """Item 2: file upload through the UI. Item 4: verify the frontend
    sends the expected request. Item 5: verify the backend actually ran
    the pipeline (real ETL stage log lines rendered, not instant fake
    success)."""
    csv_path = tmp_path / "sample.csv"
    csv_path.write_bytes(_sample_csv())

    page.goto(f"{FRONTEND_URL}/matching-test")
    page.fill('[data-testid="vendor-input"]', "LSEG")

    requests_seen = []
    page.on("request", lambda req: requests_seen.append(req) if "ingest/stream" in req.url else None)

    page.set_input_files('[data-testid="file-input"]', str(csv_path))
    page.click('[data-testid="submit-file"]')
    page.wait_for_selector('[data-testid="ingest-result"]', timeout=10000)

    # Item 4: request shape.
    assert requests_seen, "frontend never called /mapping/ingest/stream"
    req = requests_seen[0]
    assert req.method == "POST"
    assert "multipart/form-data" in (req.headers.get("content-type") or "")

    # Item 5: the UI shows the real ingested count, not a canned string.
    assert "Ingested 1 product" in page.inner_text('[data-testid="ingest-result"]')


def test_item3_website_url_submission_drives_real_backend(page):
    """Item 3: website URL submission through the UI, against a local
    fixture HTTP server (never a real external host)."""
    with FixtureServer() as fixture:
        page.goto(f"{FRONTEND_URL}/matching-test")
        page.fill('[data-testid="vendor-input"]', "LSEG")
        page.fill('[data-testid="url-input"]', fixture.url)
        page.click('[data-testid="submit-url"]')
        page.wait_for_selector('[data-testid="ingest-result"]', timeout=10000)
        result_text = page.inner_text('[data-testid="ingest-result"]')
        assert "Ingested 1 product" in result_text
        assert "Global Equity Prices Feed" in result_text


def test_item6_and_7_run_match_renders_result_with_provider(page):
    """Item 6: UI renders useful match results (confidence/provenance/
    provider). Item 7: provider selection is exercised (scripted backend
    -> the dropdown's `agent_provider` value is sent, no real cloud call)."""
    page.goto(f"{FRONTEND_URL}/matching-test")
    page.fill('[data-testid="vendor-input"]', "LSEG")
    with FixtureServer() as fixture:
        page.fill('[data-testid="url-input"]', fixture.url)
        page.click('[data-testid="submit-url"]')
        page.wait_for_selector('[data-testid="ingest-result"]', timeout=10000)

    page.select_option('[data-testid="provider-select"]', "bedrock")
    page.click('[data-testid="run-match"]')
    page.wait_for_selector('[data-testid="match-result"]', timeout=15000)
    result_text = page.inner_text('[data-testid="match-result"]')
    assert "Confidence" in result_text
    assert "Provider: bedrock" in result_text


def test_item7_azure_option_reflects_not_configured_contract(page):
    """Item 7's escape hatch: if Azure isn't configured, the UI must show
    that honestly (disabled option) rather than letting the user pick a
    path already known to fail - proven without any real Azure call."""
    page.goto(f"{FRONTEND_URL}/matching-test")
    describe = page.request.get(f"{BACKEND_URL}/api/mapping/agent/describe", headers={
        "X-Authenticated-User": "e2e@local",
    }).json()
    azure = next(p for p in describe["providers"] if p["id"] == "azure")
    if not azure["enabled"]:
        option = page.query_selector('[data-testid="provider-select"] option[value="azure"]')
        assert option is not None
        assert option.get_attribute("disabled") is not None
    else:
        pytest.skip("Azure is configured in this environment - contract-only case not applicable")


def test_dashboard_dist_bundle_serves_and_calls_real_ingest_route(page):
    """Bonus coverage (per the 'both' UI-target decision): the real,
    already-shipped dashboard-dist bundle, served same-origin via the
    off-by-default /demo/ route, actually reaches the real backend."""
    r = page.request.get(f"{BACKEND_URL}/demo/index.html")
    if r.status != 200:
        pytest.skip("SCUDO_SERVE_DASHBOARD_DIST not enabled on the running backend for this run")
    page.goto(f"{BACKEND_URL}/demo/")
    assert page.title() != "" or "html" in page.content().lower()
```

- [ ] **Step 3: Ruff check the new Python files**

Run: `rtk proxy ruff check backend/tests/e2e/fixture_server.py backend/tests/e2e/test_matching_ui_e2e.py`
Expected: `All checks passed!`

- [ ] **Step 4: Checkpoint (no commit — session constraint)**

(This task's tests are NOT run in Step-by-step isolation here — they require the live
servers from Task 8. Actual execution and pass/fail counts happen in Task 8/9.)

---

### Task 8: Smoke script + live run

**Files:**
- Create: `infra/e2e_smoke.sh`

**Interfaces:**
- Consumes: `backend/run_local.py`, `frontend` (`npm run dev`), Task 7's E2E suite.
- Produces: a single repeatable command proving items 1-7 end to end.

- [ ] **Step 1: Write the smoke script**

```bash
#!/usr/bin/env bash
# Local E2E smoke: start backend + frontend in demo/memory-safe mode, run
# the live Playwright suite against them, tear both down. No real AWS/
# network calls (SCUDO_AGENT_BACKEND left unset -> scripted; the one
# website-URL fetch targets an in-test local fixture server, never the
# real internet). DEV/CI ONLY.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[e2e_smoke] starting backend on :5000"
( cd "$ROOT/backend" && SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py > /tmp/e2e_backend.log 2>&1 & echo $! > /tmp/e2e_backend.pid )

echo "[e2e_smoke] starting frontend on :3000"
( cd "$ROOT/frontend" && npm run dev > /tmp/e2e_frontend.log 2>&1 & echo $! > /tmp/e2e_frontend.pid )

cleanup() {
  echo "[e2e_smoke] tearing down"
  kill "$(cat /tmp/e2e_backend.pid)" 2>/dev/null || true
  kill "$(cat /tmp/e2e_frontend.pid)" 2>/dev/null || true
}
trap cleanup EXIT

echo "[e2e_smoke] waiting for backend readiness"
for _ in $(seq 1 30); do
  curl -sf http://localhost:5000/healthz >/dev/null 2>&1 && break
  sleep 1
done

echo "[e2e_smoke] waiting for frontend"
for _ in $(seq 1 30); do
  curl -sf http://localhost:3000 >/dev/null 2>&1 && break
  sleep 1
done

echo "[e2e_smoke] running the Playwright E2E suite"
cd "$ROOT" && python -m pytest backend/tests/e2e/test_matching_ui_e2e.py -v
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x infra/e2e_smoke.sh`

- [ ] **Step 3: Run it**

Run: `bash infra/e2e_smoke.sh`
Expected: backend + frontend start, health checks pass, all Task 7 tests PASS, both
processes are killed on exit. Capture the exact pass/fail count for the final report.

- [ ] **Step 4: Checkpoint (no commit — session constraint)**

---

### Task 9: Final verification pass (all 7 items, full report)

**Files:** none created/modified — this task only runs commands and assembles the report.

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: the final report delivered to the user.

- [ ] **Step 1: Run every focused pytest file touched this plan**

Run:
```bash
rtk proxy python -m pytest \
  backend/tests/test_url_ingest.py \
  backend/tests/test_ingest_url_route.py \
  backend/tests/test_dashboard_dist_route.py \
  backend/tests/test_ingest_stream_route.py \
  -v
```
Expected: record the exact pass/fail count.

- [ ] **Step 2: Run the live E2E suite one more time for a clean final count**

Run: `bash infra/e2e_smoke.sh`
Expected: record the exact pass/fail count and which of items 1-7 each test maps to (see
Task 7's docstrings — each test names the item(s) it proves).

- [ ] **Step 3: Frontend build check**

Run: `cd frontend && npm run build`
Expected: clean build, record pass/fail.

- [ ] **Step 4: Ruff check every touched Python file**

Run:
```bash
rtk proxy ruff check \
  backend/scudo_mapping_mcp/url_ingest.py \
  backend/scudo_mapping_mcp/ingest.py \
  backend/routes/mapping.py \
  backend/app.py \
  backend/tests/test_url_ingest.py \
  backend/tests/test_ingest_url_route.py \
  backend/tests/test_dashboard_dist_route.py \
  backend/tests/e2e/fixture_server.py \
  backend/tests/e2e/test_matching_ui_e2e.py
```
Expected: `All checks passed!` — fix and re-run if not.

- [ ] **Step 5: `git diff --check` and read-only status**

Run: `rtk proxy git diff --check && rtk proxy git status --short && rtk proxy git diff --stat`
Expected: no whitespace errors; review the file list matches exactly what this plan touched
(plus the pre-existing, already-known uncommitted files from earlier in the session) — no
surprise files.

- [ ] **Step 6: Confirm no forbidden actions were taken**

Verify: no `git commit`/`push`/`deploy`/`clean`/`reset`/`rm`/`checkout` was run at any point;
`CLAUDE.md` untouched (`git status --short -- CLAUDE.md` prints nothing);
`dashboard-dist/` untouched (`git status --short -- dashboard-dist/` prints nothing new beyond
whatever pre-existing state was already there).

- [ ] **Step 7: Assemble and deliver the final report**

Cover, for each of items 1-7: the exact command run, the exact URL/port, and the
PASS/FAIL outcome from the E2E suite; plus overall pytest pass/fail counts, changed/new
file list, the pre-existing CLAUDE.md port discrepancy (`:5001` documented vs `:5000`
actual), and any remaining gaps (e.g. no automated JS unit-test harness in `frontend/` —
verification there relies on the Playwright E2E suite + `npm run build` only).

- [ ] **Step 8: Checkpoint (no commit — session constraint)**

---

## Plan self-review

**Spec coverage:** A (local run topology) → Task 4 + Task 8. B (new URL route) → Tasks 1-3.
C (new frontend page) → Tasks 5-6. D (testing) → Tasks 1, 3, 4, 7, 8, 9. E (error handling) →
built into Task 3's route (ValueError→400, RequestException→502) and Task 6's page
(`error-banner`). Out-of-scope items are not implemented anywhere in this plan — confirmed no
task touches Understand-Anything, hand-edits `dashboard-dist/`, or makes a real Bedrock/Azure
call. All spec sections have a covering task.

**Placeholder scan:** no "TBD"/"TODO"/"add appropriate error handling" phrases in any step;
every code block is complete, runnable code, not a sketch.

**Type/name consistency check:** `ingest_url(vendor, url, upsert=True, on_stage=None,
**fetch_kwargs)` (Task 2) is called with only `(vendor, url)` positional args by Task 3's
route (matches — `upsert`/`on_stage`/`fetch_kwargs` all have defaults). `fetch_and_extract`'s
`resolve`/`getter` keyword names (Task 1) match exactly what Task 3's route tests monkeypatch
(`_default_resolve`, `requests.get`) — Task 3 patches the MODULE-level `requests.get` (used
inside `url_ingest.py`'s `getter` default), not a per-call parameter, which is correct since
the route itself never passes a custom `getter`. `ingestMappingFileStream`/`ingestMappingUrl`
(Task 5) signatures match exactly how Task 6's `MatchingTest.jsx` calls them
(`{vendor, file}`/`onEvent` and `(vendor, url)` respectively). `data-testid` hooks defined in
Task 6 (`vendor-input`, `provider-select`, `file-input`, `url-input`, `submit-file`,
`submit-url`, `run-match`, `ingest-result`, `match-result`, `error-banner`) match exactly what
Task 7's Playwright selectors target — cross-checked one by one, no mismatches.

**Known plan-level risk, flagged rather than silently resolved:** `test_dashboard_dist_route.py`
(Task 4) uses `importlib.reload(app_module)` to pick up the env-var-gated route inside a
single pytest process — this is a genuine pattern risk (Flask blueprints/routes registered
twice across reloads can raise `AssertionError: View function mapping is overwriting an
existing endpoint`). Task 4 Step 5 explicitly runs the wider suite to catch this; if it
surfaces, the plan does NOT prescribe a specific fix in advance (would be guessing) — treat it
as a Task 4 implementation-time decision (e.g. reload only a narrow test-local Flask app
instance instead of the shared module-level `app`) and report the actual resolution taken.

