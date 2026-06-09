---
name: rdf-serialisation
description: >
  How to turn a MappingResult or a rights result into DCAT + adapted-ODRL Turtle
  for Neptune. Use this whenever triples need to be produced from a structured
  agent output — always, on every mapping or rights result. This is a
  DETERMINISTIC transform: you call a serialiser function, you never author Turtle
  yourself. If you are about to type `@prefix` or `<...> a dcat:Dataset`, stop and
  use this skill.
---

# RDF serialisation (structured result → DCAT + adapted-ODRL Turtle)

This is not a reasoning task. A language model hand-writing Turtle invents prefixes,
malforms IRIs, and drifts from the house shapes. **Never write Turtle by hand.** Call
the serialiser, then validate.

## Steps
1. Call the serialiser with the typed result object:
   - `rdf_serialise_mapping(mapping_result)` → catalogue triples (DCAT-conformant).
   - `rdf_serialise_rights(rights_result)` → adapted-ODRL triples (RDFS semantics, simplified constraints — see the **rights-odrl** skill and clarif. 13).
   The serialiser owns prefixes, the DCAT shape, deterministic IRI minting
   (`mds.<vendor>:<uuid5>`, `jpmorgan:data:cdao:…`), and the named-graph wrapper for
   provenance. You pass data; you do not template strings.
2. Call `rdf_validate_shapes(triples)` (SHACL). This is the **gate**:
   - **conforms** → return the triples for the orchestrator to publish (via **neptune-io**).
   - **violations** → return a defect (the violation report), **not** the triples. Do not "fix" Turtle by editing it; fix the upstream result and re-serialise.

## Invariants the serialiser guarantees (so you must not re-implement them)
- DCAT conformance and the adapted-ODRL shape.
- Deterministic IRIs → replay-safety [clarif. G39]. Same input, same IRIs.
- Every triple carries its named graph.

## Reference
- Serialiser + SHACL shapes: `/modules/rdf/` (`serialiser.py`, `shapes/`).
- The shapes file is the source of truth for what "valid" means; if a shape and this
  skill disagree, the shape wins.

## Do not
Hand-write or hand-edit Turtle · invent prefixes or IRIs · publish triples that failed
SHACL · bypass the serialiser "just this once".
