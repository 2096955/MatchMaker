# Aurora + Bedrock — exactly which files to change

**Audience:** the JPMC engineer running the Streamlit + SQLite build who now
needs Bedrock (the agents) and Aurora (the memory).

**Read this first, it saves a day:**

1. **There is no Terraform in this repository.** Not one `.tf` file. Every
   piece of infrastructure-as-code here is **CloudFormation YAML**
   (`infra/scudo-poc-*.yaml`, `infra/scudo-dev-*.yaml`,
   `backend/scudo/data-platform.yaml`). If you are being asked to "do the
   Terraform", the honest answer is that this codebase has none to fix —
   Bedrock and Aurora access are an **account-permissions** task, not a
   code task. See §4.
2. **Bedrock needs no code change.** It is already a supported provider. It
   is switched by a dropdown and one bearer token. See §2.
3. **Aurora splits into three unrelated things.** People say "Aurora" and
   mean three different systems in this repo. Sorting them out is most of
   the work. See §1.

Nothing below requires you to retype a large file. The Bedrock work is
**env vars only**. Two of the three Aurora concerns are **env vars only**.
Only one needs new code, and it is optional for a demo.

---

## The one-page answer

| # | What you want | Files to touch | Change type |
|---|---|---|---|
| 1a | Console CRUD (Providers/Datasets/Admin) on Aurora instead of SQLite | `backend/db.py` — **read only, no edit** | **env vars only** |
| 1b | Run the console schema on Aurora | `backend/init_db.sql` (223 lines, already PostgreSQL) | **run it, don't edit it** |
| 2 | Agents on real Bedrock instead of the scripted narrator | `streamlit_app.py` sidebar — **no edit needed** | **env vars + paste key** |
| 2a | The region/model mismatch (was a real bug, see §2.3) | *(none)* — **already fixed in the file you have** | **set `AWS_REGION`, type nothing** |
| 3 | Agent memory in Aurora rather than a local file | `backend/scudo/aurora_memory.py`, `aurora_store.py` (exist, unreachable from Streamlit) | **new file needed** — optional |
| 4 | Let users **correct** the agent in the Streamlit UI | *(none)* — Approve/Reject **already present**, `streamlit_app.py:997-1079` | **nothing to type** — see §5 |

---

## §1 — "Aurora" is three separate systems

This is the single biggest source of confusion. Do not skip it.

| | Purpose | How it talks to Aurora | Env vars | Reachable from Streamlit? |
|---|---|---|---|---|
| **A. Console DB** | Providers, datasets, admin, ingest CRUD | `psycopg` direct connection | `CONSOLE_DB_*` | Yes (Flask only) |
| **B. Matching store** | Taxonomy nodes, candidates, precedents | *no Aurora implementation exists* | `STORE_BACKEND` | Yes |
| **C. Agent memory** | Learned precedents, trajectories, skills | **RDS Data API** (`boto3` `rds-data`) | `SCUDO_AURORA_*` | **No** |

They use **different credentials and different AWS APIs**. A working
console DB tells you nothing about whether C works.

### 1A — Console DB: env vars only, zero code edits

`backend/db.py` already speaks both. The switch is one env var:

- `backend/db.py:93-100` — `_sqlite_enabled()` returns True only when
  `CONSOLE_DB_BACKEND=sqlite`
- `backend/db.py:71-74` and `:86-89` — when sqlite, returns the
  `db_sqlite_fallback` connection
- `backend/db.py:46-55` — otherwise `psycopg.connect(...)` to Aurora

**To move to Aurora: set `CONSOLE_DB_BACKEND` to anything other than
`sqlite`, and set these five.** (Do not just `unset` it if you launch via
Streamlit — `streamlit_app.py:82` re-defaults it back to `sqlite`. See §3
Config C.)

```bash
export CONSOLE_DB_BACKEND=postgres   # any value except "sqlite"; NOT unset
export CONSOLE_DB_HOST=<cluster>.cluster-xxxx.<region>.rds.amazonaws.com
export CONSOLE_DB_PORT=5432
export CONSOLE_DB_USER=scudo
export CONSOLE_DB_PASSWORD=<from Secrets Manager>
export CONSOLE_DB_NAME=scudo_console
```

