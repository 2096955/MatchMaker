---
type: Reference
title: Agent Operating Rules (Learned Preferences & Workspace Facts)
description: 'Standing rules for agents working in this repo: UI targets, mock-data
  honesty, graph schemas, and deploy boundaries.'
tags:
- reference
- agents
staleness: current
timestamp: '2026-06-28T06:28:37Z'
---

## Learned User Preferences

- Ship SCUDO matching UI from the understand-anything `packages/dashboard`, not a bolt-on in `MatchMaker/frontend/` `/mapping`.
- Rejected the ingestion-console matching pages as generic and "shadcn looking" — match the polished dashboard UX on `:5177` instead.
- Synthetic or mock demo data is acceptable when unmistakably labelled ILLUSTRATIVE and the narrative is coherent end-to-end.
- Matching UI must tell an expositional story (vendor → ETL → matcher → confidence gate → persist); every stage should explain itself in context.
- Incoherent mock taxonomy presented as real bank data is unacceptable — e.g. Marketing domain for LSEG/ICE/S&P financial-data vendors.

## Learned Workspace Facts

- SCUDO matching comprehension UI lives in `Understand-Anything/understand-anything-plugin/packages/dashboard` (local dev typically `:5177`).
- `MatchMaker/frontend/` is the Data Ingestion Framework console only — matching routes were reverted.
- Two graph schemas: **KnowledgeGraph** (`kind: "codebase"`, `layers`, `tour`) for the dashboard; **MatchPayload** for `GET /api/mapping/graph`.
- `backend/scudo/build_matching_graph.py` emits KnowledgeGraph for the dashboard; `build_match_payload()` serves the API MatchPayload shape.
- Canonical CDAO IRIs: `jpmorgan:data:cdao:*`; forbidden in shipped artifacts: `urn:cdao:*`, bare `cdao:*`, and Marketing domain nodes.
- Matcher confidence bands (canonical): PASS ≥0.85, BORDERLINE 0.75–0.85, FAIL <0.75 per `backend/scudo_mapping_mcp/config.py`.
- Taxonomy source of truth: `backend/scudo/fixtures/cdao_catalogue.json`; sync to dashboard via `backend/scudo/scripts/sync_matching_graph_to_dashboard.sh`.
- Do not edit Codex-owned files: `backend/scudo/data-platform.yaml`, `build-pipeline.yaml`, `template.yaml`.
- Console AWS deploy uses `infra/scudo-poc-*.yaml`, separate from Codex Lambda stacks in `backend/scudo/`.
- Binding SCUDO matching spec: `docs/superpowers/specs/2026-06-24-scudo-matching-frontend-spec.md`; AWS deploy gated on owner `:5177` screenshot approval.

## Related

- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
- [Matching frontend spec (binding)](/specs/matching-frontend.md)
