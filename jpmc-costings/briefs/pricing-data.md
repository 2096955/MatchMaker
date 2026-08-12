# Brief: AWS data-layer list prices (us-east-1, USD, on-demand, July 2026)

Research CURRENT prices via web search. **Do not invoke skills.**

1. Amazon Aurora PostgreSQL-Compatible: db.r6g.2xlarge per hour (client sketch says "Aurora db 2xLarge"), plus db.r6g.xlarge and db.r6g.large per hour as cheaper alternates, plus Aurora Serverless v2 per ACU-hour. Also Aurora storage per GB-month and I/O per million requests (Standard vs I/O-Optimized note).
2. Amazon OpenSearch Service (managed domain): r7g.large.search or m7g.large.search per hour, t3.small.search per hour (dev), EBS gp3 per GB-month. ALSO OpenSearch Serverless: per OCU-hour and minimum OCUs (note 4-OCU vs 2-OCU dev minimum rules).
3. Amazon DynamoDB on-demand: per million write request units, per million read request units, storage per GB-month.
4. Amazon Neptune: db.r6g.large per hour, storage per GB-month, I/O per million. (Context: repo has Neptune templates but the PoC uses FalkorDB on ECS instead — price both directions.)
5. Amazon ECS on Fargate for a small always-on service: confirm vCPU-hour/GB-hour and give monthly examples for 0.25 vCPU / 0.5GB and 1 vCPU / 2GB.
6. Amazon RDS Proxy per vCPU-hour (if used with Aurora).

Return via the structured output tool. confidence=VERIFIED only from AWS pricing pages/calculator or an authoritative current source; else INDICATIVE. source_url + as-of date in notes.
