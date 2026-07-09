---
type: Architecture
title: Deterministic Enforcement Hooks
description: Deterministic Agent-SDK lifecycle hooks (SessionStart through SubagentStop)
  that enforce SCUDO's non-negotiable invariants at the agent boundary — publish gate,
  mandatory verifier, confidence floor / HITL routing, no raw SPARQL/Turtle, deterministic
  IRIs — independent of the model in the loop.
tags:
- hooks
- architecture
staleness: current
timestamp: '2026-07-09T13:18:02Z'
---

# Hooks — Deterministic Enforcement Layer

Hooks are shell commands the Agent SDK fires on lifecycle events. They receive the
event JSON on stdin and can **allow or deny** an action (PreToolUse: exit 2 or a
`permissionDecision: "deny"` payload). They run regardless of which model is in the
loop, so they are where every non-negotiable lives. The model does *judgement*; the
hooks enforce the *invariants*. This is the single biggest lever for running the
system on a weaker/older model safely (see "Model portability" at the foot).

The agent must never be *asked in prose* to "remember to validate" or "be replay-safe".
If it can be violated, it is a hook, not a sentence in a SKILL.md.

## Hook inventory

| Event | Matcher | Enforces | Clarif. |
|---|---|---|---|
| `SessionStart` | — | Pin `ontology_snapshot`, `rubric_version`, `schema_version` into the run context. Same inputs → same pins → same end-state. | G39, L69 |
| `UserPromptSubmit` | — | Pydantic-validate the request + `BriefBundle` before any agent reasons. Deny on schema drift (catches the `Literal`-vs-`str` / union-unwrap class of bug). | — |
| `PreToolUse` | `neptune_publish_triples` | **Publish gate.** Deny unless: verifier ≥ 16 *or* item is routed to HITL; every triple carries a named graph; every IRI is deterministic (`mds.<vendor>:<uuid5>` / `jpmorgan:data:cdao:…`); and `route != RESEARCH`. | G39, A2, F31 |
| `PreToolUse` | `neptune_*`, `rdf_*` | **No hand-written queries.** Deny if any input field carries a raw SPARQL/Turtle string. Forces the parameterised query/serialiser tools. Kills the hallucinated-Turtle/SPARQL failure mode. | — |
| `PostToolUse` | `submit_mapping` | **Verifier always runs.** Invoke the 10-dimension verifier, attach `VerifierReport`. Verification cannot be skipped by a forgetful model. | — |
| `PostToolUse` | `submit_mapping` | **Confidence floor.** If `confidence < 0.80` *or* `requires_human_review` *or* verifier total `< 16` → enqueue to the **catalogue HITL queue** and block the auto-publish path (12–15 → one retry first). | F31, E27 |
| `SubagentStop` | — | Assert the Mapping Object holds `{route, bundle_ref, mapping_result, verifier_score, publish_outcome\|hitl_outcome}`. Mark incomplete otherwise. | — |
| `PostToolUse` | `*` (agent calls) | Emit per-agent input/output tokens, USD, and Neptune-query count (rediscovery detector). | — |

Routing itself is **not** a hook because it is not LLM judgement — it is a deterministic
rule in the orchestrator. The `UserPromptSubmit` hook simply validates that the route the
orchestrator stamped is one of the four and matches the payload shape.

## settings.json (excerpt)

```json
{
  "hooks": {
    "SessionStart":    [{ "hooks": [{ "type": "command", "command": ".claude/hooks/pin_versions.sh" }] }],
    "UserPromptSubmit":[{ "hooks": [{ "type": "command", "command": "python .claude/hooks/validate_bundle.py" }] }],
    "PreToolUse": [
      { "matcher": "neptune_publish_triples", "hooks": [{ "type": "command", "command": "python .claude/hooks/publish_gate.py" }] },
      { "matcher": "neptune_.*|rdf_.*",        "hooks": [{ "type": "command", "command": "python .claude/hooks/reject_raw_query.py" }] }
    ],
    "PostToolUse": [
      { "matcher": "submit_mapping", "hooks": [
          { "type": "command", "command": "python .claude/hooks/run_verifier.py" },
          { "type": "command", "command": "python .claude/hooks/confidence_floor.py" }
      ]},
      { "matcher": ".*", "hooks": [{ "type": "command", "command": "python .claude/hooks/telemetry.py" }] }
    ],
    "SubagentStop": [{ "hooks": [{ "type": "command", "command": "python .claude/hooks/mapping_object_complete.py" }] }]
  }
}
```

