# SCUDO matching-agent self-improvement — deployment consolidation handoff

**Written:** 2026-08-12  
**Repository:** `/Users/anthonylui/MatchMaker/MatchMaker`  
**Audience:** the agent consolidating the current work into the deployment workstream  
**Status:** implementation is uncommitted and undeployed; one stale backend test expectation remains to update before final deployment acceptance

This handoff covers the protected matching-agent improvement loop, immutable
promotion and rollback, signed post-promotion monitoring, and bounded SciPy
taxonomy evidence added during this session.

It does **not** cover the separate Streamlit/Citrix, console database, costings,
frontend bundle, or unrelated matching-hardening changes already present in the
dirty worktree.

## Read these first

- [This deployment-consolidation handoff](/Users/anthonylui/MatchMaker/MatchMaker/MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md)
- [Approved self-improvement design](/Users/anthonylui/MatchMaker/MatchMaker/docs/plans/2026-08-12-matching-agent-self-improvement-design.md)
- [Implementation plan and file-level work breakdown](/Users/anthonylui/MatchMaker/MatchMaker/docs/plans/2026-08-12-matching-agent-self-improvement.md)
- [Existing cross-workstream consolidation handoff](/Users/anthonylui/MatchMaker/MatchMaker/HANDOVER_CONSOLIDATION.md)
- [Current Aurora and Bedrock handoff](/Users/anthonylui/MatchMaker/MatchMaker/JPMC_AURORA_BEDROCK_FILES.md)
- [Current JPMC port README](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/README.md)
- [Root README sections describing protected evaluation and monitoring](/Users/anthonylui/MatchMaker/MatchMaker/README.md)

## Executive result

The implementation adds four bounded capabilities:

1. A protected evaluator that runs outside the promoter's authority, owns an
   Ed25519 private key and protected evaluation data, runs candidate behavior
   over held-out and adversarial cases, and emits a signed envelope.
2. An Aurora-backed promotion control plane that independently verifies the
   envelope, writes immutable artifacts, advances a signed live pointer with
   compare-and-swap semantics, supports explicit legacy migration, and can
   roll back without mutating artifacts.
3. A separately signed post-promotion monitor that resolves immutable source
   records, applies fixed precision/safety thresholds, and atomically records
   retain or rollback outcomes.
4. A bounded, read-only SciPy sparse taxonomy analyzer exposed to mapping and
   verifier agents as advisory evidence. It cannot change candidate
   similarity, confidence, the canonical `0.80/0.70` bands, or publication.

No GNN was added. The current labelled corpus and authoritative taxonomy are
not sufficient for a leakage-safe learned graph model.

## Non-negotiable runtime contracts

- Automatic publication still requires confidence `>= 0.80`.
- Borderline remains `0.70 <= confidence < 0.80`.
- Graph evidence is advisory and does not alter `Candidate.similarity`.
- Agents still cannot publish or author raw SPARQL, Cypher, or Turtle.
- The proposer cannot read or modify protected labels, metric policy, or
  evaluation thresholds.
- Evaluator and promoter authorities are separated:
  - evaluator owns the Ed25519 private key;
  - promoter receives only the evaluator public key and a separate promotion
    HMAC key.
- Generalized HITL teachings are quarantined as `rule_candidate`; only an exact
  approved/corrected product precedent is active immediately.
- Missing, malformed, unsigned or unattested skill artifacts are never injected
  into live prompts.

## Architecture and deployment flow

### A. Offline candidate optimization

The scheduler invokes a configured optimizer command over JSON stdin/stdout.
The optimizer receives training trajectories and the current skill, but no
held-out labels.

Promoter-side variable:

```text
SCUDO_SKILL_OPTIMIZER_COMMAND
```

Implementation:

- [Optimizer subprocess adapter](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/skill_optimizer_adapter.py)
- [Protected sleep-cycle orchestration](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/skillopt_sleep_runner.py)
- [External job wrapper](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/run_sleep_cycle_job.py)

### B. Independent protected evaluation

The promoter sends only candidate identity/content and an opaque evaluation
request ID to an independently provisioned evaluator wrapper/service.

