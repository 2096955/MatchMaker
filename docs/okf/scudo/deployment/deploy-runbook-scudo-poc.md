---
type: Runbook
title: SCUDO PoC Deploy Runbook
description: Step-by-step handoff runbook for one scudo-poc console deploy in us-east-1
  (commit/branch/CloudFront-domain pinned).
tags:
- runbook
- poc
staleness: historical
timestamp: '2026-06-28T06:28:37Z'
---

# SCUDO scudo-poc — AWS Deploy Runbook (handoff)

**For:** an agent/operator with **direct AWS access** to account `954976331678`
(`cb4115669a-genaipocs-aw`), region `us-east-1`. Run from **AWS CloudShell** — the
originating laptop has no AWS credentials, which is why this is handed off.

**What ships:** the SCUDO matching dashboard (interactive Upload & Test pipeline)
+ the Flask backend that serves `/api/mapping/*` (incl. the new
`/api/mapping/ingest/stream` SSE ETL endpoint and `/healthz`).

**Source of truth for the work:** branch `scudo-phase0-foundations` of
`github.com/2096955/MatchMaker` @ commit `4b335bb` (pushed — verified).
Companion handoff facts: `backend/scudo/AWS_HANDOFF.md`.

---

## 0. Preconditions (verify before doing anything)

```bash
aws sts get-caller-identity            # expect Account 954976331678
aws configure get region || echo $AWS_DEFAULT_REGION   # expect us-east-1
```

Stacks expected `CREATE_COMPLETE` (do NOT recreate — they hold live data):
`scudo-poc-net`, `scudo-poc-data`, `scudo-poc` (app), `scudo-poc-build`,
`scudo-poc-frontend`.

```bash
aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'scudo-poc')].{Name:StackName,Status:StackStatus}" \
  --output table
```

---

## 1. Get the code

```bash
cd ~ && rm -rf MatchMaker
git clone --branch scudo-phase0-foundations \
  https://github.com/2096955/MatchMaker.git
cd MatchMaker
git log --oneline -1     # expect 4b335bb feat(deploy): AWS scudo-poc dashboard packaging…
test -f dashboard-dist/index.html && echo "vendored dashboard dist present ✓"
```

The dashboard is **pre-built and vendored** at `dashboard-dist/` (18 files,
`base:/demo/`). You do NOT need node/pnpm or the dashboard repo for this deploy.

---

## 2. Deploy the dashboard (S3 + CloudFront)

```bash
bash infra/deploy_dashboard_cloudshell.sh
```

This script (committed): resolves the frontend bucket + distribution from the
`scudo-poc-frontend` stack exports, `aws s3 sync dashboard-dist/ s3://<bucket>/demo/`,
and invalidates `/demo/*`. The SPA is then served at
**`https://<cloudfront-domain>/demo/`** (current domain:
`https://dp4ji14se0pct.cloudfront.net/demo/`).

> Why `/demo/`: the build uses `base:"/demo/"`, so assets reference `/demo/...`.
> Syncing to the bucket root would white-screen. The script handles the prefix.

---

## 3. Deploy the backend (so /api/mapping/* incl. ingest/stream is live)

The backend runs from the `scudo-poc` app stack via the `scudo-poc-build`
CodeBuild project (builds the image from `backend/Dockerfile`, pushes to ECR,
and — per the revised `infra/scudo-poc-build.yaml` — also publishes the vendored
`dashboard-dist/` to S3/`demo/`).

```bash
# Point CodeBuild at the right branch if it isn't already:
aws codebuild start-build --project-name scudo-poc-build \
  --source-version scudo-phase0-foundations
# watch:
aws codebuild batch-get-builds --ids <buildId> --query "builds[0].buildStatus"
```

Then roll the app stack to the new image if it doesn't auto-deploy (check how
`scudo-poc` consumes the image — ECS service vs Lambda; see AWS_HANDOFF.md §"app").

> If CodeBuild's source is still the old branch/credentials, either update the
> project source or run step 2 standalone (the dashboard half doesn't need
> CodeBuild — only the backend image does).

---

## 4. Smoke tests (must pass before declaring it up)

```bash
# Backend liveness (new unauthenticated probe):
curl -sf https://<api-domain>/healthz            # expect {"status":"ok"}

# Matching graph (needs the gateway-injected auth header — see §5):
curl -s https://<api-domain>/api/mapping/vendors  # expect a vendors list

# Dashboard:
curl -sI https://<cloudfront-domain>/demo/ | head -1   # expect 200
# open in a browser: the SCUDO graph loads, Upload & Test panel is top-right.
```

Existing smoke helper: `infra/scudo_post_deploy_smoke.sh` (adapt domains).

---

## 5. ⚠️ MUST address before any real/external use

These are documented in README "What is NOT done" — do not skip:

1. **Auth header injection (SECURITY).** The dashboard sends
   `X-Authenticated-User` (PoC). The edge (ALB/CloudFront/API GW) **MUST strip
   any inbound `X-Authenticated-User` and inject the real authenticated
   identity** — otherwise a caller forges identity and can write precedents
   (see `backend/auth.py`). Confirm the gateway does this, or the deploy is
   insecure. For a closed demo, gate access at the network/CloudFront level.
2. **CORS.** Prod is same-origin (CloudFront routes `/api/*` → ALB), so no CORS
   config needed. If you instead expose the API on a different origin, you must
   add CORS + you reintroduce the auth-header exposure.
3. **Live matcher (Bedrock/Titan).** Matcher runs scripted unless
   `SCUDO_AGENT_BACKEND=bedrock` is set on the app and Bedrock model access for
   `amazon.titan-embed-text-v2:0` is granted. Verify model access in this
   account first (historically the hard blocker — see AWS_HANDOFF.md).
4. **SSE through ALB idle timeout (60s default).** `/ingest/stream` +
   `/agent/run` set `X-Accel-Buffering:no` and `/api/*` has `Compress:false`. A
   long live Bedrock run can exceed the ALB idle timeout — add an SSE heartbeat
   or raise the timeout before relying on live runs.

---

## 6. Rollback

- Dashboard: re-sync the previous `dashboard-dist/` (or `aws s3 sync` an earlier
  copy) to `s3://<bucket>/demo/` + invalidate `/demo/*`.
- Backend: redeploy the prior ECR image tag on the `scudo-poc` app stack.
- No CloudFormation stack changes are required by this deploy (it publishes
  assets + an app image to existing stacks), so there is no stack rollback —
  only artifact/image rollback.

---

## Quick reference

| Thing | Value |
|---|---|
| Account / region | `954976331678` / `us-east-1` |
| Branch @ commit | `scudo-phase0-foundations` @ `4b335bb` |
| Dashboard URL | `https://<cloudfront>/demo/` (now `dp4ji14se0pct.cloudfront.net`) |
| Health | `https://<api>/healthz` |
| Stacks (don't recreate) | `scudo-poc-net`, `-data`, `scudo-poc`, `-build`, `-frontend` |
| New endpoints this release | `POST /api/mapping/ingest/stream` (SSE), `GET /healthz` |
| Titan model | `amazon.titan-embed-text-v2:0` |
