# Agent Instructions — `deployment`

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

- `demo-runbook.md` (Runbook): **Demo Runbook** — How to run and verify the matching demo locally and in PoC.
- `deploy-runbook-scudo-poc.md` (Runbook): **SCUDO PoC Deploy Runbook** — Step-by-step handoff runbook for one scudo-poc console deploy in us-east-1 (commit/branch/CloudFront-domain pinned).
- `deploy.md` (Runbook): **SCUDO Backend Deploy Notes** — How to deploy the SCUDO backend stack and verify health.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
