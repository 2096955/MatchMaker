# Running SCUDO locally

Written for an engineer who has **not** built an agent system before. Read it
top to bottom once; then you only need "Quick start".

---

## Quick start

```bash
cd backend
pip install -r requirements-local.txt
cd ..
python start_local.py
```

Then open **http://localhost:3000**.

That is the whole thing. **No database, no Docker, no AWS account, no FalkorDB
and no Neptune are required** to see matching work end to end.

If you want the Providers / Datasets / Admin pages too, see
[Adding the database](#adding-the-database-optional).

---

## The one thing that was actually broken

`start_all.sh` launches `python3 app.py` directly. `app.py` gates every
`/api/*` request behind an authentication check, and a bare launch sets none of
the local environment variables — so **every API call returns HTTP 401** and
the UI looks dead apart from the pages that need no API.

That is the real cause of "the UI will not open" and "only one page works".
It is not MySQL, and not FalkorDB.

`start_local.py` fixes it by setting the environment *before* starting Flask:

| variable | why |
|---|---|
| `SCUDO_AUTH_ALLOW_DEV=1` | turns on local dev identity |
| `SCUDO_AUTH_DEV_PRINCIPAL=demo@local` | who you are, locally |
| `SCUDO_AUTH_ALLOW_DEV_WRITES=1` | lets you record review decisions |
| `STORE_BACKEND=local_file` | run without FalkorDB/Neptune, and remember decisions |
| `FRAME_SOURCE=mock` | read the bundled sample data, not S3 |

Measured, on this codebase:

| request | `python3 app.py` | `python start_local.py` |
|---|---|---|
| `/api/catalogue/products` | **401** | **200, real data** |
| `/api/mapping/vendors` | **401** | **200** |

(Both measured before the root route below was added; `/` returned a raw 404
on either launcher until then.)

---

## What SCUDO actually does

SCUDO maps a **vendor's** product (e.g. an LSEG data feed) onto **your own
catalogue** (the CDAO taxonomy). It is the "which of our categories is this
vendor thing?" problem, done automatically, with a human in the loop where the
machine is not confident.

One product flows through this:

```
  vendor file
      |
      v
  1. INGEST      read the row, normalise it
      |
      v
  2. RETRIEVE    pull candidate catalogue nodes
      |
      v
  3. SCORE       similarity of the product to each candidate
      |
      v
  4. GATE        confidence >= 0.80          -> auto-approve
                 0.70 .. 0.80                -> send to a human
                 < 0.70                      -> send to a human
      |
      v
  5. HUMAN       approve / override / reject
      |
      v
  6. REMEMBER    write the decision back as a "precedent"
```

Step 6 is the part that makes it a learning system, and it is described below.

### Where the "agent" is, and what it is not

The word *agent* here means: an LLM that is given **tools** (Python functions
it may call) and a goal, and decides which tools to call in what order. It is
not a chatbot and it is not a workflow engine.

Two things are worth being blunt about, because they surprise people:

1. **The similarity score is not produced by the LLM.** By default it is
   `jaro_winkler`, a deterministic string-similarity function. The same input
   always gives the same number. You can verify this by running the same match
   twice.
2. **Locally there is no LLM at all.** `SCUDO_AGENT_BACKEND` defaults to
   `scripted`, a hard-coded narrator, so the demo works offline. The LLM
   narrates and explains; the *decision* comes from the score and the gate.

   > **Watch the Provider dropdown on the Matching Test page.** Whatever it is
   > set to WINS over `SCUDO_AGENT_BACKEND` — picking "Amazon Bedrock" calls
   > AWS even when the backend is `scripted` (`agent.py get_agent`, which
   > treats an explicit provider as an override). That is deliberate, so the
   > dropdown never lies about which runtime ran. It also meant the dropdown
   > used to default to Bedrock with no offline option at all, so the "offline"
   > demo called AWS and failed. `start_local.py` now sets
   > `SCUDO_AGENT_PROVIDER_DEFAULT=scripted` and the dropdown offers
   > **Local scripted narrator (no AWS)**, preselected.

This is a deliberate design choice, not an accident: the scoring is auditable
and reproducible, which is what a bank needs. Turning on Bedrock (below) adds
LLM reasoning; it does not move the decision into the model.

---

## The learning loop — and how to actually see it

This is the part that usually gets described but never demonstrated. Do it
yourself, it takes two minutes.

> **The UI cannot do this.** The *Matching Test* page ingests a file and runs
> the agent, but it has **no approve / override / reject control** — nothing in
> `frontend/src` calls the decision endpoint. So the loop is driven with
> `curl` below. Building that control into the UI is the obvious next piece of
> work; it is not done, and this document does not pretend otherwise.

**1. Match a product.** Nothing has been decided yet:

```bash
curl -s -X POST http://127.0.0.1:5000/api/mapping/map \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-CARBON-029","name":"Carbon Data"}'
```

```
confidence 0.6623   status: needs_review   node: Market Data
rationale: FAIL band — best candidate 'Market Data' at 0.66 < threshold 0.70
```

Below the 0.70 floor, so it asks a human. Note it guessed **Market Data**.

**2. A human decides.** Suppose the right answer is `Pricing`, not Market Data:

```bash
curl -s -X POST http://127.0.0.1:5000/api/mapping/decision \
  -H 'Content-Type: application/json' \
  -d '{"vendor":"LSEG","product_id":"LSEG-CARBON-029","decision":"approve",
       "node_iri":"jpmorgan:data:cdao:subdomain:pricing",
       "name":"Carbon Data","suggested_confidence":0.5294}'
```

**3. Match the same product again.** Same request as step 1:

```
confidence 0.5294   status: approved   node: Pricing
rationale: precedent
```

It did not re-score. It returned **your** answer, `Pricing`, and said why:
`precedent`. That is the system telling you it learned.

> The confidence is the score you passed as `suggested_confidence` — the
> matcher's original number, preserved so the audit trail keeps what the
> machine thought at the time. Approving does not invent a new 0.95.

**4. Stop the server, start it again, and match a third time.** Still
`Pricing` / `precedent`.

Step 4 is the one that only works because of `STORE_BACKEND=local_file`, and it
is the whole reason that store exists. Open the file:

```
backend/local_memory/precedents.jsonl
```

One line per decision, plain readable JSON:

```json
{"ref": {"vendor": "LSEG", "product_id": "LSEG-CARBON-029", "name": "Carbon Data", ...},
 "node": {"iri": "jpmorgan:data:cdao:subdomain:pricing", "label": "Pricing"},
 "decision": "approve", "decided_by": "demo@local", "confidence": 0.5294,
 "provisional": false, "decided_at_ms": 1785856933598,
 "decided_at": "2026-08-04T15:22:13Z"}
```

**That file is the memory.** Delete it and the system forgets — *after the next
restart*. The running process still holds what it already replayed into memory,
so deleting the file mid-run does not make it forget immediately. In AWS the same
records live in Aurora (`backend/scudo/aurora_memory.py`); the file is the
laptop stand-in, and it is deliberately readable so you can see exactly what
was learned and by whom.

> Why this was needed: with `STORE_BACKEND=memory` the loop works but the
> decisions live in a Python dictionary that dies with the process, so you can
> never restart and observe that it remembered. The learning was real but
> invisible.

---

## Adding the database (optional)

Four route groups need PostgreSQL: **Providers, Datasets, Admin and the
Ingestion console** (`backend/routes/{providers,datasets,admin,ingest}.py`).
Matching, Catalogue and Matching Test do not.

```bash
docker compose up -d
export CONSOLE_DB_PASSWORD=scudo_local_dev     # Windows: set CONSOLE_DB_PASSWORD=...
python start_local.py
```

Two things that will otherwise waste an hour:

- **You must set `CONSOLE_DB_PASSWORD`.** `db.py` defaults it to an empty
  string for localhost, but the Postgres image requires a real password, and
  the resulting error does not say so.
- **`init_db.sql` runs on first start only.** It is executed against an empty
  data directory. If you change the schema, run `docker compose down -v`
  (which erases the data) to make it run again.

If Docker is blocked on your machine: skip it. The app runs without it, and
the DB-backed pages will return an error while everything else works.

---

## Turning on Bedrock

You said you already have Bedrock working from another VS Code. Three
variables, and one gotcha:

```bash
export SCUDO_AGENT_BACKEND=bedrock
export AWS_REGION=us-east-1                                  # your region
export SCUDO_BEDROCK_MODEL_ID=us.anthropic.claude-opus-4-8   # your region's profile
```

**The gotcha:** the built-in default model id is
`eu.anthropic.claude-opus-4-8` (`agent.py:123`) — an **EU** inference profile.
On a US-region account the default fails. Set `SCUDO_BEDROCK_MODEL_ID` to the
profile for your region; the `eu.` / `us.` prefix must match `AWS_REGION`.

Credentials are read by boto3 the normal way (`~/.aws/credentials`, env vars,
or an instance role) — SCUDO does not handle them itself.

Two independent switches, worth not confusing:

| variable | controls | default |
|---|---|---|
| `SCUDO_AGENT_BACKEND` | whether the **agent narration** uses an LLM | `scripted` |
| `SCUDO_DENSE_BACKEND` | whether **scoring** uses an LLM | `jaro_winkler` |

Leaving the second alone keeps scoring deterministic and auditable.

---

## FalkorDB, Neptune and MySQL

Short version: **nothing needs to be removed for a local run.**

| | status |
|---|---|
| **FalkorDB** | Not installed, not used. The `falkordb` pip package is dropped from `requirements-local.txt`. **But `store/falkordb_store.py` must stay on disk** — the default scoring path imports `_jaro_winkler` from that file. Verified: matching works fully with the *package* absent. |
| **Neptune** | Not installed, not used. `requests-aws4auth` dropped. `store/neptune_store.py` is only ever loaded if `STORE_BACKEND=neptune`. |
| **MySQL** | **There is no MySQL in the Python code at all.** `db.py` is psycopg/PostgreSQL; `init_db.sql` is PostgreSQL. The only remaining MySQL is in CloudFormation templates for the AWS deploy, which are never read locally. |

Both store branches are lazy imports in `store/factory.py`, so on a local run
those modules are never loaded and the packages need not exist. They are left
active on purpose: seven AWS deploy configs set `STORE_BACKEND=falkordb` and
would break if the branches were deleted.

---

## Troubleshooting

| symptom | cause |
|---|---|
| Every page fails, browser console shows 401 | started with `app.py` instead of `start_local.py` |
| `127.0.0.1:5000` shows JSON, not the UI | correct — the UI is on **port 3000**. :5000 is the API |
| "Failed to load providers" | Providers needs PostgreSQL. Start Docker, or use another page |
| `/readyz` returns 503 right after start | expected — the taxonomy seeds lazily on the first mapping request, then it returns ready |
| `ModuleNotFoundError: falkordb` | you installed `requirements.txt`; use `requirements-local.txt` |
| Match returns `status: out_of_scope` | vendor is case-sensitive: `LSEG`, not `lseg` |
