---
type: Handover
title: AWS Handoff (commit-pinned)
description: Point-in-time AWS handoff snapshot; read for history only.
tags:
- handover
- aws
staleness: historical
timestamp: '2026-07-09T13:18:02Z'
---

# SCUDO `scudo-poc` AWS Handoff - Verified Review Notes

Updated: 2026-06-23

This is the handoff for the AWS-hosted SCUDO/MatchMaker PoC in the Cognizant
cloudboost sandbox account. These notes were verified from AWS CloudShell in
`us-east-1`; the local laptop shell has no AWS credentials.

## 1. AWS Scope

- Account: `954976331678` (`cb4115669a-genaipocs-aw`)
- Region: `us-east-1`
- Backend app stack: `scudo-poc`
- Network/data runtime stack: `scudo-poc-net`
- Target data platform stack: `scudo-poc-data`
- Build/deploy stack: `scudo-poc-build`
- Frontend CloudFront URL remains: `https://dp4ji14se0pct.cloudfront.net`
- Health URL: `https://rhv5iq5rv3.execute-api.us-east-1.amazonaws.com/prod/health`

## 2. Verified Provisioned State

### `scudo-poc-net`

- Status: previously confirmed `CREATE_COMPLETE`.
- Provides VPC, private subnets, Lambda security group, NAT, and FalkorDB ECS/Fargate.
- FalkorDB service discovery endpoint: `falkordb.scudo.local:6379`.

### `scudo-poc-data`

Status: `CREATE_COMPLETE`.

Provisioned target-state stores:

- Aurora PostgreSQL Serverless v2 cluster: `scudo-poc-aurora`
- Aurora MySQL Serverless v2 cluster for the Flask console: `scudo-poc-console-mysql`
- Neptune DB cluster: `scudo-poc-neptune`
- OpenSearch VPC domain: `scudo-poc-catalog`
- AppSync GraphQL API: `scudo-poc-steward`
- Titan embeddings config: `amazon.titan-embed-text-v2:0`

Verified non-secret outputs:

- Aurora cluster ARN: `arn:aws:rds:us-east-1:954976331678:cluster:scudo-poc-aurora`
- Aurora database: `scudo`
- Console MySQL endpoint: `scudo-poc-console-mysql.cluster-cvm68w06mna8.us-east-1.rds.amazonaws.com`
- Console MySQL secret ARN: `arn:aws:secretsmanager:us-east-1:954976331678:secret:scudo/poc/console-mysql-06oiAp`
- Neptune endpoint: `scudo-poc-neptune.cluster-cvm68w06mna8.us-east-1.neptune.amazonaws.com`
- Neptune SPARQL endpoint: `https://scudo-poc-neptune.cluster-cvm68w06mna8.us-east-1.neptune.amazonaws.com:8182/sparql`
- OpenSearch endpoint: `vpc-scudo-poc-catalog-uj7wbio5zjq2kuzg3334ezxpzm.us-east-1.es.amazonaws.com`
- OpenSearch index: `scudo-catalog`
- AppSync URL: `https://i6st5phcdjfnjeoqoxjidsrgya.appsync-api.us-east-1.amazonaws.com/graphql`

Do not paste or commit the AppSync API key. It exists as a stack output for
bootstrap only and should be rotated/replaced before any non-PoC use.

Console MySQL import contract:

- Export `scudo-poc-console-mysql-endpoint`: `scudo-poc-console-mysql.cluster-cvm68w06mna8.us-east-1.rds.amazonaws.com`
- Export `scudo-poc-console-mysql-secret-arn`: `arn:aws:secretsmanager:us-east-1:954976331678:secret:scudo/poc/console-mysql-06oiAp`
- RDS cluster status: `available`
- RDS instance status: `available`
- Engine/version: `aurora-mysql` / `8.0.mysql_aurora.3.10.3`
- Port: `3306`

### `scudo-poc`

Status: `UPDATE_COMPLETE`.

Confirmed Lambda wiring:

- `scudo-poc-orchestrator` has the Aurora, Neptune, OpenSearch, AppSync, and Titan env vars.
- `scudo-poc-projection-worker` is `Active`, `LastUpdateStatus=Successful`.
- Projection worker VPC config:
  - Subnets: `subnet-0aadacea616fd53c1`, `subnet-07c7316701307076b`
  - Security group: `sg-0fd88e7b6da9c2274`
- SQS event source mapping is enabled for `arn:aws:sqs:us-east-1:954976331678:scudo-poc-projection`.

Health check result:

