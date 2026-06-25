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
