# Streamlit console — running SCUDO with no Node at all

For desktops where Citrix group policy blocks
`node_modules/@esbuild/win32-x64/esbuild.exe`, so `npm run dev` and
`npm run build` fail with `spawn UNKNOWN` and Vite cannot start.

Streamlit is pure Python. No Node, no bundler, no esbuild, no build step, and
no Flask process either.

---

## Run it

```bash
pip install -r backend/requirements.txt
pip install -r backend/requirements-local.txt      # adds streamlit
streamlit run streamlit_app.py
```

Opens on <http://localhost:8501>. If that port is taken:

```bash
streamlit run streamlit_app.py --server.port 8502
```

Nothing else needs to be running. No database, no container, no AWS
credentials, no API server.

---

## What it does

1. **Upload** a vendor CSV/JSON. Real pipeline stages are shown with real
   counts: `received → parse → validate → sink`.
2. **Run match** on an ingested product.
3. **Agent reasoning** renders as a readable trace — `💭 thinking`,
   `🔧 calls`, `↳ returns` — followed by confidence, band, and the mapped CDAO
   node.

Verified end to end on this machine: 14 agent events, confidence **0.913**,
band **pass**, mapped to **Equity Prices**.

---

## How it is wired

It calls the SCUDO package **directly** — not over HTTP:

| Step | Function |
|---|---|
| ingest | `scudo_mapping_mcp.ingest.ingest_bytes` |
| frame lookup | `scudo_mapping_mcp.frames._read_vendor_frame` |
| agent run | `scudo_mapping_mcp.agent.get_agent(provider).run(ref)` |

`agent.run()` is a generator yielding exactly the events the SSE endpoint
wraps, so there is no port to pick, no proxy to configure and no SSE parsing.

**This is not a second implementation of the matching logic.** It is a
different surface over the same code the API serves — same cost ladder, same
confidence gates, same agent. A behaviour difference between this and the React
console would be a bug in one of the two surfaces, not a design choice.

### One shape detail worth knowing

`agent.run()` yields `AgentEvent` objects with `.type` and `.payload` — **not
dicts**. The Flask route flattens them via `to_json()` so HTTP clients see
`{"type": ..., **payload}`. `streamlit_app.py:_as_dict()` flattens the same
way, deliberately, so both surfaces describe identical events rather than
drifting apart. I hit this while building: assuming dicts fails with
`AttributeError: 'AgentEvent' object has no attribute 'get'`.

---

## Environment

Set inside `streamlit_app.py` **before** the package is imported — `config.py`
reads these at import time, so setting them afterwards is too late. This is the
same ordering contract `start_local.py` exists to enforce for Flask.

| Variable | Value |
|---|---|
| `STORE_BACKEND` | `local_file` — full ladder, decisions survive restart |
| `FRAME_SOURCE` | `mock` — bundled sample data, not S3 |
| `CONSOLE_DB_BACKEND` | `sqlite` — no PostgreSQL needed |
| `SCUDO_AUTH_ALLOW_DEV` | `1` |

All use `setdefault`, so anything you export first wins.

### Using Bedrock

Pick **bedrock** in the sidebar and export your credentials plus
`SCUDO_BEDROCK_MODEL_ID` / `AWS_REGION`. Note what this changes: the **narrating agent** only.

It does not change how the score is computed. `streamlit_app.py` sets
`SCUDO_DENSE_BACKEND=opus` at import, so an LLM supplies the candidate
similarity that becomes the published confidence — on **either** agent,
including `scripted`. It also sets `SCUDO_SPECIALIST_BACKEND=local`, so
borderline cases get LLM adjudication on either agent too. For a deterministic
score, export `SCUDO_DENSE_BACKEND=jaro_winkler` before launching and leave
`SCUDO_USE_OPUS_DENSE` unset.

---

## Limits

- **Single user, single process.** Streamlit re-runs the whole script on every
  interaction; taxonomy seeding is cached with `@st.cache_resource` so it runs
  once. Fine for one person demonstrating; not a multi-user console.
- **Not the product UI.** The React console (`frontend/`) remains that. This is
  the fallback for environments where Node cannot execute, and a fast way to
  exercise the pipeline without a browser toolchain.
- **Providers / Datasets / Admin are not here.** Those are console CRUD pages;
  this app covers the matching path only. For those, use the React console —
  either via Vite, or pre-built and served by Flask at `/app/` (see
  [`CITRIX_NO_NODE.md`](CITRIX_NO_NODE.md)).
- **Verified by running it, not by using it.** The server starts clean (HTTP
  200, zero errors in the log) and the full pipeline was exercised through the
  exact call path the app uses. I have not clicked through the rendered page,
  so layout and wrapping are unverified.
