---
name: graphrag-retrieval
description: Candidate discovery via graph-augmented retrieval for vendor→CDAO mapping.
allowed-tools: graphrag_retrieve
type: Skill
title: GraphRAG Retrieval Skill
tags:
- skill
- graphrag
staleness: current
timestamp: '2026-07-09T13:18:02Z'
---

# GraphRAG retrieval (candidate discovery)

`graphrag_retrieve(query, top_k)` queries a **lexical sidecar** — a graph + vector
index built offline from the CDAO ontology definitions and the vendor catalogue.
It returns candidate CDAO nodes with their definitions and *why they are related*.

## Why a graph, not just vectors
Vector search returns what is *semantically similar*; the sidecar returns what is
*related* — through taxonomic, broader/narrower, and part-whole structure. A
vendor product's wording is often not close to the right node's label but is
related to it through the taxonomy. Retrieval over the graph surfaces those nodes,
which is exactly the no-exact-match case that is the point of this work [clarif. 83].

## It is not the source of truth
The sidecar is **non-authoritative and rebuildable**. It exists to narrow the
search, nothing more. Two rules:
- **Confirm before relying.** For any candidate you favour, read its authoritative
  definition and position via the **neptune-io** skill (`neptune_node_by_iri`).
- **Never publish from it.** Nothing sourced from the sidecar is written to the
  RDF graph. Writes and truth live in neptune-io.

## How to query
- Build the query from the vendor product's title, description, theme, and
  `asset_class` — the meaning, not the raw string.
- Start focused; widen only if the first pass returns nothing usable. Retrieval
  calls are budgeted (a hook caps them) — once you have viable candidates, stop
  searching and judge.
- Pass the candidates back to the **taxonomy-mapping** procedure as evidence,
  citing which candidate and which confirmed authoritative node supported the call.

## Reference
The sidecar is built by the offline `build_lexical_index()` job (corpus = rendered
CDAO TTL [clarif. 11] + vendor catalogue). It is refreshed when CDAO or the
catalogue changes — it lags the authoritative graph between rebuilds, which is
another reason to confirm candidates in neptune-io.

## Do not
Treat retrieved candidates as ground truth · publish anything from here · widen the
query unboundedly · skip the authoritative confirmation step.

## Related

- [Neptune I/O (confirmation)](/skills/neptune-io.md)