`export` on **every** line, not just the first. A bare `NAME=value` is a shell
variable, not an environment variable — the Python process never sees it and
you get the SQLite file with no error to tell you why.

Safety rail worth knowing: `db.py:41-45` **refuses to start** if
`CONSOLE_DB_HOST` is not localhost and `CONSOLE_DB_PASSWORD` is empty. That
error is the code protecting you from silently connecting nowhere, not a bug.

**Two known holes in the SQLite side — reproduced, not theoretical.** They
argue *for* moving to Aurora, and they only bite the local stand-in:

1. `db_sqlite_fallback.py:73-80` `translate_params()` calls `.replace()` on
   the SQL, but `routes/datasets.py:103-110` (CREATE TABLE) and `:131-137`
   (ALTER TABLE ADD COLUMN) pass a `psycopg.sql.Composed`, which has no
   `.replace` → `AttributeError`.
2. `ingestion/engine.py:559,566` uses `ing.transaction()` (including a nested
   per-row SAVEPOINT); `SqliteConnection` (`db_sqlite_fallback.py:178-193`)
   has no `transaction()` method → `AttributeError`.

Read paths are fine. It is schema-mutating Datasets operations and Ingestion
row-load that fail locally. Both work on Aurora, because those are ordinary
psycopg features — so if you hit either, that is the stand-in's limit, not
your configuration.

Call sites that light up (counted as actual `get_conn()` invocations, import
lines excluded): `routes/admin.py` (10), `routes/datasets.py` (7),
`routes/providers.py` (5), `routes/ingest.py` (2) — 24 in total.
**You do not edit any of them** — every one goes through `get_conn()`, which
is the single switch above.

### 1B — The schema

`backend/init_db.sql` is 223 lines of **portable PostgreSQL** and already
seeds admin roles/users. Run it against Aurora once. Do not retype or edit it.

Two warnings:
- It `DROP TABLE`s the `tp_*` tables before creating them — **re-running is
  destructive**.
- The database must already be named to match `CONSOLE_DB_NAME`.

### 1B(ii) — Aurora via Data API instead of a direct connection

If JPMC networking blocks port 5432 from your desktop, there is a Data API
bootstrapper: `infra/bootstrap_console_schema_data_api.py` (415 lines,
already written — you run it, you do not edit it). It takes four **required**
arguments and defaults `--sql-file` to a path relative to the repo root, so
**run it from the repo root**:

```bash
cd /path/to/MatchMaker            # --sql-file defaults to backend/init_db.sql
python infra/bootstrap_console_schema_data_api.py \
  --region <region> \
  --cluster-arn arn:aws:rds:<region>:<acct>:cluster:<name> \
  --secret-arn  arn:aws:secretsmanager:<region>:<acct>:secret:<name> \
  --database    scudo_console
```

That prints the plan and **changes nothing**. Add `--apply` to execute
(`:325-329`, `:341-345`). Do the dry run first — it tells you how many
statements it would apply, and the DDL is destructive (see 1B).

### 1C — Matching store: no Aurora implementation exists

Be clear with your architects about this, because it is the one real gap.

`backend/scudo_mapping_mcp/store/factory.py:19-59` is the **only** place a
store is constructed. It accepts exactly four values and raises `ValueError`
on anything else:

| `STORE_BACKEND` | Implementation | Survives restart? |
|---|---|---|
| `memory` | `memory_store.py` (152 lines) | No |
| `local_file` | `local_file_store.py` (393 lines) | **Yes — JSONL on disk** |
| `falkordb` | `falkordb_store.py` | (not used at JPMC) |
| `neptune` | `neptune_store.py` | (not used at JPMC) |

**There is no `aurora_store` in that list.** To put matching precedents in
Aurora you would write a new file implementing the `RetrievalStore` abstract
base in `store/base.py:59-437` — **16** abstract methods. That is a real
piece of work and it is **not needed for a demo**.

