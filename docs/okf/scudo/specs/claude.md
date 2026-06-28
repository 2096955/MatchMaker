# Agent Instructions — `specs`

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

- `auth-gate-strip-inject.md` (Spec): **Auth Gate — Strip & Inject** — Spec for stripping dev-auth and injecting real auth at the CloudFront→ALB boundary.
- `hitl-two-way-chat.md` (Spec): **HITL Two-Way Chat Spec** — Human-in-the-loop two-way chat design for reviewer adjudication.
- `i5-lift-preconditions.md` (Spec): **I5 Lift Preconditions** — Hard preconditions that must hold before invariant I5 can be lifted to let sealed PASS-band verdicts auto-persist to Neptune without reviewer approval — band semantics, seal/IAM hardening, golden-set calibration, audit-back, and governance sign-off.
- `ingestion-framework.md` (Spec): **Ingestion Framework Spec (ETL §1–11)** — Format-agnostic ETL ingestion spec; SCUDO pipeline diagrams live in architecture/, not here.
- `matching-frontend.md` (Spec): **SCUDO Matching Frontend Spec** — Binding spec: ship understand-anything dashboard, honest synthetic data, dual graph schemas, deploy gate.
- `self-improving-agent.md` (Spec): **Self-Improving Agent System Design** — Design for compounding orchestrator memory via precedents, rules, and verified-facts-only promotion.

## Token-efficient retrieval

Before loading a concept body, read only its YAML frontmatter block (`type`, `title`, `description`). Skip the body unless the frontmatter matches your task.

## After changes

If you add, rename, or remove concepts here, regenerate indices:

```bash
okf index /Users/anthonylui/MatchMaker/MatchMaker/docs/okf/scudo
```
