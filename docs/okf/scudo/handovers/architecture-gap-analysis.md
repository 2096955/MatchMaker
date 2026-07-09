---
type: Handover
title: Architecture Gap Analysis (2026-06-16)
description: Dated gap analysis between intended SCUDO architecture and implementation
  at that point in time.
tags:
- handover
- architecture
staleness: historical
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO Architecture — Gap Analysis vs Target-State Diagram

**Date:** 2026-06-16
**Method:** read-only fan-out (6 Explore agents over `backend/`, `infra/`, `docs/`) + a hand-verification pass on the orchestrator finding.
**Scope:** maps the "Strategic Architecture — Data Flow (Target State)" diagram (5 vendors → ingestion → SCUDO[Neptune+RDF+ODRL] → Fusion SPI V2 → iFusion) onto the codebase.

**Status key:** ✅ working · 🟡 partial · 🟠 stub/mock · ❌ missing

## TL;DR

Partially covered. **13 ✅ / 5 🟡 / 5 🟠 / 9 ❌** across 32 boxes. The *data plane* (ingestion → matcher → graph store → precedent memory → confidence gate → signed verdict) is real and runs locally. The *live infra* (Neptune semantic retrieval, S3 write, real vendor APIs) and the *consumer + governance edges* (Fusion SPI V2, iFusion catalog, and the entire DQ Framework) are stubs, mocks, or missing.

## ⚠️ Correction to the "orchestrator" finding (hand-verified; updated after independent review)

The diagram's **orchestrator + matching engine + nodes** loop **already exists and works** — in *two* real forms, plus one skeleton. There are THREE implementations:

| Orchestrator | File | Reality |
|---|---|---|
| Deterministic orchestrator + verifier + publish gate | `backend/scudo/orchestrator.py` | ✅ **Real.** `run()` (l.92) routes → assembles bundle → `_call_mapping` → **`_call_verifier`** (l.154, scores the real `VerifierReport`) → **`_gate_and_decide`** (l.215: verifier ≥16 + 0.80 floor + deterministic-IRI + named-graph). Bedrock-wired in `lambda_handler.py:136–224`; bundle assembler is injectable (fake for tests, real pipeline for prod). |
| MCP agent host + matcher | `backend/scudo_mapping_mcp/agent.py` + `matching.py` | ✅ **Real.** Runs `find_similar_products → get_node → get_neighbourhood`, then `map_vendor_product` runs last and **gates**. Rule: *"matcher wins; agent surfaces, matcher gates… cannot disagree by construction."* |
| Strands wiring | `scudo_strands_app.py` (repo root) | 🟠 **Skeleton.** `route()` exists but every helper raises `NotImplementedError`. A blueprint, not wired. |

So you do **not** need to build the orchestrator→matcher→nodes feed — it runs today, and the `scudo/` orchestrator already has a **real 10-dim verifier scorer + publish gate**. The open decision is consolidating the two real paths and retiring the Strands skeleton.

Namespaces: `backend/scudo_mapping_mcp/` (matcher/store/feedback/HMAC verdict), `backend/scudo/` (a **real** deterministic orchestrator + verifier + schemas + Bedrock lambda handler — only its `rdf/fake.py` serializer is a stub), and `scudo_strands_app.py` (the skeleton). *(The first row was missed in the initial pass and surfaced by an independent review.)*

## Coverage matrix

### Ingestion & vendor feeds
| Box | Status | Evidence | Gap |
|---|---|---|---|
| Feed formats JSON/XML/CSV/Parquet/XLSX | ✅ | `backend/ingestion/readers.py` (Csv/Txt/Json/Xlsx/Xml/Parquet ingesters, l.26–123); tests `test_ingest_*.py` | — |
| Ingestion framework: normalize / multi-vendor / schema-map / transform-load | ✅ | `backend/ingestion/engine.py` (739 l): `_load_metadata` l.132, `do_transformations` l.542, `_compare_columns` l.378, `_load_to_target` l.418; `factory.py` l.32 | — |
| Scheduler | ❌ | only manual `POST /api/ingest/<id>` (`routes/ingest.py:23`); cron/Airflow mentioned in `docs/ingestion_framework_spec.md:221` as future | no EventBridge/Lambda/cron |
| File-xfer / AWS API path | 🟡 | local FS only (`engine.py:_discover_files` l.176); S3 env vars in `config.py:93` but no boto3 transfer | S3 ingestion not implemented |
| Vendor catalogue source (MCP) | 🟠 | `vendor_catalogue_mcp/server.py:52` real tool surface, but `mock_backend.py:107` reads synthetic local parquet ("ONLY seam that changes on AWS cutover") | no real LSEG/Bloomberg/S&P/ICE/FactSet APIs |

