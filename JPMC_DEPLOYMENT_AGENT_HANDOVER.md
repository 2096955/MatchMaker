# Handover — JPMC deployment agent

**Written:** 2026-08-12 · **Amended:** 2026-08-13 (see [§0](#0-amendment--the-target-moved))
**Branch:** `main` @ `b974c97` (re-measure: `git rev-parse --short HEAD`)
**Status:** task is complete and ready for review. Nothing here has been pushed
or deployed.

You are picking up **deployment**. A separate work stream produced the
documentation set; this document is the deployment-specific extract, plus the
things that will cost you a day if you learn them the hard way.

**Every file named below is a clickable, repo-relative link** — they resolve
after a `git clone`.

**Read [§0](#0-amendment--the-target-moved) first, then
[§1](#1-the-five-facts-that-change-what-you-do).** §0 was added a day later and
narrows which parts of §1–§4 apply to you.

---

## 0. Amendment — the target moved

**Added 2026-08-13**, after a JPMC deployment agent supplied the real landscape
(`JPMC_BEDROCK_AURORA_AGENT_HANDOVER.md`, not in this repo — ask for it).
§1–§8 below were written assuming you deploy **this repository** via its own
CloudFormation. That assumption is wrong for the JPMC target.

|  | §1–§8 assume | The JPMC landscape |
|---|---|---|
| Repository | this one, `infra/scudo-poc-*.yaml` | JPMC Bitbucket, `lambdas/vendor_onboarding/` |
| Account / region | our demo accounts | `358183960702` / `us-east-1` |
| Compute | our CloudFormation ECS stack | existing cluster `mds-matrix` |
| Bedrock auth | ambient credentials | STS assume-role → **application inference profile ARN** |
| Aurora | RDS Data API | **direct PostgreSQL wire on `:5432`** |

**Nothing in this repository has been deleted or rewritten to match.** This
section tells you which parts of §1–§8 still hold. Where they conflict, §0 wins.

> **Which repo am I editing?** Every file path and line number in this document
> locates code in **this** repository. The JPMC target is a *different* repo
> (`lambdas/vendor_onboarding/`), so a named function like `_connect` may not
> exist there at all. Read the code edits below as **the pattern to replicate**
> and the counts (seven Bedrock sites, five env vars) as **facts about this
> repo** — re-derive both against whatever you are actually typing into. The
> `# [ALUI]` marking convention applies to lines you insert in **JPMC's** repo.

### 0.1 What survives unchanged

- [§1.2](#12-store_backendaurora-is-not-a-thing) — `STORE_BACKEND=aurora` is
  still not a valid value. Still the single most expensive misconception here.
- [§1.3](#13-the-deployed-containers-run-falkordb-not-the-default) — five
  `STORE_BACKEND` call sites, if you deploy our templates at all. Still
  `falkordb` in every template even after
  [§0.8](#08-new-a-falkordb-free-matching-store-scipy_sqlite) — deliberately so.
- [§4](#4-health-checks--use-healthz-never-readyz) — `/healthz`, never
  `/readyz`. Applies to any ALB target group anywhere.
- [§6](#6-one-thing-to-be-careful-about-saying-to-the-client) — the
  deterministic-score caveat. **The JPMC document independently corroborates
  this**: *"Bedrock must explain a recorded deterministic result. It must not
  silently become the authority for mapping, validation, or persistence
  decisions."* Two documents, arrived at separately, same conclusion.

### 0.2 What is now wrong or misleading

| Section | Status for the JPMC target |
|---|---|
| [§1.1](#11-there-is-no-terraform-in-this-repository) | Still true (zero `.tf` files) — but the JPMC landscape mentions **no CloudFormation either**. Do not offer our templates as the answer without asking what `mds-matrix` is already deployed with. |
| [§1.4](#14-three-different-bedrock-regions-are-hardcoded-and-they-disagree) model-ID prefixes | **Superseded.** An inference-profile ARN has no `us.`/`eu.` prefix. The region advice still holds. |
| [§1.5](#15-bedrock-model-access-is-the-historical-blocker--check-it-first) command | **Wrong tool.** `aws bedrock list-foundation-models` does not list application inference profiles. See [§0.4](#04-bedrock--three-facts-and-one-verified-non-problem). |
| [§2](#2-what-aurora-actually-needs) | **Wrong module.** `aurora_store.py` is RDS Data API; JPMC is wire-protocol. See [§0.5](#05-aurora--we-already-have-the-wire-protocol-path). |
| [§3](#3-the-console_db_-gap) | **Inverted.** `CONSOLE_DB_*` is not a gap here — it is the asset you port. See [§0.5](#05-aurora--we-already-have-the-wire-protocol-path). |

### 0.3 Test counts — do not cross-check against theirs

The JPMC document reports `309/2` and `422` mapping tests. **Those are not this
repository's numbers.** Ours, measured at `b974c97`:

```bash
cd backend && PYTHONPATH=. python3.11 -m pytest scudo/tests/ -q   # 468 passed, 2 failed
```

Different repositories, different suites. A mismatch here is **not** a
regression. The 2 failures are the known pre-existing `test_provenance.py`
ones ([§5](#5-verification-you-can-re-run)).

### 0.4 Bedrock — three facts, and one verified non-problem

**Do these in this order.** Item 1 may delete all the work in item 2.

**1. Ask which IAM path JPMC is taking — before writing any code.** Their own
document offers two, and they are not equivalent for us:

- *Grant `bedrock:InvokeModel` directly to the ECS task role* — **needs zero
  code change.** Set `SCUDO_BEDROCK_MODEL_ID` to the profile ARN and it works.
- *Task role assumes the Bedrock role* — needs the code in item 2.

Push for the first. It is one IAM policy against roughly 30 hand-typed lines
across five files on a Citrix desktop.

**2. If assume-role is mandated: it does not exist in this repo.** Verified —
`assume_role` and `role_arn` return **zero hits in any Python file** under
`backend/`. Every Bedrock client uses ambient credentials.

> Re-run that grep without `--include="*.py"` and you get **11 hits** — all
> CloudFormation (`AssumeRolePolicyDocument`, `Action: sts:AssumeRole`,
> `*RoleArn` properties in `backend/scudo/*.yaml`). Those are IAM trust
> policies, not runtime STS calls. Do not let them convince you the code is
> already there.

**Seven construction sites**, not five — the two beyond the mapping package are
the ones that will bite you:

| File | Line | Constructs | Note |
|---|---|---|---|
| [`backend/scudo_mapping_mcp/agent.py`](backend/scudo_mapping_mcp/agent.py) | `509` | `BedrockModel(...)` | |
| [`backend/scudo_mapping_mcp/dense_scorer.py`](backend/scudo_mapping_mcp/dense_scorer.py) | `177` | `BedrockModel(...)` | |
| [`backend/scudo_mapping_mcp/opus_dense.py`](backend/scudo_mapping_mcp/opus_dense.py) | `213` | `boto3.client("bedrock-runtime", ...)` | |
| [`backend/scudo_mapping_mcp/enrichment.py`](backend/scudo_mapping_mcp/enrichment.py) | `176` | `boto3.client("bedrock-runtime", ...)` | |
| [`backend/scudo/lambda_handler.py`](backend/scudo/lambda_handler.py) | `478-479` | `BedrockModel(...)` ×2 | **different env var — see item 3** |
| [`backend/scudo/projection_handler.py`](backend/scudo/projection_handler.py) | `350` | `boto3.client("bedrock-runtime")` | **no `region_name`** — inherits ambient region |
| [`streamlit_app.py`](streamlit_app.py) | `383` | `boto3.client("bedrock-runtime", ...)` | the credential **preflight** |

Two of those deserve a sentence each:

- `projection_handler.py:350` passes **no region argument at all**, so it takes
  whatever `AWS_REGION` the Lambda happens to have. It is gated on
  `SCUDO_EMBEDDINGS_MODEL_ID` (Titan embeddings), so it stays quiet until
  someone enables embeddings — then fails in a different region from everything
  else. This is exactly the failure mode [§1.4](#14-three-different-bedrock-regions-are-hardcoded-and-they-disagree) describes.
- `streamlit_app.py:383` is the preflight that tells the operator whether
  credentials work. Miss it and **the preflight reports on a different identity
  than the one that runs the match** — a green tick over a failing pipeline.

Remaining `BedrockModel(` matches are tests and scripts
(`tests/cloudshell_demo.py`, `tests/bedrock_smoke.py`,
`scripts/ab_capone_arm.py`) — leave them.

The good news: **`BedrockModel` accepts a `boto_session` keyword.** Verified
against the installed Strands package:

```
(self, *, boto_session: boto3.session.Session | None = None,
 boto_client_config: ..., region_name: str | None = None, ...)
```

So both client types take an assumed-role session — no library limitation, just
an argument we never pass. Write **one** helper (suggested:
`backend/scudo/shared/aws_session.py`) returning a `boto3.Session`, no-op when
the role-ARN env var is unset, and thread it into all seven. Do not inline the
STS call seven times.

**3. The profile ARN needs no code change on the mapping path — but two other
places defeat it.**

Where it works, verified: `SCUDO_BEDROCK_MODEL_ID` is read at
[`agent.py:466`](backend/scudo_mapping_mcp/agent.py) and passed **unmodified**
into `BedrockModel(model_id=...)` at `:509`, and into
`client.invoke_model(modelId=...)` at [`opus_dense.py:210`](backend/scudo_mapping_mcp/opus_dense.py).
Same for `dense_scorer.py:137` and `enrichment.py:174`. Nothing validates,
parses, splits, or allow-lists it at those four sites. The
`startswith("us.anthropic.")` in the repo
([`ab_capone_arm.py:195`](backend/scudo/scripts/ab_capone_arm.py)) is inside the
`mode == "anthropic"` branch at `:190`, not the Bedrock path — it will not touch
your ARN.

**Exception 1 — the Lambda half reads a different variable.**
[`lambda_handler.py:475`](backend/scudo/lambda_handler.py) calls
`bedrock_llm_id()`, which is
[`shared/bedrock.py:44`](backend/scudo/shared/bedrock.py) —
`_env("BEDROCK_LLM_MODEL_ID", ...)`. **Setting `SCUDO_BEDROCK_MODEL_ID` will not
reach `lambda_handler.py:478-479`.** Set **both** variables to the profile ARN.
The same function feeds the LlamaIndex clients in `shared/bedrock.py:57,66`.

**Exception 2 — Streamlit overwrites your ARN.**
[`streamlit_app.py:605` and `:612`](streamlit_app.py) both do:

```python
os.environ["SCUDO_BEDROCK_MODEL_ID"] = BEDROCK_MODELS[model_label]
```

`BEDROCK_MODELS` (`:176`) is built from a prefix table (`:168`) that emits
`us.` for any `us-*` region. So in `us-east-1` the sidebar picker **silently
replaces your profile ARN** with `us.anthropic.claude-opus-4-8` on every rerun.
The comment at `:166-167` says to set `SCUDO_BEDROCK_MODEL_ID` explicitly for
uncovered regions — but that only works when the table is *empty*, which
`us-east-1` is not. Since Streamlit is step 5 in
[§0.7](#07-order-of-work), this bites at the very end and presents as a Bedrock
permissions error. **Plan an edit here**: skip the assignment when the existing
value looks like an ARN.

**One loose end:** [`backend/routes/mapping.py:1444`](backend/routes/mapping.py)
hardcodes `"eu.anthropic.claude-opus-4-8"` as a display fallback. Cosmetic — it
reports the model on a status endpoint and does not invoke anything — but it
will show the wrong model in the UI when the env var is unset.

**And a verified non-problem, so you do not "fix" it:** our request body already
matches their POC. [`opus_dense.py:227-238`](backend/scudo_mapping_mcp/opus_dense.py)
sends `anthropic_version: "bedrock-2023-05-31"`, `max_tokens`, and `messages` —
the same payload their `cdao_poc.py` sends. We are also already ahead of their
porting requirement #3: `opus_dense.py:244-257` extracts the text block and
raises when it is empty, rather than printing the raw JSON their POC prints.

### 0.5 Aurora — we already have the wire-protocol path

[§2](#2-what-aurora-actually-needs) points you at
[`aurora_store.py`](backend/scudo/aurora_store.py). **For this target that is
the wrong file.** It is RDS Data API (`boto3.client("rds-data")` at `:20`),
which is a different protocol from `DB_HOST`/`5432`. Porting it to a wire
connection is a rewrite, not a change.

No Flask route imports it today — its importers are all Lambda-side
(`etl_handler.py`, `lambda_handler.py` via `aws_resources.py`,
`projection_handler.py`, `init_data_platform.py`). That is the current import
graph, not an enforced boundary: `backend/scudo/zones/z5_persistence/`
re-exports it, so one `from scudo.zones import ...` in a route would change it.

**The right file is [`backend/db.py`](backend/db.py)** — real psycopg v3 on port
5432. Its contract maps almost 1:1 onto theirs:

| JPMC (`invoke_local.py`) | Ours ([`db.py:39-52`](backend/db.py)) |
|---|---|
| `DB_HOST` | `CONSOLE_DB_HOST` |
| `DB_PORT` (5432) | `CONSOLE_DB_PORT` (5432) |
| `DB_NAME` | `CONSOLE_DB_NAME` |
| `DB_USER` | `CONSOLE_DB_USER` |
| `DB_PASSWORD` | `CONSOLE_DB_PASSWORD` |
| `DB_SSLMODE=require` | **no equivalent — see below** |

Same protocol, same port, same driver family. Different variable names.

**The one genuine gap: `sslmode`.** Searched the whole repo — **zero hits** in
`backend/` or `infra/`. [`db.py:46-56`](backend/db.py) never passes it, so a
cluster requiring TLS refuses the connection with an error that reads like a
credentials problem.

The pattern — psycopg v3 accepts the keyword (verified against the installed
version: a *refused connection* rather than a `TypeError` proves the kwarg was
forwarded to libpq). In `_connect`, alongside the other `os.environ.get` calls:

```python
        connect_timeout=int(os.environ.get("CONSOLE_DB_CONNECT_TIMEOUT", "10")),
        sslmode=os.environ.get("CONSOLE_DB_SSLMODE", "prefer"),  # [ALUI]
```

**Two caveats, both real:**

- `prefer` is libpq's own default (verified, libpq 18), so no existing local or
  CI path changes. **But passing it explicitly also overrides a `PGSSLMODE`
  environment variable**, which the implicit path would have honoured. Nothing
  in this repo sets `PGSSLMODE` — but if the JPMC platform sets it at the task
  level, this "safe default" silently *downgrades* TLS. Set
  `CONSOLE_DB_SSLMODE=require` explicitly in the task definition rather than
  relying on the default.
- Forwarding the kwarg is not the same as proving `require` negotiates against
  their cluster. Same caveat as [§2](#2-what-aurora-actually-needs): you will be
  the first to actually run it.

Also worth knowing before you plan the port:

- **`db.py` also passes `options=f"-c search_path={search_path},public"`**
  ([`db.py:53`](backend/db.py)) and `row_factory=dict_row` (`:54`). The
  `search_path` assumes `console` and `ingestion` schemas exist on the target
  cluster. If `mds-matrix` has neither, **the first error you see will be
  `relation does not exist` on unqualified table names — not a TLS error.**
  Check the schemas before you debug the connection.
- `db.py` opens a **connection per call** with `autocommit=False` — no pool
  anywhere in `backend/`. Fine for Lambda; check it against `mds-matrix`'s
  concurrency.
- [`db.py:41-45`](backend/db.py) already **fails fast** on a missing password
  for any non-local host. Good behaviour; it will tell you immediately.
- `CONSOLE_DB_BACKEND=sqlite` swaps in
  [`db_sqlite_fallback.py`](backend/db_sqlite_fallback.py) *before* `_connect`
  is reached, so the edit above cannot break it. Useful for local work with no
  database — but it has two known gaps (psycopg `Composed` DDL, and no
  `transaction()`), so do not treat a green SQLite run as proof the Aurora path
  works.

### 0.6 Their `skip_db` risk — our equivalent, checked

Their document flags a client-controlled DB bypass in the vendor-onboarding
diff:

```python
skip_db_req = bool(payload.get("skip_db"))   # caller-controlled
```

Their remediation is right: environment-only, and prove with a test that a
normal request cannot skip persistence. **We have no equivalent hole** — no
request-payload key disables persistence in this repo. Searched `skip_db`,
`skip_persist`, `no_persist`, `dry_run` and enumerated every `body.get(...)` /
`request.args.get(...)` in [`backend/routes/mapping.py`](backend/routes/mapping.py):
no persistence toggle. Our write-side guards are env-only
(`SCUDO_PERSIST_WRITE_TOKEN` / `SCUDO_PERSIST_ALLOW_DEV_WRITES`) — exactly what
their remediation recommends. Do not import their pattern along with the code.

**One thing a security reviewer will find, so know why it is not the same
bug:** callers *can* pass `confidence_floor` and `borderline_half_width`
([`mapping.py:208-209`](backend/routes/mapping.py)), which move the
PASS/BORDERLINE/FAIL band for that one call. That changes **what** is recorded,
never **whether** — nothing is bypassed, and the values are validated
(`0 <= floor-half < floor+half <= 1`, bools and nulls rejected). Different
class of knob; worth naming before someone else finds it and doubts the rest.

The shape of that bug is one this repo has been bitten by before, though:
caller-supplied input silently overriding server-side truth. See the
`_frame` / `SCUDO_MV_ALLOW_INLINE_FRAME` contract in
[`CLAUDE.md`](CLAUDE.md) — same failure mode, already fixed here, and the fix
is env-gated exactly as their document recommends.

### 0.7 Order of work

Their constraint, and it is the right one: **Aurora before Bedrock.** Aurora is
the unverified half — their own document says *"Aurora access is therefore not
yet verified"* — and it is the half with a network dependency
(`sg-0f8198d2cd386f638` → 5432) that no amount of code gets you past.

1. Confirm the IAM path ([§0.4](#04-bedrock--three-facts-and-one-verified-non-problem) item 1). One question, potentially deletes a day of work.
2. Port Aurora: the `sslmode` addition, then the env-var mapping. Check the
   `console` / `ingestion` schemas exist on the target cluster.
3. Verify network reachability from the task to the cluster **before** writing application code.
4. Bedrock: set the ARN in **both** `SCUDO_BEDROCK_MODEL_ID` and
   `BEDROCK_LLM_MODEL_ID` ([§0.4](#04-bedrock--three-facts-and-one-verified-non-problem)
   item 3). Add the session helper across all seven sites only if step 1 said
   assume-role.
5. Streamlit last — and fix the `streamlit_app.py:605,612` ARN clobber as part
   of it, or the sidebar will discard the model ID you set in step 4.

Mark every inserted line `# [ALUI]`, per the JPMC constraint.

### 0.8 New: a FalkorDB-free matching store (`scipy_sqlite`)

**Added 2026-08-13**, from a parallel work stream. Full detail:
[`SCIPY_SQLITE_STORE_HANDOFF.md`](SCIPY_SQLITE_STORE_HANDOFF.md). **Uncommitted.**

> **Do not use a file count as your "did I get everything?" check.** This
> worktree is *deliberately* dirty with several unrelated streams in flight, so
> `git status` totals (23 modified / 19 untracked at the time of writing) are
> not this feature. Scoping by whether a file's diff mentions the new modules
> gives **~16 modified + 15 untracked** — and even that is a judgement call, not
> a fact. Diff the files linked in the handoff narrowly; never
> `git add -A` here.

This matters to you for one reason: **it removes FalkorDB from the Citrix
Streamlit deployment**, which is the environment JPMC is actually running. It
changes nothing about Aurora or Bedrock, and it does **not** reduce the work in
§0.4–§0.7.

```bash
export STORE_BACKEND=scipy_sqlite
export SCUDO_PERSIST_TARGET=scipy_sqlite
export SCUDO_SCIPY_SQLITE_PATH=/absolute/durable/path/scudo_matching.sqlite3
```

`python start_local.py` now sets all three itself
([`start_local.py:48-49,76-79`](start_local.py)) — and defaults
`SCUDO_PERSIST_TARGET` to whatever `STORE_BACKEND` is, so the two cannot drift.

**What I verified myself** (not taken from the handoff prose):

| Claim | Verified |
|---|---|
| Complete 16-method `RetrievalStore` | ✅ exactly 16 `@abstractmethod` in [`store/base.py`](backend/scudo_mapping_mcp/store/base.py) |
| Registered in the factory | ✅ [`store/factory.py:62-65`](backend/scudo_mapping_mcp/store/factory.py), and in the error string |
| Bands unchanged at `0.80/0.70` | ✅ zero band literals in the new store — scoring is untouched |
| Concurrent readers/writers | ✅ `PRAGMA journal_mode = WAL` + `busy_timeout = 5000` ([`scipy_sqlite_schema.py:293-298`](backend/scudo_mapping_mcp/store/scipy_sqlite_schema.py)) |
| Owner-only perms, symlink rejection | ✅ `_reject_symlink` + `0o600`/`0o700` (`scipy_sqlite_schema.py:265-365`) |
| AWS templates untouched | ✅ zero `scipy_sqlite` hits in `infra/` or any `*.yaml`; all still `STORE_BACKEND=falkordb` |
| `scipy` is a declared dependency | ✅ `scipy>=1.16,<2` in **both** `requirements.txt:11` and `requirements-local.txt:30` |

**"FalkorDB-free" — I confirmed this the hard way.** The `falkordb` pip package
*is* installed in this environment, so a passing test proves nothing on its own.
I re-ran the suite with a `meta_path` blocker that raises `ImportError` on any
`falkordb` import: **47 passed**. The claim holds.

> But do not read "FalkorDB-free" as "the file can go."
> [`store/retrieval_scoring.py:16`](backend/scudo_mapping_mcp/store/retrieval_scoring.py)
> does `from .falkordb_store import _jaro_winkler` — the new store imports its
> scoring function *from the FalkorDB module*. **Five non-test files import it
> for real** (`retrieval_scoring.py:16`, `opus_dense.py:149`, `factory.py:41`,
> `scripts/calibrate_confidence_floor.py:75`,
> `scudo/scripts/cleanup_stale_cdao.py:98`); 17 files reference it in total.
> **The pip package is unnecessary; the .py file is load-bearing.**
> `factory.py:35` already carries a "Do NOT delete" comment — this is why.

**Test counts, measured at my invocation:**

| Suite | Doc claims | I measured |
|---|---|---|
| `scudo_mapping_mcp/tests/` | 569 | **569 passed** ✅ |
| `scudo/tests/` | — | **478 passed, 2 failed** (the known `test_provenance.py` two) |
| Focused lifecycle/readiness | 107 | **98 passed** ⚠️ |

> ⚠️ **The 107 is overstated by 9.** All six named files exist and collect
> (2+14+45+19+8+10 = 98) — so nothing is missing or erroring, the number is just
> wrong. Everything else in that document I reproduced exactly. Note also that
> `585 passed` / `151 passed` are attributed there to a *separate* reviewer, not
> measured in this repo — I could not reproduce 585 from either invocation form
> ([§0.3](#03-test-counts--do-not-cross-check-against-theirs) applies: quote a
> count with its invocation, or do not quote it).

**If you run one thing, run this instead.** The focused six-file suite above
*omits* [`test_scipy_sqlite_integration.py`](backend/scudo_mapping_mcp/tests/test_scipy_sqlite_integration.py)
(**33 passed**) — the largest scipy_sqlite test file, and the one that covers
the `SCUDO_SCIPY_SQLITE_PATH` env plumbing you will actually be setting. Add it:

```bash
cd backend && PYTHONPATH=. python3.11 -m pytest \
  scudo_mapping_mcp/tests/test_scipy_sqlite_integration.py -q   # 33 passed
```

**Deployment boundary — this is the part to hold onto.** Safe for **one host
with one local disk**: the Citrix Streamlit desktop, Flask on a single host,
local MCP. **Not** safe for separate ECS task filesystems, Lambda `/tmp`,
independent replicas, or EFS/NFS/SMB. So:

- ✅ It **does** get FalkorDB out of the JPMC Streamlit desktop.
- ❌ It does **not** get FalkorDB out of `mds-matrix` ECS.

Removing FalkorDB from ECS still needs the shared Aurora-backed
`RetrievalStore` + shadow cutover — which does not exist yet, and is the same
"new work with a design decision behind it" that
[§1.2](#12-store_backendaurora-is-not-a-thing) warns about. If a JPMC
stakeholder hears "we removed FalkorDB" and pictures the ECS cluster, correct it
early.

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
accepts exactly **five** values, and `aurora` is not one of them:

```
falkordb | neptune | memory | local_file | scipy_sqlite
```

Anything else raises `ValueError: Unknown STORE_BACKEND '...'`. Verified by
reading the factory's own error string.

`scipy_sqlite` is the complete durable matching-store option for a single
host. It is not supported as an Aurora backend, a shared-filesystem store, or
a multi-container deployment store.

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

[`config.py`](backend/scudo_mapping_mcp/config.py) (search for
`os.getenv("STORE_BACKEND"`) defaults `STORE_BACKEND` to `local_file`.
**Every deployed template overrides it:**

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

> **Superseded for the JPMC target (2026-08-13):** JPMC supplies a full
> **application inference profile ARN**, which has no `us.`/`eu.` prefix, so the
> prefix advice does not apply. The ARN passes through our code unmodified —
> verified, see [§0.4](#04-bedrock--three-facts-and-one-verified-non-problem).
> The **region** guidance above still holds.

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

> **Wrong tool for the JPMC target (2026-08-13):**
> `list-foundation-models` does **not** list application inference profiles, so
> against `358183960702` it returns a clean-looking result that proves nothing
> about the profile you were given. Use instead:
>
> ```bash
> aws bedrock get-inference-profile \
>   --inference-profile-identifier <the profile ARN JPMC gave you> \
>   --region us-east-1
> ```
>
> (At time of writing that ARN is the `82cwakdhexaw` profile in
> `358183960702` — confirm it against the current handover rather than
> copying it, since it is environment-specific and will not survive a move
> to their non-DEV account.)
>
> The underlying point stands and is arguably sharper here: **prove invocation
> works with the deployed task identity on day one.** Their own document warns
> not to infer that a working POC proves the ECS task has the permission.

---

## 2. What Aurora actually needs

> **Scope (added 2026-08-13):** this section describes the **RDS Data API**
> path, which is what *this repository* uses. If you are deploying to the JPMC
> `mds-matrix` target, that target uses a direct PostgreSQL wire connection
> instead and this is the **wrong module** —
> read [§0.5](#05-aurora--we-already-have-the-wire-protocol-path).

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

> **Scope (added 2026-08-13):** true for a deployment of *this* repository's
> templates. For the JPMC target this framing is **inverted** — `CONSOLE_DB_*`
> is not a gap there, it is the wire-protocol path you port onto their
> `DB_*` contract. See [§0.5](#05-aurora--we-already-have-the-wire-protocol-path).

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
