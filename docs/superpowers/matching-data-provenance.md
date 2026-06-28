# SCUDO Matching — Data Provenance & Confidence Bands

## Confidence bands (canonical — matcher config)

Source: `backend/scudo_mapping_mcp/config.py` (`CONFIDENCE_FLOOR=0.80`, `BORDERLINE_HALF_WIDTH=0.05`).

| Band       | Similarity range   | Route                          |
|------------|--------------------|--------------------------------|
| **PASS**   | ≥ 0.85             | Aurora audit + JAPI persist    |
| **BORDERLINE** | 0.75 – 0.85    | Strands orchestration layer    |
| **FAIL**   | < 0.75             | Human review (Scudo UI)        |

UI labels, edge colours, and graph captions use these numbers. The architecture diagram’s “80% / 70–79%” narrative is illustrative only; runtime behaviour follows the table above.

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
