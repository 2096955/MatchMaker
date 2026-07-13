## Learned User Preferences

- Ship SCUDO matching UI from the understand-anything `packages/dashboard`, not a bolt-on in `MatchMaker/frontend/` `/mapping`.
- Rejected the ingestion-console matching pages as generic and "shadcn looking" — match the polished dashboard UX on `:5177` instead.
- Synthetic or mock demo data is acceptable when unmistakably labelled ILLUSTRATIVE and the narrative is coherent end-to-end.
- Matching UI must tell an expositional story (vendor → ETL → matcher → confidence gate → persist); every stage should explain itself in context.
- Incoherent mock taxonomy presented as real bank data is unacceptable — e.g. Marketing domain for LSEG/ICE/S&P financial-data vendors.
- MatchMaker only — no Defra, no project switches, no commits unless explicitly asked.
- Multi-phase ontology/rights work (DCAT P0→P4; catalogue-rights UML gap Phases A→C) stops after each phase for review with changed files, verification commands, and pass/fail counts before continuing.
- When executing phased plans on a dirty worktree, preserve unrelated uncommitted changes; edit shared files narrowly (e.g. retain existing SSE heartbeat edits in `backend/routes/mapping.py`).
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
- Catalogue/rights UML gap binding spec: `docs/superpowers/specs/2026-07-13-catalogue-rights-uml-gap-analysis.md`; `ContentDeliveryModel` has 11 confirmed values; PROVISIONAL annotation is `HAS_DUTY`-only (not other rights edges).
- Known pre-existing failures in `backend/scudo/tests/test_provenance.py` (Marketing domain / incoherent Marketing branch) are unrelated to rights-model phases — distinguish from new regressions.
