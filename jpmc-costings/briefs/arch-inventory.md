# Brief: SCUDO architecture inventory for JPMC AWS TCO model

You are inventorying the SCUDO codebase at `/Users/anthonylui/MatchMaker/MatchMaker` to ground an AWS TCO cost model for a JPMC deployment. **Read only — do not modify anything. Do not invoke skills.**

## Files to read

1. `ZONES.md` (repo root) — the 5-zone architecture map.
2. `backend/scudo/template.yaml` — SAM template (Lambda functions, queues, event rules, env vars).
3. `backend/scudo/data-platform.yaml` and `backend/scudo/network-falkordb.yaml` — data stores and ECS.
4. `infra/scudo-dev-deploy.yaml` and `infra/scudo-dev-foundation.yaml` — skim for resource types + instance sizes.
5. `backend/scudo/orchestrator.py`, `backend/scudo/prompts.py`, `backend/scudo/agents.py` — the agent loop: how many Bedrock model calls happen per product mapped (orchestrator / specialist / verifier), estimated input+output tokens per call from the actual prompt templates and payload shapes, and which model IDs are configured.
6. `backend/scudo/etl_handler.py`, `backend/scudo/poller_handler.py`, `backend/scudo/projection_handler.py` — ingestion path: S3 writes, SQS messages, Aurora writes (incl. publish_outbox), per-product persistence ops.
7. `backend/scudo/matcher_bridge.py` — sparse/dense arms; where Titan embeddings and OpenSearch would sit (context: dense arm is seams-only today).

If a listed file doesn't exist, find its actual location with Glob/Grep rather than skipping.

## Report sections (dense markdown, cite file:line for load-bearing claims)

**A. COMPONENT INVENTORY** — every AWS service the architecture needs, mapped to the 5 zones, with role and any sizing evident in templates (Lambda memory, Aurora instance class, ECS task sizes, OpenSearch/Neptune config if present). Flag template-real vs seams/planned.

**B. LLM CALL MODEL** — per product mapped end-to-end: how many Bedrock InvokeModel calls, by which agent (orchestrator routing, specialist, verifier 10-dim rubric, narration), with best token estimates per call (input and output separately) derived from actual prompt templates. Describe the 4-gate confidence model: which products actually reach the LLM and what fraction the gate logic implies.

**C. PERSISTENCE MODEL** — per product: S3 PUTs, SQS messages, Aurora writes (incl. outbox), DynamoDB ops if any. Steady-state storage per product (KB estimate of canonical JSON-LD).

**D. GAPS vs the client's component list** — client list: API Gateway, Lambda authorizer JWT, Ingestion Coordinator, Sanity Check, CloudWatch, S3 raw landing, EventBridge Scheduler, EventBridge ObjectCreated rule, SQS, ETL worker VPC, S3 canonical sink, S3 quarantine, AIA Matching Engine gates A/B/C, Titan Embeddings, OpenSearch k-NN, Aurora 2xlarge, Strands Orchestrator Opus 4.8, AgentCore Memory, Specialist agent, Verifier agent 10-dim, Bedrock Evaluations, transactional outbox EventBridge+SQS, HITL AppSync WebSockets, ECS. List REAL repo components the client list misses (e.g. FalkorDB ECS, Neptune, CloudFront, ALB, NAT, Secrets Manager, VPC endpoints, CodeBuild) and any client-listed component with no repo counterpart.

Return the full report as your final message text.
