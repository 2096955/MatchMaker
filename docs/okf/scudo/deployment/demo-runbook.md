---
type: Runbook
title: Demo Runbook
description: How to run and verify the matching demo locally and in PoC.
tags:
- runbook
- demo
staleness: historical
timestamp: '2026-08-17T09:02:03Z'
---

# SCUDO Demo Runbook

| Field | Value |
|---|---|
| Status | Pre-demo, post-ARB-fix |
| Smoke gates | 111/111 passing |
| Last updated | 2026-06-10 |

## Recommended demo configuration

### Environment variables to SET for the demo

| Variable | Value | Why |
|---|---|---|
| `SCUDO_USE_OPUS_DENSE` | `true` | Enables Opus-as-dense-scorer multi-path retrieval (the new feature being demoed) |
| `SCUDO_DENSE_FALLBACK` | `1` | **CRITICAL** — closes ARB finding C1. On Opus throttle / 429 / timeout / Bedrock outage, falls back to the Jaro-Winkler stand-in instead of raising. Without this, a single Bedrock blip kills the demo with a stack trace. |
| `SCUDO_VERDICT_ALLOW_DEV` | `1` | Permits the dev signing key for the seal — production must inject via Secrets Manager (see i5-lift-preconditions.md §4.3) |
| `BEDROCK_REGION` | `eu-west-2` | Cross-region inference profile for `eu.anthropic.claude-opus-4-8` |

### Environment variables to LEAVE UNSET for the demo

| Variable | Why |
|---|---|
| `SCUDO_MCP_HOST_ENABLED` | The MCP host is built but not yet installed (ARB C2). Setting this true gives you `{enabled: false}` from `get_host()`. Leave unset until the host is wired in `app.py`. |
| `SCUDO_DENSE_BACKEND` | Don't pin — let the `SCUDO_USE_OPUS_DENSE` flag drive the retrieval path. |

## ARB fixes landed this session

All four blocking-or-high findings from the prior workflow synthesis closed in-code:

| # | Finding | Fix landed | Verifying gate |
|---|---|---|---|
| A1 | Flag-on was a kill-switch — every match → 0.5 → review | `store/falkordb_store.py` lazy-imports `opus_dense` and passes `make_opus_dense_scorer(ref.description)` into `multi_path_retrieve` | `ARB_A1_flag_on_passes_real_scorer_not_none` |
| A2 | Two parallel Opus surfaces — `dense_scorer.py` (new, unused) vs `opus_dense.py` (existing, working) | `dense_scorer.py` module docstring marked DEPRECATED with pointer to `opus_dense.py` as canonical | `ARB_A2_dense_scorer_module_is_deprecated_in_docstring` |
| B1 | Agent routed M&V tools to ingestion tier — 404 on every demo | Three `host.call(_TIER_INGESTION, ...)` sites in `agent.py` re-tiered to `_TIER_MATCH_VERIFY` with namespaced tool names (`matchverify.find_candidates`, `.get_node`, `.get_neighbourhood`) | `ARB_B1_agent_host_calls_route_to_match_verify_tier` |
| B2 | `DenseScoreError` uncaught — Bedrock blip crashed the matcher | `retrieval._dense_rescore` wraps the scorer call in try/except; logs WARNING and degrades every survivor to `_DEGRADED_SIMILARITY` (= 0.5, below floor) so the floor gate routes the case to NEEDS_REVIEW | `ARB_B2_dense_rescore_catches_scorer_exception_and_degrades` |

## Pre-demo checklist

- [ ] `SCUDO_USE_OPUS_DENSE=true` set in the demo env
- [ ] `SCUDO_DENSE_FALLBACK=1` set in the demo env
- [ ] Bedrock model access enabled for `eu.anthropic.claude-opus-4-8` in `eu-west-2` / sandbox `954976331678` (already verified per prior runs)
- [ ] Task role has `bedrock:InvokeModel` on the Opus 4.8 ARN (parked per user; sandbox auth via local creds)
- [ ] Reviewer queue is in-memory today — flag-on routes to review path until DynamoDB wiring lands; demo accordingly
- [ ] iFusion mock — backend route and frontend API helper land, but no UI button wired (ARB C3). If iFusion round-trip is part of the demo script, wire the button OR demonstrate via curl

## Demo flow

1. **Reviewer UI** shows a vendor product needing mapping (e.g. "LSEG Equity Prices Real Time")
2. **Agent runs `matchverify.find_candidates`** via MCP host (if installed) or in-process (if not). Multi-path retrieval (BM25 pre-filter → Opus dense rescore → structural pass with rank-signal tilt) returns ranked candidates
3. **Three-band gate** classifies as PASS / BORDERLINE / FAIL; specialist consulted only on BORDERLINE; anchored to the candidate set (off-list pick → NEEDS_REVIEW with `invariant_violation="specialist_off_list"`)
4. **HMAC seal v=2 with band** signed in Match&Verify; payload reaches Persistence
5. **Persistence MCP** verifies the seal and applies the I5 gate — AUTO_MAPPED → reviewer queue (until I5 lifts per `i5-lift-preconditions.md`); APPROVED → Neptune write
6. **iFusion mock publish** — `POST /api/ifusion/publish` returns `{published: true, publish_id: "mock-pub-...", channel: "spi_v2", status: "accepted"}`

## Known live caveats (not blocking demo)

- **MCP host uninstalled** (C2) — the host module exists but no one calls `set_host()` in `app.py`. The visibility tab will always show `{enabled: false}`. Either wire it or hide the tab for the demo.
- **iFusion UI button missing** (C3) — backend route and frontend helper exist; no button calls them. Demo via curl or wire the button.
- **Specialist anchor enforcement** — implementation differs from original brief: fail-closed to NEEDS_REVIEW with `invariant_violation="specialist_off_list"` field surfaced on `MappingResult`, rather than silently treating off-list pick as abstain. This is arguably better — the violation is observable in the reviewer queue. ARB to confirm preferred semantics.
- **Structural-boost-to-dense ratio** (D2) — on the Opus backend the raw boost (≤0.10) is applied directly to dense scores in [0,1]; on the legacy backend it was multiplied by `rrf_top ≈ 0.0164`. Same boost is ~60× more influential in absolute terms on the new path. Sort-key only — `Candidate.similarity` unchanged — so I5 holds, but top-1 ordering can flip differently. Ratify or recap at next ARB.

## What's still parked

- IAM grants (Bedrock + iFusion) — local creds in sandbox
- Bedrock model-access toggle for Titan — Titan parked, not needed
- Full GraphRAG-SDK adoption — built minimal SDK-inspired version in `retrieval.py`
- DynamoDB reviewer-queue wiring — in-memory list today
- Precedent hydrator (Neptune → Falkor) — workstream opened in `precedent-hydrator-workstream.md`
- BLOCKING IAM / model-access items from prior ARB review — carried forward, not resolved

## Files touched in this fix pass

- `scudo_mapping_mcp/store/falkordb_store.py` — flag-on branch wires `opus_dense.make_opus_dense_scorer`
- `scudo_mapping_mcp/retrieval.py` — `_dense_rescore` try/except + log + degraded fallback
- `scudo_mapping_mcp/dense_scorer.py` — DEPRECATED marker in module docstring
- `scudo_mapping_mcp/agent.py` — three M&V tool calls re-tiered from `_TIER_INGESTION` to `_TIER_MATCH_VERIFY` with namespaced tool names
- `scudo_mapping_mcp/tests/smoke.py` — 4 new ARB fix verification gates

## Document control

- v0.1 — initial demo runbook post-ARB fixes, 111/111 smoke green
