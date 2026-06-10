# SCUDO Mapping MCP — Architecture docs

Round-tripped artefacts. Each lives here so the diagrams and gates aren't trapped in chat.

| Doc | What it pins | When to read |
|---|---|---|
| [diagram-1-main-flow.md](diagram-1-main-flow.md) | The cost ladder end-to-end: scope → precedent → Falkor → LLM specialist → gate → auto-mapped / reviewer | When reasoning about what runs when, or comparing the diagram against `matching.map_vendor_product` |
| [diagram-2-falkor-internals.md](diagram-2-falkor-internals.md) | Rung 3 expanded: dense (Jaro-Winkler stand-in → GraphRAG Toolkit) + lexical (BM25 → LlamaIndex) + RRF + structural pass (neg-precedents, precedent boost, distance check deferred) | When touching `store.find_similar_products` or thinking about recall vs precision in retrieval |
| [i5-lift-preconditions.md](i5-lift-preconditions.md) | What must be true before sealed PASS verdicts can autonomously persist to Neptune without a human | Before any conversation about lifting I5, removing the reviewer queue, or scoping autonomous writes |

## Quick orientation for a new reader

- The system maps vendor products (LSEG, S&P Global, Bloomberg, ICE, FactSet) to JPMC's CDAO taxonomy.
- The architecture is three MCPs in a trust gradient: Ingestion (read-only vendor frames) → Match&Verify (matcher + HMAC seal signer) → Persistence (the **only** writer; gates on `sealed_status` / `sealed_band`).
- Persistence today refuses agent-driven `AUTO_MAPPED` per **I5** — every such verdict goes to the reviewer queue. Lifting that interception is the subject of `i5-lift-preconditions.md`.
- The matcher is a deterministic cost ladder. The LLM specialist runs only in the borderline window and can REINFORCE confidence but cannot INFLATE past the deterministic anchor.

## Conventions

- Diagrams are Mermaid, embedded in `.md` so they render on GitHub and in any viewer.
- Code-pointer tables (Diagram → file:method) live alongside each diagram so they don't rot independently of the code.
- `[TBC]` markers in `i5-lift-preconditions.md` are deliberately unresolved — they require JPMC input (named owners, thresholds, control IDs).
