---
type: Runbook
title: SCUDO Backend Deploy Notes
description: How to deploy the SCUDO backend stack and verify health.
tags:
- runbook
- deploy
staleness: current
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO MatchMaker AIA Stack Deployment

Target account and region:

- Account: `954976331678` (`cb4115669a-genaipocs-aw`)
- Principal: `cloudboost_account_operator/2096955@cognizant.com`
- Region: `us-east-1`
- Existing stack name: `scudo-poc`

The SAM template now reflects the target architecture's first deployable slice:
event-driven ETL plus the matching API. The specialist+verifier can run on
either the Bedrock (default) or the pre-built Azure OpenAI backend — see
"Intelligent demo" below to run the Azure + real-matcher path.

## What Gets Created

- Raw, clean canonical, quarantine, vendor catalog, and CDAO catalog S3 buckets.
- EventBridge raw-object rule -> SQS ETL queue -> ETL Lambda worker.
- DynamoDB tables for ETL jobs, facts, audit log, human review, and transaction outbox.
- Custom EventBridge bus plus projection SQS queue for mapping/persistence events.
- Existing SCUDO orchestrator Lambda behind API Gateway HTTP API.
- Existing Bedrock model wiring for the mapping specialist/verifier.

Cost-bearing always-on stores from the full diagram are exposed as explicit
connection seams. Neptune and OpenSearch stay optional (empty keeps those
projections off), but **Aurora is now mandatory** — `AuroraClusterArn` and
`AuroraSecretArn` have no defaults and the stack will fail without them, since
the DynamoDB tables were removed in the 5-zone persistence consolidation:

- `NeptuneSparqlEndpoint` (optional)
- `OpenSearchEndpoint` (optional)
- `AuroraClusterArn` (**required**)
- `AuroraSecretArn` (**required**)

## Deploy From CloudShell

CloudShell is already authenticated to the target account. From `~/scudo`:

```bash
sam build
sam deploy \
  --stack-name scudo-poc \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ApiKey="$SCUDO_API_KEY" \
    BedrockModelId=us.anthropic.claude-sonnet-5 \
    OntologySnapshot=cdao-2026-05-19 \
    RubricVersion=v1 \
    NeptuneSparqlEndpoint="" \
    OpenSearchEndpoint="" \
    AuroraClusterArn="$SCUDO_AURORA_CLUSTER_ARN" \
    AuroraSecretArn="$SCUDO_AURORA_SECRET_ARN"
```

Generate the API key once if needed:

```bash
export SCUDO_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

## Intelligent demo (Azure specialist+verifier + real matcher)

The default deploy above runs the Bedrock backend with the matcher falling back
to the in-memory sidecar mock if FalkorDB has no candidates. To run the demo the
diagram depicts — the **Azure** specialist+verifier over **real** FalkorDB
candidates — add these overrides. Azure and Aurora are already provisioned; this
is wiring, not new build. (The `openai` client now ships in the Lambda image via
`requirements-lambda.txt`, which the Azure shim imports.)

```bash
sam deploy \
  --stack-name scudo-poc \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ApiKey="$SCUDO_API_KEY" \
    OntologySnapshot=cdao-2026-05-19 \
    RubricVersion=v1 \
    AuroraClusterArn="$SCUDO_AURORA_CLUSTER_ARN" \
    AuroraSecretArn="$SCUDO_AURORA_SECRET_ARN" \
    NeptuneSparqlEndpoint="$SCUDO_NEPTUNE_SPARQL_ENDPOINT" \
    OpenSearchEndpoint="$SCUDO_OPENSEARCH_ENDPOINT" \
    AgentProviderDefault=azure \
    AzureOpenAIEndpoint="$AZURE_OPENAI_ENDPOINT" \
    AzureOpenAIApiKey="$AZURE_OPENAI_API_KEY" \
    AzureOpenAISpecialistDeployment="$AZURE_OPENAI_SPECIALIST_DEPLOYMENT" \
    AzureOpenAIVerifierDeployment="$AZURE_OPENAI_VERIFIER_DEPLOYMENT" \
    AllowMockFallback=""
```

Notes:

- **Real matcher**: the Lambda already runs `STORE_BACKEND=falkordb` with
  `FALKORDB_URL` pointing at the in-VPC FalkorDB service, and `/run` prefers
  `matcher_bridge.retrieve_candidates()` (real dense+lexical retrieval) over the
  mock. Leaving `AllowMockFallback=""` makes a store outage fail loudly instead
  of silently serving mock candidates — the honest demo posture. Set it to `1`
  only if you need the demo to survive before the stores are seeded.
- **Per-request override**: a `/run` payload may set `"agent_provider": "azure"`
  or `"bedrock"` to switch backend per call regardless of the default.
- `AzureOpenAIApiVersion` (default `2024-10-21`) and
  `AzureOpenAIReasoningEffort` (default `medium`) can be overridden if needed.

## Smoke Checks

```bash
aws cloudformation describe-stacks \
  --stack-name scudo-poc \
  --region us-east-1 \
  --query 'Stacks[0].Outputs' \
  --output table

curl "$(aws cloudformation describe-stacks \
  --stack-name scudo-poc \
  --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='HealthUrl'].OutputValue" \
  --output text)"
```

The health response includes a `resources` object showing the exact buckets,
queues, tables, bus, and optional data-store endpoints currently wired into
the Lambda environment.

To exercise the ETL path, upload a small file to `RawFeedBucketName`; S3 emits
to EventBridge, EventBridge delivers to SQS, and `scudo-poc-etl-worker` writes
either `clean/<job>/...json` or `quarantine/<job>/...json`.
