# Architecture

* [BATCH — Self-Verifying Loop Made Durable](batch.md) - How the BATCH self-verifying loop is made durable across sessions with checkpoints and replay semantics.
* [Deterministic Enforcement Hooks](hooks.md) - Deterministic Agent-SDK lifecycle hooks (SessionStart through SubagentStop) that enforce SCUDO's non-negotiable invariants at the agent boundary — publish gate, mandatory verifier, confidence floor / HITL routing, no raw SPARQL/Turtle, deterministic IRIs — independent of the model in the loop.
* [Diagram — Falkor Internals (superseded)](diagram-falkor-internals.md) - Legacy Falkor internals diagram doc; superseded by scudo-retrieval.mmd in diagrams-and-sources.
* [Diagram — Main Flow (superseded)](diagram-main-flow.md) - Legacy main-flow diagram doc; superseded by the canonical .mmd set documented in diagrams-and-sources.
* [SCUDO Architecture Diagrams & Sources](diagrams-and-sources.md) - Canonical .mmd diagram set, supersession mapping, diagrams-win-over-prose rule, and quick orientation for new readers.
* [SCUDO MatchMaker — Project Overview](overview.md) - Top-level overview of the vendor→CDAO mapping prototype: cost ladder, three-MCP trust gradient, HMAC seal, repo layout, run/deploy paths, and explicit gaps.

# Decision Record

* [ARB Review Pack](arb-review-pack.md) - Architecture review board pack summarizing key SCUDO matching design decisions and open questions.