**Use `local_file` instead** — but be precise about what it replaces.
`local_file` is the durable store for **matching precedents**: the same
scoring, the same invariants, plus an append-only journal at
`$SCUDO_MEMORY_PATH` (default `backend/local_memory/precedents.jsonl`).
It is human-readable — you can open it in VS Code and *see what the system
learned*, which is a better demo than a database you cannot inspect.

It is **not** a drop-in for everything `aurora_memory.py` does. That module
also holds priors, trajectories and promoted skills (`consult_priors`,
`record_trajectory`, `harvest_trajectories`, `consult_best_skill`,
`promote_skill`). `local_file_store.py` implements **none** of those — a grep
for `trajector|skill|prior` across it returns one comment and nothing else.
So: precedent learning works locally and is what the demo shows; the
self-improvement half is **Aurora-backed and offline-run** — not a Lambda-only
thing, but not a Streamlit thing either. It is a separate nightly job:
`python -m scudo.scripts.run_sleep_cycle_job --apply`, with the three
`SCUDO_AURORA_*` variables set (see 1C(ii)).

```bash
export STORE_BACKEND=local_file
export SCUDO_PERSIST_TARGET=local_file
```

### 1C(ii) — Agent memory (`SCUDO_AURORA_*`) — the honest status

`backend/scudo/aurora_memory.py` and `backend/scudo/aurora_store.py` are
written and working, but they reach Aurora through the **RDS Data API**:

- `aurora_store.py:14-17` — `boto3.client("rds-data")`
- `aurora_store.py:45-47` — requires **`SCUDO_AURORA_CLUSTER_ARN`**,
  **`SCUDO_AURORA_SECRET_ARN`**, **`SCUDO_AURORA_DATABASE_NAME`**

**Verified: no Flask route, and not `streamlit_app.py`, imports either
module.** They are reached from `lambda_handler.py`, the AWS entrypoints, and
the offline `scudo.scripts.run_sleep_cycle_job` CLI.

So on your Streamlit build, **matching-precedent memory is `local_file`, and
Aurora agent memory is simply unavailable to the UI** — not misconfigured,
just not wired to that process. Do not spend a day setting `SCUDO_AURORA_*`
on the desktop expecting the Streamlit screen to change; it cannot. Those
variables matter for the deployed Lambda and for the nightly sleep-cycle job,
both of which run somewhere other than your desktop.

---

## §2 — Bedrock: no code change needed to turn it on

The agents already support Bedrock. It is a **provider argument**, not a
rewrite.

### 2.1 The switch

`backend/scudo_mapping_mcp/agent.py:1224-1263` — `get_agent(provider)`:

- `get_agent("bedrock")` → `BedrockMappingAgent` **unconditionally**
- `get_agent("scripted")` → `ScriptedMappingAgent` **unconditionally**
- `get_agent(None)` → falls back to `SCUDO_AGENT_BACKEND`

The Streamlit sidebar already passes this: `streamlit_app.py:528`
(the "Agent" dropdown) and the run block (`get_agent(provider).run(ref)`).

**So: pick "bedrock" in the sidebar, paste the key, press "Apply & test".
That is the whole activation path.** No file edit.

### 2.2 What Bedrock actually needs

A Bedrock API key is **one bearer token** that carries its own region and
credentials. There is no access key, no secret, no session token.

| Env var | Set by | Notes |
|---|---|---|
| `AWS_BEARER_TOKEN_BEDROCK` | the sidebar box (`streamlit_app.py:584`) | expires ~12 h |
| `SCUDO_BEDROCK_MODEL_ID` | the sidebar dropdown (`streamlit_app.py:586,593`) | overrides the default |
| `AWS_REGION` | your shell | drives **both** the model IDs and the preflight (`:139-165`, `:364`) **and** the agent (`agent.py:467-472`) — see §2.3 |

**On IAM roles:** the *agent* does not require a bearer token — it builds
`BedrockModel(...)` (`agent.py:508-511`) and botocore will use a task role or
`~/.aws` credentials if one is present. But the **sidebar preflight refuses
to test that path**: `streamlit_app.py:350-351` returns "No API key set"
whenever `AWS_BEARER_TOKEN_BEDROCK` is empty. So on a role-based desktop the
sidebar will look unhappy while a run may still succeed. Judge it by the run,
not by the sidebar — and see §4 for what to ask your cloud team.

