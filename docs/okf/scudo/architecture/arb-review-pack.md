---
type: Decision Record
title: ARB Review Pack
description: Architecture review board pack summarizing key SCUDO matching design
  decisions and open questions.
tags:
- arb
- decision
staleness: historical
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO Architecture — ARB Review Pack

## 1. Decision being tabled

The programme is adopting **FalkorDB GraphRAG-SDK** as the dense + lexical + cypher + relationship-expansion retrieval surface for the Match&Verify MCP, replacing the in-house Jaro-Winkler / pure-Python BM25 / RRF stand-in shipped today. The three-MCP trust gradient (Ingestion read-only, Match&Verify matcher + signer, Persistence sole-writer + I5 gate) and the I5 invariant (no autonomous canonical write without human) are **unchanged** by this decision. The architecture is captured in three Mermaid diagrams (`scudo-overview`, `scudo-match-verify`, `scudo-retrieval`) now sitting at `scudo_mapping_mcp/docs/architecture/` and tabled here as the source-of-truth, superseding the prior `diagram-1-main-flow.md` / `diagram-2-falkor-internals.md` and the `dense-arm-swap.md` v0.2 build-it-ourselves plan.

## 2. What was landed in this workflow

Files written / edited in this session:

- `scudo_mapping_mcp/docs/architecture/scudo-overview.mmd` (+ `.txt`) — system-level diagram: Gateway → Agent → MCP host → three MCPs → stores + observability, trust gradient classification preserved.
- `scudo_mapping_mcp/docs/architecture/scudo-match-verify.mmd` (+ `.txt`) — internals of the matching engine: scope → precedent → match → validations → three-band gate → specialist → seal → Persistence.
- `scudo_mapping_mcp/docs/architecture/scudo-retrieval.mmd` (+ `.txt`) — internals of the retrieval surface: GraphRAG-SDK multi-path (vector / fulltext / cypher / rel-expansion) → cosine rerank → precedent boost → negative-precedent drop → distance check (deferred) → survivors.
- `scudo_mapping_mcp/docs/architecture/README.md` — index, intended audience, supersession notice.
- `scudo_mapping_mcp/docs/diagram-1-main-flow.md` — banner: **SUPERSEDED**, forward pointer to `architecture/scudo-overview.mmd`.
- `scudo_mapping_mcp/docs/diagram-2-falkor-internals.md` — banner: **SUPERSEDED**, forward pointer to `architecture/scudo-match-verify.mmd` and `architecture/scudo-retrieval.mmd`.
- `scudo_mapping_mcp/docs/dense-arm-swap.md` — banner: **SUPERSEDED-PENDING-REPLAN**, forward pointer to forthcoming `dense-arm-sdk-adoption.md`; the v0.2 critical-review findings are carried forward in §8 below.

## 3. The three diagrams

### 3.1 Overview (`scudo-overview.mmd`)

Load-bearing: the **MCPS** subgraph classification (write-isolated) encodes the trust gradient; the **AGENT → HOST → {ING, MV, PER}** fan-out shows the agent orchestrating through a single MCP host (pool, breaker, semaphore); the dotted **MV -.->|borderline specialist| AGENT** edge is the only LLM-into-matcher hop.

```mermaid
flowchart TD
    GW["Gateway: NGINX + FastAPI<br/>auth, rate-limit, identity"]
    UI["Scudo Reviewer UI<br/>human: approve / reject"]
    IN["Catalogue + CDAO taxonomy<br/>per-vendor adapters, one MCP per vendor"]

    AGENT["Strands agent, Claude Opus 4.8 on Bedrock<br/>the LLM the user engages<br/>orchestrates under deterministic guards<br/>also the borderline specialist"]
    HOST["MCP host, transport<br/>pool, circuit breaker, semaphore"]

    subgraph MCPS["MCP servers (by responsibility, write-isolated)"]
        ING["Ingestion MCP<br/>read-only: scope, frame read"]
        MV["Match and Verify MCP<br/>the matching engine: matcher, seal"]
        PER["Persistence MCP<br/>sole writer: verify seal, I5 gate, queue"]
    end

    subgraph STORES["Stores and runtime (AWS Atlas)"]
        S3["S3<br/>vendor frames"]
        FALKOR["FalkorDB working graph<br/>GraphRAG-SDK retrieval, sparse<br/>non-authoritative, rebuildable"]
        NEP["Neptune<br/>canonical RDF, sole-write"]
        QUEUE["Reviewer queue, DynamoDB"]
        SSM["SSM / Secrets Manager<br/>HMAC key, verify"]
        IFU["iFusion<br/>publish via SPI V2"]
    end

    subgraph OBS["Observability and audit"]
        XR["AWS X-Ray<br/>agent traces, Strands OTEL"]
        CW["CloudWatch<br/>metrics, logs"]
        BL["Bedrock invocation logs"]
        TA["Trajectory archive, S3<br/>sealed verdict, steps"]
    end

    GW -->|engages| AGENT
    AGENT -->|triggers tools| HOST
    HOST --> ING
    HOST --> MV
    HOST --> PER
    ING -->|frames| MV
    MV -->|sealed verdict| PER
    MV -.->|borderline specialist| AGENT
    ING --> S3
    MV --> FALKOR
    PER --> NEP
    PER --> QUEUE
    PER --> SSM
    NEP -->|publish| IFU
    NEP -.->|precedent| FALKOR
    PER -.->|needs-review| UI
    IN -.->|catalogue| S3
    AGENT -.->|telemetry| XR

    classDef agent fill:#e9defb,stroke:#6b46c1,color:#3b2566;
    classDef det fill:#d8f0ee,stroke:#2c8c84,color:#16433f;
    classDef store fill:#ece8e0,stroke:#7d7464,color:#3d362a;
    classDef good fill:#e0f0d5,stroke:#5a8f3a,color:#274a14;
    class AGENT agent
    class ING,MV,PER det
    class S3,FALKOR,NEP,QUEUE,SSM,HOST,XR,CW,BL,TA store
    class IFU,GW,UI,IN good
```

