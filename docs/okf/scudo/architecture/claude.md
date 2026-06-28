# Agent Instructions — `architecture`

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

- `arb-review-pack.md` (Decision Record): **ARB Review Pack** — Architecture review board pack summarizing key SCUDO matching design decisions and open questions.
- `batch.md` (Architecture): **BATCH — Self-Verifying Loop Made Durable** — How the BATCH self-verifying loop is made durable across sessions with checkpoints and replay semantics.
- `diagram-falkor-internals.md` (Architecture): **Diagram — Falkor Internals (superseded)** — Legacy Falkor internals diagram doc; superseded by scudo-retrieval.mmd in diagrams-and-sources.
- `diagram-main-flow.md` (Architecture): **Diagram — Main Flow (superseded)** — Legacy main-flow diagram doc; superseded by the canonical .mmd set documented in diagrams-and-sources.
- `diagrams-and-sources.md` (Architecture): **SCUDO Architecture Diagrams & Sources** — Canonical .mmd diagram set, supersession mapping, diagrams-win-over-prose rule, and quick orientation for new readers.
- `hooks.md` (Architecture): **Deterministic Enforcement Hooks** — Deterministic Agent-SDK lifecycle hooks (SessionStart through SubagentStop) that enforce SCUDO's non-negotiable invariants at the agent boundary — publish gate, mandatory verifier, confidence floor / HITL routing, no raw SPARQL/Turtle, deterministic IRIs — independent of the model in the loop.
- `overview.md` (Architecture): **SCUDO MatchMaker — Project Overview** — Top-level overview of the vendor→CDAO mapping prototype: cost ladder, three-MCP trust gradient, HMAC seal, repo layout, run/deploy paths, and explicit gaps.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