## publish_gate.py — the load-bearing one

```python
#!/usr/bin/env python3
# PreToolUse on neptune_publish_triples. Deny exit code is 2 (stderr → the model).
import json, re, sys

ev = json.load(sys.stdin)
ti = ev.get("tool_input", {})
obj = ti.get("mapping_object", {})          # the run's Mapping Object
triples = ti.get("triples", [])

def deny(why: str):
    print(f"PUBLISH BLOCKED: {why}", file=sys.stderr)
    sys.exit(2)

# 1. RESEARCH never publishes to iFusion (blueprint §4.2)
if obj.get("route") == "RESEARCH":
    deny("RESEARCH route produces an ontology-owner write-up, not a publish.")

# 2. Verifier gate, or explicit HITL routing (clarif. F31)
v = obj.get("verifier_score", {}).get("total")
if obj.get("hitl_outcome") is None and (v is None or v < 16):
    deny(f"verifier total {v} < 16 and not routed to HITL.")

# 3. Replay-safety: deterministic IRIs + named-graph provenance (clarif. G39)
IRI = re.compile(r"^(mds\.[a-z]+:[0-9a-f-]{36}|jpmorgan:data:cdao:)")
for t in triples:
    if not t.get("graph"):
        deny("a triple is missing its named graph (no provenance).")
    if not IRI.match(t.get("subject", "")):
        deny(f"non-deterministic subject IRI: {t.get('subject')!r}")

sys.exit(0)  # allow
```

The other scripts follow the same shape: read the event JSON, check a small invariant,
exit 0 to allow or 2 (PreToolUse) to deny. Keep each to one concern.

## Model portability — does this hold on older models?

Yes for the invariants, no for the quality — and that split is the whole point.

Everything in the table above is **enforced outside the model**. A Haiku-class model, an
older Sonnet, or an LLMSuite/Azure-OpenAI model [clarif. 19] *cannot* publish below the
0.80 floor, *cannot* skip the verifier, *cannot* publish on RESEARCH, *cannot* hand-write
Turtle, and *cannot* mint a non-deterministic IRI — because the hook denies the tool call,
not because the model behaved. So safety and replay-correctness are model-independent.

What degrades on a weaker model is **judgement**: lower first-pass mapping accuracy, more
Amber/Red bands, more RESEARCH/RECONCILE, weaker confidence calibration, more missed
self-flags. The visible effect is **more items falling to HITL and a lower auto-publish
rate** — i.e. graceful degradation [clarif. 18], which JPMC approved — not bad data
reaching Neptune.

Two caveats. (a) Skill auto-triggering and subagent dispatch themselves need some model
capability; that is why routing is a deterministic rule and the verifier is hook-invoked,
not left to the model to "remember". The more transitions you move from "model does X" to
"hook does X", the lower your usable model floor. (b) The hardest judgement is the
no-exact-match mapping — which is the core value [clarif. 83] — so keep your strongest
model there and validate any cheaper swap against the golden set [clarif. 33] with the eval
harness before trusting it. The nightly drift detector (CloudWatch alarm on >5pp accuracy
drop) catches a model regression in production.

## JPMC translation

Bedrock has no direct hooks primitive. On Atlas the same invariants move to: Lambda
action-group pre/post-processing (publish gate, schema validation), Bedrock Guardrails
(the raw-query / output filter), and explicit verifier + floor steps the orchestrator runs
before the publish action group. The enforcement stays deterministic; only the mechanism
changes.

## Related

- [Confidence bands & provenance (canonical)](/reference/matching-data-provenance.md)
