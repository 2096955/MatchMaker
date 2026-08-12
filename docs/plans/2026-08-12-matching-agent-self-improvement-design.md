# Matching Agent Self-Improvement and Graph Evidence Design

**Status:** Approved 2026-08-12

## Goal

Make both SCUDO matching runtimes measurably better over repeated runs without
allowing the optimizer to weaken the definition of quality. Establish the
evaluation and promotion boundary first, then add deterministic sparse graph
evidence and measure it in shadow mode. A GNN remains out of scope until the
repository has a production-scale, leakage-safe labelled corpus.

## Scope and invariants

The shared implementation serves both `backend/scudo/` and `jpmc-port/scudo/`.
The optimizer may automatically promote only versioned prompt and matching-skill
artifacts. It may never change golden cases, split membership, metric
definitions, thresholds, deterministic publish gates, confidence bands, or
rollback rules.

Load-bearing contracts remain:

- correct auto-publish precision is the primary quality target;
- false auto-publishes may not increase;
- ontology-gap abstention recall, exact-match accuracy, and calibration may not
  regress;
- the pass floor remains `0.80`;
- agents never publish or author raw graph queries;
- canonical vendor and CDAO IRIs remain deterministic;
- the evaluator uses protected held-out and adversarial cases;
- graph evidence is read-only and cannot directly change dense confidence.

## Architecture

### Shared evaluation contract

`backend/scudo/matching_self_improvement.py` remains the canonical contract.
The JPMC runtime loads that contract through an explicit shared-module adapter
instead of retaining a weaker duplicate.

The evaluator records:

- exact target accuracy;
- correct auto-publish precision;
- false-auto-pass count and rate;
- abstention recall;
- coverage;
- calibration MAE and Brier score;
- case, dataset, metric-policy, and artifact hashes;
- repeated-run stability.

Promotion compares the candidate with the active artifact on the same protected
held-out cases. A candidate must pass the fixed policy, have zero false
auto-publishes under the production policy, and strictly improve at least one
load-bearing metric without regressing any other.

### Protected automatic promotion

The protected evaluator is a separate process and signing authority. It owns
only an Ed25519 private key (`SCUDO_EVALUATION_PRIVATE_KEY`) and emits a
`SignedEvaluationEnvelope` over JSON. The scheduler/promoter is configured with
`SCUDO_EVALUATION_PUBLIC_KEY`, `SCUDO_SKILL_PROMOTION_KEY`, and
`SCUDO_PROTECTED_EVALUATOR_COMMAND`; it cannot mint evaluator signatures.
The evaluator command receives only candidate identity/content plus a strict
`evaluation_request_id`. It resolves evaluator-controlled, hash-allowlisted
holdout and adversarial bundles beneath `SCUDO_PROTECTED_EVALUATION_ROOT`;
ordinary trajectories cannot supply labels, policies, or predictions.
`SCUDO_PROTECTED_EVALUATOR_COMMAND` must target an independently provisioned
wrapper or service that owns the private key and protected root. The promoter
adapter deliberately forwards neither secret and rejects commands that directly
invoke the repository's bundled `scudo.scripts.protected_evaluator` module or
`protected_evaluator.py` path.
Candidate optimization remains separately configurable and does not receive
protected labels. Deterministic fixture evaluator commands prove orchestration
only, not model quality.

Scheduling is external: invoke
`python -m scudo.scripts.run_sleep_cycle_job --apply` from EventBridge/cron with
the documented command/public-key/request-ID configuration. No scheduler
resource is deployed by this plan.

Each candidate is immutable and content-addressed. The promotion record includes
its parent version, artifact content hash, dataset hash, policy hash, evaluation
report, source trajectories, and a machine approval that identifies the
protected automatic gate. Promotion first writes the immutable artifact and
then atomically advances the live pointer. The prior pointer is retained as the
rollback target.

The proposer receives training trajectories and failure reasons, but never the
held-out labels or writable access to evaluator configuration. Human teachings
remain immediately useful as exact precedents, but generalized rules are
quarantined as candidates until the protected evaluator promotes them.

### Sparse graph evidence

A shared `taxonomy_graph.py` module constructs a deterministic sparse adjacency
matrix from `RetrievalStore.list_taxonomy_nodes()`. It uses SciPy CSR arrays and
does not depend on backend-specific graph traversal.

The bounded analyzer returns:

- shortest path distance;
- lowest common ancestor;
- structural separation of leading candidates;
- local degree and branch ambiguity;
- connected-component and orphan status;
- cycle/asymmetric-edge diagnostics;
- optional precedent-seeded affinity.

The output is structured evidence. Mapping and verifier agents may inspect it,
and the evaluator records whether it was available and used. Initially it runs
in shadow/advisory mode and cannot alter `Candidate.similarity`, cross the
`0.80` gate, or publish.

## Runtime flow

1. Harvest complete, verified outcomes and immutable curated cases.
2. Run the active artifact to establish a baseline.
3. Generate one candidate prompt/skill change from training evidence.
4. Run candidate and baseline repeatedly against protected holdout and
   adversarial cases in fresh contexts.
5. Verify hashes, metric policy, stability, safety, and non-regression.
6. Write the immutable candidate artifact.
7. Atomically advance the live pointer when all gates pass.
8. Both runtimes consult the same artifact contract on their next run.
9. Run graph evidence in shadow mode and attach results to evaluation traces.
10. Roll back the pointer automatically if monitored precision or safety gates
    regress after the configured minimum sample.

## Failure handling

- Missing or malformed skill artifacts fail open to no skill, never to an
  unvalidated skill.
- Evaluation, hashing, split overlap, or stability failures fail closed for
  promotion.
- Artifact writes fail loud. A pointer is never advanced before its immutable
  target exists.
- Graph construction defects return explicit diagnostics and withhold evidence;
  they do not affect dense matching.
- A false auto-publish blocks promotion regardless of aggregate score.
- Repeated-run variance is retained as a finding, not discarded.

## Verification strategy

Unit tests cover metric semantics, immutable hashes, protected policy checks,
automatic approval, pointer promotion/rollback, graph topology, and tool bounds.
Contract tests run the same evaluation artifact through both runtime adapters.
An offline end-to-end test exercises baseline → candidate → held-out grade →
promotion → runtime consultation → rollback with no AWS, Neptune, FalkorDB, or
Bedrock dependency. Existing matching, agent-provider, A/B, and local E2E suites
must remain green. A live-model run is reported separately when credentials are
available; deterministic tests prove orchestration and gates, not model quality.

## GNN readiness gate

No GNN is introduced until SCUDO has a production-scale authoritative taxonomy,
stable typed edges, enough confirmed positives and hard negatives, leakage-safe
train/validation/holdout splits, and evidence that lexical, dense, and
deterministic graph features have plateaued. Any future GNN is an additional
candidate-scoring arm, never the publisher or quality authority.
