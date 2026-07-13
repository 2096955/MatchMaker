# Architecture diagrams

Customer-supplied, **approved** reference diagrams for the SCUDO 5-zone design.
These are the source of truth for the target architecture JPMC is building
toward; the repo implements them (see the root [`README.md`](../../README.md)
and [`ZONES.md`](../../ZONES.md) for the module→zone map).

| File | What it is | Provenance |
|------|------------|------------|
| [`scudo-5zone-architecture.png`](scudo-5zone-architecture.png) | **SCUDO Market Data Catalogue: Ingestion, ETL and Matching** — the five-zone target architecture (Vendor Sources & Ingestion → Ingestion Processing → Matching Engine → Agentic Layer → Persistence & Human Review), with the 0.80/0.70 confidence gate and **Aurora PostgreSQL as the single source of truth**. | Approved by Nigel (JPM) 2026-07-03. Canonical consolidation record: [`infra/HANDOVER_5zone_alignment.md`](../../infra/HANDOVER_5zone_alignment.md). |
| [`mds-datacatalog-digital-rights-uml.jpg`](mds-datacatalog-digital-rights-uml.jpg) | **MDS DataCatalog and Digital Rights** — the CDAO catalogue ontology data model: the catalogue half (Dataset / Distribution / DataService / DeliveryChannel / ProductPackage / DataTaxonomy / Field / FieldGroup) and the digital-rights half (Policy / Contract / Duty / Permission / Party, plus the `ContentDeliveryModel` enumeration). This is the shape the matcher maps vendor products *into*. | JPMC catalogue-ontology package. |
| [`catalogue-ontology-uml.mmd`](catalogue-ontology-uml.mmd) | Mermaid transcription of the **detailed CatalogueOntology-UML** image (2026-07-13): full attribute lists for every class, the complete 11-literal `ContentDeliveryModel` enumeration, InternalParty/ExternalParty, and the Document taxonomy (OrderForm / Schedule / MasterAgreement). | Transcribed from the customer-supplied UML image; gap analysis in `docs/superpowers/specs/2026-07-13-catalogue-rights-uml-gap-analysis.md`. |
| [`mds-rights-uml.mmd`](mds-rights-uml.mmd) | Mermaid transcription of the **MDSRights-UML** image (package MarketDataOntology, 2026-07-13): introduces the `Rule` supertype (Duty/Permission specialise it, with `ruleObject`/`ruleSubject` → Party), `Obligation` under Duty with DirectAccess / InternalDistribution, and adds `Pricelist` to the Document taxonomy. | Same provenance and gap-analysis doc as above. |

## Relationship to the Mermaid diagrams

The Mermaid `.mmd` diagrams under
[`backend/scudo_mapping_mcp/docs/architecture/`](../../backend/scudo_mapping_mcp/docs/architecture/)
(`scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd`) depict
the **older three-MCP internal view** (Ingestion → Match-Verify → Persistence
trust gradient) and the matcher internals. They remain accurate for *how the
matching engine is implemented* — and, checked at HEAD, they carry the **current**
band values (`scudo-match-verify.mmd` reads `floor 0.80, half-width 0.05`), not
stale ones. They predate the 5-zone *framing*, so for the target architecture the
two images in this folder win, but the `.mmd` bands are not the stale ones.

The genuinely stale `0.85 / 0.75` band labels live in the **OKF bundle**, not in
the `.mmd` files — e.g. `docs/okf/scudo/handovers/hitl-bands-2026-06-26.md` and
`docs/okf/scudo/handovers/code-review-fixes.md` (a point-in-time snapshot set).
Fixing those is a separate doc-hygiene pass; the live bands are **0.80 / 0.70**.
