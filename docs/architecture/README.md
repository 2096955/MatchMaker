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
trust gradient) and the matcher internals. They remain useful for *how the
matching engine is implemented*, but they predate the 5-zone *framing*, so for
the target architecture the two images in this folder win.

**The live bands are `passCut 0.80 / failCut 0.70`**, derived from
`scudo_mapping_mcp/config.py` `CONFIDENCE_FLOOR = 0.75` and
`BORDERLINE_HALF_WIDTH = 0.05` (floor ± half). `config.py` is the only authority
here — do not read the band ladder off any diagram, this README included. A
diagram or doc labelled `floor 0.80, half-width 0.05` is describing the
**pre-5-zone** ladder (`0.85 / 0.75`) and is stale. Stale `0.85 / 0.75` labels
are known to exist in more than one place, including the OKF bundle
(`docs/okf/scudo/handovers/hitl-bands-2026-06-26.md`,
`docs/okf/scudo/handovers/code-review-fixes.md`) — this README makes **no**
claim that any particular file is currently clean. Check the file you care about
against `config.py` before trusting it.

Note that `backend/scudo/orchestrator.py:41` declares a **separate**
`CONFIDENCE_FLOOR = 0.80` — the Runtime-A auto-approve publish gate. It is a
different control, it is correctly `0.80`, and it must not be reconciled with
the matcher ladder above.

> **Corrected 2026-08-17.** This section previously asserted, of the `.mmd`
> diagrams, that "checked at HEAD, they carry the **current** band values
> (`scudo-match-verify.mmd` reads `floor 0.80, half-width 0.05`), not stale
> ones", and that "The genuinely stale `0.85 / 0.75` band labels live in the
> **OKF bundle**, not in the `.mmd` files". **Both claims were false, and
> backwards.** `floor 0.80, half-width 0.05` *is* the `0.85 / 0.75` ladder —
> i.e. the stale one — so the diagram value cited as proof of currency was in
> fact the defect, and the stale labels were not confined to the OKF bundle.
>
> Measured, not read. Live config:
>
> ```
> $ PYTHONPATH=backend python3 -c "from scudo_mapping_mcp import config as c; print(c.CONFIDENCE_FLOOR, c.pass_threshold(), c.borderline_threshold())"
> 0.75 0.8 0.7
> ```
>
> And the diagram, re-read at the moment this correction was written
> (2026-08-17T07:42:32+0100):
>
> ```
> $ grep -n "floor" backend/scudo_mapping_mcp/docs/architecture/scudo-match-verify.mmd
> 9:    GATE{{"Three-band gate<br/>sim vs floor 0.80, half-width 0.05<br/>or required-fail"}}
> ```
>
> A separate pass was underway on that `.mmd` while this was being written, and
> it landed 56 seconds later. Re-grepped at 2026-08-17T07:43:28+0100:
>
> ```
> $ grep -n "floor" backend/scudo_mapping_mcp/docs/architecture/scudo-match-verify.mmd
> 9:    GATE{{"Three-band gate<br/>floor 0.75, half-width 0.05<br/>PASS at sim 0.80 and up, FAIL below 0.70<br/>or required-fail"}}
> ```
>
> Both greps above are real output from the timestamps shown; the first is not
> a transcription error. That is exactly why this section is written to state
> what the bands **are** rather than to adjudicate which file is stale — the
> adjudication went out of date within a minute. Nothing here asserts the
> current state of any file beyond the single line quoted at the single time
> quoted; re-check before relying on it.
