---
type: Handover
title: Code Review Fixes Handover
description: Point-in-time code review fix list including dev-auth security gaps B1/B2.
tags:
- handover
- review
staleness: historical
timestamp: '2026-06-28T06:28:37Z'
---

# Codex code-review fixes — status

Tracks the Codex full-review findings the owner prioritized. **Fixed-here** =
landed + unit-tested on `scudo-phase0-foundations`. **Deployer-action** = needs
AWS access / a redeploy I can't run locally.

## Fixed here (app code, with tests)

| # | Finding | Fix | Test |
|---|---------|-----|------|
| A1 | Fake ETL reject-count (`row-N` synthesized) | `_rows_to_frames` rejects id-less rows + returns truthful count | `test_ingest_reject_count.py` |
| A2 | Malformed JSON → 500 | `ingest_bytes` validates JSON shape → `ValueError`→400 | `test_ingest_validation.py` |
| A3 | Unbounded upload | `MAX_CONTENT_LENGTH` (5MB) + `SCUDO_MAX_ROWS` (10k), env-overridable | `test_ingest_validation.py` |
| A4 | SSE worker no cancel/backpressure | bounded queue + cancel `Event` checked between stages + non-daemon thread + `GeneratorExit` join | `test_ingest_cancel.py` |
| A5 | REST `/map` borderline auto-maps w/o specialist | wired opus_dense-backed `make_rest_specialist()` (timeout + abstain-safe) + `borderline_requires_specialist` → NEEDS_REVIEW on abstain | `test_rest_specialist.py` |
| A6 | `build_match_payload` hits global store + swallows errors | `map_vendor_product(store=...)`; payload passes its `MemoryStore`; removed bare `except: pass` | `test_payload_local_store.py` |
| A7 | 500 handler leaks `str(e)` | generic message + `error_id`; HTTPExceptions keep their code; detail logged only | (manual) |
| A8 | `/healthz` liveness-only; seed-fail hidden | added `/readyz` (503 until seeded); failed seed no longer sets `_seeded`/`seed_ok` | `test_readiness.py` |

Also (infra-adjacent, code side): CORS now honors `SCUDO_CORS_ORIGINS` (B6);
Lambda mock fallback gated behind `SCUDO_ALLOW_MOCK_FALLBACK` (B7).

## Fixed here (IaC — edits land on redeploy by the deployer)

| # | Finding | Fix in YAML |
|---|---------|-------------|
| B1 | `SCUDO_AUTH_ALLOW_DEV=1` hard-coded | `scudo-poc-app.yaml`: now `!Ref AllowDevAuth`, **default "0"** |
| B4 | Wildcard Bedrock IAM | `scudo-poc-foundation.yaml`: invoke/converse scoped to model + inference-profile ARNs (`BedrockModelId`) |
| B6 | Wildcard CORS | `SCUDO_CORS_ORIGINS` env wired in `scudo-poc-app.yaml` (`CorsOrigins` param) |
| B7 | Silent mock fallback | `SCUDO_ALLOW_MOCK_FALLBACK` env wired (`AllowMockFallback` param), default "0" |

> ⚠️ These YAML edits change the TEMPLATE. They do nothing to the running stack
> until the deployer redeploys `scudo-poc-app` / `scudo-poc-foundation`. Until
> then the **live** auth gate is still dev-open.

## Deployer-action only (needs live AWS / topology decisions)

| # | Finding | What the deployer must do |
|---|---------|---------------------------|
| B2 (blocker) | Header-trust boundary | Strip+inject `X-Authenticated-User` at the edge — full spec in `infra/AUTH_GATE_SPEC_strip_inject.md`. The B1 template change is necessary but NOT sufficient without this. |
| B3 | CloudFront→ALB over HTTP | Add ALB TLS + set CloudFront origin protocol policy to HTTPS-only; close/redirect plain HTTP. Touches the live distribution/listener — left to the deployer rather than guessed. |
| B5 | `/demo/` SPA fallback inconsistent | Add a CloudFront Function rewriting `/demo/*` misses → `/demo/index.html` (deep-link 404s otherwise). Needs the live distribution. |
| Lambda `API_KEY` fail-open | `lambda_handler.py:81` | [UNVERIFIED deployed path] confirm the Lambda actually enforces `x-api-key` in the deployed config; fail closed if `API_KEY` unset. |

## Verification done
- `pytest` across all matching test files: green (run per-file to avoid the
  known process-global store-cache cross-file pollution).
- `pnpm build:matching`: unaffected (no dashboard code changed this round).

## Not changed (verified FINE by Codex)
0.85 band float fix; SSE worker Flask-context safety (copies `g.principal`
before the thread); cleanup script dry-run default; canonical `mds.*` vendor
IRIs; shell scripts `set -euo pipefail`.

## Related

- [Auth gate spec](/specs/auth-gate-strip-inject.md)
