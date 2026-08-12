# SCUDO LLM Call Model — TCO Costing Input

Status: complete draft, ready for review. All claims cite file:line.

## 1. Bedrock InvokeModel calls per product (by agent role)

Two separate runtimes call Bedrock; they are not the same call graph.

### Runtime-A: `backend/scudo/orchestrator.py` (Strands agents, deployed Lambda)

- Routing is deterministic Python, NO LLM call: `Orchestrator.route()` (`backend/scudo/orchestrator.py:102-110`).
- Mapping specialist: 1 InvokeModel call/product via `_call_mapping` (`orchestrator.py:191-203`) -> `_structured_call` (`orchestrator.py:179-189`). Agent = Strands `Agent` over `BedrockModel` built in `backend/scudo/lambda_handler.py:411-423`.
- Verifier (10-dim rubric): 1 InvokeModel call/product via `_call_verifier` (`orchestrator.py:237-261`), separate `BedrockModel` instance (`lambda_handler.py:412,424-432`).
- Retry: verifier `total_score` in `[VERIFIER_RETRY_LO=12, VERIFIER_RETRY_HI=15]` (`orchestrator.py:40,344-355`) -> `Outcome.RETRY`; caller re-invokes `run()` once with `prior_rejection` (`orchestrator.py:118-122`), doubling both mapping+verifier calls (2+2) for that product before HITL. No second retry.
- Rights specialist: `build_rights_specialist` exists (`backend/scudo/agents.py:57-76`) but `Orchestrator.run()` never calls `self.rights`; `lambda_handler.py:581` wires `rights_specialist=None`. **0 calls — dead capability.**
- RESEARCH route (`ontology_gap=True`): `_handle_research` (`orchestrator.py:397-413`) calls `self.mapping(research_prompt(bundle))` directly (unstructured) — 1 mapping-agent call, replacing the normal mapping+verifier pair; no verifier, no publish.

Runtime-A per-product LLM call count: **2 (normal path: mapping + verifier), 4 (one retry), or 1 (RESEARCH route), 0 for rights.** No separate "narration/reasoning panel" call exists in this file — see Runtime-B below for the agent narration surface.

### Runtime-B: `backend/scudo_mapping_mcp/` (the real cost-ladder matcher)

- `matching.map_vendor_product` is the authoritative decision path (`backend/scudo_mapping_mcp/matching.py:159-589`). It calls an LLM in exactly two places, both conditional:
  - Dense arm (Rung 3), `store/falkordb_store.py:470-513`: when `SCUDO_DENSE_BACKEND=opus`, calls `opus_dense_score()` once per surviving taxonomy node (i.e. per candidate scored, not per product) — this is NOT gated to borderline only, it runs for every product whose retrieval reaches this loop.
  - Specialist (Rung 4), only for BORDERLINE-band products, `matching.py:373` calls `specialist(ref, candidates)`. The default REST specialist (`specialist.py:41-61 _best_pick`) calls `opus_dense_score()` once per candidate in the anchored candidate set (typically <=8, `max_candidates` default in `matcher_bridge.py:88` / `matching.py:161`).
  - Both call sites funnel into the SAME function `opus_dense.opus_dense_score()` (`backend/scudo_mapping_mcp/opus_dense.py:70-131`), which itself re-reads `SCUDO_DENSE_BACKEND` (`opus_dense.py:92`) and only issues a real `bedrock-runtime.invoke_model` (`opus_dense.py:238-243`) when that value is `"opus"`.
- Agent narration (`backend/scudo_mapping_mcp/agent.py`): `BedrockMappingAgent` (`agent.py:392-514`) wraps a Strands `Agent` with 4 MCP tools and system prompt stating "Three or four calls is typical" (`agent.py:420-423`) — each tool-call turn is a separate Bedrock InvokeModel (Strands tool-use loop), so **3-4 InvokeModel calls per product** for this path alone, PLUS the deterministic matcher call it triggers as its final tool (`map_vendor_product` tool, not an LLM call itself). This agent only runs when `SCUDO_AGENT_BACKEND=bedrock` or the `agent_provider`/`agent_backend` request field forces it (`agent.py:34-56`); it is a recommend-only narration layer — matcher result stays authoritative per the CONTRACT (`agent.py:12-26`).
- `ScriptedMappingAgent` (`agent.py:129-171`) is the default/no-AWS narration backend — 0 LLM calls, fake reasoning text.