### Storage & metadata tiers
| Box | Status | Evidence | Gap |
|---|---|---|---|
| S3 "as-is" store | ✅ | `frames.py:134` `_read_s3_frame` (get_object + SHA-256); `hydrate.py:162` reads canonical bundle | **write/put_object path absent** (`persistence_mcp.py:329` returns JSON string only) |
| "Aurora PostgreSQL" metadata/mapping/lineage | 🟡 | `init_db.sql` + `db.py:29` — but it's **MySQL** (`pymysql`), SCD-2 versioning on `tp_provider/dataset/col` | not Aurora-PG; tracks ETL not mapping lineage; mapping state lives only in graph |
| Graph store seam (Falkor/Neptune/memory) | ✅ | `store/factory.py:18`; `falkordb_store.py:95` + `neptune_store.py:463` (SigV4 SPARQL) + `memory_store.py:31` all implement the 15-method contract | Neptune `find_similar_products:547` is a **placeholder (sim=0.0, "M9")** |
| Canonical bundle hydrate/export | ✅ | `bundle.py:113` export / `:198` idempotent import; `hydrate.py:161` S3 replay, graceful cold-start | full-snapshot only; export doesn't write S3 |

### Graph DB + RDF + ODRL (semantic core)
| Box | Status | Evidence | Gap |
|---|---|---|---|
| Neptune ontology graph (CDAO nodes) | ✅ | `neptune_store.py:46–270` 12 SPARQL templates (skos:Concept/broader/narrower); `backend/scudo/authoritative/mcp.py` openCypher | — |
| SPARQL templates | ✅ | `neptune_store.py:46` parameterized templates, `_lit()/_iri()` escaping l.276 | — |
| RDF/DCAT + SHACL | 🟡 | `backend/scudo/rdf/fake.py:54` deterministic DCAT triples | **SHACL is a stub** (`validate_shapes:132` — no real shapes; flags only empty/malformed triples); "faithful FAKE" awaiting `modules.rdf` |
| Product-Rights-Contracts edges | 🟠 | prompts/skills exist; `backend/scudo/rdf/fake.py:121` `serialise_rights` returns empty stub | no Permission/Prohibition/Duty/Contract models |
| ODRL rights (terms/policies/license) | 🟠 | `rights-odrl.SKILL.md` + agent wiring; `tools.py:108` → fake stub | actual ODRL triple generation `NotImplemented` |

### Mapping engine (the matcher)
| Box | Status | Evidence | Gap |
|---|---|---|---|
| OpenSearch / AOSS vector search | ❌ | `opus_dense.py:5` Titan-Embed **PARKED**; dense arm uses Opus 4.8 as judge | no embeddings/vector index |
| Fuzzy + exact match | ✅ | `falkordb_store.py:53` Jaro-Winkler; `base.py:318` BM25 (exact-token recovery, smoke-tested) | — |
| Vendor-specific rule engine | 🟡 | `validations.py:59` `default_field_rules` (name→prefLabel…) | only field normalization; per-vendor scoring "via M6 bundle" |
| Confidence scoring (0.80 floor / bands) | ✅ | `config.py:44` floor 0.80 / `:49` half 0.05; `matching.py:247` 3-band PASS/BORDERLINE/FAIL | — |
| Semantic classification / search | ✅ | `opus_dense.py:70` scorer; `retrieval.py:74` `multi_path_retrieve`; flag `SCUDO_USE_OPUS_DENSE` | — |
| Ranking (RRF) | ✅ | `base.py:235` RRF_K=60; `:368` `reciprocal_rank_fusion`; fused in `falkordb_store.py:306` | — |
| Approved overrides | ✅ | `feedback.py:44` `apply_decision` (approve/override/reject); precedent reuse `matching.py:167` | — |
| NLP Python libraries | ❌ | no nltk/spacy; pure-Python JW + regex tokenize (`base.py:306`) | semantic delegated to Opus, not NLP libs |

