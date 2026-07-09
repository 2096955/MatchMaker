---
type: Handover
title: Redeploy Branding Note
description: Point-in-time note on redeploying the dashboard with updated SCUDO tab
  title + favicon (incl. the /demo/ vs /cogJPMdemo/ base-path wrinkle).
tags:
- handover
- branding
staleness: historical
timestamp: '2026-07-09T13:18:02Z'
---

# Redeploy note — branding (tab title + favicon)

**Why:** the live site still shows the OLD bundle (browser tab "Understand
Anything", generic "U" favicon). Commit `023e375` on
`scudo-phase0-foundations` fixes both:
- tab title → **SCUDO Matching Comprehension** (static `<title>` + runtime)
- favicon → SCUDO pipeline glyph (`favicon-scudo.svg`)

These are baked into the vendored `dashboard-dist/` (verified:
`dashboard-dist/index.html` has the new title + `favicon-scudo` href, and
`dashboard-dist/favicon-scudo.svg` is present).

## ⚠️ Base-path wrinkle (important)

The vendored `dashboard-dist/` is built with `base:"/demo/"` — assets reference
`/demo/...`. There are now (at least) two live paths on CloudFront
`E3E55IQ7L8SI8` / bucket `scudo-dev-frontend-954976331678`:

- `/demo/` — matches the bundle's base directly.
- `/cogJPMdemo/` — a **base-rewritten** copy the deployer made earlier
  (`/demo/` → `/cogJPMdemo/`). A plain re-sync of this `/demo/`-based bundle
  will NOT serve correctly under `/cogJPMdemo/` — the rewrite step must be
  repeated, OR rebuild with `base:"/cogJPMdemo/"`.

## Redeploy steps (CloudShell, acct 954976331678, us-east-1)

```bash
cd ~/MatchMaker && git fetch origin && git checkout scudo-phase0-foundations && git pull
git log --oneline -1     # expect 023e375 (or later) chore(deploy): … SCUDO favicon + title
test -f dashboard-dist/favicon-scudo.svg && echo "branded dist present ✓"

BUCKET=scudo-dev-frontend-954976331678
DIST=E3E55IQ7L8SI8

# --- Path A: the canonical /demo/ path ---
aws s3 sync dashboard-dist/ "s3://$BUCKET/demo/" --delete
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/demo/*"

# --- Path B: the /cogJPMdemo/ branded path (repeat the base rewrite) ---
# The dist is /demo/-based, so rewrite the prefix in a staged copy before upload:
rm -rf /tmp/cogJPMdemo && cp -r dashboard-dist /tmp/cogJPMdemo
grep -rl '/demo/' /tmp/cogJPMdemo | xargs sed -i 's#/demo/#/cogJPMdemo/#g'
aws s3 sync /tmp/cogJPMdemo/ "s3://$BUCKET/cogJPMdemo/" --delete
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/cogJPMdemo/*"
```

> Cleaner long-term fix for Path B: add a `build:matching:cogjpm` script (or a
> `--base` override) so the bundle is built with `base:"/cogJPMdemo/"` directly,
> instead of sed-rewriting. Deferred — the rewrite works for now.

## Verify after redeploy

```bash
# tab title now in the served HTML (no JS needed):
curl -s https://dp4ji14se0pct.cloudfront.net/demo/ | grep -o '<title>[^<]*</title>'
# expect: <title>SCUDO Matching Comprehension</title>
curl -sI https://dp4ji14se0pct.cloudfront.net/demo/favicon-scudo.svg | head -1   # 200
# repeat for /cogJPMdemo/ if Path B was run.
```
Browser: tab reads "SCUDO Matching Comprehension" with the blue pipeline-glyph icon.

## Note on a true branded hostname

`/cogJPMdemo/` is a path on the dev CloudFront distribution, not a custom
hostname. A real branded URL (`cogJPMdemo.<domain>`) needs Route 53 / domain
ownership + ACM cert + CloudFront alias — out of scope here, flagged for later.
