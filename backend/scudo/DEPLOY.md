# SCUDO MatchMaker AIA Stack Deployment

Target account and region:

- Account: `954976331678` (`cb4115669a-genaipocs-aw`)
- Principal: `cloudboost_account_operator/2096955@cognizant.com`
- Region: `us-east-1`
- Existing stack name: `scudo-poc`

The SAM template now reflects the target architecture's first deployable slice:
event-driven ETL plus the Bedrock-backed matching API.

## What Gets Created

- Raw, clean canonical, quarantine, vendor catalog, and CDAO catalog S3 buckets.
- EventBridge raw-object rule -> SQS ETL queue -> ETL Lambda worker.
- DynamoDB tables for ETL jobs, facts, audit log, human review, and transaction outbox.
- Custom EventBridge bus plus projection SQS queue for mapping/persistence events.
- Existing SCUDO orchestrator Lambda behind API Gateway HTTP API.
- Existing Bedrock model wiring for the mapping specialist/verifier.

Cost-bearing always-on stores from the full diagram are exposed as explicit
connection seams, not silently created:

- `NeptuneSparqlEndpoint`
- `OpenSearchEndpoint`
- `AuroraClusterArn`

Pass those values during deploy when the managed stores exist. Leaving them
empty keeps this PoC on the in-memory/mock paths while still provisioning the
event backbone.

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
    BedrockModelId=us.anthropic.claude-opus-4-8 \
    OntologySnapshot=cdao-2026-05-19 \
    RubricVersion=v1 \
    NeptuneSparqlEndpoint="" \
    OpenSearchEndpoint="" \
    AuroraClusterArn=""
```

Generate the API key once if needed:

```bash
export SCUDO_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

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
