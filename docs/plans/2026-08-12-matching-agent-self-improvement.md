# Matching Agent Self-Improvement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a protected automatic prompt/skill improvement loop shared by both SCUDO runtimes, then add deterministic SciPy taxonomy evidence and verify the complete offline path end to end.

**Architecture:** Keep `backend/scudo/matching_self_improvement.py` as the canonical contract and make `jpmc-port` consume it through a narrow adapter. Add content/policy/dataset integrity hashes, auto-publish precision, repeated-run stability, immutable automatic promotion with rollback, and a SciPy CSR taxonomy analyzer exposed as bounded read-only tools. Preserve every deterministic publish and confidence gate.

**Tech Stack:** Python 3.12, Pydantic 2, SciPy sparse/graph algorithms, pytest, existing Aurora/local-memory seams, Strands tool decorators.

---

### Task 1: Strengthen the canonical evaluation contract

**Files:**
- Modify: `backend/scudo/matching_self_improvement.py`
- Modify: `backend/scudo/tests/test_matching_self_improvement.py`

**Steps:**
1. Add failing tests for correct auto-publish precision, protected dataset and policy hashes, artifact content hashes, repeated-run stability, and forged-report rejection.
2. Run `PYTHONPATH=. pytest scudo/tests/test_matching_self_improvement.py -q` from `backend/`; expect the new tests to fail.
3. Add canonical JSON hashing, precision counters/rates, report integrity fields, and stability evidence.
4. Make `validate_promotion` reject hash mismatches, unstable reports, false auto-publishes, split mismatch, and non-improving candidates.
5. Re-run the focused tests; expect all to pass.

### Task 2: Replace the JPMC duplicate with the shared contract

**Files:**
- Modify: `jpmc-port/scudo/matching_self_improvement.py`
- Create: `jpmc-port/tests/test_shared_improvement_contract.py`

**Steps:**
1. Add a failing parity test proving the JPMC import exposes the canonical model fields and evaluation behavior.
2. Run `PYTHONPATH=. pytest tests/test_shared_improvement_contract.py -q` from `jpmc-port/`; expect failure against the current weaker duplicate.
3. Implement an explicit file-based shared-module adapter that works when only `jpmc-port` is on `PYTHONPATH`.
4. Re-run the parity test and existing JPMC tests.

### Task 3: Implement protected automatic promotion and rollback

**Files:**
- Modify: `backend/scudo/aurora_memory.py`
- Modify: `backend/scudo/skillopt_sleep_runner.py`
- Modify: `backend/scudo/tests/test_aurora_memory.py`
- Modify: `backend/scudo/tests/test_skillopt_sleep_runner.py`
- Modify: `jpmc-port/scudo/aurora_memory.py`
- Create: `jpmc-port/scudo/improvement_runner.py`
- Create: `jpmc-port/tests/test_automatic_promotion.py`

**Steps:**
1. Add failing tests for machine-gate approval, immutable artifact-first writes, expected-old live-pointer updates, rollback target retention, and rollback.
2. Add an `AutomaticPromotionApproval`/fixed gate identity without allowing ordinary callers to forge policy or dataset hashes.
3. Extend both memory adapters with immutable artifact, live pointer, previous pointer, and rollback operations.
4. Update the offline runner to compare active and candidate reports on the same protected cases before promotion.
5. Verify no runtime request path imports the offline optimizer.

**Implemented protected apply interface:** production apply uses
`run_protected_sleep_cycle`. Its evaluator returns `ProtectedEvaluation`
(`EvaluationReport` plus `TrustedEvaluationEvidence`); the runner issues the
content/identity-bound `EvaluationAttestation` and calls
`store.promote_protected_skill`. The legacy `run_sleep_cycle` /
`run_evaluated_sleep_cycle` paths are quarantine/dry compatibility paths and
cannot advance the signed live pointer. `run_sleep_cycle_job --apply` requires
distinct `SCUDO_EVALUATION_SIGNING_KEY` and `SCUDO_SKILL_PROMOTION_KEY`.

Operational authority is now asymmetric: the evaluator command owns
`SCUDO_EVALUATION_PRIVATE_KEY` and emits an Ed25519-signed
`SignedEvaluationEnvelope`; the promotion job receives only
`SCUDO_EVALUATION_PUBLIC_KEY`, `SCUDO_SKILL_PROMOTION_KEY`, and
`SCUDO_PROTECTED_EVALUATOR_COMMAND`.
That command must name an independently provisioned evaluator wrapper/service
which owns the private key and protected root. The promoter adapter forwards
neither secret and rejects direct invocation of the bundled
`scudo.scripts.protected_evaluator` module or `protected_evaluator.py`.

