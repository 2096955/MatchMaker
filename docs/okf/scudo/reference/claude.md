# Agent Instructions — `reference`

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

- `agents.md` (Reference): **Agent Operating Rules (Learned Preferences & Workspace Facts)** — Standing rules for agents working in this repo: UI targets, mock-data honesty, graph schemas, and deploy boundaries.
- `matching-data-provenance.md` (Reference): **Matching Data Provenance & Confidence Bands** — Canonical PASS/BORDERLINE/FAIL bands, IRI schemes, and synthetic-data classification for the matching demo.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
