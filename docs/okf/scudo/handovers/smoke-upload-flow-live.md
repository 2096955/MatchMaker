---
type: Handover
title: Smoke — Upload Flow Live
description: Point-in-time verification of the live upload flow.
tags:
- handover
- smoke
staleness: historical
timestamp: '2026-06-28T06:28:37Z'
---

# Live smoke test — Upload & Test pipeline (deployer-run)

**Purpose:** prove the *headline feature* actually works against the LIVE
deployment — not just that the UI renders. The upload flow streams Server-Sent
Events (SSE) through CloudFront → ALB → Flask; this is exactly where things break
in prod (buffering, idle timeout, auth) in ways local dev never shows.

**Who runs this:** an operator/agent with access to the deployed URL + (for the
curl steps) ability to send an auth header. Account 954976331678, us-east-1.

**Live base:** `https://dp4ji14se0pct.cloudfront.net/cogJPMdemo/` (or `/demo/`).
API is **same-origin** under `/api/*` (CloudFront routes it to the Flask ALB),
i.e. `https://dp4ji14se0pct.cloudfront.net/api/mapping/...`.

**Contract (verified from source @ 46d2b34):**
- Endpoints: `POST /api/mapping/ingest/stream` (multipart) and
  `POST /api/mapping/agent/run` (JSON). Both stream `text/event-stream`.
- `/api/*` is **auth-gated** — a `before_request` hook 401s without a resolvable
  principal. Header: `X-Authenticated-User` (e.g. `2096955@cognizant.com`).
- Valid vendors: `LSEG`, `S&P Global`, `Bloomberg`, `ICE`, `FactSet`.
- CSV columns accepted: `product_id,name,description` (aliases exist).
- ETL SSE events: `{type:stage, stage:received|parse|validate|sink, nodeIds, detail}`
  then `{type:final_result, ingested, products}` then `{type:done}`.

---

## Part A — API-level SSE smoke (curl, ~2 min)

Run from CloudShell (or anywhere that can reach the URL). Tests the live SSE
path end-to-end through CloudFront/ALB.

```bash
BASE=https://dp4ji14se0pct.cloudfront.net
AUTH='X-Authenticated-User: 2096955@cognizant.com'

# 0. Sanity: health (unauthenticated) + auth gate behaves
curl -s "$BASE/healthz"                                   # {"status":"ok"}
curl -s -o /dev/null -w "no-auth vendors: %{http_code}\n" "$BASE/api/mapping/vendors"
#   EXPECT 401 (auth gate works). If 200 with no header → gate is OPEN, flag it.
curl -s -H "$AUTH" "$BASE/api/mapping/vendors"            # vendors list (LSEG,…)

# 1. Make a tiny vendor file
cat > /tmp/smoke.csv <<'CSV'
product_id,name,description
LSEG-EQ-PX,Global Equity Prices,End-of-day and intraday equity prices
CSV

# 2. THE KEY TEST — stream ETL events. --no-buffer so we see frames AS THEY
#    ARRIVE (proves no CloudFront/ALB buffering). -N disables curl buffering.
echo "--- ingest/stream ---"
curl -sN --no-buffer -H "$AUTH" \
  -F vendor=LSEG -F file=@/tmp/smoke.csv \
  "$BASE/api/mapping/ingest/stream"
#   EXPECT, in order, each on its own `data:` line:
#     data: {"type":"stage","stage":"received",...}
#     data: {"type":"stage","stage":"parse","detail":{"rows":1}...}
#     data: {"type":"stage","stage":"validate",...}
#     data: {"type":"stage","stage":"sink",...}
#     data: {"type":"final_result","ingested":1,"products":[{"product_id":"LSEG-EQ-PX",...}]}
#     data: {"type":"done"}
#   Note the product_id from final_result for step 3.

# 3. Run the matcher stream for that product
echo "--- agent/run ---"
curl -sN --no-buffer -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-EQ-PX"}' \
  "$BASE/api/mapping/agent/run"
#   EXPECT data: lines of type tool_call / tool_result (find_similar_products,
#     get_taxonomy_node, map_vendor_product) then final_result then done.
```

### Pass / fail for Part A
- **PASS:** both streams emit the event sequence above, frames arrive
  incrementally (not all at once at the end), ending in `done`.
- **FAIL — all frames arrive together at the end:** CloudFront/ALB is BUFFERING
  the SSE. Check `Compress:false` on the `/api/*` behavior and ALB response
  buffering. (Backend already sets `X-Accel-Buffering:no`.)
- **FAIL — connection drops near ~60s on agent/run:** ALB idle timeout. Raise it
  or add an SSE heartbeat (known TODO for live Bedrock runs).
- **FAIL — 401 even with the header:** the gateway is STRIPPING
  `X-Authenticated-User` (good for security, but then it must INJECT a real
  identity — confirm how auth is meant to flow in prod). If 200 *without* the
  header at step 0, the gate is open — security flag.

---

## Part B — Browser UI smoke (~3 min)

1. Open `https://dp4ji14se0pct.cloudfront.net/cogJPMdemo/`.
2. Confirm tab = "SCUDO Matching Comprehension" + blue pipeline-glyph favicon.
3. Open DevTools → Console (watch for errors) and Network (filter: `ingest`).
4. In the right **Upload & Test** panel: vendor = `LSEG`, choose `/tmp/smoke.csv`
   (or any CSV with `product_id,name,description`), click **Run through pipeline**.
5. **Watch the graph:** the ETL nodes (EventBridge → SQS → Lambda →
   Validate → S3/DynamoDB) should light up in sequence, then the matcher nodes
   (Parse → Semantic → Rank → Gate). The run-event log in the panel fills with
   `ETL · received/parse/validate/sink` then matcher tool lines, ending
   `Result: …`.
6. In Network, the `ingest/stream` request should show `text/event-stream` and
   (in some browsers) stream progressively.

### Pass / fail for Part B
- **PASS:** nodes animate, run log fills, ends with a result, **no console
  errors**.
- **FAIL — "mapping store unavailable" / 503 in the log:** the live store
  (FalkorDB/Aurora) isn't reachable from the app — check backend env + VPC.
- **FAIL — CORS error in console:** the SPA is NOT same-origin with the API
  (it should be, via CloudFront `/api/*`). Don't "fix" with `VITE_API_BASE` to a
  cross-origin URL — fix the CloudFront routing.
- **FAIL — nodes never light / log empty but no error:** SSE not reaching the
  browser (buffering, see Part A).

---

## Report back
Paste the Part A stream output + Part B pass/fail + any console errors. That
plus the existing render/health checks is what makes the demo **stakeholder-ready**
on the feature axis. (Separately, the `X-Authenticated-User` gateway
strip-and-inject is still the security gate before *external* exposure — see
`infra/DEPLOY_RUNBOOK_scudo-poc.md` §5.)