The evaluator request is an opaque strict slug resolved below
`SCUDO_PROTECTED_EVALUATION_ROOT`; evaluator-owned `index.json` pins each
bundle SHA-256. Each bundle supplies fixed holdout and adversarial evidence.
The promoter sends no labels, policy, or predictions. Optimization is provided
by `SCUDO_SKILL_OPTIMIZER_COMMAND` over JSON stdin/stdout. Scheduling remains an
external EventBridge/cron responsibility; no infrastructure template is
modified here.

Legacy best rows require an explicit operator migration with protected
re-evaluation; normal promotion fails closed on malformed legacy pointers.

### Task 4: Quarantine generalized teaching rules

**Files:**
- Modify: `jpmc-port/scudo/aurora_memory.py`
- Modify: `jpmc-port/tests/test_learn_from_teaching.py`

**Steps:**
1. Add a failing test proving an exact human precedent is immediately consulted while its generalized vendor rule is not active before promotion.
2. Store teaching-derived rules as candidate evidence instead of `memory_type="rule"`.
3. Preserve fail-loud teaching, precedent, episode, and trajectory writes.
4. Re-run teaching and memory suites.

### Task 5: Build the deterministic sparse taxonomy analyzer

**Files:**
- Create: `backend/scudo_mapping_mcp/taxonomy_graph.py`
- Create: `backend/scudo_mapping_mcp/tests/test_taxonomy_graph.py`
- Modify: `backend/requirements-local.txt`
- Modify: `backend/requirements.txt`
- Modify: `jpmc-port/requirements.txt`

**Steps:**
1. Add SciPy through the package manifests.
2. Write failing tests for deterministic IRI indexing, shortest paths, lowest common ancestor, branch ambiguity, components/orphans, cycles, asymmetric declarations, and hard node bounds.
3. Implement a CSR-backed immutable topology snapshot built only from `TaxonomyNode` records.
4. Ensure malformed topology yields explicit diagnostics and never changes candidate similarity.
5. Run the focused graph tests.

### Task 6: Expose graph evidence to both agent surfaces

**Files:**
- Modify: `backend/scudo_mapping_mcp/agent.py`
- Modify: `backend/scudo_mapping_mcp/match_verify_mcp.py`
- Modify: `backend/scudo_mapping_mcp/mcp_server.py`
- Modify: `jpmc-port/scudo/tools.py`
- Modify: `jpmc-port/scudo/sidecar/__init__.py`
- Create: `jpmc-port/scudo/taxonomy_graph.py`
- Create: `jpmc-port/tests/test_taxonomy_graph_tool.py`
- Modify: relevant MCP smoke tests

**Steps:**
1. Add failing contract tests for a bounded `analyse_taxonomy_candidates` tool on mapping and verifier tool lists.
2. Add a backend tool that builds from `get_store().list_taxonomy_nodes()`.
3. Add a JPMC adapter over its available authoritative/sidecar taxonomy snapshot.
4. Fix the existing `sidecar.candidate_nodes` export defect while touching the tool boundary.
5. Return structured evidence only; assert candidate confidence is unchanged.
6. Re-run MCP, tool, and JPMC agent tests.

### Task 7: Add shared offline end-to-end proof

**Files:**
- Create: `backend/scudo/tests/test_improvement_loop_e2e.py`
- Create: `jpmc-port/tests/test_improvement_loop_e2e.py`
- Create: `backend/scudo/fixtures/matching-improvement-golden.jsonl`

**Steps:**
1. Create clearly labelled illustrative train, holdout, and adversarial cases with no identity overlap.
2. Exercise baseline evaluation, a failing candidate, a passing candidate, protected promotion, consultation by both runtime adapters, graph shadow evidence, and rollback.
3. Assert the proposer never receives held-out labels and cannot mutate evaluator configuration.
4. Run both E2E tests without network or cloud credentials.

### Task 8: Full verification

**Steps:**
1. Run focused backend self-improvement, memory, sleep-runner, graph, MCP, and E2E tests.
2. Run `PYTHONPATH=. pytest tests/ -q` from `jpmc-port/`.
3. Run the relevant backend SCUDO suite from `backend/` and report pre-existing unrelated failures separately.
4. Run `python run_e2e.py` and the deterministic A/B harness from `jpmc-port/`.
5. Run IDE lint diagnostics on every edited Python file.
6. Inspect the final diff to ensure no confidence threshold, publish gate, canonical IRI rule, or unrelated dirty-worktree change moved.
7. If live Anthropic/Bedrock credentials are available, run one clearly labelled live held-out smoke; otherwise report deterministic E2E evidence honestly and do not claim model-quality improvement.
