# ARB Critical Review — SCUDO JPMC Port (`jpmc-port/`)

**Date:** 2026-07-19  
**Audience:** Architecture Review Board / critical reviewers  
**Status:** Ready for review — local dogfood + Playwright visual check complete  
**Package:** `/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/`  
**Reference system:** Capone `backend/scudo/` (left intact)

> **Reviewing agent — read first:** Capone Aurora deploy **does not match GitHub**. Aurora work is uncommitted on a stale `848f104` base and was tangled with a streaming refactor that must stay **dropped**. Full note: [`ARB_REVIEWER_NOTE_capone_aurora.md`](./ARB_REVIEWER_NOTE_capone_aurora.md). Do not confuse Capone deploy hygiene with jpmc-port evidence (A/B Capone arm = local `backend/scudo` via `python -P`, not the Aurora deploy).
>
> **Grok + Fable work summary:** [`ARB_SUMMARY_grok_fable.md`](./ARB_SUMMARY_grok_fable.md) (critique → fix map, live Opus evidence, Capone intent table, DDL resolution).

---

## Correction set (2026-07-20 — post-consolidation verification)

Independent re-verification confirmed the port is a **faithful, non-weakening** Capone agent-intelligence port and that A/B isolation / publish invariants / gate constants are solid. The following corrects over-frames and stale numbers in earlier pack text without silently rewriting history. Prefer this block over conflicting older lines.

| # | Correction | Was | Honest reading |
|---|------------|-----|----------------|
| C1 | **Live LLM reports** | “Real Opus 4.8” as if identity were in-artifact | Reports prove an **unstubbed, tool-using** agent loop (`SCUDO_AGENT_MODE=anthropic`; outcomes ≠ stub 0.92/20). `model` in JSON is the **requested** id; shim identity was **not** captured in older reports (`stub_forbidden` was decorative — renamed `stub_forbidden_decorative` and `echoed_model: null` added to the smoke body so it isn’t misread standalone). Treat as “live agent as-configured via shim,” not cryptographic Opus attestation. Newer smoke runner records probe `echoed_model` when available — **but no attached artifact yet demonstrates a captured `echoed_model` (all three reports carry `echoed_model_captured: false`); the capability is code-only until a run under a live shim is attached.** |
| C2 | **Pytest count** | Machine-readable claim “42 passed” | Suite is **46** after store-failure raise test (was 45 at consolidation; prose previously said 45). Older JSON/handoff said 42. |
| C3 | **Teach→learn “fail-loud”** | Implied tests prove loudness | Code path has **no swallow** around teach writes (inspection). Happy-path + sparse/vendor tests exist. A store-failure raise test is now in `test_learn_from_teaching.py`. Do not claim “tests alone proved loudness” for earlier pack dates. |
| C4 | **Weak assertions** | Over-read of e2e / port-arm outcome checks | Deterministic port-arm outcome ∈ {all outcomes} is a smoke shape check; e2e ≥0.80/≥16 is trivially met by stub 0.92/20. They prove wiring, not intelligence. |
| C5 | **Cosmetic (already footnoted)** | — | Dashboard SSE tool names are illustrative; `/health` reports configured Bedrock id in all modes. |

**Still solid (do not re-litigate):** Capone A/B imports `backend/scudo` under attack; publish four-way blocked; gates 16 / 12–15 / 0.80 and UI 0.80/0.70; no material README overclaim on wired surfaces; no dense/embedding arm claimed.

---

## 1. Executive summary

We delivered a **day-one JPMC entry package** that ports Capone’s SCUDO matching stack into a slim, typable surface **without weakening the agents**, and **with** the Understand-Anything matching dashboard ready to ship.

| Pillar | What shipped | Automated evidence provenance |
|--------|----------------|-------------------------------|
| **Agents (wiring)** | Mapping + Verifier: multi-turn `agent_loop`, tools, Opus 4.8 **pin** (`us.anthropic.claude-opus-4-8` / API `claude-opus-4-8`), `max_tokens=128000` | Instantiable; live **unstubbed** runs via `SCUDO_AGENT_MODE=anthropic` — see Correction C1 (shim identity not fully in-artifact on older reports) |
| **Learning** | Every decision calls `learn_from_teaching` with **no swallowed write errors** (teaching + rules + precedent + trajectory) | Happy-path + sparse/vendor + store-failure raise under `SCUDO_LOCAL` |
| **Comparability** | Capone vs port A/B (`python -P backend/scudo/scripts/ab_capone_arm.py`) | Deterministic A/B = **gate/schema parity** only; live A/B = `--mode anthropic` or `bedrock` |
| **UI** | Vendored matching dashboard at `/demo/` + Capone-shaped façade | Playwright under **deterministic** agents (stub confidence 0.92) |
| **Evidence (numbers)** | **46/46** pytest; deterministic e2e + A/B + Playwright | **All CI numbers are DeterministicMappingAgent** unless a live anthropic report is attached |