Dependencies are already in `backend/requirements.txt:12-13`
(`boto3`, `strands-agents`). Both are **lazy-imported**, which is why the
SQLite/scripted build works without AWS at all.

### 2.3 Region and model prefix — already handled, one env var to set

There *was* a defect here (preflight pinned to `us-east-1` while the agent
defaulted to `eu-west-2`, so the sidebar could go green and the run then fail
on auth). **It is already fixed in the current `streamlit_app.py` — do not
retype anything.** For your understanding:

| Location | Behaviour now |
|---|---|
| `streamlit_app.py:139-141` | `SCUDO_REGION` ← `AWS_REGION` → `AWS_DEFAULT_REGION` → `eu-west-2` |
| `streamlit_app.py:149-155` | `_MODEL_PREFIX` derived from the region: `eu-` → `eu.`, `us-` → `us.`, else empty |
| `streamlit_app.py:157-161` | model IDs are built from that prefix |
| `streamlit_app.py:364` | preflight uses `region_name=SCUDO_REGION` — the same region as the run |
| `agent.py:123` | agent default is `eu.anthropic.claude-opus-4-8` |
| `agent.py:465-472` | agent region: `AWS_REGION` → `AWS_DEFAULT_REGION` → `eu-west-2` |

Inference-profile IDs are region-bound: `us.anthropic.*` does not resolve in
an EU region and vice versa. Because both the preflight and the model list
now derive from the same `SCUDO_REGION`, they cannot disagree.

**So there is exactly one thing to do — name your region:**

```bash
export AWS_REGION=us-east-1        # or eu-west-2, or your actual region
```

Two caveats worth knowing:

- The default if you set nothing is **`eu-west-2`**, matching the agent. If
  your Bedrock access is US, you **must** export `AWS_REGION` or the model
  IDs will be `eu.` and fail.
- Only `us-` and `eu-` regions have a prefix mapping. Any other region
  (e.g. `ap-southeast-1`) yields an **empty** prefix deliberately — set
  `SCUDO_BEDROCK_MODEL_ID` explicitly there rather than guessing.

### 2.4 What Bedrock does and does not change

**In your configuration the score does not come from the model.** It is
deterministic Jaro-Winkler:

- `opus_dense.py:149` imports `_jaro_winkler` from `store/falkordb_store.py`
- defined at `store/falkordb_store.py:166`
- `memory_store.py:35` imports the same function

`agent.py:571` — *"matcher runs regardless of what the LLM recommended"*.

State the precondition, because it is one env var wide: this holds while
`SCUDO_DENSE_BACKEND` is unset or `jaro_winkler`, which is the default
(`config.py:296`). Set `SCUDO_DENSE_BACKEND=opus` and the model **becomes
the score** — `memory_store.py:73-77` calls `opus_dense_score()` and
`:109-115` assigns it straight to `Candidate.similarity`, and with no
specialist `matching.py:439-443` takes `confidence = best.similarity`
unchanged. It can move the number **either way**, not just down. (The
`min(best, specialist)` cap at `matching.py:471-479` applies only in the
narrower case where a specialist concurs.) **Leave it unset** — that is what
makes the demo's reproducibility claim true.

Two consequences to say out loud in a demo:

1. Switching Opus → Sonnet → Haiku **will not change the number**. The model
   narrates the reasoning; the matcher scores. That is the architecture, and
   it is a feature: the score is reproducible and auditable.
2. **If Bedrock fails *at invoke time*, you still get a score.** The invoke
   is wrapped at `agent.py:751-766`, which yields an `error` event and then
   lets the matcher run anyway (`:571`). The UI warns you
   (`streamlit_app.py:808-814`) and drops the card accent to neutral
   (`:845-848`) — without that warning a failed Bedrock run looks identical
   to a successful one. Trust the warning.

   **Two earlier failures are not covered and give you no score at all:**
   a missing `strands-agents` package (`agent.py:499-506` raises
   `RuntimeError`) and `BedrockModel(...)` construction (`agent.py:508-511`).
   Both happen *before* that try/except, and `streamlit_app.py:796` iterates
   the generator with no handler — so you get a red Streamlit traceback
   rather than a degraded result. If you see that, it is setup, not
   credentials: `pip install strands-agents` and re-run.

