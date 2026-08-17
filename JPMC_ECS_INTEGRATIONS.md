# ECS `mds-matrix` — what the mapping engine needs wired in

**For:** the Terraform work on the `mds-matrix` ECS cluster (us-east-1).
**Question answered:** *"these components are created — what integrations
will be needed?"*

You have the cluster, task role, task exec role and security group. Below is
what the container actually needs to run, grouped by whether it is
**required to start**, **required for the demo**, or **optional**.

Our reference is CloudFormation (`infra/scudo-poc-app.yaml`), not Terraform —
there is no Terraform in the application repo. Treat that file as the spec to
port, but note it still carries **stale MySQL and FalkorDB wiring** that the
code no longer needs. The list below is what the current code reads.

---

## 1. The two IAM roles — what each needs

**Task exec role** (`app-mds-matrix-ecs-task-exec-…`) — pulls the image and
writes logs. Standard:

- `AmazonECSTaskExecutionRolePolicy`
- `secretsmanager:GetSecretValue` on any secret referenced in the task
  definition's `Secrets:` block (ECS resolves these **at task start**, using
  the *exec* role, not the task role — a common misattribution)
- `kms:Decrypt` if those secrets use a CMK

**Task role** (`app-mds-matrix-ecs-task-…`) — what the running app calls:

| Action | Why | Needed when |
|---|---|---|
| `bedrock:InvokeModel` | agent narration | Bedrock demo |
| **`bedrock:InvokeModelWithResponseStream`** | **the agent streams (ConverseStream)** | **Bedrock demo — ask for this by name** |
| `secretsmanager:GetSecretValue` | DB password at runtime, if not injected | Aurora |
| `rds-data:ExecuteStatement` | only if you use the RDS **Data API** path | Aurora via Data API |
| `logs:CreateLogStream`, `logs:PutLogEvents` | app logging | always |

> **The single most likely failure.** `InvokeModel` and
> `InvokeModelWithResponseStream` are authorised **separately**. A role proven
> with an `invoke_model` PoC will still be denied for the agent, and it fails
> at demo time, not at deploy time. If you use an
> **application-inference-profile ARN**, grant permission on the *profile ARN*
> as well as the underlying foundation model.

---

## 2. Integrations, by necessity

### Required to start

| Integration | Notes |
|---|---|
| **ECR** (or your image registry) | exec role needs pull rights |
| **CloudWatch Logs** log group | create it in TF; `awslogs` driver |
| **ALB + target group** | the app serves HTTP; health check path below |
| **Subnets + the SG** you created | egress 443 required (Bedrock, Secrets Manager) |

Container port: **5000** in the reference template (`ContainerPort` default).

Health checks — both already exist, and are deliberately different
([`backend/app.py:172-198`](backend/app.py)):

- `/healthz` → **liveness**. Unauthenticated, no DB. 200 means the process is
  up. **Use this for the ALB target group.**
- `/readyz` → **readiness**. 200 only once the CDAO taxonomy has seeded;
  returns **503 with the last seed error** until then. Genuinely useful, but
  if you point the ALB at it, understand that a cold task is 503 until the
  first mapping request seeds it.

Both sit outside `/api/*` so the auth gate passes them through — they will not
401 like every other route.

### Required for the demo

| Integration | Why |
|---|---|
| **Bedrock** (VPC endpoint or NAT egress) | the agent. See the role note above |
| **Aurora PostgreSQL** *(optional — see §3)* | only for the console CRUD pages |

### Optional / not needed

| Thing | Status |
|---|---|
| **FalkorDB** | not required — `STORE_BACKEND` has other options |
| **Neptune** | not on the container path |
| **MySQL** | **gone from the code.** The old template still references it; do not port that |

---

## 3. Environment variables the container reads

Minimum to boot and match, with no database and no AWS:

```
STORE_BACKEND=local_file
SCUDO_PERSIST_TARGET=local_file
FRAME_SOURCE=mock
CONSOLE_DB_BACKEND=sqlite
AWS_REGION=us-east-1
```

Add for Bedrock:

```
SCUDO_AGENT_BACKEND=bedrock
SCUDO_BEDROCK_MODEL_ID=arn:aws:bedrock:us-east-1:<acct>:application-inference-profile/<id>
```

Add for Aurora (console pages only) — and **drop** `CONSOLE_DB_BACKEND`:

```
CONSOLE_DB_HOST=<cluster-endpoint>
CONSOLE_DB_PORT=5432
CONSOLE_DB_USER=<user>
CONSOLE_DB_NAME=scudo_console
CONSOLE_DB_PASSWORD  <- inject via Secrets: in the task definition
```

Two gotchas:

- `BEDROCK_REGION` appears in the old template and is **never read** by the
  Python. Use `AWS_REGION`.
- `STORE_BACKEND=local_file` writes a journal to the container filesystem.
  That is fine for a demo but **not durable across task restarts** — mount
  EFS if you need it to persist, or accept that it resets.

---

## 4. Storage choice — the one real decision

There is **no Aurora-backed implementation of the matching store**. The
options are `memory`, `local_file`, `falkordb`, `neptune`. So:

- **Demo now:** `local_file`, no database at all.
- **Console CRUD pages:** Aurora PostgreSQL via the `CONSOLE_DB_*` vars — this
  is a *separate* database concern from matching, and needs no code change.

Do not provision Aurora expecting it to hold the matching precedents; it will
not, without new code.

---

## 5. Suggested order

1. ECR + log group + ALB, task running with the "no database" env block —
   proves the image, networking and health checks in isolation.
2. Add Bedrock permissions to the **task role**, switch `SCUDO_AGENT_BACKEND`,
   confirm streaming works.
3. Add Aurora + `CONSOLE_DB_*` only if you need Providers / Datasets / Admin.

Each step fails independently, which is the point — do not wire all three and
then debug.

---

## Related

- [`JPMC_AURORA_BEDROCK_FILES.md`](JPMC_AURORA_BEDROCK_FILES.md) — which files
  change for Aurora and Bedrock, and the credential diagnostics
- `infra/scudo-poc-app.yaml` — the CloudFormation to port (ignore its MySQL
  and FalkorDB blocks)
