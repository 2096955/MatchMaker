---
type: Handover
title: HITL Bands Handover (2026-06-26)
description: Handover noting live CodeBuild project name correction and HITL band
  UI state.
tags:
- handover
- hitl
staleness: historical
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO — HITL-visible + reviewer-tunable bands: work summary & deploy/push handover

**Date:** 2026-06-26
**Branch:** `scudo-phase0-foundations`
**For:** the deploy operator with direct AWS access to `954976331678` / `us-east-1`
(laptop has no AWS creds). This is the **incremental** handover for *this release* —
it builds on `infra/DEPLOY_RUNBOOK_scudo-poc.md` (full runbook) and
`infra/REDEPLOY_NOTE_branding.md` (the `/cogJPMdemo/` base-rewrite). Read those for
the baseline; this doc covers only what changed and what to verify.

---

## 1. Summary of work

Two things the owner asked for, on top of the existing matching demo:

1. **HITL is always visible.** Previously the reasoning + decision panels only
   appeared after a run came back borderline, so the headline Human-in-the-Loop
   feature was effectively invisible. Now, in matching mode:
   - `ReasoningPanel` and `DecisionPanel` always render (idle empty states on load).
   - The decision surface (Approve / Override / Reject) is driven by the backend
     result status **and prerequisite-gated** — Approve needs a mapped node +
     confidence, Override needs an alternative candidate, and out-of-scope /
     `band="n/a"` / no-candidate results disable the actions (no spurious POST).
   - A **"Run sample"** button runs a known LSEG product through the live pipeline
     so the panels populate without hunting for a file.
2. **The reviewer can move the confidence bands.** A "Review thresholds" control
   lets a reviewer change the borderline window (e.g. **0.75–0.85 → 0.70–0.85**).
   - The graph edges + node-info band **re-colour live** (advisory display).
   - **"Re-run with these thresholds"** re-invokes the matcher with the chosen
     window so the *actual* AUTO_MAPPED vs NEEDS_REVIEW escalation changes.

**Not in scope (called out for honesty):** the reasoning panel is one-way; a true
two-way chat remains spec-only. Auth stays dev-open for the closed demo.

### What changed in code
- **Frontend** (dashboard repo, source `@ 6fc9550`): new `src/utils/reviewBands.ts`
  (+`reviewDecision.ts`); `store.ts` reviewBands state; `DecisionPanel`,
  `ReasoningPanel`, `UploadTestPanel`, `GraphView`, `NodeInfo`, `App.tsx`,
  `MobileLayout.tsx`. FE tests **69 pass** + `build:matching` green (verified in the
  dashboard source repo, not this checkout).
- **Backend** (MatchMaker `@ 1281ab1`): `scudo_mapping_mcp/matching.py` (kw-only
  `floor`/`half` on `map_vendor_product`, default `settings`),
  `scudo_mapping_mcp/agent.py` (both `run()` thread `confidence_floor`/
  `borderline_half_width` to the authoritative matcher calls + the Strands tool
  closure; override uses a **local** agent so there is no cross-run threshold
  bleed; no contextvar), `routes/mapping.py` (`/map` + `/agent/run` accept +
  validate the optional window). BE tests: **28 pass** per-file.
