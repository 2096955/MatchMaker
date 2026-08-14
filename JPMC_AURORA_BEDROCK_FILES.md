# Aurora + Bedrock — exactly which files to change

**Audience:** the JPMC engineer running the Streamlit + SQLite build who now
needs Bedrock (the agents) and Aurora (the memory).

**Read this first, it saves a day:**

1. **There is no Terraform in this repository.** Not one `.tf` file. Every
   piece of infrastructure-as-code here is **CloudFormation YAML**
   (`infra/scudo-poc-*.yaml`, `infra/scudo-dev-*.yaml`,
   [`backend/scudo/data-platform.yaml`](backend/scudo/data-platform.yaml)). If you are being asked to "do the
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
| 1a | Console CRUD (Providers/Datasets/Admin) on Aurora instead of SQLite | [`backend/db.py`](backend/db.py) — **read only, no edit** | **env vars only** |
| 1b | Run the console schema on Aurora | [`backend/init_db.sql`](backend/init_db.sql) (223 lines, already PostgreSQL) | **run it, don't edit it** |
| 2 | Agents on real Bedrock instead of the scripted narrator | [`streamlit_app.py`](streamlit_app.py) sidebar — **no edit needed** | **env vars + paste key** |
| 2a | The region/model mismatch (was a real bug, see §2.3) | *(none)* — **already fixed in the file you have** | **set `AWS_REGION`, type nothing** |
| 3 | Agent memory in Aurora rather than a local file | [`backend/scudo/aurora_memory.py`](backend/scudo/aurora_memory.py), [`backend/scudo/aurora_store.py`](backend/scudo/aurora_store.py) (exist, unreachable from Streamlit) | **new file needed** — optional |
| 4 | Let users **correct** the agent in the Streamlit UI | *(none)* — Approve/Reject **already present**, [`streamlit_app.py:1018-1101`](streamlit_app.py) | **nothing to type** — see §5 |

---

## §1 — "Aurora" is three separate systems

This is the single biggest source of confusion. Do not skip it.

| | Purpose | How it talks to Aurora | Env vars | Reachable from Streamlit? |
|---|---|---|---|---|
| **A. Console DB** | Providers, datasets, admin, ingest CRUD | `psycopg` direct connection | `CONSOLE_DB_*` | Yes (Flask only) |
| **B. Matching store** | Taxonomy nodes, candidates, precedents | `scipy_sqlite` (SQLite, single-host); **no Aurora backend** | `STORE_BACKEND` | Yes |
| **C. Agent memory** | Learned precedents, trajectories, skills | **RDS Data API** (`boto3` `rds-data`) | `SCUDO_AURORA_*` | **No** |

They use **different credentials and different AWS APIs**. A working
console DB tells you nothing about whether C works.

### 1A — Console DB: env vars only, zero code edits

[`backend/db.py`](backend/db.py) already speaks both. The switch is one env var:

- [`backend/db.py:93-100`](backend/db.py) — `_sqlite_enabled()` returns True only when
  `CONSOLE_DB_BACKEND=sqlite`
- [`backend/db.py:71-74`](backend/db.py) and `:86-89` — when sqlite, returns the
  `db_sqlite_fallback` connection
- [`backend/db.py:46-55`](backend/db.py) — otherwise `psycopg.connect(...)` to Aurora