The evaluator-side service owns:

```text
SCUDO_EVALUATION_PRIVATE_KEY
SCUDO_PROTECTED_EVALUATION_ROOT
```

The protected root contains:

- evaluator-controlled bundles;
- an `index.json` allowlist pinning bundle SHA-256 values;
- authoritative golden cases;
- fixed evaluation policy;
- evaluator-owned predictor command/configuration.

The promoter must **not** contain the evaluator private key or protected root.
The promoter adapter deliberately rejects direct configuration of the bundled
`protected_evaluator.py`; `SCUDO_PROTECTED_EVALUATOR_COMMAND` must point to a
separately provisioned wrapper or service that supplies evaluator-side secrets.

Promoter-side variables:

```text
SCUDO_PROTECTED_EVALUATOR_COMMAND
SCUDO_PROTECTED_EVALUATION_REQUEST_ID
SCUDO_EVALUATION_PUBLIC_KEY
```

Implementation:

- [Canonical evaluation, evidence, envelope and pointer contracts](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/matching_self_improvement.py)
- [Promoter-side evaluator adapter](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/protected_evaluator_adapter.py)
- [Evaluator-side bundled implementation](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/protected_evaluator.py)
- [Scheduler/job entrypoint](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/run_sleep_cycle_job.py)

### C. Protected promotion into Aurora agent memory

The promoter verifies:

- Ed25519 envelope signature;
- candidate identity and content digest;
- held-out and adversarial report integrity;
- repeated-run stability;
- exact-match, auto-publish precision, abstention and calibration policy;
- zero false auto-publishes;
- the canonical confidence floor;
- strict improvement without regression against the current protected artifact.

The persistence transaction writes:

1. immutable artifact;
2. immutable signed promotion sequence record;
3. signed live pointer using expected-old compare-and-swap.

Aurora Data API variables:

```text
SCUDO_AURORA_CLUSTER_ARN
SCUDO_AURORA_SECRET_ARN
SCUDO_AURORA_DATABASE_NAME
SCUDO_SKILL_PROMOTION_KEY
```

Implementation:

- [Aurora Data API transaction helpers](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_store.py)
- [Aurora artifact, pointer, migration, rollback and monitor persistence](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_memory.py)
- [Canonical evaluation and signed-pointer models](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/matching_self_improvement.py)

### D. Legacy best-skill migration

Normal protected promotion fails closed if `skill:matching:best` still contains
an old duplicated artifact payload rather than a signed pointer.

Migration must use the explicit migration API. It:

- requires an operator migration reference;
- re-evaluates the new candidate through the protected path;
- archives the exact legacy payload;
- writes artifact, sequence and signed genesis pointer in one transaction;
- rolls back the whole transaction on any failure;
- treats an exact completed retry as idempotent;
- rejects conflicting migration-ref reuse.

Implementation and tests:

- [Legacy migration implementation](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_memory.py)
- [Aurora memory tests](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_aurora_memory.py)
- [Backend real lifecycle E2E](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_improvement_loop_e2e.py)

### E. Signed post-promotion monitoring and automatic rollback

Monitoring is a second independent authority. It signs immutable observations
using a monitoring Ed25519 private key. Runtime receives only:

```text
SCUDO_MONITORING_PUBLIC_KEY
SCUDO_MONITORING_AUDIENCE
SCUDO_MONITORING_DEPLOYMENT_ID
SCUDO_MONITORING_KEY_ID
SCUDO_SKILL_PROMOTION_KEY
```

Each signed envelope binds:

- audience/deployment/key ID;
- issued, active and expiry times;
- observation period;
- artifact key, version, digest and pointer sequence;
- globally identified source events;
- immutable source-record digests;
- predictions and authoritative outcomes.

Runtime resolves every observation against an immutable trajectory/audit source
record before accepting it.

Fixed `monitor-v1` policy:

```text
minimum total observations:     20
minimum auto-pass observations: 20
minimum auto-publish precision: 1.0
maximum false-auto-pass rate:   0.0
publish-gate violations:        none
```

