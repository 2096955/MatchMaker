---
name: neptune-io
description: >
  How to read from and write to the AUTHORITATIVE Neptune RDF graph (the system
  of record) for the enrichment box. Use this whenever you need to confirm a
  node's authoritative position, check an existing mapping or a conflict, or
  publish triples — i.e. any access to the canonical graph. DETERMINISTIC: you
  call parameterised query/publish functions; you never hand-write SPARQL. If you
  are about to type SELECT, CONSTRUCT, or INSERT DATA, stop and use this skill.
  (For candidate *discovery*, use the graphrag-retrieval skill instead — that hits
  a separate, non-authoritative index, not this graph.)
allowed-tools: neptune_node_by_iri neptune_existing_mapping neptune_conflicts neptune_publish_triples
---

# Neptune I/O (authoritative RDF graph — read + idempotent publish)

This skill is the **system of record**: RDF + ODRL + DCAT, reached only through
parameterised tools that return precisely the slice the step needs. No raw SPARQL
crosses this boundary (a PreToolUse hook denies it). Maximum context is not the
goal; appropriate context is.

## Two graphs — do not confuse them
- **This (authoritative) RDF graph** — truth. Reads here are facts; writes here
  are the published result. Everything below operates on it.
- **The lexical sidecar** (graphrag-retrieval skill) — a *non-authoritative*,
  rebuildable retrieval index used only to *discover candidates*. Never treat its
  output as truth and **never publish anything sourced from it**. Candidates from
  the sidecar must be confirmed here (`neptune_node_by_iri`) before you rely on them.

## Reads (bounded, read-only, idempotent)
- `neptune_node_by_iri(iri)` — one node + its authoritative definition and immediate edges. Use to confirm a retrieved candidate.
- `neptune_existing_mapping(vendor, vendor_product_ref)` — prior mapping, if any (drives NEW vs EXTEND).
- `neptune_conflicts(vendor_product_ref)` — equivalent products from other vendors with differing targets (drives RECONCILE_CONFLICT).

Each is capped. Candidate *search* is not here — that is graphrag-retrieval.

## Write — one idempotent path
- `neptune_publish_triples(named_graph, triples)` — the **only** write, to the
  authoritative graph. Idempotent and replay-safe: deterministic IRIs in,
  named-graph provenance attached, identical input → identical end-state [clarif. G39].

Preconditions (the publish gate enforces these — do not rely on prose alone):
- Triples have passed SHACL via the **rdf-serialisation** skill.
- The MappingResult passed the verifier (≥ 16) **or** is routed to HITL.
- `route != RESEARCH`. **RESEARCH never publishes to iFusion** — its output goes
  to the ontology-owner queue, not this graph.

## Reference
- Query/publish module: `/modules/neptune/` (`queries.py`, `publish.py`). Parameterised,
  reviewed SPARQL templates live here — the one place SPARQL is allowed to exist.

## Do not
Hand-write SPARQL · request unbounded results · publish on RESEARCH · publish
unverified/sub-floor mappings · publish anything sourced from the sidecar · write
through any path other than `neptune_publish_triples`.
