---
type: Handover
title: Smoke Fixes Round 1
description: Point-in-time smoke test fix log from round 1.
tags:
- handover
- smoke
staleness: historical
timestamp: '2026-08-17T09:02:03Z'
---

# Live smoke — round 1 findings + fixes

The live Upload & Test smoke (`infra/SMOKE_upload_flow_live.md`) found 3 issues.
Status of each below. Branch is now at `2390a10` (push verified).

---

## 1. Frontend hard-coded to `http://localhost:5001` — FIXED in code ✅

**Was:** the deployed bundle had `VITE_API_BASE=http://localhost:5001` baked in
(leaked from a dev `.env.local`), so the live UI called localhost → no vendors,
disabled "Run".

**Fix (`vite.config.matching.ts`):** `VITE_API_BASE` and `VITE_DEV_PRINCIPAL` are
now hard-pinned to `""` via `define:` in the matching build, so no local env can
ever leak into a production bundle. Verified: the new `dashboard-dist/` contains
**no** `localhost`, and calls relative `/api/mapping/*`.

**Action: redeploy the dashboard** (picks up `dashboard-dist/` @ `2390a10`) —
same steps as `infra/REDEPLOY_NOTE_branding.md`, both `/demo/` and `/cogJPMdemo/`:
```bash
cd ~/MatchMaker && git fetch origin && git checkout scudo-phase0-foundations && git pull
git log --oneline -1   # expect 2390a10 (or later)
grep -rc localhost dashboard-dist/assets/*.js   # expect 0
BUCKET=scudo-dev-frontend-954976331678 ; DIST=E3E55IQ7L8SI8
aws s3 sync dashboard-dist/ "s3://$BUCKET/demo/" --delete
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/demo/*"
# /cogJPMdemo/: repeat the prefix rewrite then sync (see REDEPLOY_NOTE_branding.md)
```
After redeploy, the UI should load vendors and enable "Run" — but it will only
work end-to-end once #2 is resolved.

---

## 2. Deployed backend lacks `POST /api/mapping/ingest/stream` — DEPLOY GAP ⚠️

**Confirmed:** the route IS in the source (`backend/routes/mapping.py:737`,
commit `9eb0b9a`). The 404 means the **running backend is an older image**.

**The real question the smoke surfaced:** the smoke hit
`scudo-dev-alb-2025833982.**eu-west-2**.elb.amazonaws.com` — that's the **dev**
backend in **eu-west-2**, NOT the `scudo-poc` app in **us-east-1** that this
release targets. So either:
- (a) CloudFront `/api/*` is routing to the **wrong / old** backend origin, or
- (b) the intended `scudo-poc` (us-east-1) backend was never rolled to the new
  image.

**Action (deployer, needs AWS):**
1. Decide which backend is canonical for this demo. If it's `scudo-poc`
   (us-east-1), confirm CloudFront `/api/*` points at *its* ALB, not the
   eu-west-2 dev ALB.
2. Roll that backend to the image built from `scudo-phase0-foundations`
   (CodeBuild `scudo-poc-build` succeeded earlier — ensure the app stack/service
   actually deploys that image, not just builds it).
3. Verify the route exists on the live backend:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST <api>/api/mapping/ingest/stream
   #   expect 400 (missing vendor/file) — NOT 404. 404 = route still not deployed.
   curl -s <api>/healthz   # expect {"status":"ok"} — NOT the S3 app-shell HTML
   ```
   (The smoke's `/healthz` returned S3 HTML → CloudFront served the SPA shell for
   that path, another sign `/api`+`/healthz` aren't all routed to the new backend.)

---

## 3. Auth gate is OPEN — CONFIG ⚠️ (security)

`/api/mapping/vendors` returns 200 with **no** `X-Authenticated-User`. In code,
`auth.resolve_principal` allows this only when `SCUDO_AUTH_ALLOW_DEV` is truthy
(dev fallback) — so the deployed backend has **dev auth enabled**.

**Action (deployer):** for anything beyond a closed demo, on the canonical
backend:
- Unset `SCUDO_AUTH_ALLOW_DEV` (and `SCUDO_AUTH_DEV_PRINCIPAL`) so unauthenticated
  `/api/*` returns 401, AND
- Ensure the gateway/ALB **strips inbound `X-Authenticated-User` and injects** the
  real authenticated identity (see `DEPLOY_RUNBOOK_scudo-poc.md` §5). The SPA no
  longer sends that header (fixed in #1), so identity must come from the gateway.

For a closed internal demo, gating at the network/CloudFront layer is acceptable
short-term — but document it as open.

---

## What's verified vs not
- ✅ Matcher SSE (`/agent/run`) works live end-to-end through CloudFront — proves
  the SSE transport itself is fine (no buffering/timeout problem).
- ✅ #1 fixed in code + re-vendored dist, pushed.
- ⏳ #2, #3 are deploy/config — only resolvable with AWS access. Re-run
  `SMOKE_upload_flow_live.md` after the backend is on the new image + frontend
  redeployed.