### 3.2 Match & Verify (`scudo-match-verify.mmd`)

Load-bearing: the **three-band gate** (floor 0.80 ± 0.05) decides PASS / BORDERLINE / FAIL; the **specialist** runs **only** on BORDERLINE and produces `confidence = min(dense, specialist)`; the **HMAC seal v=2** carries `band` and is signed in M&V only — Persistence is the sole verifier.

```mermaid
flowchart TD
    VP["Vendor product<br/>normalised frame, from Ingestion"]
    SCOPE{{"Scope gate<br/>in scope?"}}
    OOS["Out of scope, exit"]
    PREC{{"Precedent check<br/>confirmed MAPPED_TO?"}}
    REUSE["Reuse mapping, short-circuit"]
    MATCH["Falkor match and check<br/>GraphRAG-SDK multi-path, sparse structural<br/>similarity is the raw dense score"]
    VAL["Validations, M5 deterministic<br/>required: scope_compatible, identifier_resolves, data_class_match<br/>warn: name and description length"]
    GATE{{"Three-band gate<br/>sim vs floor 0.80, half-width 0.05<br/>or required-fail"}}
    AUTO["AUTO_MAPPED<br/>PASS, no specialist"]
    SPEC["LLM specialist, borderline only, the agent<br/>anchored to top candidate<br/>confidence is min of dense and specialist"]
    REVIEW["NEEDS_REVIEW<br/>FAIL or required-fail"]
    SEAL["HMAC seal, v=2<br/>carries band, signed here only, key from SSM"]
    PERS["to Persistence MCP<br/>I5 gate decides persist-or-queue"]

    VP --> SCOPE
    SCOPE -->|in scope| PREC
    SCOPE -->|out of scope| OOS
    PREC -->|new| MATCH
    PREC -->|confirmed| REUSE
    MATCH --> VAL
    VAL --> GATE
    GATE -->|PASS| AUTO
    GATE -->|BORDERLINE| SPEC
    GATE -->|FAIL| REVIEW
    SPEC -->|concur| AUTO
    SPEC -->|disagree| REVIEW
    AUTO --> SEAL
    REVIEW --> SEAL
    SEAL --> PERS

    classDef gate fill:#fdf0d5,stroke:#b8860b,color:#5c4209;
    classDef det fill:#d8f0ee,stroke:#2c8c84,color:#16433f;
    classDef llm fill:#e9defb,stroke:#6b46c1,color:#3b2566;
    classDef good fill:#e0f0d5,stroke:#5a8f3a,color:#274a14;
    classDef warn fill:#fdf0d5,stroke:#b8860b,color:#5c4209;
    classDef store fill:#ece8e0,stroke:#7d7464,color:#3d362a;
    class SCOPE,PREC,GATE gate
    class MATCH,VAL det
    class SPEC llm
    class AUTO,REUSE good
    class REVIEW,OOS warn
    class SEAL,PERS,VP store
```

### 3.3 Retrieval (`scudo-retrieval.mmd`)

Load-bearing: the **PATHS** subgraph is the SDK multi-path surface (vector / fulltext / cypher / rel-expansion); the **BOOST → DROP** pair is the SCUDO-specific structural pass (precedent boost ≤ +0.10 on rank only, never on similarity; negative-precedent drop) implemented as swappable SDK strategies; distance check is **deferred**.

