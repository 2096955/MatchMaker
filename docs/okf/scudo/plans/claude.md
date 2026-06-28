# Agent Instructions — `plans`

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

- `dense-arm-sdk-adoption.md` (Plan): **Dense ARM SDK Adoption (v0.3)** — Replan that ships the Opus-4.8 prompt dense arm for similarity scoring now and DROPS/PARKS the GraphRAG-SDK + Titan vector path behind the SCUDO_DENSE_BACKEND flag; supersedes dense-arm-swap v0.2.
- `dense-arm-swap.md` (Plan): **Dense ARM Swap (v0.2, superseded)** — Earlier dense-arm swap plan superseded by SDK adoption v0.3.
- `matching-dashboard.md` (Plan): **Matching Dashboard Plan** — Plan for honest matching UI on understand-anything dashboard (implemented).
- `matching-frontend-and-deploy.md` (Plan): **Matching Frontend & Deploy Plan (combined)** — Original combined plan; front-end target superseded by dashboard decision; Codex C-1..C-8 retained.
- `phase0-foundations.md` (Plan): **Phase 0 Foundations Plan** — Implementation plan for SCUDO phase-0 foundations (mostly executed).
- `precedent-hydrator-workstream.md` (Plan): **Precedent Hydrator Workstream** — Workstream plan for precedent hydration and replay at boot.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
