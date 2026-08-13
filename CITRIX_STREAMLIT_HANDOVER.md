# Streamlit console — files to create on the Citrix desktop

**Reissue, 2026-08-07.** Supersedes the previous version, which had two errors
your porting agent was right to catch. Both are corrected below and the code
now adapts rather than assuming — see *Corrections*.

A pure-Python UI for SCUDO matching. **No Node, no Vite, no esbuild, no
bundler, no Flask, no external database service, no container.** It uses a
local SQLite matching database. Built because Citrix group policy blocks
`node_modules/@esbuild/win32-x64/esbuild.exe`, so Vite cannot start at all.

---

## Corrections to the previous handover

**1. `backend/requirements-local.txt` is NOT a replacement file.** It is an
existing local-run manifest; the original Streamlit work added only a small
comment block plus `streamlit>=1.36`. Preserve the rest of the file because it
belongs to a separate local-dependency policy.

**2. The app now selects a supported local store before importing package
configuration.** It prefers `scipy_sqlite`, then `local_file`, then `memory`,
and preserves an explicitly supplied `STORE_BACKEND`. The durable default uses
`backend/.local/scudo_matching.sqlite3`, separate from the console SQLite file.

The sidebar now states which store is live and what it means:

```
Store   scipy_sqlite    complete matching state persists across restarts
Store   local_file      decisions persist across restarts
Store   memory          in-memory — forgets on restart
```

`scipy_sqlite` is a single-host option. Keep the database on local disk; it is
not a shared-filesystem or multi-container deployment backend.

---

## Files

| Path | Action |
|---|---|
| `streamlit_app.py` | **NEW** — repo root |
| `.streamlit/config.toml` | **NEW** — repo root |
| `backend/requirements-local.txt` | **APPEND** the Streamlit dependency block — do not overwrite |
| `STREAMLIT_RUN.md` | **NEW** — reference doc |

The current SciPy/SQLite matching-store integration also changes narrow files
under `backend/`. The original Streamlit work did not change `frontend/`,
`infra/`, or `jpmc-port/`; that statement does not describe later backend work.

## Run it

```bash
pip install -r backend/requirements.txt
pip install -r backend/requirements-local.txt      # adds streamlit>=1.36
streamlit run streamlit_app.py
```

Opens on <http://localhost:8501>; add `--server.port 8502` if taken.

---

## Two details that will cost you an hour if missed

**1. The `os.environ.setdefault(...)` block must stay ABOVE the
`scudo_mapping_mcp` imports.** `config.py` reads those at import time, so
moving them below silently selects the wrong backend. Same ordering contract
`start_local.py` enforces for Flask.

**2. `.streamlit/config.toml` sets `fileWatcherType = "poll"`.** inotify is
unreliable on Citrix and network drives; without it the app will not
hot-reload.

---

## What it does

1. **Upload** a vendor CSV/JSON — real stages, real counts:
   `received → parse → validate → sink`.
2. **Run match** on an ingested product.
3. **Agent reasoning** as a readable trace (`thinking` / `calls` / `returns`),
   then confidence, band, and the mapped CDAO node.

It calls the SCUDO package **directly** — not over HTTP. `agent.run()` is a
generator yielding exactly the events the SSE endpoint wraps, so there is no
port, no proxy, no SSE parsing.

**Not a second implementation of the matching logic.** Same ladder, same
gates, same agent as the API — verified by diffing both surfaces (below).

---

## Bedrock

The sidebar shows a **Bedrock key** panel only when the agent is `bedrock`:

| Field | Note |
|---|---|
| **API key** | The `bedrock-api-key-...` bearer token. Masked. |
| **Model** | Claude Opus 4.8 / Sonnet 4.5 / Haiku 4.5 |

**A Bedrock API key is a single bearer token.** It carries its own region and
credentials — no access key ID, secret, session token or `AWS_REGION`. Those
belong to the separate long-term-credential setup.
[AWS docs](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html)

Keys last ~12 h, hence pasting at the podium. Nothing is written to disk or
logged.

**Press "Apply & test" before presenting.** It makes one real streaming
invoke. A key that is expired or lacks model access looks identical to a good
one until the first call.

### Measured, on a live key (2026-08-07, us-east-1)

| Agent | Time | Confidence | Band | Narration |
|---|---|---|---|---|
| scripted | <1 s | **0.851** | pass | 135 chars |
| Claude Haiku 4.5 | 8.6 s | **0.851** | pass | 955 chars |
| Claude Opus 4.8 | 14.2 s | **0.851** | pass | 967 chars |

**Identical confidence.** The model narrates; the matcher scores
deterministically. Switching models live while the number holds is a good demo
beat and the honest description of the architecture.

---

## Verification

**Live browser run with a real Bedrock key** — 10 trace lines, **zero
overflow**; the 1080-character narration wrapped cleanly into 548 px. The
previous handover flagged wrapping as unverified; it is now verified good.

**Parity with the Flask API** — 14 events, same order and counts; deep-diff of
all payloads: 0 differences; final result deep-equal across 6 products
spanning all three bands; band edges exact at 0.80 and 0.70.

**Functional testing** — 21 browser tests including malformed JSON, missing
`product_id`, empty `product_id` (honestly reported `valid=2, rejected=1`),
0-byte files, wrong extensions, stale state, narrow viewport. Zero tracebacks,
zero JS console errors.

---

## Known issues — read before demoing

**1. A failed agent still shows a valid score.** If Bedrock fails, the backend
*deliberately* still runs the deterministic matcher and returns a real result.
The UI renders a warning above the panel saying the agent did not complete and
no narration was produced, with the accent dropped to grey. **Without that
warning this is the most dangerous state in the app**, because a failed run
otherwise looks identical to a successful one.

**2. Taxonomy count can go stale.** The sidebar's node count is cached by
`@st.cache_resource` while the store is a separate `lru_cache`. If something
rebuilds the store cache the header keeps its old number while matches return
0 candidates. **If matches suddenly return 0.000, restart the app** rather than
debugging the data.

**3. Two-file ingest: the UI replaces, the backend accumulates.** After
ingesting A then B the table shows only B, but A's frames still resolve.

**4. Paste the key into the running app, not the launch environment.** A
server started before the token was set kept failing with
`AccessDeniedException` even though the key was valid. That is what the
sidebar panel is for.

**5. Providers / Datasets / Admin are not in this app.** Matching path only.
For those use the React console — via Vite, or pre-built and served by Flask
at `/app/` (see [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md)).

---

## Two bugs found by running it, now fixed

Recorded because both would have surfaced live otherwise.

**Preflight tested the wrong API.** It called `Converse` while the agent uses
`ConverseStream`; the two are separately authorised. The sidebar reported
*"Ready — Claude Opus 4.8 responded"* seconds before a run failed on auth. Now
tests the streaming path and drains the first frame, since auth can fail on
consume rather than open.

**The two agents disagree on a field's type.** `tool_result.result` is a dict
from the scripted agent and a JSON **string** from Bedrock (Strands passes tool
output through verbatim). `result.get(...)` raised `AttributeError` and crashed
the page — and only on the Bedrock path, so it survived every scripted test.
Now parsed, with the raw text shown if it is not JSON.

---

## Related

- [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) — fuller run notes and limits
- [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md) — serving the React console from
  Flask at `/app/`
- [`CITRIX_CHECK_FRONTEND.md`](CITRIX_CHECK_FRONTEND.md) — what to click in the
  React console