Insufficient or zero-traffic windows are transient:

- `persisted=false`;
- no source events are claimed;
- no rollback occurs;
- the external scheduler may submit a later complete window.

Complete windows atomically:

- claim the window and source events;
- recheck the signed live pointer;
- either retain or create a new signed rollback sequence;
- finalize the immutable monitoring outcome.

Implementation:

- [Backend signed monitoring runtime](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/promotion_monitor.py)
- [Backend monitoring persistence and rollback transaction](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_memory.py)
- [Offline monitoring CLI](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/monitor_promotion_window.py)
- [JPMC local monitoring proof](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/promotion_monitor.py)
- [JPMC local signed rollback implementation](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/aurora_memory.py)

Example offline invocation:

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker/backend
PYTHONPATH=. python -m scudo.scripts.monitor_promotion_window \
  /absolute/path/to/signed-envelope.json \
  --audience scudo-monitor \
  --deployment-id <deployment-id> \
  --key-id <monitoring-key-id> \
  --public-key-file /absolute/path/to/monitoring-public.pem
```

No EventBridge rule, cron entry, monitoring authority service, key provisioning
or protected bundle store was deployed in this work. Those are deployment
prerequisites.

### F. SciPy taxonomy evidence

The graph analyzer:

- builds deterministic SciPy CSR structures from at most 100 taxonomy nodes;
- accepts at most 25 bounded candidate/anchor IRIs;
- bounds relation arrays, total edges and diagnostics;
- keeps concept/class and property hierarchies typed and separate;
- computes shortest paths, true DAG lowest common ancestors, component/orphan
  evidence and branch ambiguity;
- reports cyclic strongly connected components;
- withholds structural evidence when topology is malformed;
- handles display-depth truncation without claiming a false LCA;
- exposes no public caller-controlled "confirmed precedent" seed;
- never mutates matching candidates or confidence.

Backend tool surfaces:

- mapping Strands tool;
- mapping MCP server;
- Match & Verify MCP server.

JPMC behavior:

- explicit local mode may use a coherent, clearly marked illustrative graph;
- nonlocal mode fails closed with `topology_unavailable` until an authoritative
  complete snapshot operation exists;
- no flat sidecar graph is presented as authoritative topology.

Implementation:

- [Canonical taxonomy graph analyzer](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph.py)
- [Canonical graph evidence models](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph_models.py)
- [Graph vendor-sync script](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/scripts/sync_taxonomy_graph.py)
- [Backend Strands mapping tool](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/agent.py)
- [Match & Verify MCP tool](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/match_verify_mcp.py)
- [Mapping MCP tool](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/mcp_server.py)
- [JPMC vendored graph analyzer](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph.py)
- [JPMC vendored graph models](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph_models.py)
- [JPMC mapping/verifier tool registration](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/tools.py)
- [JPMC sidecar package exports](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/sidecar/__init__.py)
- [JPMC illustrative local taxonomy snapshot](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/sidecar/mock.py)

## Full touched-file inventory

All links below are absolute so another agent can open them directly.

### Canonical backend runtime

- [backend/scudo/matching_self_improvement.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/matching_self_improvement.py)
- [backend/scudo/aurora_memory.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_memory.py)
- [backend/scudo/aurora_store.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_store.py)
- [backend/scudo/skillopt_sleep_runner.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/skillopt_sleep_runner.py)
- [backend/scudo/skill_optimizer_adapter.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/skill_optimizer_adapter.py)
- [backend/scudo/protected_evaluator_adapter.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/protected_evaluator_adapter.py)
- [backend/scudo/promotion_monitor.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/promotion_monitor.py)
- [backend/scudo/scripts/protected_evaluator.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/protected_evaluator.py)
- [backend/scudo/scripts/run_sleep_cycle_job.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/run_sleep_cycle_job.py)
- [backend/scudo/scripts/monitor_promotion_window.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/scripts/monitor_promotion_window.py)

### Canonical backend tests

- [backend/scudo/tests/test_matching_self_improvement.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_matching_self_improvement.py)
- [backend/scudo/tests/test_aurora_memory.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_aurora_memory.py)
- [backend/scudo/tests/test_aurora_store.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_aurora_store.py)
- [backend/scudo/tests/test_skillopt_sleep_runner.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_skillopt_sleep_runner.py)
- [backend/scudo/tests/test_run_sleep_cycle_job.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_run_sleep_cycle_job.py)
- [backend/scudo/tests/test_protected_evaluator.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_protected_evaluator.py)
- [backend/scudo/tests/test_protected_adapters.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_protected_adapters.py)
- [backend/scudo/tests/test_improvement_loop_e2e.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_improvement_loop_e2e.py)
- [backend/scudo/tests/test_promotion_monitor.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_promotion_monitor.py)
- [backend/scudo/tests/test_promotion_monitor_parity.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_promotion_monitor_parity.py)

### Taxonomy graph runtime and tests

- [backend/scudo_mapping_mcp/taxonomy_graph.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph.py)
- [backend/scudo_mapping_mcp/taxonomy_graph_models.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph_models.py)
- [backend/scudo_mapping_mcp/scripts/sync_taxonomy_graph.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/scripts/sync_taxonomy_graph.py)
- [backend/scudo_mapping_mcp/agent.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/agent.py)
- [backend/scudo_mapping_mcp/match_verify_mcp.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/match_verify_mcp.py)
- [backend/scudo_mapping_mcp/mcp_server.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/mcp_server.py)
- [backend/scudo_mapping_mcp/tests/test_taxonomy_graph.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/tests/test_taxonomy_graph.py)
- [backend/scudo_mapping_mcp/tests/test_taxonomy_graph_tools.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/tests/test_taxonomy_graph_tools.py)

### JPMC port runtime

- [jpmc-port/scudo/matching_self_improvement.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/matching_self_improvement.py)
- [jpmc-port/scudo/_matching_self_improvement_canonical.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/_matching_self_improvement_canonical.py)
- [jpmc-port/scudo/aurora_memory.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/aurora_memory.py)
- [jpmc-port/scudo/local_state.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/local_state.py)
- [jpmc-port/scudo/promotion_monitor.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/promotion_monitor.py)
- [jpmc-port/scudo/taxonomy_graph.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph.py)
- [jpmc-port/scudo/taxonomy_graph_models.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph_models.py)
- [jpmc-port/scudo/tools.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/tools.py)
- [JPMC sidecar package initializer](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/sidecar/__init__.py)
- [jpmc-port/scudo/sidecar/mock.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/sidecar/mock.py)

### JPMC port tests

- [jpmc-port/tests/test_shared_improvement_contract.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_shared_improvement_contract.py)
- [jpmc-port/tests/test_improvement_loop_e2e.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_improvement_loop_e2e.py)
- [jpmc-port/tests/test_promotion_monitor.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_promotion_monitor.py)
- [jpmc-port/tests/test_learn_from_teaching.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_learn_from_teaching.py)
- [jpmc-port/tests/test_taxonomy_graph_tool.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_taxonomy_graph_tool.py)
- [jpmc-port/tests/test_taxonomy_graph_parity.py](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/tests/test_taxonomy_graph_parity.py)

### Dependency manifests

- [backend/requirements.txt](/Users/anthonylui/MatchMaker/MatchMaker/backend/requirements.txt)
- [backend/requirements-local.txt](/Users/anthonylui/MatchMaker/MatchMaker/backend/requirements-local.txt)
- [backend/scudo/requirements-lambda.txt](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/requirements-lambda.txt)
- [jpmc-port/requirements.txt](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/requirements.txt)

Added dependency constraints:

```text
scipy>=1.16,<2
cryptography>=44
```

### Documentation

- [README.md](/Users/anthonylui/MatchMaker/MatchMaker/README.md)
- [jpmc-port/README.md](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/README.md)
- [docs/plans/2026-08-12-matching-agent-self-improvement-design.md](/Users/anthonylui/MatchMaker/MatchMaker/docs/plans/2026-08-12-matching-agent-self-improvement-design.md)
- [docs/plans/2026-08-12-matching-agent-self-improvement.md](/Users/anthonylui/MatchMaker/MatchMaker/docs/plans/2026-08-12-matching-agent-self-improvement.md)

## Generated/vendor-synced files

These must remain byte-identical to their canonical backend owners:

- [Canonical self-improvement contract](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/matching_self_improvement.py)
  → [JPMC vendored canonical copy](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/_matching_self_improvement_canonical.py)
- [Canonical taxonomy analyzer](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph.py)
  → [JPMC vendored taxonomy analyzer](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph.py)
- [Canonical taxonomy models](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/taxonomy_graph_models.py)
  → [JPMC vendored taxonomy models](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/taxonomy_graph_models.py)

Do not hand-edit only one side. Use:

- [Taxonomy sync script](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/scripts/sync_taxonomy_graph.py)

## Verification evidence at handoff

### Latest exact backend run

Command:

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker/backend
PYTHONPATH=. /opt/homebrew/bin/python3.11 -m pytest \
  scudo/tests/test_improvement_loop_e2e.py \
  scudo/tests/test_promotion_monitor.py \
  scudo/tests/test_promotion_monitor_parity.py \
  scudo/tests/test_matching_self_improvement.py \
  scudo/tests/test_skillopt_sleep_runner.py \
  scudo/tests/test_run_sleep_cycle_job.py \
  scudo/tests/test_protected_evaluator.py \
  scudo/tests/test_protected_adapters.py \
  scudo/tests/test_aurora_memory.py \
  scudo/tests/test_aurora_store.py \
  scudo_mapping_mcp/tests/test_taxonomy_graph.py \
  scudo_mapping_mcp/tests/test_taxonomy_graph_tools.py \
  scudo_mapping_mcp/tests/test_zone_context_tool.py -q
```

