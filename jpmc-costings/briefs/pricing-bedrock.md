# Brief: Amazon Bedrock pricing (us-east-1, USD, on-demand, July 2026)

Research CURRENT prices via web search. **Do not invoke skills.**

1. Anthropic Claude Opus 4.8 on Bedrock: per 1M input and per 1M output tokens (first-party API is $5 in / $25 out per MTok — verify whether Bedrock matches or differs). Also prompt-cache read/write rates if published.
2. Anthropic Claude Sonnet 5 on Bedrock: per 1M input/output (first-party $3/$15, intro $2/$10 through Aug 2026 — check which applies on Bedrock).
3. Anthropic Claude Haiku 4.5 on Bedrock: per 1M input/output (first-party $1/$5).
4. Amazon Titan Text Embeddings V2: per 1M input tokens (and batch price note).
5. Amazon Bedrock AgentCore (runtime / memory / gateway / identity): the actual GA pricing meters — runtime per vCPU-hour + per GB-hour, AgentCore Memory short-term event price and long-term storage/retrieval prices, Gateway per 1k tool invocations, whatever the real published meters are. Load-bearing: the client wants an "AgentCore Memory" line priced.
6. Amazon Bedrock Evaluations: LLM-as-judge pricing model (just judge-model token cost or a separate meter?) and human-based eval pricing.
7. Amazon Bedrock Guardrails: per 1k text units for content filters (optional line).

Return via the structured output tool. confidence=VERIFIED only from aws.amazon.com/bedrock/pricing or an equivalently authoritative current page; else INDICATIVE. source_url + as-of date in notes. If a meter genuinely has no public price, return usd=0 confidence=RATE_MISSING and explain in notes.