**Claimed:** coherent, typable stack; gates match; dashboard ships and was visually verified; Capone A/B harness imports the real Capone package when run with `-P`.  
**Not claimed from pytest alone:** Opus quality, multi-turn LLM behaviour, or Capone parity under live models.

---

## 2. Why this work existed

| Problem | Response |
|---------|----------|
| Capone `backend/scudo` is large and hard to type into JPMC by hand | New `jpmc-port/` folder; Capone untouched |
| Agents must carry system weight (not thin wrappers) | Agentic loops + tools on Mapping **and** Verifier |
| Token budget treated as unlimited | Opus 4.8 ceiling (`128k` out); prompts require thorough tool use |
| Model must be Opus 4.8 | Default `us.anthropic.claude-opus-4-8` (same Capone pin) |
| User teaching must not evaporate | `learn_from_teaching` on every decision |
| Port must be auditable vs Capone | Deterministic + Bedrock-ready A/B runner |
| Matching dashboard must ship with the port | Vendor `dashboard-dist/` + live API façade |

**Deliberately out of scope:** porting `build_matching_graph.py` (reuse Capone fixture in dist), legacy MatchPayload `GET /api/mapping/graph`, offline SkillOpt sleep/promote in Lambda, Azure provider, typing `local_state.py` / `agents_local.py` into JPMC prod.

---

## 3. What was delivered (inventory)

```
jpmc-port/
  scudo/                      # orchestrator, agents, agent_loop, memory, RDF, tools, …
  scudo/dashboard_api.py      # Capone-shaped vendors / SSE ingest / SSE agent / decision
  dashboard-dist/             # Understand-Anything matching SPA (base /demo/)
  tests/                      # 46 pytest cases
  fixtures/ab_golden.jsonl    # shared A/B cases
  run_local.py                # SPA + APIs on :5001
  run_e2e.py
  run_ab_compare.py
  ab_capone_arm.py            # retired stub (raises); real arm under backend/scudo/scripts/
  run_opus_smoke.py           # live unstubbed anthropic smoke (requested Opus pin / shim)
  scripts/sync_dashboard_dist.sh
  docs/ARB_REVIEW_jpmc-port.md
  docs/ARB_REVIEWER_NOTE_capone_aurora.md   # Capone Aurora ≠ GitHub (reviewer must-read)
  docs/playwright-demo-tour.png
  docs/OPUS_SMOKE_REPORT.json
  docs/OPUS_AB_REPORT.json
  docs/CURSOR_SMOKE_REPORT.json
  README.md
```

---

## 4. Architecture (how the pieces fit)

```text
Browser  →  /demo/  (vendored SPA)
         →  /api/mapping/vendors | ingest/stream | agent/run | decision
                    │
                    ▼
         dashboard_api façade  ──►  Orchestrator
                                      ├─ Mapping Specialist (agentic loop + tools)
                                      ├─ Verifier (agentic loop + investigative tools)
                                      └─ Python publish gate (0.80 / ≥16 / 12–15)
                    │
         decision ──► learn_from_teaching → precedents + rules + USER TEACHINGS
                    │
         CONSULT on next /run ← skill_hint + promoted_rules
```

**Invariant:** LLMs judge; Python routes and publish-gates. Agents never call publish.

---

## 5. Load-bearing agents

| Agent | Job | Loop | Tools | Model |
|-------|-----|------|-------|-------|
| **Mapping Specialist** | Vendor product → one CDAO node | Multi-turn → `MappingResult` | catalogue lookup, `graphrag_retrieve`, `neptune_*`, RDF, zone context | Opus 4.8 |
| **Verifier** | Score on 10-dim rubric (≤20) | Multi-turn → `VerifierReport` | investigative `neptune_*`, catalogue, `rdf_validate_shapes` — **no remap / no publish** | Opus 4.8 |

Supporting surfaces: rights specialist, catalogue fill (`POST /fill`), skills packs, hooks (reject raw SPARQL/Cypher, deny publish, read cap).

**Gate constants (aligned with Capone):** confidence floor **0.80**, verifier auto-publish **≥16**, retry **12–15**, UI bands passCut **0.80** / failCut **0.70**.

---

## 6. Teach → learn (hard rule)

Every dashboard or API decision (`approve` | `reject` | `override`→`correct`) calls `aurora_memory.learn_from_teaching` with **no try/except swallow** on the write path:

