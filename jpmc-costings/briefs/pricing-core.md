# Brief: AWS core serverless/compute list prices (us-east-1, USD, on-demand, July 2026)

Research CURRENT prices via web search. For each meter give the exact billing meter name and unit price. Where free tier exists, note it but price gross. **Do not invoke skills.**

1. Amazon API Gateway REST API per million calls (and HTTP API per million as alternate)
2. AWS Lambda: per GB-second and per 1M requests (x86)
3. Amazon S3 Standard: per GB-month storage, per 1000 PUT, per 1000 GET
4. Amazon SQS standard: per million requests
5. Amazon EventBridge custom events per million; EventBridge Scheduler per million invocations
6. Amazon CloudWatch: logs ingestion per GB, logs storage per GB-month, custom metrics per metric-month, dashboards per month
7. AWS Fargate: per vCPU-hour and per GB-hour (Linux/x86)
8. Application Load Balancer: per ALB-hour and per LCU-hour
9. NAT Gateway: per hour and per GB processed
10. AWS Secrets Manager: per secret per month, per 10k API calls
11. VPC Interface Endpoint (PrivateLink): per endpoint-hour and per GB
12. Amazon CloudFront: per GB data transfer out (first US tier) and per 10k HTTPS requests
13. AWS AppSync: per million query/mutation ops, per million real-time updates, per million connection-minutes

Return via the structured output tool. confidence=VERIFIED only if you actually fetched an AWS pricing page or an authoritative aggregator quoting current prices; else INDICATIVE. Put the source URL in source_url and the as-of date in notes.
