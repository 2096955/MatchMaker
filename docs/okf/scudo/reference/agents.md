---
type: Reference
title: Agent Operating Rules (Learned Preferences & Workspace Facts)
description: 'Standing rules for agents working in this repo: UI targets, mock-data
  honesty, graph schemas, and deploy boundaries.'
tags:
- reference
- agents
staleness: current
timestamp: '2026-07-09T13:18:02Z'
---

## Learned User Preferences

- Ship SCUDO matching UI from the understand-anything `packages/dashboard`, not a bolt-on in `MatchMaker/frontend/` `/mapping`.
- Rejected the ingestion-console matching pages as generic and "shadcn looking" — match the polished dashboard UX on `:5177` instead.
- Synthetic or mock demo data is acceptable when unmistakably labelled ILLUSTRATIVE and the narrative is coherent end-to-end.
- Matching UI must tell an expositional story (vendor → ETL → matcher → confidence gate → persist); every stage should explain itself in context.
- Incoherent mock taxonomy presented as real bank data is unacceptable — e.g. Marketing domain for LSEG/ICE/S&P financial-data vendors.
- MatchMaker only — no Defra, no project switches, no commits unless explicitly asked.
- Ontology-matching work is phased P0→P4 per the DCAT design spec; stop after each phase for review before continuing.
- Keep matcher diffs narrow and bounded; preserve flag-gated compatible defaults and dense-score-only / BM25-nominator-only invariants.
- Verify the design spec's residual evidence gaps in code (matching bands, enrichment shapes, Neptune precedent) before editing affected files.

## Learned Workspace Facts

- SCUDO matching UI lives in `Understand-Anything/understand-anything-plugin/packages/dashboard` (`:5177`); `MatchMaker/frontend/` is the Data Ingestion Framework console only.
- Two graph schemas: **KnowledgeGraph** (`kind: "codebase"`, `layers`, `tour`) for the dashboard; **MatchPayload** for `GET /api/mapping/graph`.
- `backend/scudo/build_matching_graph.py` emits KnowledgeGraph; `build_match_payload()` serves the API MatchPayload shape.
- Canonical CDAO IRIs: `jpmorgan:data:cdao:*`; forbidden in shipped artifacts: `urn:cdao:*`, bare `cdao:*`, and Marketing domain nodes.
- Matcher confidence bands (canonical): PASS ≥0.80, BORDERLINE 0.70–0.80, FAIL <0.70 per `backend/scudo_mapping_mcp/config.py`.
- Taxonomy JSON source of truth: `backend/scudo/fixtures/cdao_catalogue.json`; sync to dashboard via `backend/scudo/scripts/sync_matching_graph_to_dashboard.sh`.
- Do not edit Codex-owned files: `backend/scudo/data-platform.yaml`, `build-pipeline.yaml`, `template.yaml`; console AWS deploy uses `infra/scudo-poc-*.yaml`.
- Binding SCUDO matching spec: `docs/superpowers/specs/2026-06-24-scudo-matching-frontend-spec.md`; AWS deploy gated on owner `:5177` screenshot approval.
- DCAT ontology matching spec: `docs/superpowers/specs/2026-07-03-dcat-ontology-matching-design.md`; P0 = DCAT/SKOS loader + extended `TaxonomyNode` + `taxonomy_text` threading; P1 = subsumption + enrichment projection + calibration; P2/P3 = real RDF/SHACL + ODRL; P4 = CSVW/`TurtleIngester` upload seam.
- Backend verification cwd: `MatchMaker/backend/` with `PYTHONPATH=.` for pytest, import smoke (`scudo_mapping_mcp.retrieval`, `store.memory_store`, `scudo.tools`).
- P0 ontology fixtures: `backend/scudo_mapping_mcp/tests/fixtures/` (e.g. `dcat_taxonomy.ttl`); catalogue POC `catalogue_ontology_v0_1_deontic.ttl` is transcript-derived and unverified.
- P0 matcher scope excludes `backend/scudo/tools.py`, `frames.py`, enrichment, ODRL, SHACL, and Neptune precedent schema unless a P0 test proves required.

## Related

- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
- [Matching frontend spec (binding)](/specs/matching-frontend.md)
