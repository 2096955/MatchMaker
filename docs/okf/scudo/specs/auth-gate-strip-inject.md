---
type: Spec
title: Auth Gate — Strip & Inject
description: Spec for stripping dev-auth and injecting real auth at the CloudFront→ALB
  boundary.
tags:
- spec
- auth
staleness: current
timestamp: '2026-06-28T06:28:37Z'
---

# Auth gate — strip & inject `X-Authenticated-User` (deployer spec)

**Goal:** make `/api/*` authenticated and **non-spoofable** before any exposure
beyond a closed/internal demo. Today the gate is dev-open and the edge forwards
client headers, so a caller can forge identity.

**Who runs this:** deployer/operator with AWS access (account `954976331678`,
`us-east-1`) **and** whoever controls the CloudFront distribution + ALB in front
of the `scudo-poc` backend.

**The contract (from `backend/auth.py`):** the backend trusts a single header,
default **`X-Authenticated-User`** (overridable via `SCUDO_AUTH_PRINCIPAL_HEADER`).
`auth.py` resolves identity in this order: dev-env → trusted header → 401. The
docstring's trust boundary is the spec: header-from-gateway is sound **only when
(a)** the backend is reachable *exclusively* through the gateway **and (b)** the
gateway **strips any client-supplied copy before setting its own**.

---

## The two defects to fix (both must land — coupled change)

### Defect 1 — dev auth is enabled on the backend
`SCUDO_AUTH_ALLOW_DEV` is truthy on `scudo-poc-app`, so `_resolve_from_dev()`
returns a principal for *every* request → `/api/*` answers with no header at all.

### Defect 2 — the edge forwards client `X-Authenticated-User`
The `scudo-poc-frontend` template sets the `/api/*` behavior's
`OriginRequestPolicyId: 216adef6-5c7f-47e4-b989-5492eafa07d3` — AWS-managed
**`AllViewer`**, which forwards **all** viewer headers to the origin, including a
client-supplied `X-Authenticated-User`. So even with Defect 1 fixed, a client
could forge the header and it would reach the backend. (Verify against the LIVE
distribution — the demo currently runs on the *dev* CloudFront/S3, which may
differ from this template.)

---

## Target end state

```
Browser ──(no X-Authenticated-User; SPA already pins VITE_DEV_PRINCIPAL="")──▶ CloudFront
CloudFront ──(authenticate; STRIP any inbound X-Authenticated-User)──▶ ALB
ALB/edge ──(INJECT X-Authenticated-User = the authenticated identity)──▶ Flask
Flask: SCUDO_AUTH_ALLOW_DEV unset → trusts ONLY the gateway-set header → 401 if absent
```

Pick **one** enforcement point that does the authenticate-strip-inject. Options
in order of preference for this PoC:

### Option A — Lambda@Edge / CloudFront Function on the `/api/*` behavior (recommended)
A `viewer-request` function that: (1) authenticates the request (validate the
session cookie / OIDC token the org's SSO already issues), (2) **deletes** any
inbound `X-Authenticated-User`, (3) sets `X-Authenticated-User` to the verified
principal. Replace the `AllViewer` origin-request policy with one that does **not**
forward `X-Authenticated-User` from the viewer (or an explicit allowlist that
excludes it), so the only copy reaching the origin is the function-set one.

### Option B — ALB listener rules + an auth action
If the ALB fronts the backend directly, use an `authenticate-oidc` (or
`authenticate-cognito`) action on the listener, and a rule that **drops** inbound
`X-Authenticated-User` and sets it from the authenticated claims. (ALB can't
mutate arbitrary request headers natively as richly as Lambda@Edge — confirm the
mechanism; if it can't strip+set, fall back to Option A or C.)

### Option C — JWT verification in the app (the `auth.py`-blessed path)
If the edge already issues signed JWTs, **don't** trust a header at all —
replace the body of `_resolve_from_header` in `backend/auth.py` with JWT
signature+claims verification (issuer, audience, expiry). Per the docstring this
is a one-file change; the route surface and rest of the codebase are unchanged.
Then the edge only needs to be the exclusive ingress (boundary (a)).

---

## Steps

1. **Confirm exclusive ingress (boundary (a)).** The ALB/backend must NOT be
   reachable except through CloudFront. Check the ALB security group only admits
   the CloudFront origin (managed-prefix-list `com.amazonaws.global.cloudfront.origin-facing`)
   or the edge, and there's no public NodePort/direct-attach path. If the backend
   is directly reachable, strip+inject is moot — fix this first.

2. **Implement strip+inject** (Option A/B/C above) on the live distribution.

3. **Stop the SPA sending the header** — already done: the prod build pins
   `VITE_DEV_PRINCIPAL=""` (`vite.config.matching.ts`), so the deployed dashboard
   sends no `X-Authenticated-User`. (Confirm: `grep -r X-Authenticated dashboard-dist/assets` → only the header *name* in code paths that now resolve to "", no value.)

4. **Disable dev auth on the backend.** On `scudo-poc-app`, unset
   `SCUDO_AUTH_ALLOW_DEV` (and `SCUDO_AUTH_DEV_PRINCIPAL`). Redeploy/roll the
   service. Now `auth.py` trusts only the gateway-set header.

---

## Verification (must all pass)

```bash
BASE=https://<live-distribution-domain>     # e.g. dp4ji14se0pct.cloudfront.net

# 1. Unauthenticated request is rejected (dev auth off):
curl -s -o /dev/null -w "no-auth: %{http_code}\n" "$BASE/api/mapping/vendors"
#   EXPECT 401 (was 200). If still 200 → SCUDO_AUTH_ALLOW_DEV still set, or the
#   edge is injecting a principal for anonymous sessions.

# 2. FORGED header does NOT get through (the spoofing test — the whole point):
curl -s -o /dev/null -w "forged: %{http_code}\n" \
  -H "X-Authenticated-User: attacker@evil.com" "$BASE/api/mapping/vendors"
#   EXPECT 401 (edge stripped it) — NOT 200. A 200 here means the forge path is
#   STILL OPEN; do not expose externally.

# 3. A genuinely authenticated session works:
#   Drive a real SSO/OIDC login through the edge, then confirm /api/mapping/vendors
#   returns 200 and that any precedent-writing call (POST /api/mapping/decision)
#   is attributed to the real principal, not the forged one.

# 4. Direct-to-ALB forge is impossible (boundary (a)):
#   From outside the edge, attempt the forged-header call directly against the
#   ALB DNS. EXPECT connection refused / not reachable. If it answers, ingress
#   is not exclusive — fix step 1.
```

**Done = tests 1, 2, 4 behave as above and a real login passes test 3.** Until
then the deployment stays a **closed/internal demo** (network-gated), as it is
now.

---

## Notes
- This is the single gate between "closed demo" (current, fine) and "external /
  real-data exposure". Nothing else in the app needs to change — `auth.py` is the
  single swap point by design.
- If the org's edge already does OIDC/JWT, **Option C is the cleanest** and
  removes the header-trust assumption entirely.
- Cross-ref: `backend/auth.py` docstring (trust boundary), `infra/DEPLOY_RUNBOOK_scudo-poc.md` §5,
  `infra/SMOKE_FIXES_round1.md` finding #3.

## Related

- [Code review fixes (B1/B2)](/handovers/code-review-fixes.md)
- [Deploy runbook](/deployment/deploy-runbook-scudo-poc.md)
