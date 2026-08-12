# For the Citrix agent — please check the frontend wiring

Three things were verified end to end against a running backend, and one real
defect was found and fixed. **I have not opened this in a browser** — everything
below was proven through the Flask test client and by replaying captured events
through the render logic. The visual layout is the part I cannot vouch for, and
is the main thing worth your eyes.

---

## How to run it

```bash
pip install -r backend/requirements.txt
python start_local.py
```

Open **<http://localhost:3000>** — the UI. Not :5000, which is the backend.

If nothing loads, port 5000 is occupied (macOS AirPlay, some corporate agents):

```bash
PORT=5055 VITE_API_PROXY=http://localhost:5055 python start_local.py
```

No database, container, or AWS credentials required.

---

## 1. Can the agents receive files? — YES, already wired

`ingestMappingFile` posts multipart to `/mapping/ingest`; the streaming variant
hits `/mapping/ingest/stream`. Both routes exist and respond. A real upload
emits genuine pipeline stages, not a simulation:

```
received → parse → validate → sink → final_result → done
```

**To check:** Matching Test page → choose a file → the stage list should tick
through with real row counts.

## 2. Can they match details? — YES

Ingest then match returns 200 with a band and a real CDAO node:

```
POST /api/mapping/ingest  -> 200 {"ingested": 1}
POST /api/mapping/map     -> 200 band:"pass"
                             jpmorgan:data:cdao:concept:equity-prices
```

**To check:** after upload, press **Run match**. You should get a Match result
card with a confidence and a node label.

## 3. Can they show their reasoning? — the backend always could; the UI was throwing it away

This was the real defect.

The backend streams a full trace over SSE. A real run emits **14 events**: 4
`tool_call`, 4 `tool_result`, 3 `agent_message`, plus start/final/done. All of
them were reaching the frontend.

The frontend rendered each as `e.type: {raw JSON}` **truncated at 120
characters** — which cut the agent's own sentences off mid-word and displayed a
candidate list as unreadable JSON. The information was arriving; it was not
legible.

**Now:**

```
start     Equity Prices Real Time · scripted
thinking  Mapping LSEG product 'RSN-3'. I'll use the MCP tools to find candidate CDAO nodes…
calls     find_similar_products — Equity Prices Real Time
returns   5 candidates — top: Equity Prices (0.91)
thinking  Top candidate: 'Equity Prices' at similarity 0.91. Let me inspect its taxonomy context.
calls     get_taxonomy_node — jpmorgan:data:cdao:concept:equity-prices
returns   Equity Prices
calls     get_ontology_neighbourhood — jpmorgan:data:cdao:concept:equity-prices
returns   root_iri, nodes, edges
calls     map_vendor_product — Equity Prices Real Time
returns   8 candidates — top: Equity Prices (0.91)
thinking  Recommend AUTO_MAPPED to 'Equity Prices' — confidence…
```

That output is from real captured events, not mocked.

**To check:** press Run match and look for an **"Agent reasoning"** card
(`data-testid="agent-reasoning"`) above the result. You should be able to follow
the agent finding candidates, inspecting the winner, checking its
neighbourhood, then recommending — with no JSON visible.

---

## Also fixed: refusals were losing their useful half

The backend now returns typed refusals as `{error, detail}`. Every frontend
call site read only `.error` — 16 of them — so a user saw:

```
✗ frame_not_found
```

…while the sentence saying what to do was discarded:

> *"no ingested frame for LSEG/X1; **ingest it first**, or set
> `SCUDO_MV_ALLOW_INLINE_FRAME` to score caller-supplied text"*

Fixed centrally with one axios interceptor rather than 16 edits. Plain errors
(`"provider_name is required"`) pass through unchanged — both paths tested.

**To check:** press **Run match without uploading anything first.** The banner
should tell you to ingest a file, not just say `frame_not_found`.

---

## Files changed

| File | Change |
|---|---|
| `frontend/src/api/index.js` | +27 — response interceptor folding `detail` into `error` |
| `frontend/src/pages/matching/MatchingTest.jsx` | +124 — `AgentStep` renderer; reasoning card |

`frontend/src/App.jsx` also shows as modified — that was **already dirty before
this work** and is not mine. Leave it alone.

Frontend builds clean (`npm run build`, 303 kB).

---

## What I could not verify, and what to watch for

1. **Visual layout.** Never opened in a browser. The reasoning card uses inline
   styles consistent with the existing cards, but spacing and long-line wrapping
   are unchecked. `agent_message` content can be a full sentence — confirm it
   wraps rather than overflowing.

2. **Only the `scripted` provider was exercised.** It is the safe offline
   default. With Bedrock wired, event *content* changes but the event *types*
   are the same, so the renderer should hold — worth confirming once, since a
   real model may emit longer `agent_message` bodies.

3. **The dashboard is a different codebase.** The matching dashboard lives in
   the `Understand-Anything` repo and is vendored here as `dashboard-dist/`.
   Everything above is `frontend/` (the React 18 console) only. If the dashboard
   surfaces mapping errors, it needs the same interceptor treatment — I have not
   touched that repo.

4. **Unknown event types still render** (as JSON, deliberately) rather than
   being silently dropped. If you see a raw-JSON line in the reasoning card,
   that is the backend emitting an event type the renderer does not know about
   — report it rather than filtering it out. Silent dropping is what hid this
   defect in the first place.

---

## Related documents

- [`CITRIX_FOLLOWUP.md`](CITRIX_FOLLOWUP.md) — the earlier apply: health-endpoint
  names (`/healthz`, not `/health`), the `falkordb` spelling, switching on the
  SQLite stand-in so the DB pages work without Aurora.
- [`JPMC_LOCAL_RUN_HANDOVER.md`](JPMC_LOCAL_RUN_HANDOVER.md) — what runs locally
  with no infrastructure, and why.