- `/prod/health` returned `"ok": true`.
- The health payload includes non-empty values for:
  - `SCUDO_NEPTUNE_SPARQL_ENDPOINT`
  - `SCUDO_NEPTUNE_ENDPOINT`
  - `SCUDO_OPENSEARCH_ENDPOINT`
  - `SCUDO_OPENSEARCH_INDEX`
  - `SCUDO_AURORA_CLUSTER_ARN`
  - `SCUDO_AURORA_DATABASE_NAME`
  - `SCUDO_EMBEDDINGS_MODEL_ID`
  - `SCUDO_APPSYNC_API_URL`

## 3. Build/Deploy Run

Latest successful CodeBuild run:

- Build ID: `scudo-poc-build:d72890ec-9c66-47d9-aa52-8c56bb7b6664`
- Commit deployed: `e768284` (`fix(scudo): pass a single Neptune class`)
- Result: `SUCCEEDED`
- Phases: `INSTALL`, `BUILD`, `POST_BUILD`, `UPLOAD_ARTIFACTS`, `FINALIZING`, `COMPLETED` all succeeded.

Important fixes that landed during provisioning:

- `f0d1f4e` - added `scudo-poc-data`, projection worker, and bootstrap loader.
- `adc58fa` - creates the OpenSearch service-linked role before deploy.
- `3c49425` - retries data stack after `ROLLBACK_COMPLETE`.
- `c731f99` - fixes AppSync resolver teardown ordering.
- `e768284` - passes a single clean Neptune instance class to CloudFormation.
- `fa8ca5b` - adds the console Aurora MySQL cluster and the two console import exports.

## 4. Bootstrap Confirmations

The successful build uploaded the mock CDAO catalog to S3 and initialized the
target stores. CodeBuild log markers showed:

- `[init_data_platform] Aurora schema ready`
- `[init_data_platform] Neptune CDAO nodes upserted: 19`
- `[init_data_platform] OpenSearch CDAO docs indexed: 19`

The prior statement that only a representative 6-node set was seeded is now
out of date for this branch/deployment; the current fixture load reports 19
catalog nodes.

## 5. What Is Now Provisioned From The Diagram

- Aurora audit/catalog/persistent-memory backing store: provisioned.
- Aurora MySQL backing store for the Flask ingestion console: provisioned.
- Neptune canonical RDF/SPARQL store: provisioned.
- OpenSearch lexical + k-NN-capable index endpoint: provisioned.
- Titan embeddings path: configured and used by the bootstrap/indexing code when Bedrock invocation succeeds.
- CDAO catalog bootstrap: loaded from S3 into Aurora/Neptune/OpenSearch.
- Data steward AppSync/WebSocket publish path: AppSync API is provisioned; publish mutation/subscription schema exists.
- Asynchronous projection path: EventBridge/SQS projection queue now has `scudo-poc-projection-worker` attached.

## 6. Remaining Runtime Caveats

These are not provisioning blockers, but the next agent should understand them:

1. `/run` still builds matcher candidates from the mock sidecar candidate set in `lambda_handler.py`. The real stores are provisioned and seeded, but matcher retrieval still needs to be changed to query OpenSearch/Neptune/FalkorDB instead of using only mock candidates.
2. `MappingCompleted` is still emitted for every outcome from the API path. The new projection worker gates writes to `outcome == "published"`, but the event naming is still broad and should be tightened.
3. Persistence helpers remain mostly fail-soft. A write failure can still be hidden from the HTTP response unless the caller explicitly checks downstream state.
4. AppSync uses a bootstrap API key. Replace with Cognito/IAM/OIDC before treating the steward channel as anything beyond PoC.
5. OpenSearch is a VPC domain. Any direct inspection or smoke query must run from VPC-attached compute, CloudShell with suitable networking, or another approved path.

## 7. Verification Commands

Use CloudShell in `us-east-1`:

```bash
aws cloudformation describe-stacks --stack-name scudo-poc-data --region us-east-1 \
  --query "Stacks[0].[StackStatus,Outputs[?OutputKey!='AppSyncApiKey']]" --output table

aws cloudformation list-exports --region us-east-1 \
  --query "Exports[?starts_with(Name,'scudo-poc-console-mysql')].[Name,Value]" --output table

aws rds describe-db-clusters --db-cluster-identifier scudo-poc-console-mysql --region us-east-1 \
  --query "DBClusters[0].[Status,Endpoint,Engine,EngineVersion,Port]" --output table

aws lambda get-function-configuration --function-name scudo-poc-projection-worker --region us-east-1 \
  --query "{State:State,LastUpdateStatus:LastUpdateStatus,VpcConfig:VpcConfig}" --output json

aws lambda list-event-source-mappings --function-name scudo-poc-projection-worker --region us-east-1 \
  --query "EventSourceMappings[].{State:State,Arn:EventSourceArn}" --output table

HEALTH=$(aws cloudformation describe-stacks --stack-name scudo-poc --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='HealthUrl'].OutputValue|[0]" --output text)
curl -s "$HEALTH" | python -m json.tool
```