1. Teaching episode (always)  
2. Vendor rule for CONSULT (`prefer` / `avoid`)  
3. Precedent overwrite on approve/correct  
4. Trajectory when a mapping payload is present (sparse payloads default-fill `band`/`rationale`)  

Happy-path + sparse/vendor + injected store-failure raise are covered by tests.  
Next run injects `USER TEACHINGS` into `skill_hint` and rules into the mapping prompt.  
Offline SkillOpt full `LearningArtifact` promotion remains Capone-side (by design).

---

## 7. Matching dashboard (Understand-Anything)

| Item | Detail |
|------|--------|
| Source UI | Understand-Anything `packages/dashboard` (`build:matching`, `base:/demo/`) |
| Ship form | Copied Capone `dashboard-dist/` into `jpmc-port/dashboard-dist/` |
| Serve | `SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py` → `http://127.0.0.1:5001/demo/` |
| Live APIs | Capone-compatible façade (same contracts the SPA already calls) |
| Graph | Static `matching-graph.json` in dist (not MatchPayload); regen still via Capone builder |
| Refresh | `bash scripts/sync_dashboard_dist.sh` after Capone `infra/build_dashboard_dist.sh` |

Façade endpoints:

- `GET /api/mapping/vendors`  
- `POST /api/mapping/ingest/stream` (SSE ETL stages)  
- `POST /api/mapping/agent/run` (SSE → port Mapping + Verifier)  
- `POST /api/mapping/decision` (approve/override/reject → teach→learn)

---

## 8. Capone vs port A/B

| Arm | How it runs |
|-----|-------------|
| Capone | `python -P backend/scudo/scripts/ab_capone_arm.py` with `PYTHONPATH=backend` only (`-P` required so script-dir cannot shadow Capone) |
| Port | `run_port_arm` with `PYTHONPATH=jpmc-port` |

Same golden JSONL, **multi-candidate shortlists** (distractors + expected). `ontology_gap` is an intake fact in the golden row — **not** copied from `expected_abstain`. Modes: `deterministic` (CI), `anthropic` (live Opus via Messages API / shim), `bedrock` (native AWS).

**Deterministic A/B** proves gates/schema parity and that Capone `scudo.__file__` is under `backend/` — **not** Opus quality.

**Live Opus:** `run_opus_smoke.py` and `run_ab_compare.py --mode anthropic` (requires shim or Anthropic credentials).

---

## 9. Dogfood & Playwright evidence (2026-07-19)

| Check | Result | Provenance |
|-------|--------|------------|
| `pytest tests/ -q` | **46 passed** (incl. Capone `-P` import guard + teach trajectory + store-failure raise) | **Deterministic** (`SCUDO_LOCAL`) |
| `run_e2e.py` | `published` | **Deterministic** (stub 0.92/20 trivially clears gate thresholds) |
| A/B deterministic | Capone module under `backend/`; pairwise gates | **Deterministic** — not live LLM |
| `run_opus_smoke.py` | Unstubbed anthropic loop on `lseg-ibes-equity-research`: EquityResearch, conf **0.86**, verifier **12**, outcome **hitl** (Verifier caught fabricated precedent/conflict). Report: `docs/OPUS_SMOKE_REPORT.json` | Requested id `claude-opus-4-8` via shim — see **Correction C1** (not in-artifact Opus proof on older JSON) |
| A/B `--mode anthropic` | n=3, Capone module = `backend/scudo`, target/outcome agreement **1.0**, golden exact_match **0.5** (shared Pricing miss on ICE). Report: `docs/OPUS_AB_REPORT.json` | Same honesty boundary as C1; both arms unstubbed |
| `GET /health` | reports configured model id | Config pin, not a live call |
| Teach → learn API | `learned=true`, trajectory written, vendor case-normalized | Unit + local memory |
| Playwright `/demo/` | Tour / Run sample / Approve | **Deterministic** agents (0.92 is stub literal) |
| Screenshot | `docs/playwright-demo-tour.png` | UI only |

**Honest gaps:**

1. Native AWS Bedrock Opus A/B still needs IAM in the reviewer environment  
2. Golden set smoke-sized (n=3) — expand before cutover gate  
3. Prod Neptune / Aurora not exercised (`SCUDO_LOCAL` memory)  
4. SkillOpt offline promote still Capone-only  
5. Do **not** read “46/46 · A/B 3/3” as live multi-turn LLM evidence — those numbers never left the stub  
6. **Capone Aurora ≠ GitHub** (stale `848f104` base; uncommitted Aurora; streaming refactor deliberately dropped) — see `ARB_REVIEWER_NOTE_capone_aurora.md`  
7. Older pack lines saying “42 passed” or unqualified “Real Opus 4.8” are superseded by the **Correction set** above  