Result:

```text
229 passed
1 failed
```

The failure is a stale test expectation, not a runtime failure:

- [backend/scudo/tests/test_promotion_monitor.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/tests/test_promotion_monitor.py)
- test:
  `test_runtime_rejects_wrong_audience_expired_future_and_fabricated_source`
- the production code now correctly rejects the envelope earlier with:
  `monitoring envelope was issued in the future`
- the test still expects:
  `not yet active`

Required final cleanup before deployment acceptance:

```text
Change that one expected regex from "not yet active" to
"issued in the future", or split the future-issued and future-not-before
cases into two explicit tests.
```

Production implementations containing the stricter check:

- [Backend monitor future-issued check](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/promotion_monitor.py)
- [JPMC monitor future-issued check](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/scudo/promotion_monitor.py)

### Latest exact JPMC run

Command:

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker/jpmc-port
PYTHONPATH=. /opt/homebrew/bin/python3.11 -m pytest tests/ -q
```

Result:

```text
75 passed
```

### Compilation, vendored parity and diff integrity

The following all passed in the latest run:

```bash
cd /Users/anthonylui/MatchMaker/MatchMaker
/opt/homebrew/bin/python3.11 -m compileall -q \
  backend/scudo backend/scudo_mapping_mcp jpmc-port/scudo