Runtime-B per-product LLM call count: **0 by default** (`DenseBackend=jaro_winkler`, no `SpecialistBackend` -> Rung 3 and Rung 4 both take the deterministic branch). With `DenseBackend=opus`: N calls at Rung 3 (N = candidate taxonomy nodes scored, can be large — see §2). With a borderline specialist wired AND `SCUDO_DENSE_BACKEND=opus`: additional calls, one per anchored candidate (<=8), for BORDERLINE-band products only. Agent narration (separate optional surface): 3-4 calls/product when `SCUDO_AGENT_BACKEND=bedrock`.

## 2. Confidence-gate reachability (0.80 / 0.70 bands)

Two independent gate implementations exist; both currently use floor=0.80 (Runtime-A) or the mapping_mcp `CONFIDENCE_FLOOR` default (Runtime-B — see caveat below).

### Runtime-A gate (`backend/scudo/orchestrator.py`)
- `CONFIDENCE_FLOOR = 0.80` (`orchestrator.py:41`), `VERIFIER_RETRY_LO, VERIFIER_RETRY_HI = 12, 15` (`orchestrator.py:40`), `VERIFIER_AUTOPUBLISH = 16` (`orchestrator.py:39`).
- `_gate_and_decide` (`orchestrator.py:309-382`): verifier `total < 12` OR `confidence < 0.80` OR self-flagged -> HITL (`orchestrator.py:320-342`). `total` in `[12,15]` -> RETRY once (`orchestrator.py:344-355`). `total >= 16` AND `confidence >= 0.80` -> PUBLISH (`orchestrator.py:357-382`).
- Every product that reaches this gate has ALREADY had 2 LLM calls (mapping + verifier) — the gate decides publish/HITL/retry AFTER the LLM ran, not whether the LLM runs. In Runtime-A, 100% of non-RESEARCH-routed products reach the LLM; the gate only affects the AFTER-the-fact publish decision, never LLM reachability.

### Runtime-B gate — this is where the 0.70/0.80 "fail/pass" bands actually gate LLM reachability
- Config default `CONFIDENCE_FLOOR = 0.75` in code (`backend/scudo_mapping_mcp/config.py:47`), override via `CONFIDENCE_FLOOR` env (`config.py:301-303`). NB: this differs from the CLAUDE.md-stated 0.80/0.70 product contract — the code constant is 0.75/0.05 by default, i.e. pass=0.80, borderline-floor=0.70 (see `pass_threshold`/`borderline_threshold`, `config.py:55-72`), which numerically lands on the same 0.80/0.70 edges the project uses when `BORDERLINE_HALF_WIDTH=0.05` (the default, `config.py:52`).
- Bands (`matching.py:341-534`):
  - **PASS** (`similarity >= pass_threshold=0.80`, `matching.py:357-369`): AUTO_MAPPED, **specialist NOT consulted** ("paying the LLM here would be pure cost with no upside", `matching.py:358-360`).
  - **FAIL** (`similarity < borderline_threshold=0.70`, `matching.py:520-534`): NEEDS_REVIEW, **specialist NOT consulted** ("the LLM only runs on cases it can plausibly resolve", `matching.py:521-523`).
  - **BORDERLINE** (`0.70 <= similarity < 0.80`, `matching.py:370-519`): the ONLY band that ever calls `specialist(...)` (`matching.py:373`).
  - Required-validation failure (`req_fails`, `matching.py:342-356`) also skips the specialist regardless of band.
- **Conclusion: only products whose deterministic dense/lexical similarity lands in the 10-point BORDERLINE window [0.70, 0.80) ever reach an LLM call in Runtime-B's authoritative path** — narrower than a simple "below 0.80" cut, since FAIL cases are excluded too.

### What fraction of products reach the LLM

