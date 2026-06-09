---
name: graphrag-retrieval
description: >
  How to find candidate CDAO taxonomy nodes for a vendor product using the
  GraphRAG lexical sidecar. Use this whenever you need to discover candidate
  nodes or related concepts before judging a mapping — i.e. the "what could this
  map to?" step, on every NEW or RECONCILE mapping. Returns candidates by
  relatedness, not similarity alone. This is discovery only; it is not the source
  of truth.
allowed-tools: graphrag_retrieve
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
