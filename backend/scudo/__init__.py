"""SCUDO enrichment box — Strands wiring.

Top-level modules:
  schemas       — Pydantic contracts the agents and orchestrator bind to.
  tools         — @tool definitions; bodies delegate to the subpackages below.
  catalogue     — MCPClient wrapper for the vendor catalogue MCP.
  hooks         — Strands HookProviders implementing the invariants from hooks.md.
  prompts       — Specialist + verifier prompts.
  agents        — build_specialists() returning (mapping, rights, verifier).
  orchestrator  — Deterministic routing, bundle assembly, publish gate.
  stubs         — In-memory HITL/research/publish sinks for local smoke tests.

Two-graphs split (per the neptune-io and graphrag-retrieval SKILL.md files):
  authoritative — RDF graph (system of record). node_by_iri, existing_mapping,
                  conflicts, and the publish path. Env: NEPTUNE_ENDPOINT.
  sidecar       — Lexical sidecar (non-authoritative, rebuildable). Candidate
                  discovery via PropertyGraphIndex + CypherTemplateRetriever.
                  Env: SCUDO_SIDECAR_GRAPH_ENDPOINT.
  shared        — Bedrock LLM + embedding factories used by both stores.

Selection rule per subpackage: if its endpoint env var is unset → in-memory
mock; else → real AWS path. The orchestrator and agents are insulated from
the choice.
"""