cmp \
  backend/scudo/matching_self_improvement.py \
  jpmc-port/scudo/_matching_self_improvement_canonical.py

cmp \
  backend/scudo_mapping_mcp/taxonomy_graph.py \
  jpmc-port/scudo/taxonomy_graph.py

cmp \
  backend/scudo_mapping_mcp/taxonomy_graph_models.py \
  jpmc-port/scudo/taxonomy_graph_models.py

git diff --check
```

IDE diagnostics for the edited Python files reported no errors.

### Broader backend context

An independent broad backend review reported:

```text
915 passed
2 known pre-existing failures
```

The two known failures are the existing provenance tests involving the
illustrative Marketing node. They predate this work and remain unadjudicated.
Re-run the broad suite before release rather than relying only on this report.

## Deployment checklist for the consolidating agent

1. Fix the single stale backend monitoring test expectation described above.
2. Re-run the exact backend and JPMC commands in this handoff.
3. Inspect the dirty worktree and isolate only the linked files/lines belonging
   to this work. Do not copy all modified files wholesale.
4. Confirm `scudo.agent_memory` exists in Aurora.
5. Ensure the deployment role can call RDS Data API transaction operations:
   begin, execute, commit and rollback.
6. Provision the promotion HMAC key separately from every evaluation or
   monitoring signing key.
7. Provision an evaluator wrapper/service:
   - protected root and allowlist;
   - Ed25519 private key;
   - candidate predictor command;
   - no promotion or Aurora credentials.
8. Configure the promoter job with only:
   - evaluator public key;
   - promotion HMAC key;
   - evaluator wrapper command;
   - optimizer command;
   - evaluation request ID;
   - Aurora Data API configuration.
9. Provision a separate monitoring authority and immutable source audit records.
10. Configure the external promotion-monitor scheduler and audience/deployment
    identifiers.
11. Install/package SciPy and cryptography in every target artifact.
12. Validate Lambda/container size and cold-start impact after adding SciPy.
13. Run a real Aurora transaction smoke:
    - promote;
    - consult;
    - stale CAS rejection;
    - rollback;
    - consult predecessor;
    - monitor-triggered rollback.
14. Run a real protected evaluator smoke with the production wrapper.
15. Run a live Bedrock held-out smoke separately. Deterministic E2E evidence
    proves orchestration and gates, not model quality.
16. Do not enable graph evidence as a ranking/confidence input. It remains
    advisory.
17. Do not add a GNN until the readiness conditions in the approved design are
    met.

## Not deployed or not proven

- No EventBridge or cron resource was added.
- No evaluator service or protected bundle store was provisioned.
- No monitoring authority or immutable external ledger was provisioned.
- No key material was created or stored by this work.
- No real Aurora connection was exercised from this machine.
- No live Bedrock/Opus quality comparison was run for this change.
- JPMC nonlocal graph topology remains fail-closed until an authoritative
  complete taxonomy snapshot operation exists.
- Coordinated historical replacement by a principal with unrestricted direct
  database write access remains outside the application threat model; use
  database controls or an external immutable ledger.
- No commit, push, pull request or deployment was created.

## Dirty-worktree warning

The repository contained many unrelated modified and untracked files before
this task. Several linked files—especially dependency manifests, README files,
agent tools and Aurora memory—may also contain changes from other workstreams.

The deployment agent must:

- diff each linked file;
- preserve unrelated edits;
- stage only the intended hunks;
- avoid treating the repository-wide diff/stat as attributable to this task.

In particular, do not infer that every change in these shared files came from
this implementation:

- [backend/requirements.txt](/Users/anthonylui/MatchMaker/MatchMaker/backend/requirements.txt)
- [backend/requirements-local.txt](/Users/anthonylui/MatchMaker/MatchMaker/backend/requirements-local.txt)
- [README.md](/Users/anthonylui/MatchMaker/MatchMaker/README.md)
- [backend/scudo/aurora_memory.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo/aurora_memory.py)
- [backend/scudo_mapping_mcp/agent.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/agent.py)
- [backend/scudo_mapping_mcp/match_verify_mcp.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/match_verify_mcp.py)
- [backend/scudo_mapping_mcp/mcp_server.py](/Users/anthonylui/MatchMaker/MatchMaker/backend/scudo_mapping_mcp/mcp_server.py)
- [jpmc-port/README.md](/Users/anthonylui/MatchMaker/MatchMaker/jpmc-port/README.md)

## Recommended consolidation wording

Use:

> The self-improvement, signed promotion/rollback, signed monitoring and sparse
> graph-evidence implementation is complete in the local worktree and ready for
> deployment integration review. It has not been deployed. One stale backend
> test expectation must be updated and all environment-specific Aurora,
> evaluator, monitoring-authority and scheduler integration must be validated
> in the target account.

Do not claim:

- production readiness;
- a deployed nightly loop;
- live model-quality improvement;
- a production GNN;
- authoritative nonlocal JPMC topology evidence;
- successful real-Aurora verification.
