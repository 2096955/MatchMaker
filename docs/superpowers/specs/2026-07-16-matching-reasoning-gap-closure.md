# Matching reasoning gap closure — degraded input, margin, and deploy levers

Date: 2026-07-16. Branch: `scudo-phase0-foundations`. Status: shipped. All
matcher-decision paths (levers 1–2, and lever 3's `DenseBackend`/
`AgentBackend` selection) default OFF / current-behaviour; lever 3's
advisory prompt-text edits ship unconditionally — see below.

## Motivation

An audit of the matcher's behaviour on degraded vendor records found that the
deployed scoring path rewards thin input over complete input:

- A **name-only** record ("Equity Prices Real Time", empty description) scored
  **0.913** where the same record with its full description scored **0.822** —
  the extra text dilutes the Jaro-Winkler string overlap, so removing evidence
  *raises* confidence.
- An **identifier-only** record (bare product code as the name) scored
  **0.969**.
- A **one-word generic** name ("Prices") scored **0.8476** — above the 0.80
  floor, so it **auto-mapped** with no human review.
- **No LLM adjudicates the authoritative matcher score anywhere in the
  deployed path — that's a narrower claim than "no LLM runs," and there are
  FOUR independent gates involved, not one.** Both templates set
  `STORE_BACKEND=falkordb`, so `falkordb_store.py` (not `memory_store.py`,
  which only runs under local dev / tests with `STORE_BACKEND=memory`) is
  the live dense-arm implementation. Enumerating every gate on the path to
  an LLM-backed score, and which side of each one deployment actually sits:
  - **`SCUDO_USE_OPUS_DENSE`** (`falkordb_store.py:425-441`, checked via
    `env_use_opus_dense()`) — when true, delegates the whole retrieval call
    to `retrieval.multi_path_retrieve` with `opus_dense.make_opus_dense_scorer`
    as its dense scorer. That scorer itself calls `opus_dense_score()` per
    candidate — so even flipping this gate on doesn't guarantee an Opus
    invoke; it just makes the retrieval path reachable, still subject to
    `SCUDO_DENSE_BACKEND` below. Not wired to any parameter in either
    template; always false as deployed, so this branch is never taken.
  - **`SCUDO_DENSE_BACKEND`** (a separate check — a different env var from
    `SCUDO_USE_OPUS_DENSE` above, not a repeat read of it — inside the `else`
    branch of that same method, `falkordb_store.py:470-513`, via
    `env_dense_backend()`) — when `"opus"`, calls `opus_dense_score()` for
    the primary dense arm; otherwise calls `_jaro_winkler()` **directly**,
    never reaching `opus_dense_score()`. `DenseBackend`'s deployed default
    is `jaro_winkler` in both templates, so this branch — the direct,
    non-LLM `_jaro_winkler()` call — is the one every deployment actually
    executes on every request; it's fully live, just not LLM-backed.
  - **`specialist.py`'s `_best_pick`** (used by `make_rest_specialist`,
    REST `/map`'s default specialist, reached via `resolve_specialist()`'s
    fallthrough to `"local"` — an empty `SpecialistBackend` does NOT
    disable it) has no gate of its own: it calls `opus_dense_score()`
    unconditionally for every borderline candidate. That call genuinely
    happens — but `opus_dense_score()` then reads `SCUDO_DENSE_BACKEND`
    itself, in a separate check at `opus_dense.py:92` (its own internal
    read, independent of `falkordb_store.py`'s) and takes its own
    `jaro_winkler` branch, never reaching the Opus prompt or a Bedrock
    invoke, because that env var's deployed value is still `jaro_winkler`.
  - **`SCUDO_ENRICHMENT_BACKEND`** (`enrichment.py`'s
    `classify_business_concept`) is a wholly separate, fourth gate — not
    wired to any parameter in either template, default `"off"` — and
    returns before ever reading `SCUDO_DENSE_BACKEND`. Per its own module
    docstring it also runs strictly *after* a mapping is already decided,
    never inside the cost ladder, so even flipped on it wouldn't be a
    borderline specialist.
  Net: of these four gates, only gate 3 (REST `/map`'s specialist) reaches
  `opus_dense_score()` by default — and even it lands on that function's
  own Jaro-Winkler branch, not Opus. Gate 2 (`SCUDO_DENSE_BACKEND`'s direct
  `_jaro_winkler()` else-branch) is also live by default; it's just
  non-LLM — it never calls `opus_dense_score()` at all, so "reachable by
  default" and "reaches an LLM-capable function by default" are not the
  same claim. The one place an LLM genuinely runs today
  is the Bedrock agent (`agent.py`, live on `scudo-poc-app` — see "Why the
  prompt hardening is unconditional" below); its CONTRACT confines it to
  narration and recommendation, with `map_vendor_product` remaining the
  sole authoritative result, so even that LLM never decides a score.
  Either way, no LLM's judgment ever decided a thin or ambiguous record's
  match, but the Opus dense-scorer prompt (dead in practice) and the
  Bedrock agent prompt (live, advisory) both said nothing about thin,
  ambiguous, or field-swapped input — that gap is what lever 3's prompt
  hardening closes in both places.

The common failure: string similarity is the only judge, and string
similarity has no concept of "not enough evidence".

## The three levers shipped

Levers 1 and 2 default OFF; matcher decisions and published outcomes are
unchanged until a deploy explicitly flips their flag. Lever 3's decision
path (`DenseBackend`/`AgentBackend`) is likewise unchanged by default —
but its prompt-text edits are advisory narration, not decisions, and ship
unconditionally; see "Why the prompt hardening is unconditional" below for
why that's still safe, including the one place (the Bedrock agent prompt on
`scudo-poc-app`) where the prompt text itself was already live.

1. **Input-completeness required-validation** —
   `SCUDO_INPUT_COMPLETENESS_VALIDATION`. When on, records with a missing/
   whitespace/too-short name, a bare-identifier name (e.g. "EQUITY-PRICES",
   "EQP_RT_001"), or a missing description fail a required validation and
   route to review instead of auto-mapping on string overlap. A single
   ordinary or generic word ("Prices", "FX") deliberately does NOT fail here
   — that ambiguity is the margin gate's job (lever 2), not this validation's;
   see `_looks_like_bare_identifier`'s docstring in `validations.py`. Default
   off.
2. **Margin gate** — `SCUDO_MARGIN_GATE` + `SCUDO_MARGIN_MIN`. When on, a
   top-1 candidate whose lead over its strongest challenger (the highest-
   similarity candidate among the rest — NOT the positional second entry;
   RRF fusion order is not similarity order, so the real challenger can sit
   anywhere in the tail) is below the margin is demoted out of the auto-map
   band: near-ties go to review rather than an arbitrary winner. Default off.
3. **Prompt hardening + template parameters.** The Opus dense-scorer prompt
   (`backend/scudo_mapping_mcp/opus_dense.py`) gains rules 4–6: degraded-input
   score cap (≤ 0.5), sibling-ambiguity discounting, and a field-sanity cap
   (≤ 0.4 with a "possible field mix-up" flag). The Bedrock agent prompt
   (`backend/scudo_mapping_mcp/agent.py`) gains a DEGRADED INPUT DISCIPLINE
   section: recommend needs_review on missing/suspect fields, swapped or
   commingled field contents, and near-indistinguishable candidates. And
   `infra/scudo-dev-deploy.yaml` / `infra/scudo-poc-app.yaml` gain
   `DenseBackend` (default `jaro_winkler`) and `SpecialistBackend` (default
   empty) parameters wired to `SCUDO_DENSE_BACKEND` / `SCUDO_SPECIALIST_BACKEND`
   — so the LLM arms are one stack-parameter flip instead of a template edit.
   `SpecialistBackend`'s empty default does NOT mean the same thing on every
   code path that reads it (see the SpecialistBackend section below) — this
   is a pre-existing asymmetry the new parameter surfaces, not something the
   parameter itself changes. `DenseBackend`'s empty-equivalent default
   (`jaro_winkler`) does reproduce today's deployed behaviour exactly — the
   dense arm bypasses `opus_dense_score()` entirely by calling
   `_jaro_winkler()` directly, and the one path that DOES reach
   `opus_dense_score()` by default (REST `/map`'s specialist, in
   `specialist.py`) reads the same env var again — a separate check inside
   `opus_dense_score()` itself, not a repeat of `falkordb_store.py`'s — and
   lands on that function's own `jaro_winkler` branch. Every other call
   site is
   independently gated off
   (`SCUDO_ENRICHMENT_BACKEND`, `SCUDO_USE_OPUS_DENSE`) and unreachable
   regardless of `DenseBackend`. So pointing a new parameter at the same
   env var changes nothing live (see the prompt-hardening section below for
   the full trace of which branch is, and isn't, reachable today).

## What is deliberately NOT here

- **No-match representation.** There is still no first-class "this product
  maps to nothing in the catalogue" outcome; the floor routes those to review.
  Adding one changes the 5-zone result contract and needs sign-off.
- **1-to-many bundles.** A vendor bundle that legitimately spans several
  taxonomy nodes is still forced into one-node-or-review. Same reason: a
  contract change to the mapping cardinality, not a matcher tweak.

Both belong to the 5-zone contract owners, not this work stream.

## Why the prompt hardening (lever 3) is unconditional, not flag-gated

`opus_dense.py` rules 4–6 and `agent.py`'s DEGRADED INPUT DISCIPLINE section
are plain text appended to prompts, not a new code path. The "no existing
deployed behaviour to preserve" argument holds fully for one of the two
files and only partially for the other — corrected after a Codex review pass
caught the overclaim (2026-07-16):

- **`opus_dense.py` (the dense-scorer prompt): the Opus prompt/invoke
  branch is the part that's dead — see the Motivation section above for the
  full four-gate trace.** Both templates set `STORE_BACKEND=falkordb`, so
  `falkordb_store.py` is the live dense arm; it checks `SCUDO_USE_OPUS_DENSE`
  (unwired, always false) and then `SCUDO_DENSE_BACKEND` (deployed default
  `jaro_winkler`) and calls `_jaro_winkler()` directly in the deployed case,
  never reaching `opus_dense_score()`. REST `/map`'s default specialist
  (`specialist.py`'s `_best_pick`) does call `opus_dense_score()`
  unconditionally — but that function reads `SCUDO_DENSE_BACKEND` itself,
  in its own separate internal check (`opus_dense.py:92`, independent of
  `falkordb_store.py`'s), and takes its own `jaro_winkler` branch, since
  neither template has ever deployed `opus` for that parameter.
  `_OPUS_SYSTEM_PROMPT` (used only by `_opus_invoke_score`, the branch
  gated on `backend == "opus"`) is therefore dead in every deployed
  environment. Unconditional hardening of that prompt text changes no live
  behaviour.
- **`agent.py` (the Bedrock agent prompt): NOT dead code — this was wrong.**
  `infra/scudo-poc-app.yaml`'s `AgentBackend` parameter has defaulted to
  `bedrock` since it was first added (commit `4b335bb`), and
  `POST /mapping/agent/run` additionally accepts an `agent_provider` body
  field that forces `BedrockMappingAgent` regardless of
  `SCUDO_AGENT_BACKEND` (`get_agent()` in `agent.py` — the UI's "Inference
  Runtime" dropdown uses this). So a `scudo-poc-app` deployment with Bedrock
  model access already runs this prompt today, and the hardening is a live
  behaviour change to it, not a dead-code edit.

That live-behaviour change is still shipped unconditionally rather than
behind a fourth flag, for a narrower reason than originally stated: per the
CONTRACT in `agent.py`'s module docstring, the agent's output is advisory —
it narrates tool calls and a recommendation over SSE, but
`matching.map_vendor_product` always runs alongside and is the sole
authoritative result the rest of the system (HITL, bundle, audit,
publish) consumes. The hardened prompt only changes what the agent narrates
and recommends (more likely to say "needs_review" on thin/ambiguous/
field-mixed input); it cannot change a match outcome, a band, or what gets
published. A flag here would gate cosmetic narration text, not a decision
path — not worth a fifth env var. `DenseBackend` and `AgentBackend`
themselves remain the real rollout controls for whether either LLM arm runs
at all.

## SpecialistBackend's empty default is not one behaviour

Unlike `DenseBackend`, `SpecialistBackend`'s empty-string default does NOT
reproduce one consistent behaviour — it's a pre-existing asymmetry in
`specialist.py` between two resolver functions, and the new CloudFormation
parameter simply wires the same value to both without changing either:

- `routes/mapping.py`'s REST `/map` endpoint calls `resolve_specialist()`,
  whose empty-string default falls through to `"local"` — the in-process
  `opus_dense` re-scorer **does run** even with the parameter left empty.
- `agent.py`'s agent-run endpoints (`POST /mapping/agent/run`, both the
  scripted and Bedrock code paths) and the Match-Verify / Persistence MCP
  tiers all call `specialist_from_env()`, whose empty-string default means
  **no specialist at all**.

So the same `SpecialistBackend` parameter value produces two different
runtime behaviours depending on which endpoint a request hits within the
same container. This predates this work stream and isn't fixed here — the
change here is only that `infra/scudo-dev-deploy.yaml` and
`infra/scudo-poc-app.yaml`'s `SpecialistBackend` Parameter `Description`
now say so explicitly, so a deployer isn't surprised. Set it explicitly to
`local` rather than relying on the empty default if the same backend is
wanted on every code path.

## Rollout order

1. Flip `SCUDO_INPUT_COMPLETENESS_VALIDATION` first. Cheapest, deterministic,
   and it directly closes the thin-beats-complete inversion; watch the review
   queue volume.
2. Then `SCUDO_MARGIN_GATE` with a conservative `SCUDO_MARGIN_MIN`, tuned
   against the review queue after step 1 settles.
3. Then `DenseBackend=opus` per environment. Before flipping, decide the
   `SCUDO_DENSE_FALLBACK` posture: off (default) means a Bedrock failure
   raises rather than silently reverting to the string stand-in — noisy but
   honest; on means degraded scoring continues under an outage. Note the
   0.80 floor was calibrated against raw scores, and Opus is
   non-deterministic where Jaro-Winkler was reproducible.
   `SpecialistBackend` can follow independently once the borderline band has
   real traffic to justify it.