```mermaid
flowchart TD
    Q["Vendor product<br/>name, id, description"]
    subgraph PATHS["GraphRAG-SDK multi-path (over the working graph)"]
        VEC["vector, dense<br/>Titan embeddings, Bedrock via LiteLLM"]
        FT["fulltext, lexical<br/>RediSearch"]
        CY["cypher<br/>structured graph query"]
        REL["relationship expansion<br/>traverse from seed nodes"]
    end
    RR["cosine rerank<br/>ranked candidates, SDK default, swappable"]
    BOOST["plus precedent boost<br/>rank-signal tilt up to 0.10, recomputed from edges"]
    DROP["minus negative-precedent drop<br/>rejected mappings removed"]
    DIST["distance check, SciPy sparse<br/>deferred: needs anchor data"]
    SURV["Surviving candidates<br/>top candidate and similarity, to the gate"]
    NONE["No survivors, to review"]

    Q --> VEC
    Q --> FT
    Q --> CY
    Q --> REL
    VEC --> RR
    FT --> RR
    CY --> RR
    REL --> RR
    RR --> BOOST
    BOOST --> DROP
    DROP --> SURV
    DROP --> NONE
    DROP -.->|deferred| DIST

    classDef det fill:#d8f0ee,stroke:#2c8c84,color:#16433f;
    classDef dense fill:#dbe9fb,stroke:#3b6fb0,color:#1d3a5f;
    classDef fuse fill:#fbe4e0,stroke:#c0563a,color:#5a1f12;
    classDef store fill:#ece8e0,stroke:#7d7464,color:#3d362a;
    classDef good fill:#e0f0d5,stroke:#5a8f3a,color:#274a14;
    classDef warn fill:#fdf0d5,stroke:#b8860b,color:#5c4209;
    classDef deferred fill:#ffffff,stroke:#7d7464,color:#7d7464,stroke-dasharray:4 3;
    class VEC dense
    class FT,CY,REL det
    class RR fuse
    class BOOST,DROP store
    class DIST deferred
    class SURV good
    class NONE warn
    class Q store
```

## 4. What changed from the prior architecture

Concrete deltas against `diagram-1-main-flow.md` / `diagram-2-falkor-internals.md`:

- **Agent moved to the top of the orchestration.** Previously the agent was only invoked by Match&Verify on the BORDERLINE path. Now the agent is the entry point the user engages via the Gateway, and orchestrates all three MCPs through deterministic guards. The agent retains its second role as the borderline specialist.
- **MCP host added as a NEW component.** A transport / pool / circuit-breaker / semaphore layer sits between the agent and the three MCPs. This did not exist on the prior diagrams and does not exist in code today — it is target-state for the I5-lift / Strands-on-Bedrock production deployment.
- **Retrieval consolidated under GraphRAG-SDK multi-path.** Prior diagrams showed an in-house "dense + lexical → RRF" composition. The new retrieval diagram shows four SDK-native arms (vector, fulltext, cypher, relationship-expansion) feeding a single rerank stage. The in-house RRF is replaced by SDK cosine rerank.
- **Structural pass becomes swappable SDK strategies.** Precedent boost (≤ +0.10 on rank-signal only, never on similarity) and negative-precedent drop (rejected mappings removed pre-scoring) are repositioned as strategies that plug into the SDK rather than custom logic in `find_similar_products`. Distance check remains deferred (no anchor data).
- **Trust gradient, three-band gate, seal v=2-with-band, I5 gate — all UNCHANGED.** This is the point: the retrieval surface is being swapped underneath an unchanged control envelope.

## 5. Consistency findings — diagram vs shipped code

Fifteen findings from the validation pass, grouped by severity. **Blocking + high findings must be resolved before the diagrams are called authoritative.**

### 5.1 Blocking

**[scudo-retrieval / BLOCKING] VEC arm "Titan via LiteLLM" — IAM, Bedrock model access, and SDK preprocessing risk to the raw-dense floor anchor.**
- *Inconsistency:* the diagram shows Titan embeddings live via Bedrock/LiteLLM; in reality (a) the M&V task role has `bedrock:InvokeModel` for Opus 4.8 only, not for `amazon.titan-embed-text-v2:0`; (b) the eu-west-2 Bedrock "Model access" toggle for Titan-Embed in sandbox 954976331678 is region- and account-scoped and is a separate enablement; (c) GraphRAG-SDK typically runs its own preprocessing / normalisation pipeline, which collides with the load-bearing discipline at `falkordb_store.py:4-16` that "`Candidate.similarity` is the raw dense score and the 0.80 floor is calibrated against the dense distribution".
- *Which changes:* **both — code and diagram**. Code: IAM policy add for the Titan model ARN; account-level model-access enablement; an interceptor or SDK config that preserves the raw dense score on `Candidate.similarity`. Diagram: annotate that VEC is gated by §4.1 / §4.2 of `i5-lift-preconditions.md` and that "raw dense as similarity" is an invariant the SDK adoption must satisfy.
- *Recommended resolution:* do not ship the diagram as authoritative until (i) the model-ARN IAM add is merged and applied, (ii) Bedrock model access is enabled for Titan-Embed in eu-west-2 / 954976331678, and (iii) a probe test demonstrates the SDK returning raw dense scores (or that an intercept preserves them) — and the golden-set recalibration is in the same change as the SDK adoption per §4.2.