### 2.5 Do not delete `falkordb_store.py`

Even with FalkorDB unused, the default scoring path imports
`_jaro_winkler` from that file. Deleting it breaks matching entirely. The
**pip package** is not needed (the real `import falkordb` is inside a
method) — only the file must stay on disk. `factory.py:21-32` says this too.

---

## §3 — Copy-paste run configurations

Three configurations, in increasing order of AWS dependency. Each is
complete — nothing implied.

### Config A — what you have now (no AWS, no DB)

```bash
export STORE_BACKEND=local_file          # was memory: now survives restart
export SCUDO_PERSIST_TARGET=local_file
export CONSOLE_DB_BACKEND=sqlite
export FRAME_SOURCE=mock
export SCUDO_AUTH_ALLOW_DEV=1
export SCUDO_AUTH_DEV_PRINCIPAL=streamlit@local
export SCUDO_VERDICT_ALLOW_DEV=1
export SCUDO_PERSIST_ALLOW_DEV_WRITES=1
streamlit run streamlit_app.py
```

`streamlit_app.py:75-82` sets all of these via `setdefault`, so it already
works with no exports at all. `local_file` is chosen automatically when your
checkout supports it (`_best_local_store`, `streamlit_app.py:49-71`).

### Config B — add Bedrock (agents become real, still no DB)

Everything in A, plus:

```bash
export AWS_REGION=us-east-1              # must match the model prefix
# then in the sidebar: Agent = "bedrock", paste the key, "Apply & test"
```

Do **not** put the token in the launch environment and expect it to work
better — one known failure was a server started before the token was set and
it kept returning `AccessDeniedException`. Paste it into the **running** app.

### Config C — add Aurora for the console pages

Everything in B, plus **remove** the sqlite line and add:

```bash
export CONSOLE_DB_BACKEND=postgres        # NOT unset — see the note below
export CONSOLE_DB_HOST=<cluster-endpoint>
export CONSOLE_DB_PORT=5432
export CONSOLE_DB_USER=scudo
export CONSOLE_DB_PASSWORD=<secret>
export CONSOLE_DB_NAME=scudo_console
```

**Why `export …=postgres` and not `unset`.** `streamlit_app.py:82` does
`os.environ.setdefault("CONSOLE_DB_BACKEND", "sqlite")` at import time, so if
you unset the variable the app puts `sqlite` straight back and you stay on the
file. `_sqlite_enabled()` (`db.py:93-100`) tests for the literal string
`sqlite`, so **any other non-empty value** selects the PostgreSQL path;
`postgres` is just a readable choice.

`start_local.py:65` does the same defaulting for the Flask console, so `unset`
does not work there either. `unset` is only sufficient if you launch
`backend/app.py` **directly**, which is not the documented way to run it.
Simplest rule: always `export CONSOLE_DB_BACKEND=postgres` — it is correct for
all three launch paths.

Then run `backend/init_db.sql` once against that database.

