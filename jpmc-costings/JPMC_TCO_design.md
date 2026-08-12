# JPMC SCUDO TCO Workbook — Design Specification

Target file: `jpmc-costings/JPMC_SCUDO_POAP_TCO_v1.xlsx`
Design date: 2026-07-22. Currency: **USD** throughout (no GBP anywhere). Region: **us-east-1**.
Mechanisms inherited from `SEGRO_structure_spec.md` — but ZERO SEGRO/HCL/Azure/GBP content may appear.

## User-locked decisions

1. **Volume model**: 10 vendor deliveries/month x ~989 products = **9,890 items/month**. LLM calls scale per-product, gated by confidence bands.
2. **Build cost**: `Points x PointRate`, PointRate an adjustable $/point input **defaulting to 0** (RATE_MISSING convention — visible zero = "un-costed, not free").
3. **Horizon**: **Monthly + 12 months only** — no multi-year roll-up.
4. **Model SKU default**: Opus 4.8 on all four agent lines with a live ModelSKU toggle (Opus 4.8 / Sonnet 5 / Haiku 4.5). Q4 answer was not captured; this is the recommended default, flagged to user.
5. **Timeline**: kickoff w/c 2026-07-27 -> go-live 2026-11-01 = **14-week nominal core** (SEGRO used 29). Focus is TCO, not build.

## Sheet plan (tab order)

| # | Sheet | Purpose |
|---|-------|---------|
| 1 | Overview | Headline monthly TCO + 12-mo total, scenario banner, waterfall (base/block helpers, spec 4h), top cost drivers |
| 2 | Control Panel >>> | Scenario inputs, amber FFFFF2CC fill, named ranges, validations |
| 3 | Rates | USD us-east-1 unit prices, one row per meter, Status + Source/Basis columns (from research workflow, verifier-corrected) |
| 4 | Pod Build | Client's table shape: Pod / Short Title / AWS Components / Points / Run freq per mo / Est data vol GB / Input tokens K / Output tokens K / Cost estimate $ monthly + Points x PointRate build column |
| 5 | Monthly TCO | Per-component run-cost lines, qty x Rates lookup, grouped by the 5 zones |
| 6 | 12mo Run Cost | Months 1-12 columns (Nov 2026 - Oct 2027), ramp profile, =SUM row per component group |
| 7 | Gantt | 14-week timeline, SEGRO bar mechanism (cell IF + ;;; format + CF fill), SlipWeeks-aware, milestone glyphs |
| 8 | Assumptions | Every ASSUMPTION/RATE_MISSING line restated with basis + how to correct |

## Control Panel inputs (named ranges, amber fill, validations)

| Name | Default | Validation | Drives |
|------|---------|-----------|--------|
| DeliveriesPerMonth | 10 | decimal >=0 | volume model |
| ProductsPerDelivery | 989 | decimal >=1 | ItemsPerMonth = Deliveries x Products |
| DatasetCount | 5000 | decimal >=1 | catalogue size, storage lines |
| LlmReachablePct | 1.0 | decimal 0-1 | fraction of items reaching the agent loop. CODE-VERIFIED default: the deployed Strands orchestrator invokes specialist+verifier per product UNCONDITIONALLY (not band-gated) — so 1.0, DERIVED. Lower it to model a band-gated future |
| ModelSKU | Opus-4.8 | list: Opus-4.8,Sonnet-5,Haiku-4.5 | token rate selection via SKU gate (spec 4g pattern, vendor fixed = Bedrock) |
| InputTokensPerCallK | 0.8 | decimal >=0 | per-call input tokens (thousands). CODE-VERIFIED LIKELY ~0.69-0.94K (specialist), ~0.38-0.52K (verifier) — see llm_call_model.md. Client sketch said 50K; carried as the HIGH scenario in a scenario row, not the default |
| OutputTokensPerCallK | 0.25 | decimal >=0 | per-call output tokens (thousands). CODE-VERIFIED LIKELY ~0.21-0.27K |
| CallsPerItem | 2 | decimal >=0 | Bedrock calls per item. CODE-VERIFIED: 2 (mapping specialist + verifier). The dense-arm per-candidate opus_dense_score is OFF by default (DenseBackend=jaro_winkler, infra/scudo-poc-app.yaml:76-79); if flipped to opus it fires PER TAXONOMY NODE scored — carry as an explicit optional line with its own multiplier, default 0 |
| PointRate | 0 | decimal >=0 | $ per build point, DEFAULT 0 = RATE_MISSING visible zero |
| EnvMultiplier | 1 | list: 1,2,3 | non-prod environment multiplier on always-on infra |
| AuroraSize | 2xlarge | list: large,xlarge,2xlarge | Aurora instance class selector (client sketch says 2xLarge) |
| HitlPct | 0.15 | decimal 0-1 | fraction of items routed to HITL review (AppSync traffic) |
| StoragePerItemKB | 50 | decimal >=0 | canonical JSON-LD per product (refine from arch report) |
| SlipWeeks | 0 | decimal >=0 | Gantt stretch (14-week core, spec 5b with 29->14) |
| ContingencyPct | 0.15 | decimal >=0 | uplift on monthly TCO subtotal |