### Orchestrator + verifier + DQ framework
| Box | Status | Evidence | Gap |
|---|---|---|---|
| Orchestrator routing + publish gate | ✅ | **two real:** `backend/scudo/orchestrator.py` (route + verifier + gate, Bedrock-wired) and `agent.py` + `matching.map_vendor_product` (matcher-gates). **skeleton:** `scudo_strands_app.py:262` all helpers `NotImplementedError` | consolidate the two real paths; retire Strands |
| Independent verifier (10-dim rubric) + HMAC verdict | ✅ | **two real mechanisms:** `verdict.py:1–255` HMAC sign/verify (production-grade); AND `scudo/orchestrator.py:154` `_call_verifier` scores the real `VerifierReport` (`schemas.py:207` — 10 dims, total≤20, `recompute_total`) and gates in `_gate_and_decide:215`; Bedrock-wired (`lambda_handler.py:147`) | verifier output quality depends on the live LLM; `scudo_strands_app.py:252` rubric is the unwired skeleton's copy |
| DQ framework: completeness/accuracy/cross-ref | ❌ | zero matches for dq/completeness/accuracy; `validations.py:83` is scope/IRI/length checks only | **no DQ framework at all** |
| DQ metrics + error reports | ❌ | `mcp_host.py:281` metrics are circuit-breaker/availability, not data quality | no quality dimensions/error reports |
| Vendor-specific quality assessments | ❌ | only `check_scope` allow/deny + adapters | no profiling/quality scoring |
| Notifications to business | ❌ | reviewer queue exists; no SNS/SES/email/alert | no notification mechanism |

### Fusion ecosystem (consumer)
| Box | Status | Evidence | Gap |
|---|---|---|---|
| Neptune ↔ Fusion integration layer | 🟡 | Neptune persistence real; `routes/ifusion.py:1` "deterministic mock" | no SPI V2 transport |
| Fusion JSON output | ❌ | mock returns a receipt dict (`ifusion.py:86`), not a Fusion metadata envelope | no serializer/schema |
| iFusion catalog consumer | ❌ | codebase publishes TO Neptune; no consume-FROM iFusion; rights are outbound only | no SPI pull / physical-name mapping / rights ingestion |
| Fusion SPI V2 | 🟠 | `routes/ifusion.py:75` `_do_publish` synthetic id, in-memory OrderedDict; `demo-runbook.md:45` flags mock | no SPI client / enrichment / bidirectional sync |

## Prioritized gaps to reach target state

1. **DQ Framework** (❌ entire box) — biggest greenfield: completeness/accuracy/cross-reference, metrics, error reports, vendor quality, business notifications. Nothing exists.
2. **Fusion SPI V2 + iFusion** (🟠/❌) — real SPI client, Fusion JSON envelope, and the consumer (pull + physical-name mapping + rights ingestion).
3. **Live infra swaps** — Neptune semantic retrieval (replace `sim=0.0` placeholder), S3 write path, real vendor APIs, real CDAO ontology load.
4. **ODRL + SHACL** (🟠) — replace `backend/scudo/rdf/fake.py` stubs with real ODRL triple generation + SHACL shapes.
5. **Orchestrator consolidation** — two real orchestrators already exist (`backend/scudo/orchestrator.py` with a real verifier + publish gate, and `agent.py`+`matching.py`); pick one as the conductor and retire the `scudo_strands_app.py` skeleton.
6. **Scheduler**, **S3 write**, **Aurora-vs-MySQL** decision.

## Where the self-improving layer plugs in

The `self-improving-agent` skill sits on the **working core** — `upsert_precedent` (✅), `rank_signals_for` consult (✅), `hydrate`/bundle (✅), the matcher (✅), the signed verdict (✅). It compounds today on FalkorDB/memory and is **independent of the missing DQ/Fusion edges**. Its nightly routine can also become the host for DQ metrics once that framework exists.

## ASCII view

