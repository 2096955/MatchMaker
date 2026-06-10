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

- [`../i5-lift-preconditions.md`](../i5-lift-preconditions.md)

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