- **FE↔BE contract:** the FE sends `confidence_floor` + `borderline_half_width`
  (derived `floor=(passCut+failCut)/2`, `half=(passCut−failCut)/2`, 4dp so odd-span
  windows round-trip exactly through the backend's 2dp gate). The BE reads them by
  key-presence and validates `0 ≤ floor−half < floor+half ≤ 1` (→ 400 otherwise).

Process: Codex independently reviewed the plan (outset) + each phase + the final
combined diff + the recovery (see §6). FE & BE built by parallel agents.

---

## 2. Git push (do this first)

- **MatchMaker** — the feature code is commit `1281ab1`; this handover is added as
  a separate doc-only commit on the same branch. Push the branch (it carries both)
  so CodeBuild builds them:
  ```bash
  git -C <MatchMaker> push origin scudo-phase0-foundations
  ```
- **Dashboard** (`github.com/Egonex-AI/Understand-Anything`) — the author (`2096955`)
  **cannot push it (403, not a collaborator)**. The source `@ 6fc9550` is delivered
  as a git bundle: `infra/handoff/scudo-dashboard.bundle` (thin, `origin/main..branch`).
  A collaborator with push access applies it:
  ```bash
  git clone <Understand-Anything> ua && cd ua
  git fetch /path/to/scudo-dashboard.bundle scudo-phase0-foundations:scudo-phase0-foundations
  git checkout scudo-phase0-foundations && git push origin scudo-phase0-foundations
  ```
  > The AWS deploy does **not** need the dashboard repo — the built dashboard is
  > vendored in MatchMaker at `dashboard-dist/`. The bundle is only so the source
  > repo stays in sync.

---

## 3. AWS deployment

Both halves must ship. **Order doesn't matter, but the backend image MUST roll** —
the reviewer band-override is new backend code; without the image roll the
frontend "Re-run with these thresholds" silently has no effect (the BE ignores the
extra body keys on the old image).

### 3a. Frontend (dashboard → S3 + CloudFront)
The new bundle is vendored at `dashboard-dist/` (main JS `index-CdVj71F2.js`,
`base:/demo/`, title "SCUDO Matching Comprehension", `favicon-scudo.svg`).
```bash
cd <MatchMaker>
bash infra/deploy_dashboard_cloudshell.sh      # syncs dashboard-dist/ → s3://<bucket>/demo/ + invalidates /demo/*
```
For the branded **`/cogJPMdemo/`** path, also do the base-rewrite copy per
`infra/REDEPLOY_NOTE_branding.md` (sed `/demo/`→`/cogJPMdemo/` into a second prefix
+ invalidate `/cogJPMdemo/*`). Current distribution: `dp4ji14se0pct.cloudfront.net`
(bucket `scudo-dev-frontend-954976331678`, dist `E3E55IQ7L8SI8`).

### 3b. Backend (image roll — REQUIRED for the band override)
The console/app image (`backend/Dockerfile`) is built — and the vendored
`dashboard-dist/` frontend is published — by the CodeBuild project defined in
`infra/scudo-poc-build.yaml`, named
`${StackPrefix}-console-build` (default **`scudo-poc-console-build`**) and exported
as `scudo-poc-console-build-project-name`. Resolve it rather than hard-coding:
```bash
PROJ="$(aws cloudformation list-exports \
  --query "Exports[?Name=='scudo-poc-console-build-project-name'].Value" --output text)"
aws codebuild start-build --project-name "$PROJ" \
  --source-version scudo-phase0-foundations
# watch buildStatus → SUCCEEDED, then roll the scudo-poc app to the new image
```
> NOTE: `scudo-poc-build` (in `backend/scudo/build-pipeline.yaml`) is a SEPARATE
> project (the Lambda/Codex stack) — do **not** use it here. `DEPLOY_RUNBOOK_scudo-poc.md §3`
> still references `scudo-poc-build`; treat that as stale and prefer the resolved
> `scudo-poc-console-build`. (See runbook §3 for how `scudo-poc` consumes the image.)

---

## 4. Verification

```bash
# Backend liveness
curl -sf https://<api-domain>/healthz                      # {"status":"ok"}

# New bundle is live (frontend)
curl -s https://dp4ji14se0pct.cloudfront.net/cogJPMdemo/ | grep -o 'index-CdVj71F2'   # match
#  (or /demo/) — and the served main JS contains "Review thresholds" + "Under your bands"

# Band override reached the backend (after the image roll):
#  valid window → 200/normal; malformed window → 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<api-domain>/api/mapping/map \
  -H 'Content-Type: application/json' -H 'X-Authenticated-User: <dev>' \
  -d '{"vendor":"LSEG","product_id":"X-1","name":"Global Equity Prices","confidence_floor":0.9,"borderline_half_width":0.2}'   # expect 400 (floor+half>1)
```
UI check: open `/cogJPMdemo/` → the right rail shows the HITL panels on load →
**Run sample** → a result appears → move the threshold inputs (e.g. 0.70–0.85) →
edges/node-info re-band → **Re-run with these thresholds** → escalation changes →
Approve → "HITL: Approved".

---

## 5. Caveats / MUST address before external use
Unchanged from the runbook §5 — still open, do not skip for real use:
1. **Auth (security) — ACCEPTED RISK, recorded 2026-06-27 (owner decision).**
   The live app runs `AllowDevAuth=1` with `SCUDO_CORS_ORIGINS` empty (→ CORS `*`)
   behind an internet-facing ALB + a CloudFront `/api/*` behavior with **no edge
   gate** (no WAF / Lambda@Edge / CloudFront Function). `auth.py:resolve_principal`
   resolves **every** request as the owner, so the API is reachable
   **unauthenticated by anyone with the URL**. Because `AgentBackend` defaults to
   `bedrock` (`us.anthropic.claude-opus-4-8`), `/api/mapping/agent/run` triggers
   **paid Bedrock Opus inference per call**, and record-decision writes pollute the
   precedent store. Data is **synthetic** → no data-confidentiality breach; the
   accepted exposures are **cost-abuse + demo-integrity**. The template comment
   (`scudo-poc-app.yaml:21`) requires the edge strip+inject
   (`infra/AUTH_GATE_SPEC_strip_inject.md`) *before* enabling dev-auth — that gate
   is **intentionally deferred** for this closed demo, gated only by URL obscurity.
   - **Mitigations available without a redeploy:** share the URL only with the
     intended audience; retire/rotate the distribution when the demo window ends;
     watch Bedrock spend in Cost Explorer for `us.anthropic.claude-opus-4-8`.
   - **Cost-only guard (optional, needs a roll):** set `AgentBackend=scripted` —
     removes the paid-inference vector; dense matching + the band override
     (`/api/mapping/map`) are unaffected, only the live agent reasoning changes.
   - **Proper fix when this goes beyond a closed demo:** implement
     `AUTH_GATE_SPEC_strip_inject.md` and roll `AllowDevAuth=0` (see §3b).
2. **SSE vs ALB 60s idle timeout** for long live Bedrock runs (heartbeat / raise timeout).
3. **Live matcher** needs `SCUDO_AGENT_BACKEND=bedrock` + Titan model access.
4. **Band override is backend-gated** — only effective once the §3b image rolls.

## 6. Provenance (review trail)
Codex (independent ARB) reviewed: the plan at the outset (REWORK → folded in), the
FE diff and the BE diff per-phase (REWORK → fixed → APPROVE), the final combined
diff (caught the band 4dp round-trip drift + the Bedrock threshold-bleed — both
fixed), and the post-recovery re-apply (APPROVE). During integration the
MatchMaker `backend/` working tree was deleted wholesale and restored from HEAD
(reflog clean, no committed work lost); the uncommitted Phase 2 was re-applied
verbatim and re-verified. Tests: FE 69 (dashboard repo), BE 28 (per-file, this repo).

## 7. Rollback
- Frontend: re-sync the previous `dashboard-dist/` to `s3://<bucket>/demo/` (+ `/cogJPMdemo/`) and invalidate.
- Backend: redeploy the prior ECR image tag on `scudo-poc`.
- No CloudFormation stack changes in this release.

## Related

- [Deploy runbook (scudo-poc)](/deployment/deploy-runbook-scudo-poc.md)