---

## 10. Decisions for ARB scrutiny

| # | Decision | Rationale | Trade-off |
|---|----------|-----------|-----------|
| 1 | Keep Capone; add port | JPMC typing risk; Capone workstreams continue | Dual maintenance until cutover |
| 2 | Orchestrator owns publish | Deterministic IRI / floor / graph integrity | Agents cannot “just publish” |
| 3 | Investigative Verifier | Tool-rich Mapping + tool-less Verifier is asymmetric | More tokens/turns (accepted) |
| 4 | Fail-loud teach→learn | Human corrections must stick | Full SkillOpt artifact promote stays offline |
| 5 | Vendor dashboard dist + façade | Ship UI without rewriting SPA | Graph regen stays Capone-side |
| 6 | A/B via subprocesses | Package name collision (`scudo`) | Heavier runner; correct isolation |

---

## 11. Capone vs port — do not confuse these

| Topic | Capone Lambda (typical today) | jpmc-port Bedrock path |
|-------|-------------------------------|-------------------------|
| Agent construction | Often tool-less in `lambda_handler` | Full tools + hooks + skills |
| Call style | Single-shot structured output | Multi-turn `agent_loop` |
| Verifier tools | None | Investigative set |
| Model | Opus 4.8 | Same default |
| Gates | 0.80 / 16 / 12–15 | Same |
| Teaching → memory | Mostly auto-publish precedent | **Every decision distills** |
| Matching UI | Capone Flask `/demo/` or CloudFront | Port `run_local.py` `/demo/` |
| Matcher MCP | Separate surface | Not the A/B twin |

---

## 12. Recommended ARB questions

1. Accept **agentic investigative Verifier** as JPMC baseline, or require Capone Lambda’s tool-less verifier for parity?  
2. Confirm teach→learn on every decision (no swallowed writes; SkillOpt remaining offline)?  
3. Confirm `jpmc-port/` as the **typing vehicle** into JPMC, Capone as R&D trunk until Bedrock A/B on a larger golden set?  
4. What **minimum golden-set size / policy** gates cutover?  
5. Is vendoring `dashboard-dist/` (vs rebuilding inside JPMC from Understand-Anything) acceptable for day-one ship?

---

## 13. How to reproduce

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker/jpmc-port
pip install -r requirements.txt

# Automated dogfood
SCUDO_LOCAL=1 PYTHONPATH=. python -m pytest tests/ -q
SCUDO_LOCAL=1 PYTHONPATH=. python run_e2e.py
SCUDO_LOCAL=1 python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode deterministic \
  --out /tmp/scudo-ab-arb

# Matching dashboard (Playwright / browser)
SCUDO_LOCAL=1 SCUDO_SERVE_DASHBOARD_DIST=1 python run_local.py
# open http://127.0.0.1:5001/demo/
# Run sample → Approve → Start Guided Tour

# Live unstubbed anthropic agent (Messages via local shim — not DeterministicMappingAgent)
# Model identity: requested pin; see Correction C1 / echoed_model on newer smoke runs
unset SCUDO_LOCAL
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_API_KEY="$(tr -d '[:space:]' < ~/.codex/shim-router/router.key)"
export SCUDO_ANTHROPIC_MODEL_ID=claude-opus-4-8
PYTHONPATH=. python run_opus_smoke.py --out /tmp/scudo-opus-smoke.json
python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode anthropic \
  --out /tmp/scudo-ab-opus

# Optional native AWS Bedrock (IAM credentials required)
unset SCUDO_LOCAL
python run_ab_compare.py \
  --golden fixtures/ab_golden.jsonl \
  --mode bedrock \
  --out /tmp/scudo-ab-bedrock
```

---

## 14. Verdict

**Ready for ARB critical review** on architecture, local (deterministic) evidence, Capone-import-correct A/B harness, and UI ship surface — with live **unstubbed** anthropic reports attached under Correction C1’s honesty boundary.

**In one sentence:** `jpmc-port` is a typable JPMC SCUDO stack with Opus-capable agentic Mapping + Verifier wiring, teach→learn without swallowed writes, a Capone A/B harness that actually imports Capone, and a vendored matching dashboard verified on `/demo/` — while CI numbers remain deterministic unless a live anthropic report is attached (shim model identity not fully in-artifact on older reports).

**Still required before any production cutover claim:** larger golden set under live LLM, reconciliation of Aurora/Neptune deploy realities, and Capone Aurora↔GitHub alignment per `ARB_REVIEWER_NOTE_capone_aurora.md`.
