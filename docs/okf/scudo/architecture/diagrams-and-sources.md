---
type: Architecture
title: SCUDO Architecture Diagrams & Sources
description: Canonical .mmd diagram set, supersession mapping, diagrams-win-over-prose
  rule, and quick orientation for new readers.
tags:
- architecture
- diagrams
staleness: current
timestamp: '2026-06-28T06:28:37Z'
---

> **Note:** The canonical Mermaid (`.mmd`) diagrams referred to below live in the repo at `backend/scudo_mapping_mcp/docs/architecture/`; they are not part of this `.md` bundle. Where the text below says "this directory", it means that repo path.

# SCUDO Architecture Diagrams

This directory is the **source-of-truth** for SCUDO mapping architecture diagrams.
All diagrams are authored in [Mermaid](https://mermaid.js.org/) (`.mmd`) and
distributed alongside a sibling plain-text export (`.txt`) for teammates who
cannot render Mermaid directly.

Scope: the JPMC SCUDO programme (Cognizant delivery) that maps vendor products
(LSEG, S&P Global, Bloomberg, ICE, FactSet) onto the CDAO taxonomy in Neptune,
using the FalkorDB GraphRAG-SDK as the retrieval substrate, an Opus 4.8 borderline
specialist, and a strict three-MCP trust gradient with an I5 reviewer gate.

If a diagram in this folder disagrees with prose elsewhere in the repo, **this
folder wins**. Update prose to match, not the other way around.

## Diagrams

| Filename | What it covers | When to read |
|----------|----------------|--------------|
| `scudo-overview.mmd` | End-to-end flow across the three MCPs (Ingestion -> Match&Verify -> Persistence), HMAC seal handoff, I5 reviewer-queue interception, and the Bedrock + Neptune + DynamoDB + Falkor data plane. | You are new to SCUDO, or you need to explain the system to a reviewer / architect / governance forum in one picture. Start here. |
| `scudo-match-verify.mmd` | Internals of the Match&Verify MCP: matcher pipeline, gate logic, borderline-specialist call, HMAC seal signing, and the swappable SCUDO structural strategies (precedent boost, negative-precedent drop, distance check) plugged into the SDK. | You are changing matcher behaviour, adjusting thresholds, touching the seal-signing path, or onboarding a new vendor adapter into the matcher. |
| `scudo-retrieval.mmd` | Internals of the FalkorDB GraphRAG-SDK retrieval call: vector + fulltext + cypher + relationship-expansion + cosine rerank, and where the SCUDO-specific structural strategies inject. | You are tuning retrieval recall/precision, swapping a SDK strategy, debugging a missed candidate, or evaluating dense-arm production-readiness against the I5 lift preconditions. |

## Conventions

All diagrams in this folder follow the same drawing conventions so they read
consistently and survive Mermaid version drift:

- **ASCII tokens only** in node IDs and labels. No smart quotes, no en/em dashes,
  no Unicode arrows. Mermaid's parser is fussy and CI renderers vary.
- **No arrows inside node labels.** Arrows belong on edges. If a node needs to
  describe a transition, split it into two nodes joined by an edge.
- **Hexagons (`{{ ... }}`) for gates and trust boundaries.** The I5 reviewer
  gate, the HMAC seal verifier, and any IAM-Deny boundary are always hexagons so
  they are visually unmissable.
- **`classDef` colours by responsibility**, not by component name. The palette is:
  - Ingestion MCP / read-only vendor frames
  - Match&Verify MCP / matcher + signer
  - Persistence MCP / sole writer
  - Bedrock (Opus 4.8 + embeddings)
  - Falkor / SDK retrieval
  - Neptune + DynamoDB persistence
  - Gates and trust boundaries (hexagons)
- **`.mmd` is the source.** The sibling `.txt` is a hand-checked plain-text
  rendering for teammates without a Mermaid renderer (Outlook, Confluence Cloud
  without the plugin, screen readers). When you change a `.mmd`, refresh the
  `.txt` in the same commit.

## I5 lift decision context

These diagrams show the I5 reviewer gate as **physically enforced** by the
Persistence MCP intercepting every `AUTO_MAPPED` decision into the DynamoDB
reviewer queue. Whether and when SCUDO can *lift* that gate (i.e. allow
agent-driven `AUTO_MAPPED` to bypass the reviewer queue for selected
high-confidence bands) is governed by:

- [`../i5-lift-preconditions.md`](/specs/i5-lift-preconditions.md)

Read that document before proposing any change to the gate's behaviour in
`scudo-overview.mmd` or `scudo-match-verify.mmd`. The preconditions cover
dense-arm production-grade evidence, golden-set calibration, MRGR registration,
signing-key hardening, integration test coverage, the audit-back loop, and the
governance sign-off matrix.

## Supersedes

These three diagrams **supersede** the previous architecture diagrams kept at
the top of `docs/`:

- `../diagram-1-main-flow.md` -> superseded by `scudo-overview.mmd`
- `../diagram-2-falkor-internals.md` -> superseded by `scudo-retrieval.mmd`
  (with Match&Verify-specific internals moved to `scudo-match-verify.mmd`)

A reconciliation agent is marking those two files **deprecated** in-place with a
banner pointing here. Do not edit them; edit the `.mmd` files in this folder
instead. The earlier `dense-arm-swap.md` plan v0.2 (which described building the
retrieval primitives ourselves) is also superseded by the decision to adopt the
FalkorDB GraphRAG-SDK, but its critical-review findings carry forward into the
SCUDO-specific structural strategies shown in `scudo-retrieval.mmd` and
`scudo-match-verify.mmd`.

---

# SCUDO Mapping MCP — Architecture docs

Round-tripped artefacts. Each lives here so the diagrams and gates aren't trapped in chat.

| Doc | What it pins | When to read |
|---|---|---|
| [diagram-1-main-flow.md](/architecture/diagram-main-flow.md) | The cost ladder end-to-end: scope → precedent → Falkor → LLM specialist → gate → auto-mapped / reviewer | When reasoning about what runs when, or comparing the diagram against `matching.map_vendor_product` |
| [diagram-2-falkor-internals.md](/architecture/diagram-falkor-internals.md) | Rung 3 expanded: dense (Jaro-Winkler stand-in → GraphRAG Toolkit) + lexical (BM25 → LlamaIndex) + RRF + structural pass (neg-precedents, precedent boost, distance check deferred) | When touching `store.find_similar_products` or thinking about recall vs precision in retrieval |
| [i5-lift-preconditions.md](/specs/i5-lift-preconditions.md) | What must be true before sealed PASS verdicts can autonomously persist to Neptune without a human | Before any conversation about lifting I5, removing the reviewer queue, or scoping autonomous writes |

## Quick orientation for a new reader

- The system maps vendor products (LSEG, S&P Global, Bloomberg, ICE, FactSet) to JPMC's CDAO taxonomy.
- The architecture is three MCPs in a trust gradient: Ingestion (read-only vendor frames) → Match&Verify (matcher + HMAC seal signer) → Persistence (the **only** writer; gates on `sealed_status` / `sealed_band`).
- Persistence today refuses agent-driven `AUTO_MAPPED` per **I5** — every such verdict goes to the reviewer queue. Lifting that interception is the subject of `i5-lift-preconditions.md`.
- The matcher is a deterministic cost ladder. The LLM specialist runs only in the borderline window and can REINFORCE confidence but cannot INFLATE past the deterministic anchor.

## Conventions

- Diagrams are Mermaid, embedded in `.md` so they render on GitHub and in any viewer.
- Code-pointer tables (Diagram → file:method) live alongside each diagram so they don't rot independently of the code.
- `[TBC]` markers in `i5-lift-preconditions.md` are deliberately unresolved — they require JPMC input (named owners, thresholds, control IDs).
