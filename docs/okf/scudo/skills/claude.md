# Agent Instructions — `skills`

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

- `graphrag-retrieval.md` (Skill): **GraphRAG Retrieval Skill** — Candidate discovery via graph-augmented retrieval for vendor→CDAO mapping.
- `neptune-io.md` (Skill): **Neptune I/O Skill** — Authoritative RDF graph reads and writes against Neptune (or FalkorDB stand-in).
- `rdf-serialisation.md` (Skill): **RDF Serialisation Skill** — Publish confirmed mappings as RDF/JSON-LD with ODRL rights metadata.
- `rights-odrl.md` (Skill): **Rights & ODRL Skill** — Attach ODRL policy metadata to published canonical product records.
- `taxonomy-mapping.md` (Skill): **Taxonomy Mapping Specialist Skill** — Procedure for mapping a vendor product to a JPMC CDAO taxonomy node via retrieval, confirmation, and confidence calibration.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