**To move to Aurora: set `CONSOLE_DB_BACKEND` to anything other than
`sqlite`, and set these five.** (Do not just `unset` it if you launch via
Streamlit — [`streamlit_app.py:82`](streamlit_app.py) re-defaults it back to `sqlite`. See §3
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

Safety rail worth knowing: [`backend/db.py:41-45`](backend/db.py) **refuses to start** if
`CONSOLE_DB_HOST` is not localhost and `CONSOLE_DB_PASSWORD` is empty. That
error is the code protecting you from silently connecting nowhere, not a bug.

**Two known holes in the SQLite side — reproduced, not theoretical.** They
argue *for* moving to Aurora, and they only bite the local stand-in:

1. [`backend/db_sqlite_fallback.py:73-80`](backend/db_sqlite_fallback.py) `translate_params()` calls `.replace()` on
   the SQL, but [`backend/routes/datasets.py:103-110`](backend/routes/datasets.py) (CREATE TABLE) and `:131-137`
   (ALTER TABLE ADD COLUMN) pass a `psycopg.sql.Composed`, which has no
   `.replace` → `AttributeError`.
2. `ingestion/engine.py:559,566` uses `ing.transaction()` (including a nested
   per-row SAVEPOINT); `SqliteConnection` ([`backend/db_sqlite_fallback.py:178-193`](backend/db_sqlite_fallback.py))
   has no `transaction()` method → `AttributeError`.

Read paths are fine. It is schema-mutating Datasets operations and Ingestion
row-load that fail locally. Both work on Aurora, because those are ordinary
psycopg features — so if you hit either, that is the stand-in's limit, not
your configuration.

Call sites that light up (counted as actual `get_conn()` invocations, import
lines excluded): [`backend/routes/admin.py`](backend/routes/admin.py) (10), [`backend/routes/datasets.py`](backend/routes/datasets.py) (7),
[`backend/routes/providers.py`](backend/routes/providers.py) (5), [`backend/routes/ingest.py`](backend/routes/ingest.py) (2) — 24 in total.
**You do not edit any of them** — every one goes through `get_conn()`, which
is the single switch above.

### 1B — The schema

[`backend/init_db.sql`](backend/init_db.sql) is 223 lines of **portable PostgreSQL** and already
seeds admin roles/users. Run it against Aurora once. Do not retype or edit it.

Two warnings:
- It `DROP TABLE`s the `tp_*` tables before creating them — **re-running is
  destructive**.
- The database must already be named to match `CONSOLE_DB_NAME`.

### 1B(ii) — Aurora via Data API instead of a direct connection

If JPMC networking blocks port 5432 from your desktop, there is a Data API
bootstrapper: [`infra/bootstrap_console_schema_data_api.py`](infra/bootstrap_console_schema_data_api.py) (415 lines,
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

### 1C — Matching store: durable locally, still not Aurora

**Updated 2026-08-14.** An earlier version of this section said no durable
SQL-backed matching store existed. That is now out of date: `scipy_sqlite`
shipped and is the recommended local store. Aurora is still not a matching
backend.

[`backend/scudo_mapping_mcp/store/factory.py`](backend/scudo_mapping_mcp/store/factory.py) is the **only** place a
store is constructed. It accepts exactly five values and raises `ValueError`
on anything else:

| `STORE_BACKEND` | Implementation | Survives restart? |
|---|---|---|
| `memory` | [`backend/scudo_mapping_mcp/store/memory_store.py`](backend/scudo_mapping_mcp/store/memory_store.py) (152 lines) | No |
| `local_file` | [`backend/scudo_mapping_mcp/store/local_file_store.py`](backend/scudo_mapping_mcp/store/local_file_store.py) (393 lines) | **Yes — JSONL on disk** |
| `falkordb` | [`backend/scudo_mapping_mcp/store/falkordb_store.py`](backend/scudo_mapping_mcp/store/falkordb_store.py) | (not used at JPMC) |
| `neptune` | [`backend/scudo_mapping_mcp/store/neptune_store.py`](backend/scudo_mapping_mcp/store/neptune_store.py) | (not used at JPMC) |
| **`scipy_sqlite`** | [`backend/scudo_mapping_mcp/store/scipy_sqlite_store.py`](backend/scudo_mapping_mcp/store/scipy_sqlite_store.py) | **Yes — SQLite. Recommended** |

**There is still no `aurora_store` in that list**, and that matters for a
different reason than before. `scipy_sqlite` implements the full **16-method**
`RetrievalStore` contract ([`backend/scudo_mapping_mcp/store/base.py`](backend/scudo_mapping_mcp/store/base.py))
over SQLite, with revision-stamped SciPy sparse indexes — so durability is
solved. What it does **not** solve is *sharing*: it is **single-host only**.
Several ECS tasks or Lambdas cannot write to one SQLite file, which is exactly
what an Aurora-backed store would give you. That remains real work, and the
AWS templates deliberately stay on FalkorDB.

**Use `scipy_sqlite` for the demo** — it is the default in `start_local.py`,
`run_cognizant.py` and `streamlit_app.py`:

| Store | Durable? | Inspectable | Use it when |
|---|---|---|---|
| `scipy_sqlite` | **Yes** (SQLite) | `sqlite3` queries | **Default. Any real demo** |
| `local_file` | Yes (JSONL) | open it in an editor | You want to *show* the journal on screen |
| `memory` | No | — | Throwaway checks |

**Whichever you choose, all three launchers must agree.** Streamlit and Flask
are separate processes; if one is on `scipy_sqlite` and the other on
`local_file`, a decision approved in the UI is invisible to the API — a silent
split-brain that looks exactly like the memory not working.

It is **not** a drop-in for everything [`backend/scudo/aurora_memory.py`](backend/scudo/aurora_memory.py) does. That module
also holds priors, trajectories and promoted skills (`consult_priors`,
`record_trajectory`, `harvest_trajectories`, `consult_best_skill`,
`promote_skill`). [`backend/scudo_mapping_mcp/store/local_file_store.py`](backend/scudo_mapping_mcp/store/local_file_store.py) implements **none** of those — a grep
for `trajector|skill|prior` across it returns one comment and nothing else.
So: precedent learning works locally and is what the demo shows; the
self-improvement half is **Aurora-backed and offline-run** — not a Lambda-only
thing, but not a Streamlit thing either. It is a separate nightly job:
`python -m scudo.scripts.run_sleep_cycle_job --apply`, with the three
`SCUDO_AURORA_*` variables set (see 1C(ii)).

```bash
export STORE_BACKEND=scipy_sqlite
export SCUDO_PERSIST_TARGET=scipy_sqlite
export SCUDO_SCIPY_SQLITE_PATH=backend/.local/scudo_matching.sqlite3
```

### 1C(ii) — Agent memory (`SCUDO_AURORA_*`) — the honest status

[`backend/scudo/aurora_memory.py`](backend/scudo/aurora_memory.py) and [`backend/scudo/aurora_store.py`](backend/scudo/aurora_store.py) are
written and working, but they reach Aurora through the **RDS Data API**:

- [`backend/scudo/aurora_store.py:19-20`](backend/scudo/aurora_store.py) — `_rds_data()` returns
  `boto3.client("rds-data")`. (The bare `import boto3` is at `:16`.)
- [`backend/scudo/aurora_store.py:50-52`](backend/scudo/aurora_store.py) — the three
  `_require(...)` calls inside `_execute()`: **`SCUDO_AURORA_CLUSTER_ARN`**,
  **`SCUDO_AURORA_SECRET_ARN`**, **`SCUDO_AURORA_DATABASE_NAME`**. They are
  deliberately validated *before* the boto3 client is constructed, so a missing
  variable fails loud without touching boto3.

**Verified: no Flask route, and not [`streamlit_app.py`](streamlit_app.py), imports either
module.** They are reached from [`backend/scudo/lambda_handler.py`](backend/scudo/lambda_handler.py), the AWS entrypoints, and
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

[`backend/scudo_mapping_mcp/agent.py:1242-1281`](backend/scudo_mapping_mcp/agent.py) — `get_agent(provider)`
(span confirmed by AST, not by eye — an earlier draft said `:1224-1263`, which lands
inside `AzureMappingAgent` and shows you no dispatch logic at all):

- `get_agent("bedrock")` → `BedrockMappingAgent` **unconditionally**
- `get_agent("scripted")` → `ScriptedMappingAgent` **unconditionally**
- `get_agent(None)` → falls back to `SCUDO_AGENT_BACKEND`

The Streamlit sidebar already passes this: [`streamlit_app.py:547`](streamlit_app.py)
(the "Agent" dropdown) and the run block (`get_agent(provider).run(ref)`).

**So: pick "bedrock" in the sidebar, paste the key, press "Apply & test".
That is the whole activation path.** No file edit.

### 2.2 What Bedrock actually needs

**Read this first if you are at JPMC.** Your `cdao_poc.py` proves your account
uses **`sts.assume_role` → temporary credentials**, and an
**application-inference-profile ARN** as the model id. That is a *different*
mechanism from the bearer token this document originally described, and it
changes which parts apply to you.

| | Cognizant sandbox (original) | **JPMC (your `cdao_poc.py`)** |
|---|---|---|
| Auth | `AWS_BEARER_TOKEN_BEDROCK`, ~12 h | **`sts.assume_role`** on a role ARN |
| Model id | `us.anthropic.claude-opus-4-8` | **application-inference-profile ARN** |
| Region | carried inside the token | explicit `us-east-1` |

**Good news: neither needs a code change.** Both were verified here.

**1. The ARN works as a model id, verbatim.** Measured: constructing
`BedrockModel(model_id="arn:aws:bedrock:us-east-1:...:application-inference-profile/...")`
stores that exact string. So the `us.`/`eu.` prefix logic in §2.3 is simply
**irrelevant to you** — you bypass it by setting the ARN explicitly:

```bash
export SCUDO_BEDROCK_MODEL_ID="arn:aws:bedrock:us-east-1:<acct>:application-inference-profile/<id>"
export AWS_REGION=us-east-1
```

**2. Assume-role needs no code either.** The agent builds
`BedrockModel(model_id=..., region_name=...)`
([`backend/scudo_mapping_mcp/agent.py:509-512`](backend/scudo_mapping_mcp/agent.py))
with **no explicit credentials**, so botocore resolves them through its normal
chain. Measured: that chain contains **`assume-role` as its second provider**,
ahead of SSO and shared credentials. Meaning a profile in `~/.aws/config` is
enough — no `sts.assume_role` call in Python:

```ini
[profile bedrock-poc]
role_arn       = arn:aws:iam::<acct>:role/app-bedrock-access-...
source_profile = default
region         = us-east-1
```

then `export AWS_PROFILE=bedrock-poc` before launching Streamlit. botocore
refreshes the temporary credentials for you, which your PoC script has to do
by hand.

**If `~/.aws/config` is not writable, or a stale `AWS_PROFILE` is in the way,
use this instead.** It needs no profile at all — export the three values your
`cdao_poc.py` already obtains from `assume_role`:

```bash
export AWS_ACCESS_KEY_ID=<AccessKeyId>
export AWS_SECRET_ACCESS_KEY=<SecretAccessKey>
export AWS_SESSION_TOKEN=<SessionToken>
export AWS_REGION=us-east-1
```

Measured: botocore resolves these as method `env`, carrying the session token,
with no profile present. The trade-off is that they **expire** (typically
1 hour) and nothing refreshes them — re-export and restart Streamlit when
calls start failing with an expiry error.

> **Watch out for a stale `AWS_PROFILE`.** If it names a profile that does not
> exist (e.g. `adfs` on a Citrix desktop that never provisioned it), botocore
> raises `ProfileNotFound` **and does not fall back** — measured: it fails even
> when valid `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are set. It surfaces
> at the first AWS call, not at start-up, so it looks like a Bedrock fault.
> Check with `echo $AWS_PROFILE` and `aws configure list-profiles`, then
> `unset AWS_PROFILE` if it names something absent.

| Env var | Set by | Notes |
|---|---|---|
| `AWS_PROFILE` **or** `AWS_BEARER_TOKEN_BEDROCK` | your shell / the sidebar box ([`streamlit_app.py:603`](streamlit_app.py)) | **profile for JPMC**; bearer token expires ~12 h |
| `SCUDO_BEDROCK_MODEL_ID` | your shell (JPMC: the ARN) or the sidebar dropdown | overrides the default |
| `AWS_REGION` | your shell | drives the model IDs, the preflight (`:158-184`, `:383`) **and** the agent ([`backend/scudo_mapping_mcp/agent.py:467-472`](backend/scudo_mapping_mcp/agent.py)) — see §2.3 |

**The one thing that will mislead you:** the sidebar preflight refuses to test
the role path. [`streamlit_app.py:374-375`](streamlit_app.py) returns
*"No API key set"* whenever `AWS_BEARER_TOKEN_BEDROCK` is empty — even when
your role credentials are working perfectly. **On a role-based desktop the
sidebar will look unhappy while the run succeeds.** Judge it by pressing
"Run match", not by the sidebar.

**A second trap, worth knowing before you demo:** your `cdao_poc.py` calls
**`invoke_model`**; the agent calls **`ConverseStream`** (via Strands). Those
are *separately authorised* IAM actions. A role that passes your PoC can still
be denied for the agent. Ask for `bedrock:InvokeModelWithResponseStream`
explicitly — see §4.

Dependencies are already in [`backend/requirements.txt`](backend/requirements.txt)
— `boto3` and `strands-agents`, at **`:13-14`** as of 2026-08-12. (An earlier
draft said `:12-13`; a `scipy>=1.16,<2` line was inserted at `:11` by the
self-improvement work stream and pushed both down one. **Match on the package
name, not the line number** — this file is edited by more than one work
stream.) Both are **lazy-imported**, which is why the SQLite/scripted build
works without AWS at all.

### 2.3 Region and model prefix — already handled, one env var to set

> **JPMC: you can skim this section.** It governs how the *sidebar dropdown*
> builds `us.`/`eu.` model ids. You are setting `SCUDO_BEDROCK_MODEL_ID` to a
> full inference-profile ARN (§2.2), which overrides all of it. Only
> `AWS_REGION` still matters to you.

There *was* a defect here (preflight pinned to `us-east-1` while the agent
defaulted to `eu-west-2`, so the sidebar could go green and the run then fail
on auth). **It is already fixed in the current [`streamlit_app.py`](streamlit_app.py) — do not
retype anything.** For your understanding:

| Location | Behaviour now |
|---|---|
| [`streamlit_app.py:158-160`](streamlit_app.py) | `SCUDO_REGION` ← `AWS_REGION` → `AWS_DEFAULT_REGION` → `eu-west-2` |
| [`streamlit_app.py:168-174`](streamlit_app.py) | `_MODEL_PREFIX` derived from the region: `eu-` → `eu.`, `us-` → `us.`, else empty |
| [`streamlit_app.py:176-180`](streamlit_app.py) | model IDs are built from that prefix |
| [`streamlit_app.py:383`](streamlit_app.py) | preflight uses `region_name=SCUDO_REGION` — the same region as the run |
| [`backend/scudo_mapping_mcp/agent.py:123`](backend/scudo_mapping_mcp/agent.py) | agent default is `eu.anthropic.claude-opus-4-8` |
| [`backend/scudo_mapping_mcp/agent.py:465-472`](backend/scudo_mapping_mcp/agent.py) | agent region: `AWS_REGION` → `AWS_DEFAULT_REGION` → `eu-west-2` |

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

- [`backend/scudo_mapping_mcp/opus_dense.py:149`](backend/scudo_mapping_mcp/opus_dense.py) imports `_jaro_winkler` from [`backend/scudo_mapping_mcp/store/falkordb_store.py`](backend/scudo_mapping_mcp/store/falkordb_store.py)
- defined at [`backend/scudo_mapping_mcp/store/falkordb_store.py:166`](backend/scudo_mapping_mcp/store/falkordb_store.py)
- [`backend/scudo_mapping_mcp/store/memory_store.py:35`](backend/scudo_mapping_mcp/store/memory_store.py) imports the same function

[`backend/scudo_mapping_mcp/agent.py:572`](backend/scudo_mapping_mcp/agent.py) — *"matcher runs regardless of what the LLM recommended"*.

State the precondition, because it is one env var wide: this holds while
`SCUDO_DENSE_BACKEND` is unset or `jaro_winkler`, which is the default
([`backend/scudo_mapping_mcp/config.py:301`](backend/scudo_mapping_mcp/config.py)). Set `SCUDO_DENSE_BACKEND=opus` and the model **becomes
the score** — [`backend/scudo_mapping_mcp/store/memory_store.py:73-77`](backend/scudo_mapping_mcp/store/memory_store.py) calls `opus_dense_score()` and
`:109-115` assigns it straight to `Candidate.similarity`, and with no
specialist [`backend/scudo_mapping_mcp/matching.py:439-443`](backend/scudo_mapping_mcp/matching.py) takes `confidence = best.similarity`
unchanged. It can move the number **either way**, not just down. (The
`min(best, specialist)` cap at [`backend/scudo_mapping_mcp/matching.py:471-479`](backend/scudo_mapping_mcp/matching.py) applies only in the
narrower case where a specialist concurs.) **Leave it unset** — that is what
makes the demo's reproducibility claim true.

Two consequences to say out loud in a demo:

1. Switching Opus → Sonnet → Haiku **will not change the number**. The model
   narrates the reasoning; the matcher scores. That is the architecture, and
   it is a feature: the score is reproducible and auditable.
2. **If Bedrock fails *at invoke time*, you still get a score.** The invoke
   is wrapped at [`backend/scudo_mapping_mcp/agent.py:751-766`](backend/scudo_mapping_mcp/agent.py), which yields an `error` event and then
   lets the matcher run anyway (`:571`). The UI warns you
   ([`streamlit_app.py:827-833`](streamlit_app.py)) and drops the card accent to neutral
   (`:864-867`) — without that warning a failed Bedrock run looks identical
   to a successful one. Trust the warning.

   **Two earlier failures are not covered and give you no score at all:**
   a missing `strands-agents` package ([`backend/scudo_mapping_mcp/agent.py:499-506`](backend/scudo_mapping_mcp/agent.py) raises
   `RuntimeError`) and `BedrockModel(...)` construction ([`backend/scudo_mapping_mcp/agent.py:508-511`](backend/scudo_mapping_mcp/agent.py)).
   Both happen *before* that try/except, and [`streamlit_app.py:815`](streamlit_app.py) iterates
   the generator with no handler — so you get a red Streamlit traceback
   rather than a degraded result. If you see that, it is setup, not
   credentials: `pip install strands-agents` and re-run.

### 2.5 Do not delete [`backend/scudo_mapping_mcp/store/falkordb_store.py`](backend/scudo_mapping_mcp/store/falkordb_store.py)

Even with FalkorDB unused, the default scoring path imports
`_jaro_winkler` from that file. Deleting it breaks matching entirely. The
**pip package** is not needed (the real `import falkordb` is inside a
method) — only the file must stay on disk. [`backend/scudo_mapping_mcp/store/factory.py:21-32`](backend/scudo_mapping_mcp/store/factory.py) says this too.

---

## §3 — Copy-paste run configurations

Three configurations, in increasing order of AWS dependency. Each is
complete — nothing implied.

### Config A — what you have now (no AWS, no DB)

```bash
export STORE_BACKEND=scipy_sqlite        # durable SQLite matching store
export SCUDO_PERSIST_TARGET=scipy_sqlite
export CONSOLE_DB_BACKEND=sqlite
export FRAME_SOURCE=mock
export SCUDO_AUTH_ALLOW_DEV=1
export SCUDO_AUTH_DEV_PRINCIPAL=streamlit@local
export SCUDO_VERDICT_ALLOW_DEV=1
export SCUDO_PERSIST_ALLOW_DEV_WRITES=1
streamlit run streamlit_app.py
```

[`streamlit_app.py:75-82`](streamlit_app.py) sets all of these via `setdefault`, so it already
works with no exports at all. `local_file` is chosen automatically when your
checkout supports it (`_best_local_store`, [`streamlit_app.py:49-71`](streamlit_app.py)).

### Config B — add Bedrock (agents become real, still no DB)

**B(i) — JPMC: assume-role + inference-profile ARN.** Everything in A, plus:

```bash
export AWS_PROFILE=bedrock-poc           # the ~/.aws/config profile from §2.2
export AWS_REGION=us-east-1
export SCUDO_BEDROCK_MODEL_ID="arn:aws:bedrock:us-east-1:<acct>:application-inference-profile/<id>"
streamlit run streamlit_app.py
# in the sidebar: Agent = "bedrock", then press "Run match".
# IGNORE the red "No API key set" — it only tests the bearer-token path (§2.2).
```

Sanity-check the credentials outside the app first — if this fails, the app
will too, and the error is clearer here:

```bash
aws sts get-caller-identity --profile bedrock-poc
```

**B(ii) — bearer-token accounts.** Everything in A, plus:

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

**Why `export …=postgres` and not `unset`.** [`streamlit_app.py:82`](streamlit_app.py) does
`os.environ.setdefault("CONSOLE_DB_BACKEND", "sqlite")` at import time, so if
you unset the variable the app puts `sqlite` straight back and you stay on the
file. `_sqlite_enabled()` ([`backend/db.py:93-100`](backend/db.py)) tests for the literal string
`sqlite`, so **any other non-empty value** selects the PostgreSQL path;
`postgres` is just a readable choice.

[`start_local.py:65`](start_local.py) does the same defaulting for the Flask console, so `unset`
does not work there either. `unset` is only sufficient if you launch
[`backend/app.py`](backend/app.py) **directly**, which is not the documented way to run it.
Simplest rule: always `export CONSOLE_DB_BACKEND=postgres` — it is correct for
all three launch paths.

Then run [`backend/init_db.sql`](backend/init_db.sql) once against that database.

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
2. An IAM role you can assume (JPMC's path) **or** an API key
   (`bedrock-api-key-...`)
3. **`bedrock:InvokeModelWithResponseStream` on that role — ask for it by
   name.** This is the one most likely to bite you. Your `cdao_poc.py` proves
   `bedrock:InvokeModel`, and people reasonably assume that covers everything.
   It does not: the agent uses **`ConverseStream`**, which is authorised
   separately. A role that passes your PoC can still be denied for the agent,
   and the failure appears only at demo time. (The same bug already bit once
   here, which is why the preflight streams —
   [`streamlit_app.py:394`](streamlit_app.py).)
4. If you use an **application-inference-profile ARN**, permission on the
   profile ARN itself — not just on the underlying foundation model.

**Diagnose credentials before blaming the app.** Run these in the same shell
you launch Streamlit from, in this order — the first one that fails is your
answer:

```bash
echo $AWS_PROFILE                 # names a profile that does not exist? unset it
aws configure list-profiles       # what this desktop actually has
aws sts get-caller-identity       # do you have ANY working base credential?
aws sts assume-role --role-arn <ROLE_ARN> --role-session-name probe   # can you assume it?
```

A note on `ProfileNotFound`: it proves the *selector* is broken, not that
assume-role is unavailable. Clearing `AWS_PROFILE` only unblocks resolution —
it cannot tell you whether assume-role would work, because **`role_arn` and
`source_profile` live inside a profile**. Measured: with no profile selected
botocore never attempts assume-role at all. So use `get-caller-identity` to
find the base credential, and the explicit `assume-role` call to test the
role.

A precise ask for your platform team:

> Please grant role `app-bedrock-access-…` the actions `bedrock:InvokeModel`
> **and `bedrock:InvokeModelWithResponseStream`**, on both the foundation
> model and the application-inference-profile ARN, in `us-east-1`.

**For Aurora** — you need one of:
- Network reachability to port 5432 plus a Secrets Manager password
  → then §1A is env vars only, or
- RDS **Data API** enabled on the cluster plus the cluster/secret ARNs
  → then use [`infra/bootstrap_console_schema_data_api.py`](infra/bootstrap_console_schema_data_api.py)

If your platform team insists on Terraform, they are writing it fresh
against those requirements. The existing CloudFormation templates
([`infra/scudo-poc-foundation.yaml`](infra/scudo-poc-foundation.yaml), [`infra/scudo-poc-app.yaml`](infra/scudo-poc-app.yaml)) are the
reference for what resources are expected — hand those over as the spec.

---

## §5 — Correcting the agent, and what it remembers

The requirement is: *users engage with the agents to query and correct the
system, and it remembers.* Here is the true state.

### The correction UI exists in Streamlit — use it

[`streamlit_app.py:1018-1101`](streamlit_app.py) renders a **Reviewer decision** block with
**Approve** and **Reject** buttons after a match, calling `apply_decision`
into whichever store is live. Under `STORE_BACKEND=local_file` the decision
is journalled and replayed at startup, so **it survives a restart with no
Aurora and no Bedrock**.

Four details in that code you should know before demoing:

- **The buttons live outside the `run_clicked` block on purpose**
  ([`streamlit_app.py:1004-1012`](streamlit_app.py)). Streamlit reruns the script on every click;
  a button drawn inside the run block would vanish before its own click was
  processed and fail *silently*. Do not "tidy" them back inside.
- **Staleness guard** (`:1025-1026`): change vendor or product without
  re-running and the buttons disappear, rather than recording a decision
  against a product nobody is looking at.
- **Approve and reject are not symmetric** (`:1083-1094`). Measured:
  approve → the next match short-circuits to that node; reject → the node is
  filtered out and the match re-ranks without it (0.9083 equity-prices became
  0.6138 fixed-income).
- **Failures are reported, never swallowed** (`:1069-1080`) — including an
  unwritable journal directory, which is realistic on a locked-down desktop
  or a network share. If you see no green message, nothing was recorded.

### What works underneath

**The correction loop is real and it does learn.** Verified end to end:
a match scoring 0.5294 `needs_review`, approved by a human, then re-matched
→ returns the approved node with rationale `"precedent"`.

- **Ingress:** [`backend/routes/mapping.py:585`](backend/routes/mapping.py) `record_decision()` —
  approve / override / reject
- **Storage:** [`backend/scudo_mapping_mcp/store/base.py:78-117`](backend/scudo_mapping_mcp/store/base.py) `upsert_precedent()` — positive
  precedents for approve/override, **negative** precedents for reject so the
  triple is filtered out of future candidates
- **Durability:** with `STORE_BACKEND=local_file`, every decision is one JSON
  line in `backend/local_memory/precedents.jsonl`, replayed on startup
  through the *same* `upsert_precedent` the live path uses
  ([`backend/scudo_mapping_mcp/store/local_file_store.py:92-143`](backend/scudo_mapping_mcp/store/local_file_store.py)) — so replay cannot drift from live
- **Recall:** the next match short-circuits to the human-confirmed result and
  the rank signal boosts that node for products with the same vendor
  signature

### What genuinely does NOT exist — set expectations here

**RESOLVED 2026-08-14 — free-text chat now exists.**
`backend/scudo_mapping_mcp/chat.py` adds a chat layer over the SAME six tools,
surfaced as Streamlit step 04. The MAPPING entry point is unchanged
(`get_agent(provider).run(ref)`, a generator over one product reference).

So "users query the agents intelligently" now means:
- watch a **structured reasoning trace** (thinking / calls / returns) ✅
- **correct** the outcome with Approve / Reject, and have it remembered ✅
- **ask the agent an open question in your own words** ✅ — `bedrock` backend
  gives a real tool-calling loop; `scripted` is a keyword-routed no-AWS
  stand-in that says so in its own replies

**`override` is not exposed in Streamlit.** The store supports it
([`backend/scudo_mapping_mcp/store/base.py:78-117`](backend/scudo_mapping_mcp/store/base.py)) and the Flask API accepts it, but the Streamlit UI
offers only Approve and Reject. Correcting a match *to a different node* from
that screen would be new UI — ask before building it.

**The demo story that IS true end to end:**
match → correct → re-match → it remembered — the evidence is in
`backend/.local/scudo_matching.sqlite3` (`positive_precedents`), or as a
readable `precedents.jsonl` if you run `STORE_BACKEND=local_file`. That
is a stronger demo than a database nobody can inspect, and it needs neither
Aurora nor Bedrock.

---

## §6 — Files by change type (the checklist)

**Do not edit — read only:**
| File | Why |
|---|---|
| [`backend/db.py`](backend/db.py) | already dual-mode; env vars decide |
| [`backend/init_db.sql`](backend/init_db.sql) | run it; do not retype (destructive re-run) |
| [`backend/scudo_mapping_mcp/store/factory.py`](backend/scudo_mapping_mcp/store/factory.py) | the swap point already works |
| [`backend/scudo_mapping_mcp/store/falkordb_store.py`](backend/scudo_mapping_mcp/store/falkordb_store.py) | **must stay on disk** — supplies `_jaro_winkler` |
| [`backend/scudo/aurora_memory.py`](backend/scudo/aurora_memory.py), [`backend/scudo/aurora_store.py`](backend/scudo/aurora_store.py) | Lambda-side; unreachable from Streamlit. **Read-only *for the Streamlit/Bedrock switch-over* — not globally**: [`MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md`](MATCHING_AGENT_DEPLOYMENT_CONSOLIDATION.md) edits both as part of a separate work stream. That is not a violation of this checklist. |
| [`infra/bootstrap_console_schema_data_api.py`](infra/bootstrap_console_schema_data_api.py) | run it if 5432 is blocked |

**Small typed edits — none required for Bedrock:**
| File | Lines | Change |
|---|---|---|
| *(none)* | — | the region/prefix defect is **already fixed** — see §2.3 |

**New code, only if you want it (§5):**
| File | Change |
|---|---|
| [`streamlit_app.py`](streamlit_app.py) | an **Override** control (Approve/Reject already exist) |
| [`backend/scudo/aurora_store.py`](backend/scudo/aurora_store.py) (new) | only if matching precedents must live in Aurora; **16** abstract methods on `RetrievalStore` ([`backend/scudo_mapping_mcp/store/base.py`](backend/scudo_mapping_mcp/store/base.py)), counted from the AST |

**Env vars only — no file changes at all:** everything in §3.

---

## Verification basis

Every file:line citation above was read from the working tree on
**2026-08-12** and re-checked after writing (the Streamlit file had moved on
under two earlier drafts — the region/prefix defect and the missing correction
UI were both already fixed, and this document was corrected rather than
telling you to re-fix working code).

### Executed on this machine — you can re-run these

**JPMC's auth path needs no code change (2026-08-14).** Two measurements,
prompted by the `cdao_poc.py` script:

- `BedrockModel(model_id="arn:aws:bedrock:…:application-inference-profile/…")`
  stores that ARN verbatim — an inference-profile ARN is accepted as a model
  id, so the `us.`/`eu.` prefix logic is bypassed entirely.
- botocore's credential resolver lists **`assume-role` as its second
  provider** (after `env`, ahead of SSO and shared credentials). So an
  `~/.aws/config` profile with `role_arn` + `source_profile` is sufficient;
  the agent's credential-free `BedrockModel(...)` construction picks it up.
- Exporting `assume_role`'s three outputs as `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` resolves as method `env` with
  the token carried, **with no profile present** — the fallback when
  `~/.aws/config` cannot be written.

**Two `AWS_PROFILE` facts (2026-08-14), both measured:**

- A stale `AWS_PROFILE` naming a non-existent profile raises `ProfileNotFound`
  **and does not fall back to the rest of the chain** — it fails even with
  valid `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` exported. It is raised at
  credential *resolution*, not at `Session()` construction, so it appears at
  the first AWS call and reads as a Bedrock failure.
- With **no** profile selected, botocore never attempts assume-role, because
  `role_arn`/`source_profile` are profile-scoped. Clearing `AWS_PROFILE`
  therefore cannot prove or disprove that assume-role works.

Not verified: an actual invoke against JPMC's account. Whether that role holds
`InvokeModelWithResponseStream` (not just `InvokeModel`) can only be confirmed
there — see §4.

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

**No Aurora matching store exists** (though `scipy_sqlite` now covers
durability on a single host). `get_store()` with
`STORE_BACKEND=aurora` raises `ValueError: Unknown STORE_BACKEND 'aurora'`.
`RetrievalStore` has **16** abstract methods a new store must implement.

One nuance found while verifying: `Settings.from_env()` *accepts*
`STORE_BACKEND=aurora` — only `SCUDO_PERSIST_TARGET` is validated against an
allow-list. The refusal comes from `get_store()`, so a bad `STORE_BACKEND`
fails at first store use, not at import.

**`scipy_sqlite` durability, measured 2026-08-14.** Ingest → match →
approve → **new process** → same contract:

| | Result |
|---|---|
| Process 1 | `Q-CONTRACT-X` scored **0.8317 pass**, approve returned 200 |
| SQLite tables | `positive_precedents` = 1, `negative_precedents` = 0 |
| **Process 2 (fresh)** | status **`approved`**, rationale **`precedent`** |

Note the table names: counting a `precedents` table would silently report zero
for ever. Decisions live in `positive_precedents` *and* `negative_precedents`.

### Not verified — be honest about these

- **A real Aurora connection and a live Bedrock invoke.** Neither is reachable
  from this machine. §1A and §3 Config C are read from the code path, not
  executed against a cluster.
- **Independent review — CORRECTED 2026-08-12.** This bullet previously said
  "Codex CLI is not installed here", which was wrong and would have told you the
  review gate could not be established. `codex-cli 0.145.0` **is** installed
  (`/Users/anthonylui/bin/codex`) and **two full review rounds ran against this
  document** — 12 findings, all applied — plus a later round of 7 findings on
  [`JPMC_IMPORT_AGENT_BRIEF.md`](JPMC_IMPORT_AGENT_BRIEF.md), each hand-verified
  against source before being applied. What *did* fail was a fan-out of three
  parallel audit agents, on a provider error — a subagent problem, not a Codex
  one. If you want another round, just call Codex.
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