### 5.2 High

**[scudo-overview / high] HOST (MCP host with pool, circuit breaker, semaphore) does not exist in shipped code.**
- *Inconsistency:* the diagram shows `AGENT -->|triggers tools| HOST` and `HOST --> {ING, MV, PER}`. In code (`scudo_mapping_mcp/agent.py:343-367, 421-477`) `BedrockMappingAgent._build_agent()` constructs a Strands Agent whose tools are local Python `@tool` functions calling straight into the package; there is no MCP client, no pool, no breaker, no semaphore. The only `urllib3.PoolManager` use (`neptune_store.py`) is unrelated; "circuit breaker" appears only as target-state in `i5-lift-preconditions.md`.
- *Which changes:* **diagram** (and code follows later). HOST is target-state for the I5-lift / production Strands-on-Bedrock deployment.
- *Recommended resolution:* style HOST node and its edges as aspirational (dashed border / dotted edges) consistent with the convention `diagram-1-main-flow.md` already used for autonomous-Neptune persistence. Pin a separate workstream owner for HOST as a NEW deliverable (see §6).

**[scudo-overview / high] `NEP -.->|precedent| FALKOR` write-back path is not implemented.**
- *Inconsistency:* `feedback.apply_decision` (`feedback.py:138-142`) calls `store.upsert_precedent` on the **single** active store selected by `STORE_BACKEND` via `store/factory.py:7-24`; the stores are alternatives, not synchronised. There is no Neptune→Falkor hydrator. In `STORE_BACKEND=neptune` deployments Falkor is bypassed; in `STORE_BACKEND=falkor` deployments Neptune is bypassed. The edge as drawn is misleading.
- *Which changes:* **either diagram or code, by ARB choice**. If the architectural intent is "Neptune is canonical, Falkor is the rebuildable working graph hydrated from Neptune", a hydrator is missing in code. If the intent is "two backend modes, only one active at a time", the diagram must say so.
- *Recommended resolution:* clarify the intent (Open Question §6) and either (a) drop the edge and annotate the two store modes, or (b) keep the edge and open a workstream for the Neptune→Falkor hydrator with an explicit owner.

**[scudo-retrieval / high] Diagram describes a TARGET state; today there is no GraphRAG-SDK integration at all.**
- *Inconsistency:* `find_similar_products` (`falkordb_store.py:180-285`) is a single-host scan (`MATCH (t:TaxonomyNode) RETURN ...`), then pure-Python Jaro-Winkler over the label (`_jaro_winkler`, lines 52-90), then pure-Python BM25 over the same label corpus (`base.py:281-362`), fused via RRF (not cosine). No `db.idx.vector.queryNodes`, no `db.idx.fulltext.*`, no cypher-as-retrieval, no rel-expansion, no SDK import. The module banner (`falkordb_store.py:4-37`) and the seam docstring (`base.py:18-23`) still name "AWS GraphRAG Toolkit" as the production target and call the toolkit graph-store "vector-only, no traversal" — stale under SDK adoption.
- *Which changes:* **code must catch up to diagram** (the diagram is the adopted target). Concurrently, the in-file production-swap commentary must be updated to point at GraphRAG-SDK, not the AWS toolkit.
- *Recommended resolution:* the diagram is approved as TARGET STATE; the dense-arm-sdk-adoption.md plan is the implementation route; in the same change as the SDK lands, rewrite the `falkordb_store.py:4-37` and `base.py:18-23` banners. Until that lands, annotate the retrieval diagram as TARGET.