```
LEGEND   [✅] working   [🟡] partial   [🟠] stub/mock   [❌] missing

  ┌── 5 VENDOR FEEDS ──┐    ┌── INGESTION ───────────┐    ┌── STORAGE / METADATA ──────────┐
  │ LSEG  S&P  ICE     │    │ [✅] readers JSON/XML/  │    │ [✅] graph seam Falkor/Neptune/ │
  │ FactSet  Bloomberg │──▶ │      CSV/Parquet/XLSX   │──▶ │      memory (15-method contract)│
  │ [🟠] catalogue MCP │    │ [✅] ETL normalize/map  │    │ [✅] S3 read   [❌] S3 write     │
  │      (synthetic)   │    │ [🟡] AWS/S3 xfer        │    │ [🟡] metadata DB = MySQL,       │
  └────────────────────┘    │ [❌] scheduler          │    │      not Aurora-PG (no lineage) │
                            └────────────────────────┘    │ [✅] bundle hydrate/export      │
                                                           └───────────────┬─────────────────┘
                                                                           ▼
  ┌══════════════════════ SCUDO CORE — the working data plane ═══════════════════════════════┐
  ║                                                                                           ║
  ║   ┌── ORCHESTRATOR ─────────────┐         ┌── MAPPING ENGINE (matcher) ───────────────┐  ║
  ║   │ [✅] agent.py host:          │         │ [✅] fuzzy + exact (Jaro-Winkler / BM25)   │  ║
  ║   │   find_similar_products →    │ ─feeds▶ │ [✅] semantic search (Opus dense judge)    │  ║
  ║   │   get_node →                 │         │ [✅] RRF ranking + neg-precedent drop      │  ║
  ║   │   get_neighbourhood →        │ ◀nodes─ │ [✅] confidence floor 0.80 + 3 bands       │  ║
  ║   │   MATCHER GATES (final)      │         │ [✅] approved overrides (feedback)         │  ║
  ║   │ [🟠] strands scaffold (stub) │         │ [🟡] vendor rules  [❌] AOSS vectors/NLP   │  ║
  ║   └───────────┬─────────────────┘         └───────────────────┬───────────────────────┘  ║
  ║               ▼                                                ▼                          ║
  ║   ┌── VERIFIER / GATE ──────────┐         ┌── GRAPH / RDF / ODRL ─────────────────────┐  ║
  ║   │ [✅] deterministic gate      │         │ [✅] Neptune ontology (CDAO nodes)         │  ║
  ║   │ [✅] HMAC-signed verdict      │         │ [✅] SPARQL templates                      │  ║
  ║   │ [✅] 10-dim verifier scorer  │         │ [🟡] RDF/DCAT (SHACL = stub)               │  ║
  ║   │      + gate (scudo/orch.py)  │         │ [🟠] ODRL triples / rights-contract edges  │  ║
  ║   └──────────────────────────────┘         │ [🟠] Neptune semantic retrieval (sim=0.0)  │  ║
  ║                                             └────────────────────────────────────────────┘ ║
  ║   ┌── SELF-IMPROVING LOOP (new skill) ──────────────────────────────────────────────────┐ ║
  ║   │ [✅] precedent write   [✅] rank_signals_for consult   [✅] distill → refuter → memory │ ║
  ║   └──────────────────────────────────────────────────────────────────────────────────────┘║
  └══════════════════════════════════════════┬═══════════════════════════════════════════════┘
                                              │  Fusion SPI V2  [🟠 mock]
                                              ▼
  ┌── FUSION ECOSYSTEM (consumer) ──────────────────────────────────────────────┐
  │ [🟡] Neptune↔Fusion integration   [🟠] iFusion publish (mock receipt)        │
  │ [🟠] SPI V2 client   [❌] Fusion JSON output   [❌] iFusion consumer (pull)   │
  └─────────────────────────────────────────────────────────────────────────────┘

  ┌── DQ FRAMEWORK ── [❌] ENTIRELY MISSING ────────────────────────────────────┐
  │ completeness · accuracy · cross-reference · DQ metrics · error reports ·     │
  │ vendor quality assessments · notifications to business                       │
  └─────────────────────────────────────────────────────────────────────────────┘
```