**This changes nothing you can see in Streamlit** — the Streamlit app is the
matching path only, and it does not read the console DB
([`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) §"Known issues" item 5). Config C is for the
React/Flask console.

---

## §4 — The Terraform question, answered honestly

There is **no Terraform in this repository**. Confirmed: zero `.tf` files.

So "we cannot work out the Terraform for Bedrock and Aurora" is really two
requests to your cloud team, neither of which is a code change:

**For Bedrock** — you need, in the target account and region:
1. Model access granted for the Claude models (a console request per model)
2. An API key (`bedrock-api-key-...`) OR an IAM role with
   `bedrock:InvokeModelWithResponseStream`
3. **`ConverseStream` permission specifically.** The agent streams. A key
   that can call `Converse` and not `ConverseStream` will pass a naive test
   and fail live — this exact bug already bit once, which is why the
   preflight streams (`streamlit_app.py:370`).

**For Aurora** — you need one of:
- Network reachability to port 5432 plus a Secrets Manager password
  → then §1A is env vars only, or
- RDS **Data API** enabled on the cluster plus the cluster/secret ARNs
  → then use `infra/bootstrap_console_schema_data_api.py`

If your platform team insists on Terraform, they are writing it fresh
against those requirements. The existing CloudFormation templates
(`infra/scudo-poc-foundation.yaml`, `infra/scudo-poc-app.yaml`) are the
reference for what resources are expected — hand those over as the spec.

---

## §5 — Correcting the agent, and what it remembers

The requirement is: *users engage with the agents to query and correct the
system, and it remembers.* Here is the true state.

### The correction UI exists in Streamlit — use it

`streamlit_app.py:997-1079` renders a **Reviewer decision** block with
**Approve** and **Reject** buttons after a match, calling `apply_decision`
into whichever store is live. Under `STORE_BACKEND=local_file` the decision
is journalled and replayed at startup, so **it survives a restart with no
Aurora and no Bedrock**.

Four details in that code you should know before demoing:

- **The buttons live outside the `run_clicked` block on purpose**
  (`streamlit_app.py:985-991`). Streamlit reruns the script on every click;
  a button drawn inside the run block would vanish before its own click was
  processed and fail *silently*. Do not "tidy" them back inside.
- **Staleness guard** (`:1004-1005`): change vendor or product without
  re-running and the buttons disappear, rather than recording a decision
  against a product nobody is looking at.
- **Approve and reject are not symmetric** (`:1061-1073`). Measured:
  approve → the next match short-circuits to that node; reject → the node is
  filtered out and the match re-ranks without it (0.9083 equity-prices became
  0.6138 fixed-income).
- **Failures are reported, never swallowed** (`:1047-1059`) — including an
  unwritable journal directory, which is realistic on a locked-down desktop
  or a network share. If you see no green message, nothing was recorded.

### What works underneath

**The correction loop is real and it does learn.** Verified end to end:
a match scoring 0.5294 `needs_review`, approved by a human, then re-matched
→ returns the approved node with rationale `"precedent"`.

- **Ingress:** `backend/routes/mapping.py:585` `record_decision()` —
  approve / override / reject
- **Storage:** `store/base.py:78-117` `upsert_precedent()` — positive
  precedents for approve/override, **negative** precedents for reject so the
  triple is filtered out of future candidates
- **Durability:** with `STORE_BACKEND=local_file`, every decision is one JSON
  line in `backend/local_memory/precedents.jsonl`, replayed on startup
  through the *same* `upsert_precedent` the live path uses
  (`local_file_store.py:92-143`) — so replay cannot drift from live
- **Recall:** the next match short-circuits to the human-confirmed result and
  the rank signal boosts that node for products with the same vendor
  signature

### What genuinely does NOT exist — set expectations here

**The agent is not conversational.** The entry point is
`get_agent(provider).run(ref)` — a generator over **one product reference**.
There is no free-text chat box and no way to ask an arbitrary question.

So "users query the agents intelligently" today means:
- watch a **structured reasoning trace** (thinking / calls / returns) ✅
- **correct** the outcome with Approve / Reject, and have it remembered ✅
- **ask the agent an open question in your own words** ❌ — not built

**`override` is not exposed in Streamlit.** The store supports it
(`store/base.py:78-117`) and the Flask API accepts it, but the Streamlit UI
offers only Approve and Reject. Correcting a match *to a different node* from
that screen would be new UI — ask before building it.

**The demo story that IS true end to end:**
match → correct → re-match → it remembered — with the evidence visible in
`backend/local_memory/precedents.jsonl`, which you can open on screen. That
is a stronger demo than a database nobody can inspect, and it needs neither
Aurora nor Bedrock.

---

## §6 — Files by change type (the checklist)

**Do not edit — read only:**
| File | Why |
|---|---|
| `backend/db.py` | already dual-mode; env vars decide |
| `backend/init_db.sql` | run it; do not retype (destructive re-run) |
| `backend/scudo_mapping_mcp/store/factory.py` | the swap point already works |
| `backend/scudo_mapping_mcp/store/falkordb_store.py` | **must stay on disk** — supplies `_jaro_winkler` |
| `backend/scudo/aurora_memory.py`, `aurora_store.py` | Lambda-side; unreachable from Streamlit |
| `infra/bootstrap_console_schema_data_api.py` | run it if 5432 is blocked |

**Small typed edits — none required for Bedrock:**
| File | Lines | Change |
|---|---|---|
| *(none)* | — | the region/prefix defect is **already fixed** — see §2.3 |

**New code, only if you want it (§5):**
| File | Change |
|---|---|
| `streamlit_app.py` | an **Override** control (Approve/Reject already exist) |
| `store/aurora_store.py` (new) | only if matching precedents must live in Aurora; ~15 ABC methods |

**Env vars only — no file changes at all:** everything in §3.

---

## Verification basis

Every file:line citation above was read from the working tree on
**2026-08-12** and re-checked after writing (the Streamlit file had moved on
under two earlier drafts — the region/prefix defect and the missing correction
UI were both already fixed, and this document was corrected rather than
telling you to re-fix working code).

### Executed on this machine — you can re-run these

**The memory loop survives a process restart.** Two separate Python
processes, `STORE_BACKEND=local_file`:

| | Result |
|---|---|
| Process 1 — first match | confidence **0.8839**, status `AUTO_MAPPED` |
| Process 1 — human approves | decision written, **1 line** in the journal |
| **Process 2 (fresh)** — same product | confidence **0.8839**, status **`APPROVED`**, rationale **`precedent`** |

That is the demo in three lines: it matched, a human corrected/confirmed it,
and a brand-new process **remembered** — with no Aurora, no Bedrock and no
database.

**Console DB switch is env-only.** `CONSOLE_DB_BACKEND=sqlite` → sqlite
selected; cleared → psycopg path; and a remote `CONSOLE_DB_HOST` with an empty
password raises `CONSOLE_DB_PASSWORD is required...` before connecting.

**Nothing AWS loads on the local path.** Importing the Flask app with the
local env loads **no** `aurora*` module and **does not import `boto3`**.

**The score is deterministic and LLM-free.** `_jaro_winkler` returns
`0.908333` identically on repeat calls; its source contains no boto3/Bedrock
reference. `opus_dense` does import it from `falkordb_store` — which is why
that file must stay on disk (§2.5).

**No Aurora matching store exists.** `get_store()` with
`STORE_BACKEND=aurora` raises `ValueError: Unknown STORE_BACKEND 'aurora'`.
`RetrievalStore` has **16** abstract methods a new store must implement.

One nuance found while verifying: `Settings.from_env()` *accepts*
`STORE_BACKEND=aurora` — only `SCUDO_PERSIST_TARGET` is validated against an
allow-list. The refusal comes from `get_store()`, so a bad `STORE_BACKEND`
fails at first store use, not at import.

### Not verified — be honest about these

- **A real Aurora connection and a live Bedrock invoke.** Neither is reachable
  from this machine. §1A and §3 Config C are read from the code path, not
  executed against a cluster.
- **Independent review did not happen.** Codex CLI is not installed here and
  three parallel audit agents failed on a provider error. Everything above is
  single-reviewer work, verified by execution where execution was possible.
- Carried from earlier sessions rather than re-run today: reject →
  0.9083 became 0.6138 re-rank; scripted/Haiku/Opus returning identical
  confidence.

## Related

- [`CITRIX_STREAMLIT_HANDOVER.md`](CITRIX_STREAMLIT_HANDOVER.md) — the Streamlit build, its known issues,
  and the measured Bedrock timings
- [`JPMC_LOCAL_RUN_HANDOVER.md`](JPMC_LOCAL_RUN_HANDOVER.md) — the no-Docker/no-Postgres local run
- [`STREAMLIT_RUN.md`](STREAMLIT_RUN.md) — run notes and limits
- [`HANDOVER_CONSOLIDATION.md`](HANDOVER_CONSOLIDATION.md) — internal: which
  handover docs are current vs superseded, and the verified ground truth