**[scudo-retrieval / high] RR "cosine rerank, SDK default, swappable" — today's fuser is RRF and "swappable" must be load-bearing.**
- *Inconsistency:* the fuser today is RRF (`base.py:344-362`); the code is built around the discipline `Candidate.similarity = raw dense, RRF + boost drive sort key only` (`falkordb_store.py:194-209, 265-282`). If the SDK rerank replaces similarity with cosine-of-cosines, the 0.80 floor stops measuring the quantity it was calibrated against, and `matching.py:323-356`'s disagreement-cap loses its reference quantity.
- *Which changes:* **diagram caption must elevate "swappable" from a default to an invariant; code must enforce it.**
- *Recommended resolution:* either (a) run the SDK rerank but pass the raw dense score through on `Candidate.similarity` (rerank affects ORDER only, as RRF does today), or (b) configure the SDK with a custom reranker that preserves the raw-dense contract. If the SDK forces cosine and cannot be intercepted, move the floor + disagreement-cap **outside** the SDK boundary. Annotate "swappable" on the diagram as a hard invariant.

**[scudo-match-verify / high] Persistence I5 gate routes on `sealed_status` only, NOT on `sealed_band`.**
- *Inconsistency:* `commit_mapping` (`persistence_mcp.py:225, 246-265`) reads `sealed_status` from the payload and routes solely on status — `AUTO_MAPPED → _enqueue(reason='auto_mapped_agent_path')`, `NEEDS_REVIEW → _enqueue(reason='needs_review')`, `OUT_OF_SCOPE → refuse`. The seal carries `band` (`verdict.py:160`), and `verdict.py:36-37` explicitly anticipates band-gating ("Persistence MCP can gate on `sealed_band` … refuse to write APPROVED if `band=fail` without explicit override"), but `commit_mapping` never reads `payload['band']`. The reviewer queue is also still in-memory (`persistence_mcp.py:91` comment: "placeholder in-memory implementation").
- *Which changes:* **code**. The diagram's "I5 gate decides persist-or-queue" is correct for status routing, but if the band is intended to be load-bearing at the gate, the code must honour it.
- *Recommended resolution:* extend `commit_mapping` to read `sealed_band` and refuse / down-route `AUTO_MAPPED` seals where `band == 'fail'`. Track the DynamoDB reviewer-queue wiring as a separate workstream and annotate PERS in the diagram as "in-memory queue today, DynamoDB target" if ARB confirms that as the framing.

### 5.3 Medium

**[scudo-overview / medium] `NEP -->|publish| IFU` iFusion edge is target-state, no SPI V2 wiring exists.** No `ifusion|iFusion|SPI|spi_v2` matches in the codebase except the diagram files. Persistence writes to Neptune via `upsert_precedent` and stops. Drawing this as a solid edge alongside genuinely-shipped edges overstates readiness — style it aspirational (dashed) like the prior diagram-1 convention.

**[scudo-overview / medium] `PER → SSM` edge mis-describes how the signing key is loaded.** `verdict._load_signing_key` (`verdict.py:85-96`) reads `SCUDO_VERDICT_SIGNING_KEY` from env, with a dev fallback gated by `SCUDO_VERDICT_ALLOW_DEV=1`. No `boto3` Secrets Manager / SSM call. The contract docstring (`verdict.py:41-44`) says prod MUST inject via Secrets Manager — i.e. an infra / task-definition responsibility. Either re-label the edge as "env var injected by Secrets Manager at task start" or extend `verdict.py` to fetch directly.

**[scudo-match-verify / medium] Specialist "anchored to top candidate" is not enforced in code.** `SpecialistScorer` (`matching.py:121-124`) signature matches the diagram. Concur branch identifies agreement by `specialist_pick.node.iri == best.node.iri` (line 310). But there is **no check** that `specialist_pick.node.iri` is in `{c.node.iri for c in candidates}`. A misbehaving specialist returning an off-list IRI falls through to the disagree branch (line 335) and the off-list IRI surfaces as `alternative_iri` / `alternative_label` (lines 347-348). Either add `assert specialist_pick.node.iri in candidate_iris` (abstain otherwise) or soften the diagram caption.

**[scudo-match-verify / medium] SEAL "key from SSM" is aspirational — env var in code.** v=2 + band are correctly implemented (`verdict.py:155, 160`); `verify()` accepts v ∈ {1,2} (lines 215-219). But `_load_signing_key` is env-var only. Soften the diagram caption to "key from env (Secrets Manager-injected)" **or** extend `verdict.py` to fetch from Secrets Manager directly with a cached client.

