# E2E UI/backend validation of the SCUDO matching path

**Date:** 2026-07-07 · **Status:** approved (brainstormed, this session) → implementing

## Intent

Prove — or make true — that the UI can drive the real backend matching path for both a
vendor file upload and a website URL submission, covering 7 specific verification items:
(1) local backend+frontend in demo/memory-safe mode, (2) file upload through the UI,
(3) website URL submission through the UI, (4) frontend request-shape verification,
(5) backend-log/observable proof the real matching pipeline runs (not a mocked success
screen), (6) UI rendering of useful match results (confidence, provenance, provider, error
states), (7) provider/vendor (Bedrock/Azure) selection, proving the contract honestly even
where a provider isn't configured.

## Ground truth verified before designing (not assumed)

- Two frontends exist. `frontend/` (React18, Vite, editable, MatchMaker-native) has **no**
  matching-path UI at all — only ingestion-console/catalogue/admin/provider/report pages
  (verified by listing `frontend/src/pages/`). `dashboard-dist/` (vendored build of the
  Understand-Anything dashboard) **does** call the real matching routes — confirmed by
  grepping the minified bundle for `/api/mapping/ingest/stream` and `/api/mapping/agent/run`
  — but has **no** provider/vendor dropdown and **no** website-URL field anywhere in the
  bundle (grepped for `agent_provider`, `agent/describe`, `website`, url-input patterns — all
  absent). Its source lives in the Understand-Anything repo (off-limits) and the dist itself
  must never be hand-edited (CLAUDE.md convention) — so items 3 and 7 cannot live there.
- The dashboard bundle's API calls are same-origin relative (`Fwe(d) = "" + d`, confirmed by
  reading the minified `Fwe`/`p1n` helpers directly) — no baked-in host — so it is drivable
  locally provided it's served same-origin with the Flask API.
- Real backend routes already exist and are genuine, not mocked: `POST /mapping/ingest/stream`
  (multipart file upload, streams real ETL stage events over SSE — `backend/routes/mapping.py:1063`),
  `GET /mapping/agent/describe` (reports bedrock always-enabled, azure conditionally enabled
  based on 3 required env vars — `:1231`), `POST /mapping/agent/run` (SSE agent run, accepts
  `agent_provider` — `:1280`). **No route exists anywhere for website-URL submission** — this
  is a new, from-scratch route, not a wiring gap.
- `SCUDO_AGENT_BACKEND` defaults to `"scripted"` (`agent.py:62`, `mapping.py:1234`) — the
  `ScriptedMappingAgent` runs deterministically with **zero real LLM/AWS calls** unless the
  env var is explicitly set to `bedrock`/`azure`. The whole E2E proof can therefore run
  hermetically by default.
- `lxml==5.3.0` is already a backend dependency (`backend/requirements.txt:8`) — no new HTML-
  parsing dependency needed.
- `ingest_bytes(vendor, filename, data, upsert, on_stage)` (`scudo_mapping_mcp/ingest.py:186`)
  already parses a JSON array of `{product_id, name, description}` rows when `filename` ends
  in `.json` — the real, already-tested path a synthesized single-row URL "product" can ride
  on unchanged.
- `frontend/vite.config.js` already proxies `/api` → `http://localhost:5000` by default, and
  `backend/run_local.py` runs Flask on **port 5000** (`app.run(port=5000, ...)`) — this
  contradicts CLAUDE.md's stated ":5001". Not fixing CLAUDE.md (forbidden); reporting the
  discrepancy instead.

## A — Local run topology

- **Backend**: `python backend/run_local.py` → Flask on `:5000`. Pins `STORE_BACKEND=memory`,
  `FRAME_SOURCE=mock`, dev-auth allowed; leaves `SCUDO_AGENT_BACKEND` unset (→ `scripted`).
- **Frontend (new page)**: `npm run dev` in `frontend/` → Vite on `:3000`, already proxying
  `/api` → `:5000` (no vite.config.js change needed).
- **dashboard-dist**: new, **off-by-default** static route added to `backend/app.py`, gated
  behind `SCUDO_SERVE_DASHBOARD_DIST=1` (unset in every other context — zero behavior change
  to the deployed app), serving `dashboard-dist/` under `/demo/*` from the *same* Flask
  process. This achieves true same-origin for the bundle's relative `/api/...` fetches with no
  separate proxy process and no edits to the vendored dist itself (read-only, per constraint).

## B — New backend route: `POST /api/mapping/ingest/url`

New, narrow, real (not a stub) — the user's explicit choice over a contract-only stub.