(a) **Current wiring (deployed defaults, verified live in `infra/scudo-poc-app.yaml:76-79,86-89` — `DenseBackend=jaro_winkler` default, `SpecialistBackend=""` default which falls through to `local`/`make_rest_specialist` per `specialist.py:202-205`):**
- Rung 3 dense score is Jaro-Winkler (deterministic string similarity), never Bedrock — `store/falkordb_store.py:470,499-513` takes the `else` (non-opus) branch.
- Rung 4: BORDERLINE-band products (dense score in [0.70,0.80)) DO invoke `specialist(...)` -> `_best_pick` -> `opus_dense_score()` (`specialist.py:52`) -> but `opus_dense_score()` re-checks `SCUDO_DENSE_BACKEND` itself (`opus_dense.py:92`) and, since that's still `jaro_winkler`, takes the deterministic branch (`opus_dense.py:94-102`) — **no Bedrock invoke_model call actually fires**, even though the code path that WOULD call it is live.
- **Fraction reaching an actual LLM invoke in Runtime-B today: 0%**, regardless of what fraction lands in BORDERLINE. This matches project memory's four-gate finding (`matching-reasoning-gap-closure.md`): "only gate 3 reaches opus_dense_score() ... lands on Jaro-Winkler."
- Runtime-A (Strands orchestrator) is a separate, always-on-LLM path: 100% of non-RESEARCH products get 2 LLM calls, independent of Runtime-B's bands (it doesn't consult the cost-ladder gate at all — `Orchestrator.run()`, `orchestrator.py:113-151`, has no dependency on `matching.py`).
- BedrockMappingAgent narration (`agent.py:392`) is a third, independent, request-triggered path (`SCUDO_AGENT_BACKEND=bedrock` or explicit `agent_provider`) — 100% of the products routed to it get 3-4 LLM calls, also independent of the cost-ladder bands.

