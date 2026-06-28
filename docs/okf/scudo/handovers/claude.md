# Agent Instructions — `handovers`

> Localized OKF navigation rules for this folder. Follow the project root **CLAUDE.md** (repository root) for global rules. See also the bundle root [../claude.md](../claude.md).

## Read-first protocol

1. **Read `index.md` in this folder before anything else.**
2. At bundle root, also check `log.md` for recent changes before searching.
3. Use the title and description in each index entry to decide which concept to open.
4. Only read a concept file when its index entry matches your task.
5. Follow cross-links inside concepts — do not search the filesystem.

## Forbidden in this folder

- Do **not** glob, ripgrep, or keyword-scan all `.md` files to find information.
- Do **not** create a new subfolder without checking this folder's `index.md` first — similar content may already exist under a different name.
- Do **not** open every concept file to "see what's inside" — use index entries and YAML frontmatter.
- Do **not** move or rename concepts without updating `index.md` (run `okf index <bundle>`).

## Concepts in this folder

- `architecture-gap-analysis.md` (Handover): **Architecture Gap Analysis (2026-06-16)** — Dated gap analysis between intended SCUDO architecture and implementation at that point in time.
- `aws-handoff.md` (Handover): **AWS Handoff (commit-pinned)** — Point-in-time AWS handoff snapshot; read for history only.
- `code-review-fixes.md` (Handover): **Code Review Fixes Handover** — Point-in-time code review fix list including dev-auth security gaps B1/B2.
- `dashboard-repo-push.md` (Handover): **Handover — Push Dashboard Source Repo** — Instructions for pushing stranded dashboard commits to Egonex-AI/Understand-Anything via git-bundle workaround.
- `hitl-bands-2026-06-26.md` (Handover): **HITL Bands Handover (2026-06-26)** — Handover noting live CodeBuild project name correction and HITL band UI state.
- `redeploy-branding.md` (Handover): **Redeploy Branding Note** — Point-in-time note on redeploying the dashboard with updated SCUDO tab title + favicon (incl. the /demo/ vs /cogJPMdemo/ base-path wrinkle).
- `smoke-fixes-round1.md` (Handover): **Smoke Fixes Round 1** — Point-in-time smoke test fix log from round 1.
- `smoke-upload-flow-live.md` (Handover): **Smoke — Upload Flow Live** — Point-in-time verification of the live upload flow.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