Derived named cells: ItemsPerMonth (=DeliveriesPerMonth*ProductsPerDelivery), LlmItems (=ItemsPerMonth*LlmReachablePct), MonthlyTCO, Run12mo, TotalBuildPoints, TotalBuildCost.

## Token cost formula pattern (per agent line, spec 4g SKU gate)

cost = LlmItems * CallsPerItem_line * ( InputTokensPerCallK/1000 * IF(ModelSKU="Opus-4.8",RateOpusIn,IF(ModelSKU="Sonnet-5",RateSonnetIn,RateHaikuIn)) + OutputTokensPerCallK/1000 * <same for output> )

### Token scenario framing (three visible scenario rows on Monthly TCO)

- CODE-VERIFIED LIKELY (default): 2 calls/item, ~0.8K in / 0.25K out per call -> at 9,890 items/mo on Opus 4.8 this is ~19.8K calls, ~15.8M in + ~4.9M out tokens = roughly $79 + $124 = ~$203/mo. Cheap — state it plainly.
- CLIENT-SKETCH HIGH: 4 calls/item at 50K in / 1K out (the user's original table numbers) -> ~$10.9K/mo input alone. Carried as a labelled scenario row so JPMC sees both framings.
- DENSE-ARM OPTIONAL: opus_dense_score per taxonomy node if SCUDO_DENSE_BACKEND=opus is enabled (~0.5K in / 0.08K out x nodes-scored x items) — default 0 (off, matches deployed config), amber multiplier cell.

AgentCore Memory lines (client wants this priced): short-term events = LlmItems x events-per-item (default 2) x $0.25/1k; long-term records + retrievals as separate lines with amber quantity defaults, all VERIFIED rates from rates.json.

Rates in $/MTok from Rates sheet; each agent line (Strands orchestrator, AgentCore Memory, Specialist, Verifier) carries its own CallsPerItem share and token overrides where the arch report justifies them.

## Ramp profile (12mo Run Cost)

Month 1 = Nov 2026 (go-live). Ramp row: M1 50%, M2 75%, M3+ 100% of steady-state volume (editable amber cells). Always-on infra (Aurora, OpenSearch, ECS, NAT, ALB, endpoints) does NOT ramp — only volume-driven meters do.

## Provenance discipline

Every Rates row: Status in {VERIFIED, INDICATIVE, ASSUMPTION, DERIVED, RATE_MISSING} + Basis/Source column citing URL + as-of date. RATE_MISSING lines carried at $0 with explicit note. Status text bold red FFCC171E, no fill (SEGRO convention). Zeros display as "-" via number format "$#,##0;($#,##0);-". Blue font = hardcoded input, black = formula, green = cross-sheet link.

## Gantt (14 weeks)

Week 1 = w/c 2026-07-27 ... week 14 = w/c 2026-10-26 (go-live 2026-11-01). Parent rows use spec 5b formulas with core span 14: Live start =MIN(18,IF(G<=14,ROUND((G-1)*(14+SlipWeeks)/14,0)+1,G+SlipWeeks)). Child rows use the unchanged MEDIAN interpolation. Bars: cell =IF(AND(col$4>=$I<r>,col$4<=$J<r>),1,"") + ;;; format + CF expression fill (single colour FF2E7D32). Month labels row 3 at Jul/Aug/Sep/Oct/Nov boundaries. Milestone glyphs for kickoff, infra-ready, first delivery E2E, HITL live, go-live 2026-11-01. Pod-based lanes (from client image) instead of SEGRO phases; JPMC's larger resourcing = pods run in parallel.

## Pod Build sheet rows

Exactly the client's sketch rows (API GW entry+routing, Lambda authorizer, Ingestion Coordinator, Sanity Check, CloudWatch, S3 raw landing, EventBridge Scheduler, EventBridge ObjectCreated, SQS, ETL worker VPC, S3 canonical, S3 quarantine, AIA Matching Engine A/B/C, Titan Embeddings, OpenSearch fuzzy+kNN, Aurora 2xlarge, Strands Orchestrator, AgentCore Memory, Specialist agent, Verifier agent 10-dim, Bedrock Evaluations, JAPI Persist outbox, HITL AppSync, ECS) PLUS repo-real components the sketch misses (from arch report: e.g. FalkorDB ECS / Neptune option, CloudFront, ALB, NAT, Secrets Manager, VPC endpoints, CodeBuild) added as clearly-marked extra rows. Each row: Points (client's where given, ASSUMPTION where not), run freq, data vol, tokens, monthly cost formula -> cross-links to Monthly TCO lines.

## Build phases for Gantt lanes (pods, not SEGRO P1-P6)

Pod A Ingestion (API GW -> S3 -> SQS -> ETL), Pod B Matching (engine + Titan + OpenSearch), Pod C Agentic (Strands + AgentCore + specialist/verifier + evals), Pod D Persistence/HITL (Aurora + outbox + AppSync + ECS), Pod E Platform (VPC, CloudWatch, security, CI/CD). 14 weeks, pods parallel after a 2-week foundation.