(b) **Intended agentic loop (flip `DenseBackend=opus` and wire a `SpecialistBackend`):**
- Rung 3 becomes N calls/product (N = taxonomy nodes scored — small in the demo fixture, 14 nodes in `backend/scudo/fixtures/cdao_catalogue.json`, `@graph` array length verified directly) if `SCUDO_USE_OPUS_DENSE` is also set to route through `retrieval.multi_path_retrieve`, OR N calls straight from the `else` inline loop at `falkordb_store.py:477-498` if just `SCUDO_DENSE_BACKEND=opus` — both paths score EVERY surviving node, not just the top candidate, so 100% of products that reach Rung 3 (i.e. didn't get scope-denied or precedent-hit) generate N LLM calls, independent of band.
- With `SpecialistBackend` also wired, BORDERLINE-band products (the [0.70,0.80) window) get an ADDITIONAL <=8 calls (one per anchored candidate, `specialist.py:41-61`).
- So under the intended full-agentic wiring, LLM reachability is no longer "borderline-only" — the Rung-3 dense-scoring loop alone would call the LLM for essentially every in-scope product (not gated by the confidence bands at all), which is a materially different cost shape from the "only borderline reaches the LLM" design intent stated in the `matching.py` module docstring (`matching.py:10-20`).

## 3. Token estimates per call type

Method: rendered the actual prompt templates against a representative BriefBundle/MappingResult built from the real schemas and a realistic vendor-product payload, measured directly via Python `model_dump_json()`/`len()` — exact character counts, not guesses. Converted chars to tokens using 3.3-4.5 chars/token (JSON's punctuation density pushes real usage toward more tokens per char than prose).

Measured character counts (this session):
- `MAPPING_SYSTEM` = 435 chars (`backend/scudo/prompts.py:13-21`); `VERIFIER_SYSTEM` = 271 chars (`prompts.py:30-35`).
- `mapping_prompt(bundle)` rendered with 8 CandidateNodes = 2655 chars (`prompts.py:38-70`); `BriefBundle.model_dump_json()` alone = 1573 chars for the same bundle.
- `verifier_prompt(result, ...)` rendered = 1434 chars (`prompts.py:83-104`), embedding `MappingResult.model_dump_json()` = 694 chars.
- `VerifierReport.model_dump_json()` (all-2s, no defects) = 625 chars.
- `opus_dense._OPUS_SYSTEM_PROMPT` = 1687 chars (`backend/scudo_mapping_mcp/opus_dense.py:159-191`); output capped at `max_tokens=256` in the Bedrock request body (`opus_dense.py:230`).
- `BedrockMappingAgent.SYSTEM_PROMPT` = 2082 chars (`backend/scudo_mapping_mcp/agent.py:408-440`).

`BriefBundle.candidates` allows up to 25 entries (`backend/scudo/schemas.py:97`); the 8-candidate sample above is mid-range, not the ceiling — HIGH below assumes ~20 candidates.

| Call type | Input tok LOW | Input tok LIKELY | Input tok HIGH | Output tok LOW | Output tok LIKELY | Output tok HIGH |
|---|---|---|---|---|---|---|
| Runtime-A mapping specialist (`orchestrator.py:191-203`, system+prompt) | 554 (3 candidates) | 690-940 (8 candidates) | 1175-1600 (25 candidates) | 150 (bare MappingResult) | 210-270 (rationale+1-2 evidence) | 360+ (long rationale/multiple evidence) |
| Runtime-A verifier, 10-dim rubric (`orchestrator.py:237-261`) | 380 | 380-520 | 520-650 (with defects_pre appended, `orchestrator.py:245-250`) | 140 | 190-270 (10 scores + notes + defects) | 350+ (many defects listed) |
| Runtime-B `opus_dense_score` per candidate pair (`opus_dense.py:70-131,194-260`) | 430 | 430-590 | 590-700 (longer descriptions if `SCUDO_TAXONOMY_UML_TEXT`/`_TEXT` flags add taxonomy definitions, `opus_dense.py:307-337`) | ~50 (score+short reason, hard-capped) | 60-100 | 256 (hard `max_tokens` ceiling, `opus_dense.py:230`) |
| Runtime-B `BedrockMappingAgent` narration, per tool-use turn (`agent.py:392-514`, "3-4 calls typical") | 530 (turn 1, system+product only) | 700-970 (mid turns, accumulating tool-call results in context) | 970-1300+ (turn 3-4, full tool-result history in context) | 40-80 (tool-call directive) | 80-150 | 150-300 (final rationale paragraph, `agent.py:424-426`) |

Narrative:
- **Runtime-A per-product cost floor is 2 calls** (mapping + verifier): LIKELY ~690+380=1070 input tok / ~210+190=400 output tok combined, before any retry.
- **Retry doubles it**: LIKELY ~2140 input / ~800 output tok for a product that lands in the verifier retry band (`VERIFIER_RETRY_LO..HI = 12..15`, `orchestrator.py:40`).
- **Runtime-B's opus_dense per-pair cost is the multiplier risk**: under the intended agentic wiring (`SCUDO_DENSE_BACKEND=opus`), this fires once per surviving taxonomy node at Rung 3 (`store/falkordb_store.py:477-498`) — for a catalogue of size K, that's K calls at ~430-590 input tok / ~60-100 output tok EACH, per product, dwarfing the Runtime-A per-product cost once K exceeds ~3-4 nodes. The demo fixture's taxonomy (`backend/scudo/fixtures/cdao_catalogue.json`, `@graph` array) has 14 nodes — i.e. 14 opus_dense calls per product if this path is live, on top of anything else.
- **Agent narration (BedrockMappingAgent) is the most expensive single path per product it touches**: 3-4 turns x ~700-1300 growing input tok (multi-turn context accumulates prior tool results) = roughly 3000-4500 input tok and 300-600 output tok per product, independent of the cost-ladder bands (see §2) and only incurred when `SCUDO_AGENT_BACKEND=bedrock` or `agent_provider` forces it.

## 4. Model IDs configured (env vars / code defaults)

| Surface | Env var | Code default | Citation |
|---|---|---|---|
| Runtime-A Lambda mapping+verifier `BedrockModel` | `BEDROCK_LLM_MODEL_ID` | `us.anthropic.claude-opus-4-8` | `backend/scudo/shared/bedrock.py:26,43-44`; consumed via `bedrock_llm_id()` at `backend/scudo/lambda_handler.py:408,410-412` |
| Runtime-A embeddings (not LLM, for completeness) | `BEDROCK_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2:0` | `backend/scudo/shared/bedrock.py:27,47-48` |
| Runtime-B dense scorer + `BedrockMappingAgent` narration | `SCUDO_BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-4-8` | `backend/scudo_mapping_mcp/opus_dense.py:64,210`; `backend/scudo_mapping_mcp/agent.py:40,451` |
| Region, Runtime-A | `AWS_REGION` | `us-east-1` | `backend/scudo/shared/bedrock.py:19,39-40` |
| Region, Runtime-B | `AWS_REGION` / `AWS_DEFAULT_REGION` | `eu-west-2` | `backend/scudo_mapping_mcp/opus_dense.py:211`; `agent.py:453-458` |
| Deploy-time override (CFN param) | `BedrockModelId` (`infra/scudo-poc-app.yaml:73-74`) | maps to `SCUDO_BEDROCK_MODEL_ID` env in the ECS task def | `infra/scudo-poc-app.yaml:73-74` |

Two DIFFERENT env vars select the model in the two runtimes (`BEDROCK_LLM_MODEL_ID` vs `SCUDO_BEDROCK_MODEL_ID`) — they are not the same knob and both default to Opus 4.8, just via different cross-region inference profiles (`us.` vs `eu.` prefix) reflecting Runtime-A's us-east-1 deployment vs Runtime-B's eu-west-2 deployment. `shared/bedrock.py:20-25` explicitly notes Opus 4.8 is "the strongest available Claude on Bedrock" chosen for the Mapping Specialist and flags Sonnet 4.6 as a ~5x-cheaper swap option, not yet made.
