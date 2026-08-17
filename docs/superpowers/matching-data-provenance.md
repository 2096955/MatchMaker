# SCUDO Matching — Data Provenance & Confidence Bands

## Confidence bands (canonical — matcher config)

Source: `backend/scudo_mapping_mcp/config.py` (`CONFIDENCE_FLOOR=0.75`, `BORDERLINE_HALF_WIDTH=0.05`
→ `PASS_CUT=0.80`, `FAIL_CUT=0.70`). The floor is the band **centre**, not the pass
edge — do not read `0.75` as a threshold.

| Band       | Similarity range   | Route                          |
|------------|--------------------|--------------------------------|
| **PASS**   | ≥ 0.80             | Aurora audit + JAPI persist    |
| **BORDERLINE** | 0.70 – 0.80    | Strands orchestration layer    |
| **FAIL**   | < 0.70             | Human review (Scudo UI)        |

UI labels, edge colours, and graph captions use these numbers, and FE `DEFAULT_BANDS`
must agree (`CLAUDE.md` 5-zone contract, 2026-07-04).

> **Corrected 2026-08-16.** This table read PASS ≥ 0.85 / BORDERLINE 0.75–0.85 /
> FAIL < 0.75, sourced from a `CONFIDENCE_FLOOR=0.80` that no longer exists in
> `config.py`. The floor moved to `0.75` under the 5-zone alignment
> (`docs/superpowers/plans/2026-07-04-scudo-5zone-alignment.md` Task 1); this doc
> did not follow. Verified by execution, not by reading:
> `python3 -c "from scudo_mapping_mcp import config as c; print(c.pass_threshold(), c.borderline_threshold())"`
> → `0.8 0.7`.
>
> Two different `CONFIDENCE_FLOOR` constants exist. This one is the MCP matcher
> ladder (`scudo_mapping_mcp/config.py:49` = `0.75`). `backend/scudo/orchestrator.py:41`
> declares a **separate** `CONFIDENCE_FLOOR = 0.80` — the Runtime-A auto-approve
> publish gate. It is a different control and did **not** change. Do not
> reconcile them.

## IRI schemes

| Kind           | Pattern                              |
|----------------|--------------------------------------|
| Vendor product | `mds.<vendor>:<uuid5>`               |
| CDAO node      | `jpmorgan:data:cdao:<type>:<slug>`   |

Forbidden in shipped artifacts: `urn:cdao:*`, bare `cdao:*` (legacy demo seed).

## Data classification

| Value / branch              | Source                         | Synthetic? | Coherent? | Caption role |
|-----------------------------|--------------------------------|------------|-----------|--------------|
| Market Data domain          | `cdao_catalogue.json`          | yes        | yes       | Pricing + indices target |
| Reference Data domain       | `cdao_catalogue.json`          | yes        | yes       | Instruments + entities |
| Marketing domain (removed)  | was `cdao_catalogue.json`      | yes        | **no**    | Incoherent for LSEG/ICE — deleted |
| Pipeline stage nodes        | `build_matching_graph.py`      | yes        | yes       | Architecture exposition |
| Vendor samples              | `build_matching_graph.py`      | yes        | yes       | Demo products for ICE/LSEG/SPG |

All synthetic nodes carry `provenance: { source: "synthetic" }` and a `caption` (≤140 chars) in API/graph payloads. The UI shows a persistent **ILLUSTRATIVE DATA** banner when `meta.dataProvenance === "synthetic"`.
