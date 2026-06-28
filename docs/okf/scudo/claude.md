# Agent Instructions — `.`

> Localized OKF navigation rules for this folder. Follow the project root **CLAUDE.md** (repository root) for global rules.

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

## Subdirectories

- `architecture/` — read `architecture/index.md` first
- `deployment/` — read `deployment/index.md` first
- `handovers/` — read `handovers/index.md` first
- `plans/` — read `plans/index.md` first
- `reference/` — read `reference/index.md` first
- `skills/` — read `skills/index.md` first
- `specs/` — read `specs/index.md` first

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```

## Bundle navigation map

```
index.md          ← START HERE (root listing)
├── claude.md     ← you are here (root agent OS)
├── <concept>.md  ← one concept per file
└── <subdir>/
    ├── index.md  ← read before entering subdir
    ├── claude.md ← localized subdir rules
    └── <concept>.md
```
