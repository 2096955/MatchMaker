---
type: Plan
title: Precedent Hydrator Workstream
description: Workstream plan for precedent hydration and replay at boot.
tags:
- plan
- precedent
staleness: current
timestamp: '2026-06-28T06:28:37Z'
---

# Precedent Hydrator — Workstream
| Field | Value |
|---|---|
| Status | OPEN — workstream not yet started |
| Goal | Hydrate Neptune→Falkor precedents so the rank-signal tilt and precedent-reuse paths work in Neptune-canonical deployments |
| Owner | _[TBC]_ |
| Linked diagrams | docs/architecture/scudo-overview.mmd (NEP -.->\|precedent\| FALKOR edge) |

## 1. Problem statement
- Today: STORE_BACKEND selects ONE active store. feedback.apply_decision writes to that one. The two stores never synchronise.
- In falkor mode: precedent edges live in Falkor, rank-signal tilt + precedent-reuse work, but there is no canonical source-of-record.
- In neptune mode: Neptune is canonical, but Falkor working graph (used by find_similar_products for retrieval) has no precedent edges → rank-signal tilt is zero, precedent-reuse miss-rates rise, the cost ladder degrades.
- The architecture diagram shows the working graph rehydrating from Neptune; the code does not.

## 2. Three options
(a) Read-through: at query time, fetch precedents from Neptune on-demand and cache in Falkor. Lazy. Solves cold-start but precedent-reuse path needs a synchronous Neptune query.
(b) Background replicator: a sidecar process tails Neptune writes and replicates precedent edges to Falkor. Eager. Higher operational complexity but query-time has no Neptune dependency.
(c) Eager dual-write: persistence_mcp writes to both Neptune AND Falkor in the same transaction. Simplest but violates "Neptune is canonical, Falkor is rebuildable" — Falkor becomes a co-source-of-truth.

## 3. Recommended starting point
Option (b) — background replicator — because:
- Preserves Neptune as canonical
- Preserves Falkor as rebuildable from Neptune (the diagram's promise)
- Query-time latency unaffected
- Can be killed for an unsynchronised Falkor and rebuilt — no data loss

## 4. Open questions
- What's the trigger? Neptune change-stream? Persistence MCP emit + SQS? Periodic poll?
- What's the SLA? Read-your-write within N seconds, or eventual?
- How does the replicator handle bundle imports (M6)?
- Does the rebuild path use the same code as the replicator?

## 5. Out of scope for this workstream
- M6 bundle import / export (separate flow, lives in bundle.py)
- Reviewer queue (separate workstream — DynamoDB wiring)
- I5 lift preconditions (separate gate — see i5-lift-preconditions.md)

## 6. Document control
- v0.1 — workstream opened 2026-06-10
