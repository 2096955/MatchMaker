---
type: Architecture
title: BATCH — Self-Verifying Loop Made Durable
description: How the BATCH self-verifying loop is made durable across sessions with
  checkpoints and replay semantics.
tags:
- batch
- architecture
staleness: current
timestamp: '2026-08-17T09:02:03Z'
---

# Batch matching — the self-verifying loop, made durable

`batch.py` adds a batch layer over the per-product `Orchestrator`. The orchestrator
maps **one** vendor product (maker → independent verifier → deterministic gate →
`Outcome`); `BatchMatcher` runs **N** products as a loop that does not stop while
anything is still wrong, and survives interruption.

The design is a direct application of three operating-discipline skills (the
architecture *is* the leverage — these are not runtime AgentSkills dropped into
`scudo/skills/`):

- **`self-verifying-loop`** — plan → execute → **independent** verify →
  **requeue rejects with their reason** → loop until a pass rejects nothing or the
  retry budget is hit → **quarantine** (never drop) whatever never passes. The
  per-unit claim ("vendor product X → CDAO node Y") is mechanically checkable (the
  verifier rubric + the orchestrator's pre-verify defects + IRI determinism), and
  the verifier is structurally independent of the maker (separate Strands agent,
  separate context, sees only the result + rubric). That is the regime where the
  loop buys a real guarantee.
- **`loops`** — durable runtime: **idempotent** publish (a replayed publish is a
  no-op), **checkpointed** ledger + **resume** (a resumed run skips units already
  terminal — no re-call of the LLM), and run-history observability (the ledger).
- **`compound-system`** — maker ≠ verifier; verify load-bearing work
  independently; the reason rides back to the *maker* only, never the verifier.

## Outcome → ledger mapping

| Orchestrator `Outcome` | Ledger destination | Terminal? |
|---|---|---|
| `PUBLISHED` | `passed` | yes (verified clean) |
| `RESEARCH_QUEUED` | `research` | yes (escalated to ontology owner) |
| `HITL` (floor breach / self-flag) | `quarantined` | yes (needs a human; no retry path) |
| `RETRY` | requeued with reason, up to `retry_budget` passes | on exhaustion → HITL queue + `quarantined` |

`retry_budget` defaults to **2** (pass 1 = initial, pass 2 = the single retry the
orchestrator's own "retry once then HITL" intends). Budget-exhausted units are
enqueued to the orchestrator's **existing** `hitl_queue` — the human channel is
reused, never duplicated, and the unit is never dropped.

## Wiring durability

```python
from scudo.batch import BatchMatcher
from scudo.stubs import IdempotentPublishSink, InMemoryLedgerStore, InMemoryPublishSink

sink = IdempotentPublishSink(InMemoryPublishSink())   # replay-safe publish
orch = Orchestrator(..., publish_sink=sink)           # build with the wrapped sink
matcher = BatchMatcher(orch, ledger_store=InMemoryLedgerStore(), retry_budget=2)

ledger = matcher.run_batch(payloads, run_id="2026-06-20-lseg")
# after a crash / interruption, resume from the checkpointed ledger:
ledger = matcher.run_batch(payloads, run_id="2026-06-20-lseg", resume=True)
```

## Non-goals (v1)

Sequential, not concurrent (so no per-key race / double-publish). The bundle is
re-assembled each pass — caching it across passes is a future optimisation.

## Verify

```
python -m scudo.tests.batch_smoke   # → SCUDO BATCH SMOKE OK
python -m scudo.tests.smoke         # → SCUDO SMOKE OK (per-product, unchanged)
```
