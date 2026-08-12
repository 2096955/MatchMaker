# Handover — JPMC deployment agent

**Written:** 2026-08-12 · **Branch:** `main` @ `fd2a65e` (re-measure: `git rev-parse --short HEAD`)
**Status:** task is complete and ready for review. Nothing here has been pushed
or deployed.

You are picking up **deployment**. A separate work stream produced the
documentation set; this document is the deployment-specific extract, plus the
things that will cost you a day if you learn them the hard way.

**Every file named below is a clickable, repo-relative link** — they resolve
after a `git clone`.

**Read [§1](#1-the-five-facts-that-change-what-you-do) before anything else.**
Four of the five contradict what the documentation set implies, and one of
them ([§1.2](#12-store_backendaurora-is-not-a-thing)) will send you looking for
a configuration value that does not exist.

---

## 1. The five facts that change what you do

### 1.1 There is no Terraform in this repository

The client's stated blocker is *"they're struggling to understand how to use
Terraform for getting access to Bedrock and Aurora."* Measured:
`git ls-files | grep '\.tf$'` returns **zero rows**.

Everything here is **CloudFormation**, in [`infra/`](infra):

| Template | What it stands up |
|---|---|
| [`infra/scudo-poc-foundation.yaml`](infra/scudo-poc-foundation.yaml) | VPC, subnets, base IAM |
| [`infra/scudo-poc-app.yaml`](infra/scudo-poc-app.yaml) | ECS service, ALB, the container env — **this is the one you will edit** |
| [`infra/scudo-poc-build.yaml`](infra/scudo-poc-build.yaml) | CodeBuild project |
| [`infra/scudo-poc-frontend.yaml`](infra/scudo-poc-frontend.yaml) | S3 + CloudFront |
| [`infra/scudo-dev-*.yaml`](infra) | The same four for the dev account |

So the honest answer to the client is: **they do not need Terraform.** If their
platform team mandates it, that is a net-new port of five CloudFormation
templates, and it should be scoped as such — not presented as "just point
Terraform at it." Do not write Terraform speculatively; find out first whether
CloudFormation is acceptable in their account.

The runbook is [`infra/DEPLOY_RUNBOOK_scudo-poc.md`](infra/DEPLOY_RUNBOOK_scudo-poc.md).

### 1.2 `STORE_BACKEND=aurora` is not a thing

This is the one that wastes a day. The matching store factory
([`backend/scudo_mapping_mcp/store/factory.py`](backend/scudo_mapping_mcp/store/factory.py))
accepts exactly **four** values, and `aurora` is not one of them:

```
falkordb | neptune | memory | local_file
```

Anything else raises `ValueError: Unknown STORE_BACKEND '...'`. Verified by
reading the factory's own error string.

**Aurora is not a matching-store backend at all.** It is reached through a
completely separate surface — [`backend/scudo/`](backend/scudo), the
**Lambda/AWS half** of the system:
[`aurora_store.py`](backend/scudo/aurora_store.py) (audit/decision rows) and
[`aurora_memory.py`](backend/scudo/aurora_memory.py) (agent memory, skill
promotion). Nothing in `backend/scudo_mapping_mcp/` — the half that actually
does the matching — talks to Aurora.

Practical consequence: **switching the matcher to Aurora is not a config
change, because it is not a thing the matcher does.** If someone asks for
"matching state in Aurora", that is new work with a design decision behind it.

### 1.3 The deployed containers run `falkordb`, not the default

[`config.py:242`](backend/scudo_mapping_mcp/config.py) defaults
`STORE_BACKEND` to `local_file`. **Every deployed template overrides it:**

- [`infra/scudo-poc-app.yaml:269`](infra/scudo-poc-app.yaml) — `falkordb`
- [`infra/scudo-dev-deploy.yaml`](infra/scudo-dev-deploy.yaml) — `falkordb`, at four separate task definitions (`:421`, `:483`, `:526`, `:583`)

If you change the store backend, **there are five call sites, not one.** Grep
`STORE_BACKEND` across `infra/*.yaml` before you claim you have changed it.

### 1.4 Three different Bedrock regions are hardcoded, and they disagree

This is a live inconsistency, not a documentation artifact. All three verified
by reading source:

| Location | Default region |
|---|---|
| [`backend/scudo_mapping_mcp/agent.py:468-473`](backend/scudo_mapping_mcp/agent.py) | `eu-west-2` |
| [`backend/scudo/shared/bedrock.py:19`](backend/scudo/shared/bedrock.py) | `us-east-1` |
| [`backend/scudo/tests/cloudshell_demo.py:291`](backend/scudo/tests/cloudshell_demo.py) | `us-east-1` |

Each falls back to `AWS_REGION` first, so **setting `AWS_REGION` explicitly in
the task definition makes all three agree** — and
[`infra/scudo-poc-app.yaml:264`](infra/scudo-poc-app.yaml) does exactly that
(`!Ref AWS::Region`). The disagreement bites when something runs **outside**
that task definition: a local invoke, a script, a Lambda whose env you did not
set. Set `AWS_REGION` everywhere rather than trusting the defaults.

The model IDs disagree too, and this one matters more:

| Location | Default model |
|---|---|
| [`agent.py:124`](backend/scudo_mapping_mcp/agent.py) | `eu.anthropic.claude-opus-4-8` (**`eu.` prefix**) |
| [`infra/scudo-poc-app.yaml:74-75`](infra/scudo-poc-app.yaml) | `us.anthropic.claude-sonnet-5` (**`us.` prefix**) |

Those inference-profile prefixes are region-locked. An `eu.` model ID in a
`us-east-1` account fails at invoke time with an access/validation error that
reads like a permissions problem and is not. **Match the prefix to the region
you deploy in.**

### 1.5 Bedrock model access is the historical blocker — check it first

From verified project history: the hard blocker on the first AWS deployment was
**Bedrock model access not being granted in the account**, not IAM policy and
not networking. It presents as an `AccessDeniedException` on invoke that looks
exactly like a missing IAM permission.

Do this before anything else, in the target account and region:

```bash
aws bedrock list-foundation-models --region <region> --query 'modelSummaries[].modelId' --output text
```

If the model you intend to use is absent, someone with console access must
request it under **Bedrock → Model access**. That request can take time to
approve. **Start it on day one**, because it blocks the only interesting part
of the demo and no amount of deployment work routes around it.

---

## 2. What Aurora actually needs

Three environment variables, all **fail-loud** — the code raises before it ever
constructs a boto3 client, so a missing one gives you a clear error rather than
a timeout ([`aurora_store.py:23-26`](backend/scudo/aurora_store.py), and the
`_require(...)` calls at `:50-52`):

| Variable | What it is |
|---|---|
| `SCUDO_AURORA_CLUSTER_ARN` | the cluster ARN |
| `SCUDO_AURORA_SECRET_ARN` | the Secrets Manager secret holding the credentials |
| `SCUDO_AURORA_DATABASE_NAME` | the database name |

The error text is `"<NAME> is not set — Aurora persistence is required"`.

**Access is via the RDS Data API** (`boto3.client("rds-data")`), not a
PostgreSQL wire connection. That is a deliberate and load-bearing choice: no
VPC attachment is needed to reach the database, and credentials come from
Secrets Manager rather than a connection string. The execution role needs
`rds-data:ExecuteStatement` and `secretsmanager:GetSecretValue`.

**Schema bootstrap** is
[`infra/bootstrap_console_schema_data_api.py`](infra/bootstrap_console_schema_data_api.py).
Read its docstring before running it — two things in there are not obvious:

- It **refuses to run** the console schema file if either console-owned schema
  already has tables, because [`backend/init_db.sql`](backend/init_db.sql)
  intentionally **drops** the versioned `tp_*` tables. That refusal is a
  safety feature; do not force past it on a database with data in it.
- Console objects must be **schema-qualified** (`console.<name>`). It never
  creates unqualified tables in `public`, and it deliberately does not do the
  `ALTER TABLE ... SET SCHEMA` relocate dance — that was ambiguous under RDS
  Data API transaction semantics.

**Caveat, stated plainly:** no Aurora connection and no Bedrock invoke has ever
been executed against the JPMC account from this machine. Everything in this
section is read from source and CloudFormation. **You will be the first person
to actually run it.** Expect to find something.

---

## 3. The `CONSOLE_DB_*` gap

The console database variables — `CONSOLE_DB_HOST`, `CONSOLE_DB_PASSWORD`, and
the rest, consumed by [`backend/db.py`](backend/db.py) — appear in **no
CloudFormation template.** Verified.

There is also dead configuration in the templates: `my_sql_*` parameters left
over from the MySQL era. **MySQL is gone from the code** (zero imports,
measured), so those parameters configure nothing. Do not wire anything to them.

So the console DB is currently: fail-fast in code, unconfigured in infra. If
the console pages (Providers, Datasets, Admin, Ingestion) are in scope for the
deployment, **this is a genuine gap you need to close**, not something you have
overlooked in the templates. If they are not in scope, say so explicitly — the
matching path does not need them.

`backend/db.py` fails fast on a missing `CONSOLE_DB_PASSWORD` for any non-local
host, which is the correct behaviour and will tell you immediately if this is
unresolved.

---

## 4. Health checks — use `/healthz`, never `/readyz`

[`infra/scudo-poc-app.yaml:63-65`](infra/scudo-poc-app.yaml) defaults
`HealthCheckPath` to `/healthz`. **Keep it there.**

A fresh container returns **503 from `/readyz` with `error: null`**, because
seeding only runs when a mapping request arrives. Gate an ALB target group on
`/readyz` and the target **never becomes healthy** — the service will not come
up, and the error message tells you nothing. This has already cost time once.

---

## 5. Verification you can re-run

```bash
# Test suite — set PYTHONPATH or you get a spurious third failure
cd backend && PYTHONPATH=. python3.11 -m pytest scudo/tests/ -q   # 468 passed, 2 failed

# Confirm there really is no Terraform
git ls-files | grep -c '\.tf$'                                     # 0

# Every place STORE_BACKEND is set for a deployed container
grep -rn STORE_BACKEND infra/*.yaml                                # 5 hits, all falkordb

# Bedrock model access in the target account
aws bedrock list-foundation-models --region <region> --query 'modelSummaries[].modelId' --output text
```

**The 2 failures are expected and pre-existing**, both in
[`backend/scudo/tests/test_provenance.py`](backend/scudo/tests/test_provenance.py),
documented in [`CLAUDE.md`](CLAUDE.md) as unadjudicated. Do not "fix" them
without adjudicating. **If you see 3 failures you forgot `PYTHONPATH`** — the
extra one spawns a real subprocess that dies with `ModuleNotFoundError: No
module named 'scudo'`.

Measured 2026-08-12 at `fd2a65e`. Both numbers drift; re-run rather than quote.

---

## 6. One thing to be careful about saying to the client

Under the shipped defaults the **matching score is deterministic** —
`SCUDO_DENSE_BACKEND` defaults to `jaro_winkler`
([`config.py:301`](backend/scudo_mapping_mcp/config.py)), and the LLM only
narrates the result. That is a genuine selling point for an auditable system,
and [`infra/scudo-poc-app.yaml:76-79`](infra/scudo-poc-app.yaml) keeps that
default.

**But state the default as the reason — never say a cap protects you.** Under
`SCUDO_DENSE_BACKEND=opus` the LLM's score reaches published confidence
uncapped on four branches, **two of which auto-map with no human review**. A
comment in the Streamlit app asserting the opposite was corrected in source on
2026-08-12 for exactly this reason. Full derivation is in
[`FINAL_HANDOVER_2026-08-12.md`](FINAL_HANDOVER_2026-08-12.md) §3.

The `DenseBackend` CloudFormation parameter accepts `opus`. **Flipping it is a
governance decision, not a tuning knob.** If anyone asks for it, route it
through whoever owns model risk at JPMC.

Related: the agent is **not conversational**. `get_agent(provider).run(ref)` is
a generator over one product reference; there is no free-text entry point. The
client's ask about "engaging with the agents to intelligently query" today
means *watching a structured reasoning trace*. Genuine Q&A is new work — scope
it explicitly rather than letting the demo imply it exists.

---

## 7. Constraints inherited

- **The JPMC engineer types every change by hand** on a locked-down Citrix
  desktop. A wrong command or a stale line number costs them hours. Verify
  before you assert, and prefer symbol names over line numbers when you can —
  the `:NNN` suffixes in this document were correct at `fd2a65e` and will
  drift.
- **No commits, no deploys, unless the user asks.**
- **Do not claim production readiness** or "fully operational".
- **Never commit `jpmc-port/`, the repo-root `MEMORY.md`, or
  `backend/.local/`.** The first two carry local router artifacts that must not
  land in a client-facing repo; the third is a runtime database file. They are
  deliberately uncommitted, not overlooked.

---

## 8. Related

- [`infra/DEPLOY_RUNBOOK_scudo-poc.md`](infra/DEPLOY_RUNBOOK_scudo-poc.md) — the step-by-step deploy
- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — every file that needs touching for Aurora/Bedrock
- [`FINAL_HANDOVER_2026-08-12.md`](FINAL_HANDOVER_2026-08-12.md) — the documentation work stream's closing report
- [`JPMC_IMPORT_AGENT_BRIEF.md`](JPMC_IMPORT_AGENT_BRIEF.md) — the broader entry point
- [`README.md`](README.md) — where the agent, its tools and the engine live
- [`ZONES.md`](ZONES.md) — the approved 5-zone architecture
- [`backend/scudo/AWS_HANDOFF.md`](backend/scudo/AWS_HANDOFF.md) — prior deployment notes
- [`CLAUDE.md`](CLAUDE.md) — agent instructions and contracts
