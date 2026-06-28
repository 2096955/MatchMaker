---
type: Handover
title: Handover — Push Dashboard Source Repo
description: Instructions for pushing stranded dashboard commits to Egonex-AI/Understand-Anything
  via git-bundle workaround.
tags:
- handover
- dashboard
staleness: historical
timestamp: '2026-06-28T06:28:37Z'
---

## Dashboard source bundle — apply & push

# Dashboard source bundle — apply & push

`scudo-dashboard.bundle` carries the 7 unpushed dashboard commits
(branch `scudo-phase0-foundations`, tip `6741756`) for
`github.com/Egonex-AI/Understand-Anything`. It exists because `2096955` lacks
push access there (403). See `../DASHBOARD_REPO_PUSH_HANDOFF.md` for context.

**Thin bundle:** it contains only `origin/main..scudo-phase0-foundations` and
*requires* base commit `7f5a717` (already on the dashboard repo's `main`), so
apply it inside a normal clone of that repo.

## Apply (from a clone you can push)

```bash
git clone https://github.com/Egonex-AI/Understand-Anything.git
cd Understand-Anything
git fetch /path/to/scudo-dashboard.bundle scudo-phase0-foundations:scudo-phase0-foundations
git checkout scudo-phase0-foundations
git log --oneline -1          # expect 6741756 (HITL reasoning + decision UI)
git push -u origin scudo-phase0-foundations
# then open a PR: scudo-phase0-foundations → main
```

If `git fetch` complains about a missing base, your clone's `main` is behind —
`git fetch origin && git checkout main && git pull` first so `7f5a717` is present.

## Verify the bundle before applying
```bash
git bundle verify scudo-dashboard.bundle   # should say "is okay"
```

The matching UI is gated on `VITE_MATCHING_MODE` / the matching vite config, so
merging it does not change the default Understand-Anything dashboard build.

---

## Handoff — push the dashboard SOURCE repo

# Handoff — push the dashboard SOURCE repo

**For:** a collaborator/deploy agent **with write access to
`github.com/Egonex-AI/Understand-Anything`**. The MatchMaker author (`2096955`)
is **not** a collaborator there — `git push` returns 403 — so the dashboard
source commits are stranded locally and need pushing by someone with access.

> **This is NOT required to deploy.** The built dashboard (`dashboard-dist/`,
> including the HITL UI) is vendored + pushed in the MatchMaker repo, so the
> live demo deploys from MatchMaker alone. This handoff is only to get the
> dashboard **editable source** onto the remote so it isn't lost and others can
> rebuild it.

## What exists locally (unpushed)

- Repo: `Egonex-AI/Understand-Anything`, package `packages/dashboard` (+ a one-line
  `packages/core` type change).
- Branch: **`scudo-phase0-foundations`** — **does not exist on the remote**, never
  pushed (no upstream).
- HEAD: **`6741756`**. It is **7 commits ahead of `origin/main`**:

  ```
  6741756 feat(dashboard): human-in-the-loop — reasoning transcript + decision UI
  e6ba0a2 fix(dashboard): force same-origin API base in matching build
  f1b1df7 feat(dashboard): SCUDO pipeline-glyph favicon for matching build
  1bf3fdd fix(dashboard): rewrite static <title> in matching build (no title flash)
  84fe88f fix(dashboard): brand browser tab title in matching mode
  f7a91eb feat(dashboard): SCUDO matching mode — floating edges, real drill-down, upload & test
  a6a6386 feat(core): add optional data{band,weight} to GraphEdge for SCUDO matching
  ```

  These are the entire dashboard side of the SCUDO matching work: matching mode +
  floating edges + the real per-layer drill-down, the Upload & Test live-pipeline
  UI, the HITL reasoning + decision panels, branding, and the same-origin API fix.

## What needs to happen

The local commits live on the laptop at
`/Users/anthonylui/Understand-Anything/understand-anything-plugin`. Get them onto
the remote one of these ways:

1. **If you can push from that laptop** (you have creds for the Egonex-AI org):
   ```bash
   cd ~/Understand-Anything/understand-anything-plugin
   git push -u origin scudo-phase0-foundations
   # then open a PR: scudo-phase0-foundations → main
   ```
2. **If you work from your own clone:** the author can hand you a bundle/patch
   (`git bundle create scudo-dashboard.bundle origin/main..scudo-phase0-foundations`
   or `git format-patch origin/main`), which you `git fetch`/`git am` into your
   clone and push.
3. **Fork route** (only with owner approval — it publishes the code to a new
   location): fork under a namespace you can write to, push the branch there,
   PR upstream.

## Build note (so the source actually builds for whoever pulls it)

- The dashboard is a **pnpm workspace**; `packages/dashboard` depends on
  `packages/core` (a `workspace:*` dep with vite aliases to `../core/dist/*.js`).
- To build: `pnpm install` at the repo root, build `core` first (`pnpm --filter
  @understand-anything/core build`), then `pnpm --filter
  @understand-anything/dashboard build:matching`. (This ordering is why MatchMaker
  vendors a prebuilt `dist/` rather than building in CodeBuild — see
  `infra/build_dashboard_dist.sh`.)
- `dist/` is gitignored in that repo (correct — it's build output).

## After the push — verify
```bash
git ls-remote origin scudo-phase0-foundations   # should now resolve to 6741756
```
Then a PR into `main` is the clean way to land it; the matching UI is gated on
`VITE_MATCHING_MODE` / the matching vite config, so it does not affect the
default Understand-Anything dashboard build.

## Note
A stray `packages/dashboard/public/matching-graph.json` shows as modified locally
— that's only the `analyzedAt` timestamp regenerating; it's content-identical and
can be discarded (`git checkout -- …`), not committed.