- Request body: JSON `{"vendor": "...", "url": "https://..."}`.
- **SSRF guard** (new, TDD'd as its own pure-ish unit): only `http`/`https` schemes accepted;
  resolve the hostname via DNS and reject if any resolved address is loopback, private
  (RFC1918), link-local, or the cloud metadata address `169.254.169.254`. Bounded fetch timeout
  (~10s) and response-size cap (reuse the existing `SCUDO_MAX_UPLOAD_BYTES`, default 5MB, same
  ceiling as file upload).
- **Fetch + extract**: GET the URL, parse with `lxml`, extract `<title>` text and a text
  excerpt from the body — first 2000 characters of visible (script/style-stripped) text.
- **Synthesize + reuse**: build one JSON row `{"product_id": <uuid5 of the URL, namespaced
  under a fixed app UUID — deterministic, replay-safe, matches the existing mds.<vendor>:
  <uuid5> convention's derivation style>, "name": <title>, "description": <excerpt>}`, encode
  as UTF-8 JSON bytes, and call the
  **existing, unmodified** `ingest_bytes(vendor, "<slug>.json", data, upsert=True, on_stage=...)`.
  All downstream parsing/upsert/ETL-stage-emission behavior is 100% reused — the new surface
  area is strictly: URL validation, fetch, HTML→text extraction, JSON synthesis.
- Response shape mirrors `/mapping/ingest`'s existing JSON response (`ingested`, `products`).
- Errors: invalid scheme / SSRF-blocked / DNS failure / timeout / oversized response all return
  a clear 4xx with a message — never a silent empty success.

## C — New frontend page: `frontend/src/pages/matching/MatchingTest.jsx`

Route `/matching-test`, linked from `Layout.jsx` nav (small addition, follows existing
nav-link pattern).

- **Provider dropdown**: populated from `GET /api/mapping/agent/describe` on mount. Bedrock
  always shown enabled; Azure shown but disabled (with a tooltip/label) when the API reports
  `enabled: false` — this is the literal proof of item 7's contract, including the
  not-configured case, without any real cloud call.
- **File control**: standard `<input type="file">` + vendor text input → `POST
  /api/mapping/ingest/stream` (existing real route, multipart). Renders the SSE stage events
  as they arrive (received/parse/validate/sink) so the UI visibly reflects real pipeline
  progress, not an instant fake success.
- **URL field**: text input + vendor text input → `POST /api/mapping/ingest/url` (section B).
  Renders the JSON response (ingested count + product) or a clear error banner.
- **Run match**: after either ingest path succeeds, a "Run match" button calls `POST
  /api/mapping/agent/run` with `{vendor, product_id, agent_provider}` (provider from the
  dropdown) and renders the SSE stream — `tool_call`/`tool_result`/`agent_message` as
  scrolling log lines, `final_result` as a result card showing confidence, rationale/
  provenance text, and which provider actually ran; `error` renders as a distinct error state,
  never silently swallowed.

## D — Testing

- **Hermetic pytest** (`backend/tests/test_ingest_url_route.py` or similar), no real network:
  - SSRF guard: rejects non-http(s) schemes, rejects resolved-private/loopback/link-local/
    metadata addresses (DNS resolution mocked), accepts a normal public-looking hostname
    (mocked resolution to a public-looking IP).
  - Happy path: mocks the actual HTTP fetch (e.g. via `responses`/`unittest.mock`) to return
    fixed HTML, asserts the synthesized JSON row is correct and that `ingest_bytes` (or the
    mapping store) receives/produces the expected frame — proving real reuse of the existing
    pipeline, not a parallel fake path.
  - TDD throughout: RED (watch the right failure) → GREEN → refactor.
- **Live E2E** (Python `playwright`, already installed locally — confirmed via `python -c
  "import playwright"`): a new test file drives, against one real running local backend
  (`run_local.py`, scripted agent, memory store, mock frames):
  1. the new `frontend/` `/matching-test` page (file upload + URL + provider dropdown + run),
  2. the `dashboard-dist` bundle via the new `/demo/` route (its existing file-upload +
     agent-run flow).
  The URL-ingestion step in this suite targets a **small local fixture HTTP server** spun up
  in-test (e.g. Python's `http.server` on an ephemeral port serving one static HTML fixture),
  never a real external URL — per the user's explicit instruction.
- **Smoke script**: starts backend + frontend (and the dashboard-dist static route) in the
  background, runs the Playwright suite, tears both down — a repeatable one-command proof.

## E — Error handling / fail-open vs fail-loud

- URL fetch failure, SSRF block, timeout, oversized response: **fail loud** to the caller (4xx
  + message) — this is a live request path, not an offline advisory read; silently swallowing
  would defeat the entire proof that the UI can drive a real backend path.
- Azure requested but not configured: already fails closed inside
  `AzureMappingAgent._require_config` (existing code, unmodified) — the new UI must reflect
  `enabled: false` from `/agent/describe` and disable the option, rather than letting a user
  pick a path that's already known to fail.

## Out of scope

- Real Bedrock/Azure calls anywhere in automated tests (scripted backend only).
- Touching the Understand-Anything repo or hand-editing `dashboard-dist` (read-only, per
  constraint).
- Production-grade URL-fetch hardening beyond the SSRF guard described above (e.g. no
  full allowlist policy, no rate limiting, no auth-bypass protection beyond what already
  exists on `/api/*`).
- Rebuilding `dashboard-dist` from source.
- Any change to `CLAUDE.md`, commit/push/deploy/clean/reset/rm/checkout.

## Verification plan

Final report will cover, per item 1-7: exact commands run, exact URLs/ports used, Playwright
pass/fail counts, focused pytest pass/fail counts, changed/new files, and remaining gaps —
including the pre-existing CLAUDE.md port discrepancy (:5001 documented vs :5000 actual) and
any items that could not be fully proven live.