**[scudo-retrieval / medium] BOOST node positioning contradicts code; `*rrf_top` scaling is invisible.** Values match: per-approval 0.02, cap 0.10, derived from confirmed-only `MAPPED_TO` edges (`base.py:232-253`; `falkordb_store.py:462-473`). The I5 discipline holds: `sort_key = rank_score + scaled_boost`, `similarity = round(dense_scores[iri], 4)` — boost never touches similarity. **However** the diagram positions BOOST AFTER RR (rerank), implying boost operates on rerank output; in code today, boost is co-applied with RRF inside `find_similar_products` as a single sort-key composition. Also `scaled_boost = raw_boost * rrf_top` (`falkordb_store.py:281`): if SDK cosine outputs sit in [0, 1] (vs RRF's ~0.016 top contribution), the `*rrf_top` scaling becomes wrong. Re-derive scaling under cosine rerank; annotate "recomputed from edges, no stored counter" on the BOOST node.

### 5.4 Low

**[scudo-overview / low] Trust-gradient import boundaries match the diagram.** `ingestion_mcp.py:41-51` has zero verdict / feedback / bundle imports and no Bedrock; `match_verify_mcp.py:54-65` imports `verdict_seal` (sign side) + `matching` only; `persistence_mcp.py:64-78` is the only module importing `feedback.apply_decision`, `bundle.import_bundle/export_bundle`, and the verify side of `verdict_seal`. On `MV -.-> AGENT`: `matching.py:131` takes `specialist: Optional[SpecialistScorer] = None` as a kwarg; `agent.py:260, 410` invokes `map_vendor_product` with the agent supplying the specialist callback **in-process** — not a cross-MCP hop. Edge is directionally fine for visualisation; add a legend note that this is an in-process callback today (will become a host-mediated call under HOST).

**[scudo-match-verify / low] Three-band gate values match diagram (floor 0.80, half-width 0.05).** `config.py:44, 49, 123-124, 179-182` defines both as module constants and Settings fields; `matching.py:243-246` reads them and computes `pass_threshold = floor + half`, `borderline_threshold = floor - half`. Diagram accurate.

**[scudo-match-verify / low] `confidence = min(dense, specialist)` on concur; disagreement caps below floor.** Concur (line 318): `confidence = min(best.similarity, specialist_pick.similarity)` — exactly as diagram. Disagree (line 351): `confidence = min(best.similarity, borderline_threshold - 0.01)` = `min(best, 0.74)` — guaranteed below the 0.80 floor and forces NEEDS_REVIEW. Accurate.

**[scudo-retrieval / low] Negative-precedent drop is correctly inside the seam, ahead of scoring.** `falkordb_store.py:218-233` fetches negatives up front and skips them before the scoring loop (line 229: `if iri in rejected: continue`). The comment at :219-223 notes this was moved into the seam per Diagram 2. Minor nit: the diagram orders DROP after BOOST in dataflow but in code negative-precedent drop is a pre-filter; the per-candidate scope / data-class drop happens inside the scoring loop. Diagram conflates two structural drops into one node — fine at this altitude; worth a footnote in case relationship-expansion (REL) ever surfaces a negatively-precedented node late.

## 6. Open questions for ARB

**Diagram-vs-code reconciliation:**
- [scudo-overview] Is the HOST / transport / pool / circuit-breaker layer planned for the I5-lift work, or only for a future production Strands-on-Bedrock deployment? If the latter, the diagram should mark HOST as target-state to match the diagram-1 convention.
- [scudo-overview] When `STORE_BACKEND=neptune` in prod, what populates the FalkorDB working graph the diagram shows M&V reading from? Either Falkor is unused in that mode (and the diagram should say so) or there is an unimplemented Neptune→Falkor hydrator the `NEP -.->|precedent| FALKOR` edge is hinting at.
- [scudo-overview] Is the iFusion SPI V2 publisher owned by SCUDO or by a downstream team? The edge should be styled aspirational until a publisher module exists in this repo or a documented out-of-repo consumer is named.
- [scudo-overview] Should the diagram label clarify that Secrets Manager injects `SCUDO_VERDICT_SIGNING_KEY` at task-start (env var), not that Persistence calls SSM at verify time?
- [scudo-match-verify] Should the diagram be tightened to "key from env (Secrets Manager-injected)" for honesty, or should `verdict.py` be extended to fetch directly from Secrets Manager / SSM Parameter Store with a cached client?
- [scudo-match-verify] Is the "anchored to top candidate" invariant intended to be enforced in code (assert `specialist_pick.node.iri ∈ candidate IRI set`, abstain otherwise), or is the current "surface as alternative on disagree" behaviour the actual contract and the diagram caption needs softening?
- [scudo-match-verify] Should `commit_mapping` read `sealed_band` from the seal payload and refuse / down-route `AUTO_MAPPED` seals where `band == 'fail'` (as `verdict.py:36-37` itself anticipates), so the I5 gate framing is band-aware end-to-end?
- [scudo-match-verify] Reviewer queue is still the in-memory placeholder list in `persistence_mcp.py` — is the DynamoDB wiring tracked as a separate workstream, or should the diagram annotate PERS as "in-memory queue today, DynamoDB target"?
- [scudo-retrieval] Does GraphRAG-SDK expose the raw dense (cosine) score per candidate before its rerank, or only reranked-and-normalised scores? This decides whether `Candidate.similarity` can keep its raw-dense, floor-anchored contract or whether the floor + disagreement-cap must move outside the SDK boundary.
- [scudo-retrieval] Does the SDK accept a custom reranker callable (the "swappable" claim on the diagram), or are we forced to either accept its cosine rerank or post-rerank ourselves outside the SDK?
- [scudo-retrieval] Does the SDK do its own text preprocessing (stopwording, chunking, normalisation) before computing embeddings, and if so does it apply that to the QUERY as well as to indexed labels? If asymmetric, the 0.80 floor's calibration distribution shifts and a recalibration is mandatory — not optional.
- [scudo-retrieval] Should the in-file production-swap commentary (`falkordb_store.py:4-37` and `base.py:18-23`) be updated in the same change as the diagram lands, so the next reviewer doesn't trip on the "AWS GraphRAG Toolkit, vector-only" framing? And should `dense-arm-swap.md` be marked SUPERSEDED with a forward pointer? (Yes, the latter is done in this workflow.)
- [scudo-retrieval] Is the `*rrf_top` scaling of the boost (`falkordb_store.py:281`) still correct under cosine rerank? Cosine scores sit in [-1, 1] (or [0, 1] post-normalisation), an entirely different order of magnitude from RRF's ~0.016 top contribution — the scaling factor will need re-derivation.

**Explicit decisions ARB is asked to take:**
- Pin a specific Titan model version (`amazon.titan-embed-text-v2:0`) and dimension (1024) to the Model Inventory under MRGR registration?
- Confirm the MCP host component as a NEW deliverable to be owned by **[TBC]**?
- Confirm that agent-at-top orchestration is the target state regardless of I5 status?
- Re-table `dense-arm-sdk-adoption.md` (yet to be drafted, replacing v0.2) at the next ARB session?

## 7. Preconditions still open (linked to `i5-lift-preconditions.md`)

The architecture being approved here **does not lift I5**; it nominates the retrieval surface and orchestration shape. The following §4 preconditions of `i5-lift-preconditions.md` remain open after this architecture change:

- **§4.1 Dense arm is production-grade** — depends on the SDK adoption landing in code, IAM grant for Titan model ARN, and eu-west-2 Bedrock model-access toggle (the BLOCKING finding above).
- **§4.2 Floor and band widths calibrated against a golden set** — must happen in the same change as the SDK adoption; SDK preprocessing risk (Open Question above) makes recalibration mandatory, not optional.
- **§4.3 Verdict seal hardening cluster** — SSM/SM key custody, IRSA scoping, rotation policy, replay window tightening, image signing — none closed by this architecture change.
- **§4.4 Retrieval path integration-tested against real stores** — current tests are seam-level; FalkorDB + Neptune end-to-end integration tests not yet in CI.
- **§4.5 Per-decision audit-back loop with integrity** — not implemented; sampled-review acting authority **[TBC]**.
- **§4.6 Canonical taxonomy DQ pass** — not evidenced in repo.
- **§4.7 Precedent edge integrity** — precedents created before the dense swap must be re-scored under embeddings (not done; no dense arm yet).
- **§4.8 Controlled rollout** — phased lift plan exists in `i5-lift-preconditions.md` §5 but is not active.
- **§4.9 Commercial / TPRM envelope** — SoW amendment, TPRM reclassification, OpRisk capital impact — not closed.
- **§4.10 LLM-in-the-loop classification** — borderline specialist (Opus 4.8) and any Path A verifier require Model Inventory registration + independent MRGR validation.
- **§4.11 Governance sign-off** — full §10 sign-off matrix unsatisfied.
- **§4.12 Path A verifier seam** — only required if Path A is chosen; not built.

This ARB approves the **architecture**, not the lift.

## 8. Carry-forward findings from the superseded `dense-arm-swap.md` v0.2 critical review

Eight themes from the adversarial review of the build-it-ourselves plan apply equally to the SDK adoption path:

1. **Fail-closed contract must be CODE not prose in `matching.py`.** Any condition that should abstain (specialist off-list, SDK timeout, missing dense score) must raise / route to review, not be documented as a "should".
2. **Match&Verify needs `bedrock:InvokeModel` IAM + eu-west-2 model-access toggle for Titan.** Same blocker as v0.2; restated as the BLOCKING finding in §5.1.
3. **Score normalisation [-1, 1] vs [0, 1] — verify against SDK output range.** The 0.80 floor was calibrated against Jaro-Winkler's character-similarity distribution; whatever range the SDK returns dictates a new floor — re-derived against the golden set, in the same change.
4. **Rollback model must gate the write side too.** Whatever feature flag wraps SDK adoption must be observable in Persistence's I5 gate, so a rollback flips both retrieval and write-side behaviour atomically.
5. **Reindex must use the shadow-property protocol; no whole-graph rebuild on live traffic.** The SDK's index lifecycle must support shadow-build → atomic alias swap; if not, we wrap it.
6. **Observability not wired anywhere — must be specified before flag flip.** Per-arm latency, hit rate, score distribution, rerank-vs-raw-dense gap, boost-scaling diagnostics, precedent-drop counts — all required for the §4.5 audit-back loop. None exist today.
7. **Persistence dependency: reviewer queue still in-memory list, separate workstream.** Restated as a Medium finding in §5.3; the DynamoDB wiring gap blocks any meaningful Phase 2 / 3 evidence per `i5-lift-preconditions.md` §5.
8. **SDK version must be pinned to a hash for the same preprocessing-policy property v0.2 derived.** Any SDK upgrade silently changes embedding model, tokeniser, or normalisation — invalidating calibration. Pin to a commit hash, gate upgrades behind the §4.10 model-change control.

## 9. What ARB is asked to do

Three explicit decisions:

1. **Approve** the three diagrams in `scudo_mapping_mcp/docs/architecture/` (`scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd`) as the SCUDO architecture **source-of-truth**, superseding `docs/diagram-1-main-flow.md` and `docs/diagram-2-falkor-internals.md` (already marked SUPERSEDED in this workflow).
2. **Acknowledge** the supersession of `docs/dense-arm-swap.md` (the build-it-ourselves plan) and **approve** `docs/dense-arm-sdk-adoption.md` being drafted as the replacement plan, to come back to ARB at a future session.
3. **Confirm** that the §5 consistency findings (one blocking, five high, five medium, four low) must be resolved — by diagram amendment (mark target-state) or code change — before the diagrams ship to wider distribution. In particular, the BLOCKING IAM / model-access / SDK-preprocessing items must be evidenced before the retrieval diagram is presented outside this forum as anything other than TARGET STATE.

## 10. Document control

- **Workflow run ID:** (filled by workflow runtime)
- **Date tabled:** 2026-06-10
- **Files touched in this workflow:**
  - `scudo_mapping_mcp/docs/architecture/scudo-overview.mmd` (+ `.txt`)
  - `scudo_mapping_mcp/docs/architecture/scudo-match-verify.mmd` (+ `.txt`)
  - `scudo_mapping_mcp/docs/architecture/scudo-retrieval.mmd` (+ `.txt`)
  - `scudo_mapping_mcp/docs/architecture/README.md`
  - `scudo_mapping_mcp/docs/diagram-1-main-flow.md` (marked SUPERSEDED)
  - `scudo_mapping_mcp/docs/diagram-2-falkor-internals.md` (marked SUPERSEDED)
  - `scudo_mapping_mcp/docs/dense-arm-swap.md` (marked SUPERSEDED-PENDING-REPLAN)
- **Validation findings preserved at:** §5 of this pack.
- **Forward references:**
  - `scudo_mapping_mcp/docs/i5-lift-preconditions.md` v0.2 — unchanged by this architecture decision; preconditions tracked in §7.
  - `scudo_mapping_mcp/docs/dense-arm-sdk-adoption.md` — to be drafted, replaces `dense-arm-swap.md`.

## 11. Decisions ratified

- **2026-06-10:** Three diagrams (`scudo-overview.mmd`, `scudo-match-verify.mmd`, `scudo-retrieval.mmd`) **APPROVED** as the SCUDO architecture source of truth (user sign-off). `docs/diagram-1-main-flow.md` and `docs/diagram-2-falkor-internals.md` confirmed SUPERSEDED.
- **2026-06-10:** Supersession of `docs/dense-arm-swap.md` **ACKNOWLEDGED**; `docs/dense-arm-sdk-adoption.md` **APPROVED in principle** as the replacement plan (see WS-E for the draft).
- **2026-06-10:** BLOCKING items in §5.1 (IAM grant for Titan model ARN / Bedrock model-access toggle in eu-west-2 / SDK preprocessing risk to the raw-dense floor anchor) **PARKED**. Park rationale and re-entry conditions live in `docs/dense-arm-sdk-adoption.md` §6.
- **2026-06-10:** MCP host **ADOPTED** as a SCUDO-owned visibility platform — NEW deliverable (see WS-C). Closes Open Question §6 "Confirm the MCP host component as a NEW deliverable to be owned by …".
- **2026-06-10:** Specialist-anchored-to-top-candidate invariant **ENFORCED IN CODE** (see WS-D). Closes the §5.3 medium finding "Specialist 'anchored to top candidate' is not enforced in code" and the matching Open Question in §6.

**End of document.**

## Related

- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
